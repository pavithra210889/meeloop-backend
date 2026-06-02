from sqlmodel import Column, SQLModel, Field, JSON, Index
from datetime import datetime, timezone
from typing import Dict, Optional
from ..uuid_utils import generate_uuid


class Notification(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)

    # Core fields
    notification_type: str = Field(index=True)
    notification_category: str = Field(index=True)

    # Recipient & Sender
    recipient_id: str = Field(foreign_key="user.id", index=True)
    sender_id: str | None = Field(default=None, foreign_key="user.id", nullable=True, index=True)

    # Content
    title: str
    message: str
    image_url: str | None = None

    # Navigation
    redirect_to: str | None = None
    redirect_type: str | None = None
    redirect_id: str | None = None

    # Status
    is_read: bool = Field(default=False, index=True)
    read_at: datetime | None = None
    is_pushed: bool = Field(default=False, index=True)
    push_sent_at: datetime | None = None
    push_failed: bool = Field(default=False)
    push_error: str | None = None

    # Aggregation
    group_key: str | None = Field(default=None, index=True)
    aggregated_count: int = Field(default=1)

    # Metadata
    meta: Dict = Field(default_factory=dict, sa_column=Column(JSON))
    priority: int = Field(default=0, index=True)  # 0=normal, 1=high, 2=urgent

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    # Soft delete
    deleted_at: datetime | None = None

    __table_args__ = (
        Index('idx_notification_recipient_read_created', 'recipient_id', 'is_read', 'created_at'),
        Index('idx_notification_recipient_category_created', 'recipient_id', 'notification_category', 'created_at'),
    )


class NotificationPreference(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)

    # Main toggle
    notifications_enabled: bool = Field(default=True)

    # Category toggles
    messages_enabled: bool = Field(default=True)  # Includes all message types (text, media, reactions, etc.)
    calls_enabled: bool = Field(default=True)
    missed_calls_enabled: bool = Field(default=True)
    posts_enabled: bool = Field(default=True)
    likes_enabled: bool = Field(default=True)
    comments_enabled: bool = Field(default=True)
    comment_replies_enabled: bool = Field(default=True)
    stories_enabled: bool = Field(default=True)
    followers_enabled: bool = Field(default=True)
    loop_friend_requests_enabled: bool = Field(default=True)
    loop_messages_enabled: bool = Field(default=True)  # Includes all loop message types (text, reactions, etc.)
    loop_matches_enabled: bool = Field(default=True)
    account_security_enabled: bool = Field(default=True)
    login_alerts_enabled: bool = Field(default=True)
    account_status_enabled: bool = Field(default=True)
    reports_enabled: bool = Field(default=True)

    # Additional settings
    quiet_hours_start: Optional[str] = None  # Time as string "HH:MM"
    quiet_hours_end: Optional[str] = None
    quiet_hours_enabled: bool = Field(default=False)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
