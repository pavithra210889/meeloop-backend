from sqlmodel import SQLModel, Field
from pydantic import BaseModel
from datetime import datetime, timezone
from sqlalchemy import UniqueConstraint
from ..uuid_utils import generate_uuid


class Contact(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("contact_owner_id", "normalized_number", name="uq_contact_owner_number"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    name: str
    email: str | None = Field(default=None)
    phone_num: str
    normalized_number: str = Field(index=True)
    contact_owner_id: str | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BaseContact(BaseModel):
    name: str
    email: str | None = None
    phone_num: str
