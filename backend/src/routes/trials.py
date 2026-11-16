import csv
import io
import json
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from src.config.database import get_db
from src.models import BlindTrial, TrialUnit
from src.schemas.trial import TrialInput, InspectionInput, SealInput
from src.middlewares.auth import current_user, trial_owner, inspector_or_owner
from src.services import trials
from src.services.audit import record

router = APIRouter(prefix='/trials')


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    """列表同样盲态：非负责人只看到已发码的试验与盲码，不看到配方与窑次映射。"""
    query = select(BlindTrial).order_by(BlindTrial.id.desc())
    if u.role != 'admin':
        query = query.where(BlindTrial.status.in_(('locked', 'sealed', 'unblinded')))
    return [trials.trial_dict(db, t, u) for t in db.scalars(query)]


@router.get('/queue')
def queue(db=Depends(get_db), u=Depends(inspector_or_owner)):
    return trials.inspection_queue(db, u)


@router.post('', status_code=201)
def create(data: TrialInput, db=Depends(get_db), u=Depends(trial_owner)):
    trial = trials.create(db, data, u)
    db.commit()
    payload = trials.trial_dict(db, trial, u)
    claimed = trials.active_claims(db, [b for group in data.groups for b in group.batches])
    payload['claim_conflicts'] = sorted(b for b, c in claimed.items() if c.trial_id != trial.id)
    return payload


@router.post('/inspections', status_code=201)
def inspect_any(data: InspectionInput, db=Depends(get_db), u=Depends(inspector_or_owner)):
    """检验员按盲码登记，无需知道盲码所属试验。"""
    result = trials.register_inspection(db, data, u)
    db.commit()
    return {'blind_code': result.blind_code, 'fired': result.fired, 'passed': result.passed,
            'revision': result.revision}


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    """越权查询同样返回盲态序列化，不包含配方与窑次映射。"""
    trial = trials._trial(db, id)
    if u.role != 'admin':
        record(db, u.email, 'trial.read_blind', {'id': id})
        db.commit()
    return trials.trial_dict(db, trial, u)


@router.post('/{id}/lock')
def lock(id: int, db=Depends(get_db), u=Depends(trial_owner)):
    trials.lock(db, id, u)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(409, '窑次锁定冲突，请刷新后重试')
    return trials.trial_dict(db, trials._trial(db, id), u)


@router.post('/{id}/terminate')
def terminate(id: int, db=Depends(get_db), u=Depends(trial_owner)):
    trials.terminate(db, id, u)
    db.commit()
    return trials.trial_dict(db, trials._trial(db, id), u)


@router.post('/{id}/inspections', status_code=201)
def inspect(id: int, data: InspectionInput, db=Depends(get_db), u=Depends(inspector_or_owner)):
    unit = db.scalar(select(TrialUnit).where(TrialUnit.trial_id == id,
                                             TrialUnit.blind_code == data.blind_code.strip().upper()))
    if not unit:
        raise HTTPException(404, '盲码不属于该试验')
    result = trials.register_inspection(db, data, u)
    db.commit()
    return {'blind_code': result.blind_code, 'fired': result.fired, 'passed': result.passed,
            'revision': result.revision}


@router.post('/{id}/seal')
def seal(id: int, data: SealInput, db=Depends(get_db), u=Depends(trial_owner)):
    trials.seal(db, id, data, u)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(409, '封存时检验记录已变化，请刷新后重试')
    return trials.trial_dict(db, trials._trial(db, id), u)


@router.post('/{id}/unblind')
def unblind(id: int, db=Depends(get_db), u=Depends(trial_owner)):
    trials.unblind(db, id, u)
    db.commit()
    return trials.trial_dict(db, trials._trial(db, id), u)


@router.post('/{id}/review/retry')
def retry(id: int, db=Depends(get_db), u=Depends(trial_owner)):
    trials.retry_review(db, id, u)
    db.commit()
    return trials.trial_dict(db, trials._trial(db, id), u)


@router.post('/{id}/review/approve')
def approve(id: int, db=Depends(get_db), u=Depends(trial_owner)):
    review = trials.approve(db, id, u)
    db.commit()
    return {'approved': review.approved, 'approved_at': review.approved_at}


@router.get('/{id}/export')
def export(id: int, format: str = 'csv', db=Depends(get_db), u=Depends(current_user)):
    """下载同样按盲态裁剪：揭盲前不含配方、窑次映射与分组结果。"""
    trial = trials._trial(db, id)
    if format not in ('csv', 'json'):
        raise HTTPException(422, '支持 CSV 或 JSON')
    payload = trials.trial_dict(db, trial, u)
    # 下载同样按角色裁剪：非负责人永远拿不到配方与窑次映射。
    reveal = u.role == 'admin' and trial.status == 'unblinded'
    if format == 'json':
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        media, name = 'application/json', f'trial-{trial.id}.json'
    else:
        out = io.StringIO()
        out.write('﻿')
        writer = csv.writer(out)
        if reveal:
            writer.writerow(['盲码', '窑次', '组别', '配方', '烧成件数', '合格件数', '原始检验', '检验修订'])
            for row in payload.get('revealed', []):
                writer.writerow([row['blind_code'], row['batch'], row['group'], row['recipe'],
                                 row['fired'], row['passed'], row['inspection_id'], row['inspection_revision']])
        elif u.role == 'admin':
            writer.writerow(['盲码', '窑次', '组别', '配方'])
            for group in payload.get('groups', []):
                for unit in group['units']:
                    writer.writerow([unit['blind_code'], unit['batch'], group['name'], group['recipe']])
        else:
            writer.writerow(['盲码', '检验状态'])
            for unit in payload.get('blind_codes', []):
                writer.writerow([unit['blind_code'], '已登记' if unit['inspected'] else '待检验'])
        data = out.getvalue().encode('utf-8')
        media, name = 'text/csv; charset=utf-8', f'trial-{trial.id}.csv'
    record(db, u.email, 'trial.export', {'id': trial.id, 'format': format, 'revealed': reveal})
    db.commit()
    return Response(data, media_type=media,
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})
