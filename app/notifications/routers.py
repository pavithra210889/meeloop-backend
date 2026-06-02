from fastapi import APIRouter, Depends, HTTPException, Query
from .models import Notification, NotificationPreference
from .schemas import NotificationResponse, NotificationPreferenceResponse, NotificationPreferenceUpdate
from .services.notification_service import notification_service
from .services.preference_service import preference_service
from ..users.models import User
from ..users.routers import get_current_active_user
from ..dependencies import SessionDep
from typing import Annotated, Optional, List

router = APIRouter(tags=["notifications"])


@router.get("/notifications/", response_model=List[NotificationResponse])
async def get_notifications(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    category: Optional[str] = Query(None, description="Filter by category"),
    is_read: Optional[bool] = Query(None, description="Filter by read status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    notifications = await notification_service.get_notifications(
        user_id=current_user.id,
        session=session,
        category=category,
        is_read=is_read,
        limit=limit,
        offset=offset
    )
    return notifications


@router.get("/notifications/unread-count/")
async def get_unread_count(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    category: Optional[str] = Query(None, description="Filter by category")
):
    """Get unread notification count"""
    count = await notification_service.get_unread_count(
        user_id=current_user.id,
        session=session,
        category=category
    )
    return {"count": count}


@router.post("/notifications/{notification_id}/read/", response_model=NotificationResponse)
async def mark_as_read(
    notification_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep
):
    """Mark notification as read"""
    try:
        notification = await notification_service.mark_as_read(
            notification_id=notification_id,
            user_id=current_user.id,
            session=session
        )
        return notification
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/notifications/read-all/")
async def mark_all_as_read(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    category: Optional[str] = Query(None, description="Mark all in category")
):
    """Mark all notifications as read"""
    count = await notification_service.mark_all_as_read(
        user_id=current_user.id,
        session=session,
        category=category
    )
    return {"marked_count": count}


@router.delete("/notifications/{notification_id}/")
async def delete_notification(
    notification_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep
):
    """Delete notification (soft delete)"""
    success = await notification_service.delete_notification(
        notification_id=notification_id,
        user_id=current_user.id,
        session=session
    )
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"detail": "Notification deleted"}


@router.delete("/notifications/clear-all/")
async def clear_all_notifications(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    category: Optional[str] = Query(None, description="Clear only this category")
):
    """Delete all notifications for current user"""
    count = await notification_service.clear_all_notifications(
        user_id=current_user.id,
        session=session,
        category=category
    )
    return {"deleted_count": count}


@router.get("/notifications/preferences/", response_model=NotificationPreferenceResponse)
async def get_preferences(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep
):
    """Get notification preferences"""
    preference = await preference_service.get_preferences(
        user_id=current_user.id,
        session=session
    )
    return preference


@router.put("/notifications/preferences/", response_model=NotificationPreferenceResponse)
async def update_preferences(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    preferences: NotificationPreferenceUpdate
):
    """Update notification preferences"""
    updates = preferences.model_dump(exclude_unset=True)
    preference = await preference_service.update_preferences(
        user_id=current_user.id,
        session=session,
        **updates
    )
    return preference
