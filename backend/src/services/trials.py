"""盲评试验：按窑线登记、锁定占用、盲码检验、封存与揭盲复盘。"""
import secrets
import time
from fastapi import HTTPException
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError
from src.models import (BlindTrial, TrialGroup, TrialUnit, BatchClaim, Inspection,
                        BlindReview)
from src.services.audit import record

CODE_PREFIX = 'BLD'


def _clean(value, label, limit=60):
    text = (value or '').strip()
    if not text:
        raise HTTPException(422, f'{label}不能为空')
    if len(text) > limit:
        raise HTTPException(422, f'{label}长度不能超过 {limit} 个字符')
    return text


def _trial(db, trial_id):
    trial = db.get(BlindTrial, trial_id)
    if not trial:
        raise HTTPException(404, '盲评试验不存在')
    return trial


def _groups(db, trial_id):
    return list(db.scalars(select(TrialGroup).where(TrialGroup.trial_id == trial_id)
                           .order_by(TrialGroup.position)))


def _units(db, trial_id):
    return list(db.scalars(select(TrialUnit).where(TrialUnit.trial_id == trial_id)
                           .order_by(TrialUnit.id)))


def _inspections(db, trial_id):
    return {i.unit_id: i for i in db.scalars(
        select(Inspection).where(Inspection.trial_id == trial_id).order_by(Inspection.id))}


def _new_code(db, reserved=None):
    for _ in range(12):
        code = f'{CODE_PREFIX}-{secrets.token_hex(3).upper()}'
        if reserved is not None and code in reserved:
            continue
        if not db.scalar(select(func.count()).select_from(TrialUnit).where(TrialUnit.blind_code == code)):
            if reserved is not None:
                reserved.add(code)
            return code
    raise HTTPException(503, '盲码生成繁忙，请稍后重试')


def _enqueue_review(review_id):
    try:
        from redis import Redis
        from src.config.settings import REDIS_URL
        r = Redis.from_url(REDIS_URL, socket_timeout=2)
        r.rpush('review:queue', review_id)
        r.close()
    except Exception:
        pass  # 数据库 queued 状态由 worker 兜底


def active_claims(db, batches):
    if not batches:
        return {}
    rows = db.scalars(select(BatchClaim).where(BatchClaim.active == True, BatchClaim.batch.in_(batches)))
    return {c.batch: c for c in rows}


# ---------- 负责人：登记 ----------

def create(db, data, user):
    name = _clean(data.name, '试验名称', 160)
    line = _clean(data.line, '窑线', 60)
    if db.scalar(select(BlindTrial).where(BlindTrial.name == name)):
        raise HTTPException(409, '该试验名称已存在，请修改名称')
    groups = []
    seen = {}
    for position, raw in enumerate(data.groups):
        group_name = _clean(raw.name, '组名', 60)
        recipe = _clean(raw.recipe, '配方', 500)
        batches = []
        for value in raw.batches:
            batch = _clean(value, '窑次', 60)
            if batch in seen:
                raise HTTPException(409, f'窑次 {batch} 在同一试验中重复登记')
            seen[batch] = group_name
            batches.append(batch)
        if not batches:
            raise HTTPException(422, f'组 {group_name} 至少需要一个窑次')
        groups.append((group_name, recipe, batches))
    trial = BlindTrial(name=name, line=line, status='registered', version=0,
                       created_by=user.id, created_at=time.time(), seal_snapshot=[])
    db.add(trial)
    db.flush()
    reserved = set()
    for position, (group_name, recipe, batches) in enumerate(groups):
        group = TrialGroup(trial_id=trial.id, position=position, name=group_name, recipe=recipe)
        db.add(group)
        db.flush()
        for batch in batches:
            db.add(TrialUnit(trial_id=trial.id, group_id=group.id, batch=batch,
                             blind_code=_new_code(db, reserved)))
    # 登记阶段可以先起草；窑次占用在锁定时原子建立，避免起草中的试验长期占位。
    db.flush()
    return trial


# ---------- 负责人：锁定 ----------

