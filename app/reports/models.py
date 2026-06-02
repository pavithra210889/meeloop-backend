from enum import Enum
from typing import Optional, List
from datetime import datetime, timezone
from ..datetime_utils import UTCDatetime
from pydantic import BaseModel
from sqlmodel import SQLModel, Field
from ..uuid_utils import generate_uuid


class ReportTarget(str, Enum):
    user = "user"
    post = "post"
    comment = "comment"
    loop_profile = "loop_profile"
    loop_message = "loop_message"


class ReportReason(str, Enum):
    spam = "spam"
    impersonation = "impersonation"
    hate = "hate"
    harassment = "harassment"
    sexual = "sexual"
    self_harm = "self_harm"
    violence = "violence"
    misinformation = "misinformation"
    illegal = "illegal"
    other = "other"


class ReportStatus(str, Enum):
    open = "open"
    under_review = "under_review"
    action_taken = "action_taken"
    dismissed = "dismissed"


class Report(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    reporter_id: str = Field(foreign_key="user.id")
    target_type: ReportTarget
    target_id: str
    reported_user_id: Optional[str] = Field(default=None, foreign_key="user.id")
    reason: ReportReason
    details: Optional[str] = None
    attachments: Optional[str] = Field(default=None, description="JSON array of URLs")
    status: ReportStatus = Field(default=ReportStatus.open)
    reviewed_by: Optional[str] = Field(default=None, foreign_key="user.id")
    reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReportCreate(BaseModel):
    target_type: ReportTarget
    target_id: str
    reason: ReportReason
    details: Optional[str] = None
    attachments: Optional[List[str]] = None


class ReportRead(BaseModel):
    id: str
    reporter_id: str
    target_type: ReportTarget
    target_id: str
    reported_user_id: Optional[str]
    reason: ReportReason
    details: Optional[str]
    attachments: Optional[List[str]]
    status: ReportStatus
    created_at: UTCDatetime
    reviewed_by: Optional[str]
    reviewed_at: UTCDatetime | None


class ReportStatusUpdate(BaseModel):
    status: ReportStatus
    moderation_notes: Optional[str] = None


class ModerationAction(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    report_id: str = Field(foreign_key="report.id")
    moderator_id: str = Field(foreign_key="user.id")
    action: str
    action_meta: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
