from sqlmodel import SQLModel, Field
from datetime import datetime, timezone
from ..uuid_utils import generate_uuid


class AdminAuditLog(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    admin_id: str = Field(foreign_key="user.id", index=True)
    action: str = Field(description="Action taken, e.g. 'ban_user', 'delete_post'")
    target_type: str | None = Field(default=None, description="e.g. 'user', 'post', 'report'")
    target_id: str | None = Field(default=None)
    meta: str | None = Field(default=None, description="JSON extra info")
    ip_address: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