def lock(db, trial_id, user):
    trial = _trial(db, trial_id)
    if trial.status == 'locked':
        return trial
    if trial.status != 'registered':
        raise HTTPException(409, '只有登记中的试验可以锁定')
    units = _units(db, trial_id)
    batches = [u.batch for u in units]
    conflict = {b: c for b, c in active_claims(db, batches).items() if c.trial_id != trial.id}
    if conflict:
        batch = sorted(conflict)[0]
        other = db.get(BlindTrial, conflict[batch].trial_id)
        raise HTTPException(409, f'窑次 {batch} 已被在途试验「{other.name}」占用')
    now = time.time()
    changed = db.execute(
        update(BlindTrial)
        .where(BlindTrial.id == trial.id, BlindTrial.status == 'registered')
        .values(status='locked', locked_at=now)
    ).rowcount
    if not changed:
        db.rollback()
        trial = _trial(db, trial_id)
        if trial.status == 'locked':
            return trial
        raise HTTPException(409, '试验状态已变化，请刷新后重试')
    claimed = set(db.scalars(select(BatchClaim.batch).where(
        BatchClaim.trial_id == trial.id, BatchClaim.active == True)))
    try:
        for batch in batches:
            if batch not in claimed:
                db.add(BatchClaim(trial_id=trial.id, batch=batch, active=True, claimed_at=now))
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '所选窑次已被其他在途试验锁定，请刷新后调整')
    record(db, user.email, 'trial.lock', {'id': trial.id, 'batches': len(batches)})
    return trial


# ---------- 负责人：终止 ----------

def terminate(db, trial_id, user):
    trial = _trial(db, trial_id)
    if trial.status in ('sealed', 'unblinded'):
        raise HTTPException(409, '已封存或已揭盲的试验不能终止')
    if trial.status == 'terminated':
        return trial
    now = time.time()
    db.execute(update(BatchClaim).where(BatchClaim.trial_id == trial.id, BatchClaim.active == True)
               .values(active=False, released_at=now))
    trial.status = 'terminated'
    trial.terminated_at = now
    record(db, user.email, 'trial.terminate', {'id': trial.id})
    return trial


# ---------- 检验：盲码登记 ----------

def register_inspection(db, data, user):
    code = (data.blind_code or '').strip().upper()
    unit = db.scalar(select(TrialUnit).where(TrialUnit.blind_code == code))
    if not unit:
        raise HTTPException(404, '盲码不存在，请核对检验单')
    trial = db.scalar(select(BlindTrial).where(BlindTrial.id == unit.trial_id).with_for_update())
    if not trial or trial.status in ('registered', 'terminated', 'unblinded'):
        raise HTTPException(409, '该盲码所属试验不在可检验状态')
    if trial.status == 'sealed':
        raise HTTPException(409, '试验已封存，检验记录已冻结')
    now = time.time()

    def bump_version():
        # 条件更新把“封存”与“检验修订”串行化：封存后版本不再增长，修订立即失败。
        changed = db.execute(update(BlindTrial)
                             .where(BlindTrial.id == trial.id, BlindTrial.status == 'locked')
                             .values(version=BlindTrial.version + 1)).rowcount
        if not changed:
            db.rollback()
            raise HTTPException(409, '试验已封存，检验记录不可再修订')

    existing = db.scalar(select(Inspection).where(Inspection.unit_id == unit.id))
    if existing:
        if data.revision is None:
            raise HTTPException(409, f'该窑次已登记，当前修订版本为 {existing.revision}')
        if data.revision != existing.revision:
            raise HTTPException(409, f'检验记录已更新到版本 {existing.revision}，请刷新后重新修订')
        bump_version()
        changed = db.execute(
            update(Inspection)
            .where(Inspection.id == existing.id, Inspection.revision == data.revision)
            .values(fired=data.fired, passed=data.passed, revision=existing.revision + 1,
                    updated_by=user.id, updated_at=now)
        ).rowcount
        if not changed:
            db.rollback()
            raise HTTPException(409, '检验记录已被其他录入更新，请刷新后重试')
        record(db, user.email, 'inspection.revise', {'trial': trial.id, 'code': code,
                                                     'revision': existing.revision + 1})
        db.flush()
        return db.scalar(select(Inspection).where(Inspection.id == existing.id))
    if data.revision not in (None, 0):
        raise HTTPException(409, '该窑次尚无检验记录，不能按修订版本提交')
    bump_version()
    inspection = Inspection(trial_id=trial.id, unit_id=unit.id, blind_code=code,
                            fired=data.fired, passed=data.passed, revision=1,
                            created_by=user.id, updated_by=user.id,
                            created_at=now, updated_at=now)
    db.add(inspection)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '该窑次已被其他录入登记，请刷新后重试')
    record(db, user.email, 'inspection.register', {'trial': trial.id, 'code': code})
    return inspection


