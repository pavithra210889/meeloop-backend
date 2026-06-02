from datetime import datetime, timezone
from enum import Enum
from sqlmodel import Relationship, Text, JSON, SQLModel, Field
from ..users.models import User
from ..uuid_utils import generate_uuid


class TemplateType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    STICKER = "sticker"


class MemeTemplates(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    template_type: TemplateType = Field(default=TemplateType.IMAGE)

    content: str
    urls: list[str] = Field(sa_type=Text)
    hash_tags: list[str] = Field(sa_type=JSON)
    metadata_info: dict = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_id: str = Field(foreign_key="user.id")
    updated_by_id: str = Field(foreign_key="user.id")

    created_by: User = Relationship(back_populates="created_meme_templates",sa_relationship_kwargs=dict(foreign_keys="[MemeTemplates.created_by_id]"))
    updated_by: User = Relationship(back_populates="updated_meme_templates",sa_relationship_kwargs=dict(foreign_keys="[MemeTemplates.updated_by_id]"))
