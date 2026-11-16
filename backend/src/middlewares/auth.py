from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.config.database import get_db
from src.models import User
from src.utils.security import decode_token
bearer = HTTPBearer(auto_error=False)
def current_user(token: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
    try:
        user = db.get(User, decode_token(token.credentials))
        if user is None: raise ValueError('missing user')
        return user
    except Exception:
        raise HTTPException(401, '登录已过期，请重新登录')
def can_write(user=Depends(current_user)):
    if user.role not in ('admin','analyst'): raise HTTPException(403, '观察员只有读取权限')
    return user
def admin_only(user=Depends(current_user)):
    if user.role != 'admin': raise HTTPException(403, '需要业务负责人批准')
    return user
def trial_owner(user=Depends(current_user)):
    """盲评试验的登记、锁定、封存、揭盲与终止只允许负责人执行。"""
    if user.role != 'admin': raise HTTPException(403, '盲评试验需要业务负责人操作')
    return user
def inspector_or_owner(user=Depends(current_user)):
    if user.role not in ('admin','inspector'): raise HTTPException(403, '仅检验员或负责人可登记检验件数')
    return user