def inspection_queue(db, user):
    trials = {t.id: t for t in db.scalars(select(BlindTrial).where(
        BlindTrial.status.in_(('locked', 'sealed'))))}
    units = list(db.scalars(select(TrialUnit).where(TrialUnit.trial_id.in_(list(trials))).order_by(TrialUnit.id)))
    inspections = {}
    if units:
        inspections = {i.unit_id: i for i in db.scalars(
            select(Inspection).where(Inspection.unit_id.in_([u.id for u in units])))}
    items = []
    for unit in units:
        trial = trials[unit.trial_id]
        inspection = inspections.get(unit.id)
        items.append({
            'blind_code': unit.blind_code,
            'trial_status': trial.status,
            'inspected': inspection is not None,
            'revision': inspection.revision if inspection else 0,
            'fired': inspection.fired if inspection else None,
            'passed': inspection.passed if inspection else None,
        })
    return items


# ---------- 负责人：封存 ----------

def _snapshot(db, trial):
    groups = {g.id: g for g in _groups(db, trial.id)}
    return [{'blind_code': u.blind_code, 'batch': u.batch, 'group': groups[u.group_id].name}
            for u in sorted(_units(db, trial.id), key=lambda x: x.blind_code)]


def seal(db, trial_id, data, user):
    trial = db.scalar(select(BlindTrial).where(BlindTrial.id == trial_id).with_for_update())
    if not trial:
        raise HTTPException(404, '盲评试验不存在')
    if trial.status == 'sealed':
        return trial
    if trial.status != 'locked':
        raise HTTPException(409, '只有已锁定的试验可以封存')
    if trial.version != data.revision:
        raise HTTPException(409, '检验记录已发生变化，请刷新后重新封存')
    units = _units(db, trial.id)
    inspections = _inspections(db, trial.id)
    missing = [u.blind_code for u in units if u.id not in inspections]
    if missing:
        raise HTTPException(409, f'还有 {len(missing)} 个盲码未完成检验，不能封存')
    now = time.time()
    changed = db.execute(
        update(BlindTrial)
        .where(BlindTrial.id == trial.id, BlindTrial.status == 'locked',
               BlindTrial.version == data.revision)
        .values(status='sealed', seal_version=data.revision, sealed_at=now,
                seal_snapshot=_snapshot(db, trial))
    ).rowcount
    if not changed:
        db.rollback()
        raise HTTPException(409, '检验记录已发生变化，请刷新后重新封存')
    trial = _trial(db, trial_id)
    review = db.scalar(select(BlindReview).where(BlindReview.trial_id == trial.id))
    if not review:
        review = BlindReview(trial_id=trial.id, status='pending', attempts=0,
                             snapshot_version=data.revision, created_at=now)
        db.add(review)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, '封存状态已变化，请刷新后重试')
    record(db, user.email, 'trial.seal', {'id': trial.id, 'revision': data.revision})
    return trial


# ---------- 负责人：揭盲 ----------

def unblind(db, trial_id, user):
    trial = _trial(db, trial_id)
    if trial.status == 'unblinded':
        return trial
    if trial.status != 'sealed':
        raise HTTPException(409, '只有封存后的试验可以揭盲')
    now = time.time()
    trial.status = 'unblinded'
    trial.unblinded_at = now
    # 揭盲后试验不再在途，释放窑次占用以便后续批次复核登记。
    db.execute(update(BatchClaim).where(BatchClaim.trial_id == trial.id, BatchClaim.active == True)
               .values(active=False, released_at=now))
    review = db.scalar(select(BlindReview).where(BlindReview.trial_id == trial.id))
    if not review:
        review = BlindReview(trial_id=trial.id, status='queued', attempts=0,
                             snapshot_version=trial.seal_version or trial.version, created_at=now)
        db.add(review)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            trial = _trial(db, trial_id)
            trial.status = 'unblinded'
            trial.unblinded_at = now
            review = db.scalar(select(BlindReview).where(BlindReview.trial_id == trial.id))
    elif review.status == 'pending':
        review.status = 'queued'
        review.snapshot_version = trial.seal_version or trial.version
    record(db, user.email, 'trial.unblind', {'id': trial.id})
    db.flush()
    if review and review.status == 'queued':
        _enqueue_review(review.id)
    return trial


