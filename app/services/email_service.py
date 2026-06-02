import logging
from typing import List, Optional
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from pydantic import EmailStr
from ..config import settings
from pathlib import Path

logger = logging.getLogger(__name__)

class EmailService:
    """Service for sending emails"""

    def __init__(self):
        self.enabled = bool(settings.MAIL_USERNAME and settings.MAIL_PASSWORD)
        if self.enabled:
            self.conf = ConnectionConfig(
                MAIL_USERNAME=settings.MAIL_USERNAME,
                MAIL_PASSWORD=settings.MAIL_PASSWORD,
                MAIL_FROM=settings.MAIL_FROM,
                MAIL_PORT=settings.MAIL_PORT,
                MAIL_SERVER=settings.MAIL_SERVER,
                MAIL_STARTTLS=settings.MAIL_STARTTLS,
                MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
                USE_CREDENTIALS=settings.USE_CREDENTIALS,
                VALIDATE_CERTS=True
            )
            self.fastmail = FastMail(self.conf)
        else:
            print("Email service not configured")
            self.fastmail = None

    async def send_otp(self, email: str, otp_code: str) -> bool:
        """
        Send OTP via Email.
        """
        if not self.enabled:
            print(f"[MOCK EMAIL - SERVICE DISABLED] To: {email}, OTP: {otp_code}")
            return True

        if not settings.ENABLE_OTP:
            print(f"[SAFEMODE - MOCK EMAIL] To: {email}, OTP: {otp_code}")
            return True

        message = MessageSchema(
            subject="Your Meeloop Verification Code",
            recipients=[email],
            body=f"<p>Your meeloop verification code is: <strong>{otp_code}</strong></p><p>This code will expire in 10 minutes.</p>",
            subtype=MessageType.html
        )

        try:
            await self.fastmail.send_message(message)
            print(f"Email sent to {email}")
            return True
        except Exception as e:
            print(f"Error sending email: {e}")
            return False

# Singleton instance
email_service = EmailService()
