from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime, timezone
from app.loops.models import GenderEnum
import re
from enum import Enum


class LocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    location_name: str | None = None


class NearbyUserResponse(BaseModel):
    id: str
    username: str
    name: str
    profile_pic: str | None = None
    bio: str | None = None
    distance_meters: float
    location_name: str | None = None


class BaseUser(BaseModel):
    name: str
    username: str
    email: EmailStr | None = None


class UserCreate(BaseUser):
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=50, description="User's full name")
    username: Optional[str] = Field(None, min_length=3, max_length=30, description="Unique username")
    email: Optional[EmailStr] = None  # Email updates are not allowed for security reasons
    bio: Optional[str] = Field(None, max_length=150, description="User's bio")
    profile_pic: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    gender: Optional[GenderEnum] = None
    is_private: Optional[bool] = Field(None, description="Whether account is private")

    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        if v is None:
            return v
        # Username should only contain letters, numbers, and underscores
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('Username can only contain letters, numbers, and underscores')
        return v

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if v is None:
            return v
        # Name should not be empty or just whitespace
        if not v.strip():
            raise ValueError('Name cannot be empty or just whitespace')
        return v.strip()

    @field_validator('date_of_birth')
    @classmethod
    def validate_date_of_birth(cls, v):
        if v is None:
            return v
        # Date of birth cannot be in the future
        if v > datetime.now(timezone.utc):
            raise ValueError('Date of birth cannot be in the future')
        return v


class User(BaseUser):
    id: str | None
    is_active: bool
    is_verified: bool
    bio: str
    profile_pic: str
    phone_number: str | None = None


_PLACEHOLDER_EMAIL_SUFFIXES = ("@phone.placeholder", "@truecaller.temp")


class UserMeResponse(BaseModel):
    """Response schema for /me/ — email is None for phone-only accounts."""
    id: str
    username: str
    name: str
    email: str | None = None
    phone_number: str | None = None
    bio: str
    profile_pic: str | None = None
    is_active: bool
    is_verified: bool
    is_loop_enabled: bool = False
    mfa_enabled: bool = False
    date_of_birth: datetime | None = None
    gender: str | None = None


class SignupRequest(BaseModel):
    registration_token: str
    name: str = Field(min_length=2, max_length=50)
    username: str = Field(min_length=5, max_length=30)
    password: str = Field(min_length=6)
    date_of_birth: datetime | None = None
    gender: GenderEnum | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str | None = None
    new_password: str = Field(min_length=6)
    confirm_password: str = Field(min_length=6)


class OTPChannelEnum(str, Enum):
    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class OTPRequest(BaseModel):
    contact: str  # Can be phone number or email
    channel: OTPChannelEnum = OTPChannelEnum.SMS

    @field_validator('channel')
    @classmethod
    def validate_channel(cls, v, values):
        contact = values.data.get('contact')
        if contact and '@' in contact and v != OTPChannelEnum.EMAIL:
             # Basic check: if it looks like an email, channel must be email
             # (Logic can be refined, but client should send correct inputs)
             pass
        return v


class OTPVerify(BaseModel):
    contact: str
    otp_code: str


class UpdatePhoneRequest(BaseModel):
    phone_number: str


class VerifyUpdatePhoneRequest(BaseModel):
    phone_number: str
    otp_code: str


class UpdateEmailRequest(BaseModel):
    email: EmailStr


class VerifyUpdateEmailRequest(BaseModel):
    email: EmailStr
    otp_code: str


class MFAVerifyRequest(BaseModel):
    code: str = Field(..., description="6-digit TOTP code")


class MFASetupResponse(BaseModel):
    secret: str
    qr_code_url: str
    backup_codes: list[str] | None = None


class MFALoginResponse(BaseModel):
    mfa_required: bool = True
    pre_auth_token: str
    message: str = "MFA verification required"
    available_methods: list[str] = ["totp"]


class PasskeyRegistrationOptions(BaseModel):
    options: dict


class PasskeyAuthenticationOptions(BaseModel):
    options: dict


class PasskeyRegistrationFinish(BaseModel):
    credential: dict


class DeviceKeyUpload(BaseModel):
    public_key: str


class ForgotPasswordRequest(BaseModel):
    contact: str  # Email or phone number
    channel: OTPChannelEnum = OTPChannelEnum.EMAIL


class ResetPasswordVerify(BaseModel):
    contact: str
    otp_code: str


class ResetPasswordComplete(BaseModel):
    reset_token: str
    new_password: str = Field(min_length=6)
    confirm_password: str = Field(min_length=6)


class UserKeysResponse(BaseModel):
    user_id: str
    devices: dict[str, str] # device_id -> public_key


class UserPreferenceResponse(BaseModel):
    model_config = {"from_attributes": True}

    ui_mode: str
    theme_mode: str
    language: str


class UserPreferenceUpdate(BaseModel):
    ui_mode: Optional[str] = None
    theme_mode: Optional[str] = None
    language: Optional[str] = None

