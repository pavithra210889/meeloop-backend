from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, Index
from geoalchemy2 import Geography
from typing import Any, Optional, List
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel
from ..uuid_utils import generate_uuid


class GenderEnum(str, Enum):
    male = "male"
    female = "female"
    other = "other"


class LoopProfile(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True)
    displayname: str
    bio: Optional[str] = None
    profile_pic: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    gender: Optional[GenderEnum] = Field(default=None, description="User's gender")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_suspended: bool = Field(default=False)
    suspended_until: Optional[datetime] = None

    # PostGIS location (SRID 4326 / WGS84)
    location: Any = Field(
        default=None,
        sa_column=Column(Geography(geometry_type="POINT", srid=4326), nullable=True),
    )
    location_name: Optional[str] = Field(default=None)
    location_updated_at: Optional[datetime] = Field(default=None)
    location_sharing_enabled: bool = Field(default=True)

    __table_args__ = (
        Index("idx_loopprofile_location_gist", "location", postgresql_using="gist"),
    )


class LoopRequestStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class LoopMessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class LoopFriend(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    loop_profile_id: str = Field(foreign_key="loopprofile.id")
    friend_profile_id: str = Field(foreign_key="loopprofile.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LoopRequest(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    requester_profile_id: str = Field(foreign_key="loopprofile.id")
    receiver_profile_id: str = Field(foreign_key="loopprofile.id")
    status: LoopRequestStatus = Field(default=LoopRequestStatus.pending)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LoopChat(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    profile1_id: str = Field(foreign_key="loopprofile.id")
    profile2_id: str = Field(foreign_key="loopprofile.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_message_at: Optional[datetime] = Field(default=None)
    last_message_content: Optional[str] = Field(default=None)
    messages: List["LoopMessage"] = Relationship(back_populates="chat")

    def __str__(self) -> str:
        return f"LChat:{self.id[:8]}"


class LoopMessage(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    chat_id: str = Field(foreign_key="loopchat.id")
    sender_profile_id: str = Field(foreign_key="loopprofile.id")
    content: str
    message_type: LoopMessageType = Field(default=LoopMessageType.TEXT)
    media_url: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_for_profile_id: Optional[str] = None
    chat: Optional[LoopChat] = Relationship(back_populates="messages")
    reactions: List["LoopReaction"] = Relationship(back_populates="message")

    def __str__(self) -> str:
        return f"{self.message_type}:{self.id[:8]}"


class LoopReaction(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    message_id: str = Field(foreign_key="loopmessage.id")
    profile_id: str = Field(foreign_key="loopprofile.id")
    emoji: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: Optional[LoopMessage] = Relationship(back_populates="reactions")


class LoopProfilePhoto(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    loop_profile_id: str = Field(foreign_key="loopprofile.id", index=True)
    photo_url: str
    order: int = Field(default=0, description="Display order 0-7")
    is_primary: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RandomSession(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    profile_id: str = Field(foreign_key="loopprofile.id")
    connected_profile_id: str = Field(foreign_key="loopprofile.id")
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: Optional[datetime] = None