def retry_review(db, trial_id, user):
    trial = _trial(db, trial_id)
    if trial.status != 'unblinded':
        raise HTTPException(409, '尚未揭盲，不能生成复盘')
    review = db.scalar(select(BlindReview).where(BlindReview.trial_id == trial.id))
    if not review:
        raise HTTPException(404, '复盘记录不存在，请重新揭盲')
    if review.status == 'completed' and review.result:
        return review
    if review.status in ('queued', 'running'):
        return review
    review.status = 'queued'
    review.error = ''
    record(db, user.email, 'review.retry', {'id': trial.id})
    db.flush()
    _enqueue_review(review.id)
    return review


# ---------- 复盘生成（worker） ----------

def generate_review(db, review_id):
    review = db.get(BlindReview, review_id)
    if not review or review.status != 'queued':
        return None
    claimed = db.execute(
        update(BlindReview)
        .where(BlindReview.id == review.id, BlindReview.status == 'queued')
        .values(status='running')
    ).rowcount
    if not claimed:
        db.rollback()
        return None
    db.commit()
    review = db.get(BlindReview, review_id)
    trial = db.get(BlindTrial, review.trial_id)
    if not trial or trial.status != 'unblinded':
        review.status = 'failed'
        review.error = '试验未处于揭盲状态'
        db.commit()
        return review
    try:
        review.attempts += 1
        groups = {g.id: g for g in _groups(db, trial.id)}
        units = _units(db, trial.id)
        inspections = _inspections(db, trial.id)
        rows = []
        group_rows = []
        for group in sorted(groups.values(), key=lambda g: g.position):
            members = [u for u in units if u.group_id == group.id]
            rows_for_group = []
            for unit in sorted(members, key=lambda u: u.blind_code):
                inspection = inspections.get(unit.id)
                if not inspection:
                    raise ValueError(f'盲码 {unit.blind_code} 缺少原始检验')
                row = {
                    'blind_code': unit.blind_code, 'batch': unit.batch,
                    'group': group.name, 'recipe': group.recipe,
                    'fired': inspection.fired, 'passed': inspection.passed,
                    'yield': round(inspection.passed / inspection.fired * 100, 3),
                    'inspection_id': inspection.id, 'inspection_revision': inspection.revision,
                }
                rows.append(row)
                rows_for_group.append(row)
            fired = sum(r['fired'] for r in rows_for_group)
            passed = sum(r['passed'] for r in rows_for_group)
            group_rows.append({
                'name': group.name, 'recipe': group.recipe,
                'units': len(rows_for_group), 'fired': fired, 'passed': passed,
                'yield': round(passed / fired * 100, 3) if fired else 0.0,
                'linked_inspections': [r['inspection_id'] for r in rows_for_group],
            })
        best = max(group_rows, key=lambda g: g['yield'])
        worst = min(group_rows, key=lambda g: g['yield'])
        delta = round(best['yield'] - worst['yield'], 3)
        if delta == 0:
            summary = f'{trial.line} 窑线两组试验完成揭盲：两组加权良率均为 {best["yield"]:.2f}%。'
        else:
            summary = (f'{trial.line} 窑线两组试验完成揭盲：{best["name"]} 加权良率 '
                       f'{best["yield"]:.2f}%，与 {worst["name"]} 相差 {delta:.2f} 个百分点。')
        review.result = {
            'summary': summary,
            'groups': group_rows,
            'units': sorted(rows, key=lambda r: (r['group'], r['blind_code'])),
            'comparison': {'leading': best['name'], 'trailing': worst['name'],
                           'yield_delta': delta, 'same_line': True},
            'metrics': [
                {'label': '试验组数', 'value': len(group_rows), 'unit': '组'},
                {'label': '已检验窑次', 'value': len(rows), 'unit': '窑'},
                {'label': '加权良率差', 'value': delta, 'unit': '百分点'},
                {'label': '检验修订', 'value': sum(i.revision - 1 for i in inspections.values()), 'unit': '次'},
            ],
            'findings': [
                f'{g["name"]}：{g["units"]} 窑，{g["fired"]} 件烧成，加权良率 {g["yield"]:.2f}%。'
                for g in group_rows
            ],
            'method': '揭盲后按组汇总烧成与合格件数，以件数加权计算良率；每窑结果关联原始检验编号与修订版本。',
            'warnings': ['复盘只比较同一窑线内的工艺差异，不构成因果结论，也不自动判废。'],
        }
        review.status = 'completed'
        review.error = ''
        review.completed_at = time.time()
        record(db, 'worker', 'review.completed', {'trial': trial.id, 'groups': len(group_rows)})
        db.commit()
    except Exception as exc:
        db.rollback()
        review = db.get(BlindReview, review_id)
        if review:
            review.status = 'failed'
            review.error = str(exc)[:990]
            review.completed_at = time.time()
            record(db, 'worker', 'review.failed', {'trial': review.trial_id, 'error': review.error})
            db.commit()
        raise
    return review


