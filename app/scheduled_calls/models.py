from datetime import datetime, timezone
from enum import Enum
from sqlmodel import SQLModel, Field
from ..uuid_utils import generate_uuid


class ScheduledCallStatus(str, Enum):
    PENDING = "pending"
    REMINDED = "reminded"
    TRIGGERED = "triggered"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ScheduledCall(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    scheduler_id: str = Field(foreign_key="user.id", index=True)
    participant_id: str = Field(foreign_key="user.id", index=True)
    scheduled_at: datetime = Field(index=True)
    is_video_call: bool = Field(default=False)
    status: str = Field(default=ScheduledCallStatus.PENDING)
    note: str | None = None
    call_id: str | None = Field(default=None, foreign_key="call.id")
    reminder_sent_at: datetime | None = None
    trigger_sent_at: datetime | None = None
    cancelled_by: str | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
