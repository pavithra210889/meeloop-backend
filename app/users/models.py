from app.loops.models import GenderEnum
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, Index
from geoalchemy2 import Geography
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, EmailStr
from datetime import datetime, timezone
from ..datetime_utils import UTCDatetime
from ..uuid_utils import generate_uuid

if TYPE_CHECKING:
    from ..messages.models import Chat, Message, Reaction
    from ..posts.models import Post, Comment


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    user_id: str | None = None
    username: str | None = None  # Kept for backward compatibility


class BaseUser(SQLModel):
    name: str
    username: str
    email: EmailStr | None = None


class UserCreate(BaseUser):
    password: str
    date_of_birth: datetime | None = None
    gender: GenderEnum | None = None


class FCMToken(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id")
    token: str = Field(unique=True, description="FCM registration token")
    device_id: str | None = Field(default=None, description="Device identifier")
    device_type: str | None = Field(
        default=None, description="Device type (android, ios, web)"
    )
    is_active: bool = Field(
        default=True, description="Whether this token is currently active"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    user: "User" = Relationship(back_populates="fcm_tokens")


class Follow(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    follower_id: str = Field(foreign_key="user.id")
    following_id: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    follower: "User" = Relationship(
        back_populates="following",
        sa_relationship_kwargs={"foreign_keys": "[Follow.follower_id]"},
    )
    following: "User" = Relationship(
        back_populates="followers",
        sa_relationship_kwargs={"foreign_keys": "[Follow.following_id]"},
    )


class Block(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    blocker_id: str = Field(foreign_key="user.id")
    blocked_id: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    blocker: "User" = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[Block.blocker_id]"}
    )
    blocked: "User" = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[Block.blocked_id]"}
    )


class User(BaseUser, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    password: str | None = Field(
        default=None, description="Password hash (nullable for OAuth users)"
    )
    is_active: bool = Field(default=True)
    is_verified: bool = Field(default=False)
    bio: str = Field(default="started using meme application")
    profile_pic: str = Field(default="/defaults/profile/default_user.png")
    date_of_birth: datetime | None = Field(
        default=None, description="User's date of birth"
    )
    gender: GenderEnum | None = Field(default=None, description="User's gender")
    is_loop_enabled: bool = Field(
        default=False, description="Whether user has enabled loop feature"
    )
    suspended_until: datetime | None = Field(
        default=None, description="Account suspension end time"
    )
    google_id: str | None = Field(
        default=None, unique=True, description="Google user ID"
    )
    truecaller_id: str | None = Field(
        default=None, unique=True, description="Truecaller user ID"
    )
    facebook_id: str | None = Field(
        default=None, unique=True, description="Facebook user ID"
    )
    phone_number: str | None = Field(
        default=None, unique=True, description="User's phone number"
    )
    auth_provider: str = Field(
        default="local",
        description="Authentication provider: 'local', 'google', 'truecaller', 'facebook', or 'phone'",
    )
    mfa_enabled: bool = Field(default=False, description="Whether MFA is enabled")
    mfa_secret: str | None = Field(default=None, description="TOTP Secret Key")
    backup_codes: str | None = Field(default=None, description="JSON list of hashed backup codes")
    is_superadmin: bool = Field(default=False, description="Whether user has super admin access")
    is_private: bool = Field(default=False, description="Whether account is private (only approved followers see posts)")

    def __str__(self) -> str:
        return f"@{self.username}" if self.username else self.id[:8]

    chat_participant_one: list["Chat"] = Relationship(
        back_populates="participant_one",
        sa_relationship_kwargs={"primaryjoin": "Chat.participant_one_id == User.id"},
    )
    chat_participant_two: list["Chat"] = Relationship(
        back_populates="participant_two",
        sa_relationship_kwargs={"primaryjoin": "Chat.participant_two_id == User.id"},
    )
    sent_messages: list["Message"] = Relationship(
        back_populates="sender",
        sa_relationship_kwargs={"primaryjoin": "Message.sender_id == User.id"},
    )
    received_messages: list["Message"] = Relationship(
        back_populates="receiver",
        sa_relationship_kwargs={"primaryjoin": "Message.receiver_id == User.id"},
    )
    posts: list["Post"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"primaryjoin": "Post.posted_by == User.id"},
    )
    comments: list["Comment"] = Relationship(back_populates="commented_by")

    followers: list["Follow"] = Relationship(
        back_populates="following",
        sa_relationship_kwargs={"foreign_keys": "[Follow.following_id]"},
    )
    following: list["Follow"] = Relationship(
        back_populates="follower",
        sa_relationship_kwargs={"foreign_keys": "[Follow.follower_id]"},
    )
    fcm_tokens: list["FCMToken"] = Relationship(back_populates="user")
    reactions: list["Reaction"] = Relationship(back_populates="user")
    sessions: list["UserSession"] = Relationship(back_populates="user")
    created_meme_templates: list["MemeTemplates"] = Relationship(
        back_populates="created_by",
        sa_relationship_kwargs={"foreign_keys": "[MemeTemplates.created_by_id]"},
    )
    updated_meme_templates: list["MemeTemplates"] = Relationship(
        back_populates="updated_by",
        sa_relationship_kwargs={"foreign_keys": "[MemeTemplates.updated_by_id]"},
    )
    passkeys: list["UserPasskey"] = Relationship(back_populates="user")

    # PostGIS location (SRID 4326 / WGS84)
    location: Any = Field(
        default=None,
        sa_column=Column(Geography(geometry_type="POINT", srid=4326), nullable=True),
    )
    location_name: str | None = Field(default=None)
    location_updated_at: datetime | None = Field(default=None)

    __table_args__ = (
        Index("idx_user_location_gist", "location", postgresql_using="gist"),
    )


class UserPreference(SQLModel, table=True):
    """User app preferences (synced across devices)"""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)
    ui_mode: str = Field(default="MODERN", description="UI layout mode")
    theme_mode: str = Field(default="SYSTEM", description="Theme: SYSTEM, LIGHT, DARK")
    language: str = Field(default="en", description="App language code")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserPasskey(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id")
    credential_id: str = Field(index=True, description="WebAuthn Credential ID")
    public_key: str = Field(description="WebAuthn Public Key")
    sign_count: int = Field(default=0, description="Signature counter")
    transports: str | None = Field(default=None, description="Comma-separated transports")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = Field(default=None)
    name: str | None = Field(default=None, description="Friendly name for the passkey")

    user: "User" = Relationship(back_populates="passkeys")


class UserBasic(BaseModel):
    id: str
    username: str
    name: str
    profile_pic: str | None = None
    bio: str | None = None
    is_following: bool = False


class FollowerResponse(BaseModel):
    id: str
    follower: UserBasic
    following_id: str
    created_at: UTCDatetime
    is_following: bool | None = None


class FollowingResponse(BaseModel):
    id: str
    follower_id: str
    following: UserBasic
    created_at: UTCDatetime
    is_following: bool | None = None


class UserStatsResponse(SQLModel):
    user_id: str
    post_count: int
    followers_count: int
    following_count: int
    is_following: bool


class FCMTokenCreate(BaseModel):
    token: str
    device_id: str | None = None
    device_type: str | None = None


class FCMTokenUpdate(BaseModel):
    token: str
    device_id: str | None = None
    device_type: str | None = None
    is_active: bool | None = None


class TruecallerAuthRequest(BaseModel):
    request_id: str
    access_token: str


class FacebookAuthRequest(BaseModel):
    access_token: str


class LoginResponse(UserBasic):
    access_token: str | None = None
    token_type: str | None = None
    mfa_required: bool = False
    pre_auth_token: str | None = None
    message: str | None = None
    available_methods: list[str] | None = None
    is_superadmin: bool = False


class UserSeo(BaseModel):
    id: str
    username: str
    name: str
    profile_pic: str
    bio: str
    post_count: int
    followers_count: int
    following_count: int
    is_private: bool


class UserDevice(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id")
    device_id: str | None = Field(
        default=None, description="Client-provided device identifier"
    )
    public_key: str | None = Field(
        default=None, description="RSA Public Key for E2EE"
    )
    user_agent: str | None = Field(default=None, description="Raw User-Agent header")
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_ip: str | None = Field(default=None)
    last_login_at: datetime | None = Field(default=None)
    is_active: bool = Field(default=True)


class LoginActivity(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str | None = Field(default=None, foreign_key="user.id")
    device_id: str | None = Field(default=None)
    ip_address: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)
    success: bool = Field(default=True)
    reason: str | None = Field(default=None)
    login_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OTP(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    phone_number: str = Field(index=True, description="Phone number for OTP")
    otp_code: str = Field(description="The OTP code")
    expires_at: datetime = Field(description="OTP expiration time")
    attempts: int = Field(default=0, description="Number of verification attempts")
    is_verified: bool = Field(
        default=False, description="Whether OTP has been verified"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OTPRequest(BaseModel):
    phone_number: str


class OTPVerify(BaseModel):
    phone_number: str
    otp_code: str


class UserSession(SQLModel, table=True):
    """Server-side session for user authentication"""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    session_token: str = Field(
        unique=True, index=True, description="Unique session token"
    )
    device_id: str | None = Field(default=None, description="Device identifier")
    user_agent: str | None = Field(default=None, description="User agent string")
    ip_address: str | None = Field(default=None, description="IP address")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(description="Session expiration time")
    last_activity: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Last activity timestamp"
    )
    is_active: bool = Field(default=True, description="Whether session is active")

    user: "User" = Relationship(back_populates="sessions")


class SessionResponse(BaseModel):
    """Response model for session information"""

    id: str
    device_id: str | None
    user_agent: str | None
    ip_address: str | None
    created_at: UTCDatetime
    last_activity: UTCDatetime
    is_current: bool = False  # True if this is the current session


class GoogleSignInRequest(BaseModel):
    id_token: str = Field(..., alias="id_token")
