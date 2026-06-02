import os
import warnings
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings:
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./database.sqlite")
    DATABASE_ECHO: bool = os.getenv("DATABASE_ECHO", "false").lower() == "true"

    # Security
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "3e9283738c952a00f6e81b72cbb2d0c94826b38b6ae934c066789cd5f912cdc6"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES") or "30"
    )
    SESSION_EXPIRE_DAYS: int = int(
        os.getenv("SESSION_EXPIRE_DAYS") or "30"
    )

    # Passkeys (WebAuthn)
    RP_ID: str = os.getenv("RP_ID", "localhost")
    RP_NAME: str = os.getenv("RP_NAME", "Meeloop")
    RP_ORIGIN: str = os.getenv("RP_ORIGIN", "http://localhost:3000")
    # Comma-separated Android APK key hashes (base64url of SHA-256 cert fingerprint)
    # Include both release and debug hashes for dev testing
    ANDROID_APK_KEY_HASHES: str = os.getenv("ANDROID_APK_KEY_HASHES", "")

    # GIPHY (GIF search)
    GIPHY_API_KEY: Optional[str] = os.getenv("GIPHY_API_KEY")

    # Google Maps (static map proxy)
    GOOGLE_MAPS_API_KEY: Optional[str] = os.getenv("GOOGLE_MAPS_API_KEY")

    # Firebase
    FIREBASE_CREDENTIALS: Optional[str] = os.getenv("FIREBASE_CREDENTIALS")
    FIREBASE_PROJECT_ID: Optional[str] = os.getenv("FIREBASE_PROJECT_ID")

    # Media
    MEDIA_ROOT: str = os.getenv("MEDIA_ROOT", "media")
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE") or "10485760")  # 10MB

    # Cloudflare R2
    R2_ACCOUNT_ID: str = os.getenv("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID: str = os.getenv("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY: str = os.getenv("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET_NAME: str = os.getenv("R2_BUCKET_NAME", "")
    R2_PUBLIC_URL: str = os.getenv("R2_PUBLIC_URL", "")
    R2_REGION: str = os.getenv("R2_REGION", "auto")

    def __init__(self):
        """Initialize settings and validate configuration."""
        # Validate R2_PUBLIC_URL
        if self.R2_PUBLIC_URL and not self.R2_PUBLIC_URL.startswith(
            ("http://", "https://")
        ):
            warnings.warn(
                f"R2_PUBLIC_URL '{self.R2_PUBLIC_URL}' should include protocol (http:// or https://). "
                "The service will automatically add https:// if missing.",
                UserWarning,
            )

    # Notifications
    ENABLE_PUSH_NOTIFICATIONS: bool = (
        os.getenv("ENABLE_PUSH_NOTIFICATIONS", "true").lower() == "true"
    )

    # CORS
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "*").split(",")

    # Socket.IO
    SOCKETIO_CORS_ORIGINS: str = os.getenv("SOCKETIO_CORS_ORIGINS", "*")

    # Redis (for Socket.IO multi-worker support)
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Admins (comma-separated usernames)
    ADMIN_USERNAMES: str = os.getenv("ADMIN_USERNAMES", "")

    # IP geolocation (ipinfo.io)
    IPINFO_TOKEN: Optional[str] = os.getenv("IPINFO_TOKEN")

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: Optional[str] = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI: Optional[str] = os.getenv("GOOGLE_REDIRECT_URI")
    # Extra client IDs accepted by the backend (comma-separated, e.g. desktop app client ID)
    GOOGLE_EXTRA_CLIENT_IDS: str = os.getenv("GOOGLE_EXTRA_CLIENT_IDS", "")

    # Truecaller SDK
    TRUECALLER_APP_KEY: Optional[str] = os.getenv("TRUECALLER_APP_KEY")

    # Facebook OAuth
    FACEBOOK_APP_ID: Optional[str] = os.getenv("FACEBOOK_APP_ID")
    FACEBOOK_APP_SECRET: Optional[str] = os.getenv("FACEBOOK_APP_SECRET")
    FACEBOOK_REDIRECT_URI: Optional[str] = os.getenv("FACEBOOK_REDIRECT_URI")

    # SMS Service Configuration (MSG91)
    # For OTP-based phone authentication
    # Options: "msg91" (production) or "mock" (development - logs OTP instead of sending)
    SMS_PROVIDER: str = os.getenv("SMS_PROVIDER", "msg91")  # Options: "msg91", "mock"
    ENABLE_OTP: bool = os.getenv("ENABLE_OTP", "true").lower() == "true"

    # MSG91 Configuration
    MSG91_AUTH_KEY: Optional[str] = os.getenv("MSG91_AUTH_KEY")
    MSG91_TEMPLATE_ID: Optional[str] = os.getenv("MSG91_TEMPLATE_ID")

    # WhatsApp Configuration (Meta Cloud API)
    WHATSAPP_PHONE_NUMBER_ID: Optional[str] = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    WHATSAPP_ACCESS_TOKEN: Optional[str] = os.getenv("WHATSAPP_ACCESS_TOKEN")
    WHATSAPP_TEMPLATE_NAME: str = os.getenv("WHATSAPP_TEMPLATE_NAME", "otp_default")

    # Email Configuration (SMTP)
    MAIL_USERNAME: Optional[str] = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD: Optional[str] = os.getenv("MAIL_PASSWORD")
    MAIL_FROM: Optional[str] = os.getenv("MAIL_FROM")
    MAIL_PORT: int = int(os.getenv("MAIL_PORT") or "587")
    MAIL_SERVER: Optional[str] = os.getenv("MAIL_SERVER")
    MAIL_STARTTLS: bool = os.getenv("MAIL_STARTTLS", "true").lower() == "true"
    MAIL_SSL_TLS: bool = os.getenv("MAIL_SSL_TLS", "false").lower() == "true"
    USE_CREDENTIALS: bool = os.getenv("USE_CREDENTIALS", "true").lower() == "true"

    # TURN Server (coturn with use-auth-secret)
    TURN_SECRET: str = os.getenv("TURN_SECRET", "")
    TURN_SERVER: str = os.getenv("TURN_SERVER", "80.225.224.38:3478")
    TURN_TTL: int = int(os.getenv("TURN_TTL") or "3600")

    # Environment
    ENVIRONMENT: str = os.getenv(
        "ENVIRONMENT", "development"
    )  # Options: "development", "production"

    # Logging
    LOG_PAYLOADS: bool = os.getenv("LOG_PAYLOADS", "false").lower() == "true"


settings = Settings()
