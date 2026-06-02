from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime

from app.loops.models import GenderEnum, LoopMessageType


class LoopProfilePhotoResponse(BaseModel):
    id: str
    photo_url: str
    order: int
    is_primary: bool


class LoopProfilePublic(BaseModel):
    id: str | None
    displayname: str
    bio: str | None = None
    profile_pic: str | None = None
    date_of_birth: datetime | None = None
    gender: GenderEnum | None = None
    photos: List[LoopProfilePhotoResponse] = []
    location_name: str | None = None
    location_sharing_enabled: bool = True
    distance_meters: float | None = None


class LoopProfileSetupRequest(BaseModel):
    displayname: str
    bio: str | None = None
    gender: GenderEnum | None = None
    date_of_birth: str | None = None
    photo_urls: List[str] = []


class LoopProfilePhotoAdd(BaseModel):
    photo_url: str
    order: int = 0
    is_primary: bool = False


class LoopProfilePhotoReorder(BaseModel):
    photo_ids: List[str]


class LoopMessageCreate(BaseModel):
    content: str
    message_type: LoopMessageType = LoopMessageType.TEXT
    media_url: Optional[str] = None


class LoopMessageResponse(BaseModel):
    id: str
    chat_id: str
    sender_profile_id: str
    content: str
    message_type: LoopMessageType
    media_url: Optional[str]
    created_at: datetime
    reactions: List[dict] = []


class LoopChatResponse(BaseModel):
    id: str
    last_message_at: Optional[str] = None
    last_message_content: Optional[str] = None
    other_profile: LoopProfilePublic


class LoopReactionCreate(BaseModel):
    emoji: str


class LoopReactionResponse(BaseModel):
    id: str
    message_id: str
    profile_id: str
    emoji: str
    created_at: datetime


class LoopRequestWithProfiles(BaseModel):
    id: str
    status: str
    created_at: datetime
    requester_profile: LoopProfilePublic
    receiver_profile: LoopProfilePublic


class LoopLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    location_name: str | None = None


class RandomSessionRead(BaseModel):
    id: str
    user_id: str
    started_at: datetime
    ended_at: Optional[datetime]
