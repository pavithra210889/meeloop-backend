from fastapi import Depends, HTTPException
from typing import Annotated
from ..users.routers import get_current_active_user
from ..users.models import User


def require_superadmin(current_user: Annotated[User, Depends(get_current_active_user)]) -> User:
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_user


SuperAdminDep = Annotated[User, Depends(require_superadmin)]
