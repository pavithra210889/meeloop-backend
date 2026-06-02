from sqlmodel import Field, SQLModel, Relationship
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from ..datetime_utils import UTCDatetime
from pydantic import BaseModel
from ..uuid_utils import generate_uuid


# Helper function for setting expiration
def expire_in_24_hours():
    return datetime.now(timezone.utc) + timedelta(hours=24)


class StoryMedia(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    story_id: str = Field(foreign_key="story.id")
    media_url: str
    media_type: str

    story: Optional["Story"] = Relationship(back_populates="media_file")


class Story(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id")

    media_file: Optional[StoryMedia] = Relationship(
        back_populates="story",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    views: List["StoryView"] = Relationship(
        back_populates="story",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

    text: str | None = Field(default=None)
    expires_on: datetime = Field(default_factory=expire_in_24_hours)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        return (self.text or "")[:25] or f"Story:{self.id[:8]}"


class StoryMediaOut(SQLModel):
    id: str
    media_url: str
    media_type: str


class StoryOut(SQLModel):
    id: str
    user_id: str
    text: Optional[str]
    created_at: UTCDatetime
    updated_at: UTCDatetime
    media_file: Optional[StoryMediaOut] = None


class StoryView(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    story_id: str = Field(foreign_key="story.id")
    viewer_id: str = Field(foreign_key="user.id")
    viewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    story: Optional["Story"] = Relationship(back_populates="views")


class StoryViewerOut(BaseModel):
    viewer_id: str
    username: str
    name: str = ""
    profile_pic: Optional[str]
    viewed_at: UTCDatetime
