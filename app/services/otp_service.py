import random
import string
from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import Session, select
import logging

from ..users.models import OTP
from ..config import settings

logger = logging.getLogger(__name__)


class OTPService:
    """Service for generating, storing, and verifying OTPs"""
    
    OTP_LENGTH = 6
    OTP_EXPIRY_MINUTES = 10
    MAX_ATTEMPTS = 5
    OTP_RESEND_COOLDOWN_SECONDS = 60  # 1 minute cooldown between OTP requests
    
    def generate_otp(self) -> str:
        """Generate a random 6-digit OTP"""
        return ''.join(random.choices(string.digits, k=self.OTP_LENGTH))
    
    def create_otp(self, phone_number: str, session: Session) -> Optional[str]:
        """
        Create and store an OTP for a phone number or email.
        
        Args:
            phone_number: The phone number or email to send OTP to
            session: Database session
            
        Returns:
            The generated OTP code or None if failed
        """
        # Check if it's an email
        is_email = "@" in phone_number
        
        if not is_email:
            # Normalize phone number (remove spaces, dashes, etc.)
            phone_number = self.normalize_phone_number(phone_number)
        
        if not phone_number:
            logger.error("Invalid contact format")
            return "INVALID_FORMAT"

        # Check for recent OTP request (cooldown)
        # TODO: re-enable cooldown after testing
        # recent_otp = session.exec(
        #     select(OTP)
        #     .where(OTP.phone_number == phone_number)
        #     .where(OTP.is_verified == False)
        #     .order_by(OTP.created_at.desc())
        # ).first()
        #
        # if recent_otp:
        #     time_since_last = (datetime.now() - recent_otp.created_at).total_seconds()
        #     if time_since_last < self.OTP_RESEND_COOLDOWN_SECONDS:
        #         remaining = int(self.OTP_RESEND_COOLDOWN_SECONDS - time_since_last)
        #         logger.warning(f"OTP request too soon. Wait {remaining} seconds")
        #         return "RATE_LIMITED"
        
        # Invalidate any existing unverified OTPs for this phone number
        existing_otps = session.exec(
            select(OTP)
            .where(OTP.phone_number == phone_number)
            .where(OTP.is_verified == False)
        ).all()
        
        for otp in existing_otps:
            otp.is_verified = True  # Mark as used/invalid
            session.add(otp)
        
        # Generate new OTP
        otp_code = self.generate_otp()
        expires_at = datetime.now() + timedelta(minutes=self.OTP_EXPIRY_MINUTES)
        
        # Store OTP
        otp = OTP(
            phone_number=phone_number,
            otp_code=otp_code,
            expires_at=expires_at,
            attempts=0,
            is_verified=False
        )
        
        try:
            session.add(otp)
            session.commit()
            session.refresh(otp)
            masked_contact = phone_number
            if not is_email and len(phone_number) > 3:
                 masked_contact = phone_number[:3] + "***"
            logger.info(f"OTP created for: {masked_contact}")
            return otp_code
        except Exception as e:
            logger.error(f"Failed to create OTP: {e}")
            session.rollback()
            return None
    
    def verify_otp(self, phone_number: str, otp_code: str, session: Session) -> bool:
        """
        Verify an OTP code for a phone number or email.
        
        Args:
            phone_number: The phone number
            otp_code: The OTP code to verify
            session: Database session
            
        Returns:
            True if OTP is valid, False otherwise
        """
        # Check if it's an email
        is_email = "@" in phone_number
        
        if not is_email:
            phone_number = self.normalize_phone_number(phone_number)
        
        if not phone_number or not otp_code:
            return False
        
        # Find the most recent unverified OTP for this phone number
        otp = session.exec(
            select(OTP)
            .where(OTP.phone_number == phone_number)
            .where(OTP.is_verified == False)
            .order_by(OTP.created_at.desc())
        ).first()
        
        if not otp:
            logger.warning(f"No OTP found for phone number: {phone_number[:3]}***")
            return False
        
        # Check if OTP has expired
        if datetime.now() > otp.expires_at:
            logger.warning(f"OTP expired for phone number: {phone_number[:3]}***")
            otp.is_verified = True  # Mark as used
            session.add(otp)
            session.commit()
            return False
        
        # Check max attempts
        if otp.attempts >= self.MAX_ATTEMPTS:
            logger.warning(f"Max attempts reached for phone number: {phone_number[:3]}***")
            otp.is_verified = True  # Mark as used
            session.add(otp)
            session.commit()
            return False
        
        # Increment attempts
        otp.attempts += 1
        session.add(otp)
        
        # Verify OTP code
        if otp.otp_code == otp_code:
            otp.is_verified = True
            session.commit()
            logger.info(f"OTP verified successfully for phone number: {phone_number[:3]}***")
            return True
        else:
            session.commit()
            logger.warning(f"Invalid OTP code for phone number: {phone_number[:3]}***")
            return False
    
    def normalize_phone_number(self, phone_number: str) -> str:
        """
        Normalize phone number by removing spaces, dashes, and other non-digit characters.
        Keeps + sign if present at the start.
        
        Args:
            phone_number: Raw phone number string
            
        Returns:
            Normalized phone number
        """
        if not phone_number:
            return ""
        
        if not phone_number.startswith("+"):
            return ""
            
        normalized = "+" + ''.join(c for c in phone_number[1:] if c.isdigit())
        return normalized
    
    def cleanup_expired_otps(self, session: Session) -> int:
        """
        Clean up expired OTPs from the database.
        
        Args:
            session: Database session
            
        Returns:
            Number of OTPs deleted
        """
        try:
            expired_otps = session.exec(
                select(OTP).where(OTP.expires_at < datetime.now())
            ).all()
            
            count = len(expired_otps)
            for otp in expired_otps:
                session.delete(otp)
            
            session.commit()
            logger.info(f"Cleaned up {count} expired OTPs")
            return count
        except Exception as e:
            logger.error(f"Failed to cleanup expired OTPs: {e}")
            session.rollback()
            return 0


# Singleton instance
otp_service = OTPService()

