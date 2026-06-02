from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    notification_type: str
    notification_category: str
    recipient_id: str
    sender_id: Optional[str]
    title: str
    message: str
    image_url: Optional[str]
    redirect_to: Optional[str]
    redirect_type: Optional[str]
    redirect_id: Optional[str]
    is_read: bool
    read_at: Optional[datetime]
    is_pushed: bool
    push_sent_at: Optional[datetime]
    priority: int
    meta: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class NotificationPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    notifications_enabled: bool
    messages_enabled: bool
    calls_enabled: bool
    missed_calls_enabled: bool
    posts_enabled: bool
    likes_enabled: bool
    comments_enabled: bool
    comment_replies_enabled: bool
    stories_enabled: bool
    followers_enabled: bool
    loop_friend_requests_enabled: bool
    loop_messages_enabled: bool
    loop_matches_enabled: bool
    account_security_enabled: bool
    login_alerts_enabled: bool
    account_status_enabled: bool
    reports_enabled: bool
    quiet_hours_start: Optional[str]
    quiet_hours_end: Optional[str]
    quiet_hours_enabled: bool
    created_at: datetime
    updated_at: datetime


class NotificationPreferenceUpdate(BaseModel):
    notifications_enabled: Optional[bool] = None
    messages_enabled: Optional[bool] = None
    calls_enabled: Optional[bool] = None
    missed_calls_enabled: Optional[bool] = None
    posts_enabled: Optional[bool] = None
    likes_enabled: Optional[bool] = None
    comments_enabled: Optional[bool] = None
    comment_replies_enabled: Optional[bool] = None
    stories_enabled: Optional[bool] = None
    followers_enabled: Optional[bool] = None
    loop_friend_requests_enabled: Optional[bool] = None
    loop_messages_enabled: Optional[bool] = None
    loop_matches_enabled: Optional[bool] = None
    account_security_enabled: Optional[bool] = None
    login_alerts_enabled: Optional[bool] = None
    account_status_enabled: Optional[bool] = None
    reports_enabled: Optional[bool] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    quiet_hours_enabled: Optional[bool] = None

