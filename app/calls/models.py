from datetime import datetime, timezone
from ..datetime_utils import UTCDatetime
from sqlmodel import SQLModel, Field
from enum import Enum
from pydantic import BaseModel
from ..users.models import UserBasic
from ..uuid_utils import generate_uuid


class CallStatus(str, Enum):
    MISSED = "missed"
    ANSWERED = "answered"
    DECLINED = "declined"
    ONGOING = "ongoing"
    ENDED = "ended"


class Call(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    call_from: str = Field(foreign_key="user.id")
    call_to: str = Field(foreign_key="user.id")
    call_status: str = Field(default=CallStatus.MISSED)
    duration_seconds: int | None = None
    is_video_call: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CallResponse(BaseModel):
    id: str
    call_from: UserBasic
    call_to: UserBasic
    call_status: str
    duration_seconds: int | None
    is_video_call: bool
    created_at: UTCDatetime
    updated_at: UTCDatetime