def approve(db, trial_id, user):
    trial = _trial(db, trial_id)
    review = db.scalar(select(BlindReview).where(BlindReview.trial_id == trial.id))
    if not review:
        raise HTTPException(409, '复盘尚未生成，不能批准')
    if review.approved:
        return review
    if trial.status != 'unblinded':
        raise HTTPException(409, '只有揭盲后的复盘可以批准')
    if review.status != 'completed' or not review.result:
        raise HTTPException(409, '复盘尚未完成，半成品不可批准')
    if review.snapshot_version != (trial.seal_version or review.snapshot_version):
        raise HTTPException(409, '复盘基于过期封存版本，请重新生成')
    if len(review.result.get('groups', [])) != 2:
        raise HTTPException(409, '复盘未包含完整两组结果，不可批准')
    review.approved = True
    review.approved_by = user.id
    review.approved_at = time.time()
    record(db, user.email, 'review.approve', {'trial': trial.id})
    return review


# ---------- 序列化 ----------

def _is_owner(user):
    return bool(user) and user.role == 'admin'


def _unblinded(trial):
    return trial.status in ('unblinded',)


def trial_dict(db, trial, viewer=None):
    """盲态序列化：揭盲前检验员只得到盲码，配方、窑次与分组映射不返回。"""
    groups = _groups(db, trial.id)
    units = _units(db, trial.id)
    inspections = _inspections(db, trial.id)
    review = db.scalar(select(BlindReview).where(BlindReview.trial_id == trial.id))
    owner = _is_owner(viewer)
    # 配方与窑次映射只向负责人开放；揭盲后其余角色仍只见盲码与件数。
    revealed = owner and _unblinded(trial)
    show_mapping = owner  # 负责人登记配方，始终可见；其他角色只见盲码
    data = {
        'id': trial.id, 'name': trial.name, 'line': trial.line, 'status': trial.status,
        'version': trial.version, 'seal_version': trial.seal_version,
        'created_at': trial.created_at, 'locked_at': trial.locked_at,
        'sealed_at': trial.sealed_at, 'unblinded_at': trial.unblinded_at,
        'terminated_at': trial.terminated_at,
        'group_count': len(groups), 'unit_count': len(units),
        'inspected_count': sum(1 for u in units if u.id in inspections),
        # blinded 描述“当前查看者是否仍处于盲态”：非负责人即使试验已揭盲也只看到盲码。
        'blinded': not revealed,
        'can_reveal': owner and _unblinded(trial),
        'review': {'status': review.status, 'attempts': review.attempts,
                   'approved': review.approved, 'approved_at': review.approved_at,
                   'error': review.error, 'snapshot_version': review.snapshot_version}
                  if review else None,
    }
    if show_mapping:
        data['groups'] = [{'name': g.name, 'recipe': g.recipe,
                           'units': [{'blind_code': u.blind_code, 'batch': u.batch,
                                      'inspected': u.id in inspections}
                                     for u in units if u.group_id == g.id]}
                          for g in groups]
    else:
        data['blind_codes'] = [
            {'blind_code': u.blind_code, 'inspected': u.id in inspections,
             'fired': inspections[u.id].fired if u.id in inspections else None,
             'passed': inspections[u.id].passed if u.id in inspections else None,
             'revision': inspections[u.id].revision if u.id in inspections else 0}
            for u in units]
    if revealed:
        mapping = {g.id: g for g in groups}
        data['revealed'] = [
            {'blind_code': u.blind_code, 'batch': u.batch,
             'group': mapping[u.group_id].name, 'recipe': mapping[u.group_id].recipe,
             'fired': inspections[u.id].fired if u.id in inspections else None,
             'passed': inspections[u.id].passed if u.id in inspections else None,
             'inspection_id': inspections[u.id].id if u.id in inspections else None,
             'inspection_revision': inspections[u.id].revision if u.id in inspections else None}
            for u in units
        ]
    if revealed and review and review.status == 'completed' and review.result:
        data['result'] = review.result
    return data
