from datetime import datetime
from ..datetime_utils import UTCDatetime
from pydantic import BaseModel
from typing import Optional
from ..users.models import UserBasic


class ScheduledCallCreate(BaseModel):
    participant_id: str
    scheduled_at: datetime
    is_video_call: bool = False
    note: Optional[str] = None


class ScheduledCallUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    is_video_call: Optional[bool] = None
    note: Optional[str] = None


class ScheduledCallResponse(BaseModel):
    id: str
    scheduler: UserBasic
    participant: UserBasic
    scheduled_at: UTCDatetime
    is_video_call: bool
    status: str
    note: Optional[str]
    call_id: Optional[str]
    created_at: UTCDatetime
    updated_at: UTCDatetime
