from fastapi.security import OAuth2PasswordBearer
import jwt
import secrets
from passlib.context import CryptContext

from datetime import datetime, timedelta, timezone
from .config import settings

# Use settings from config.py instead of hardcoded values
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES


pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict):
    """Create JWT token (kept for backward compatibility, but sessions are preferred)"""
    expires_delta = ACCESS_TOKEN_EXPIRE_MINUTES
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + timedelta(minutes=expires_delta)
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str):
    """Decode JWT token (kept for backward compatibility)"""
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    return payload


def generate_session_token() -> str:
    """Generate a secure random session token"""
    return secrets.token_urlsafe(32)  # 32 bytes = 43 characters base64url encoded


def create_registration_token(contact_info: str) -> str:
    """
    Create a temporary registration token for verified users who haven't completed signup.
    Expires in 15 minutes.
    """
    expires_delta = timedelta(minutes=15)
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {"sub": contact_info, "scope": "registration", "exp": expire}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_registration_token(token: str) -> str | None:
    """
    Verify and decode a registration token.
    Returns the contact_info (email or phone) if valid, None otherwise.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        scope = payload.get("scope")
        if scope != "registration":
            return None
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def create_reset_token(contact_info: str) -> str:
    """
    Create a temporary password reset token. Expires in 15 minutes.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode = {"sub": contact_info, "scope": "password_reset", "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_reset_token(token: str) -> str | None:
    """
    Verify and decode a password reset token.
    Returns the contact_info if valid, None otherwise.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("scope") != "password_reset":
            return None
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
