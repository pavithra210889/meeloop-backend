import httpx
from typing import Optional
from ..config import settings
import logging
import json

logger = logging.getLogger(__name__)


class SMSService:
    """Service for sending SMS messages (OTP codes) via MSG91"""
    
    def __init__(self):
        self.provider = settings.SMS_PROVIDER or "mock"
        self.msg91_auth_key = settings.MSG91_AUTH_KEY
        self.msg91_template_id = settings.MSG91_TEMPLATE_ID
        
    async def send_otp(self, phone_number: str, otp_code: str) -> bool:
        """
        Send OTP code via SMS using MSG91.
        
        Args:
            phone_number: The phone number to send OTP to
            otp_code: The OTP code to send
            
        Returns:
            True if SMS sent successfully, False otherwise
        """
        # Check if real OTP sending is enabled
        if not settings.ENABLE_OTP:
            # Safe mode: print OTP to console and return success without sending
            print(f"\n{'='*50}")
            print(f"📱 OTP for {phone_number}: {otp_code}")
            print(f"{'='*50}\n")
            return True

        if self.provider == "msg91":
            return await self._send_via_msg91(phone_number, otp_code)
        elif self.provider == "mock":
            # For development/testing - print OTP to console
            print(f"\n📱 [MOCK SMS] OTP for {phone_number}: {otp_code}\n")
            return True
        else:
            logger.error(f"Unknown SMS provider: {self.provider}. Supported: 'msg91', 'mock'")
            return False
    
    async def _send_via_msg91(self, phone_number: str, otp_code: str) -> bool:
        """Send SMS via MSG91 Flow API"""
        if not self.msg91_auth_key:
            print("MSG91 Auth Key not configured")
            return False
        
        if not self.msg91_template_id:
            print("MSG91 Template ID (Flow ID) not configured")
            return False
        
        try:
            # Prepare phone number: remove + if present
            clean_phone = phone_number.replace("+", "").replace(" ", "").replace("-", "")
            
            url = "https://control.msg91.com/api/v5/flow"
            
            headers = {
                "accept": "application/json",
                "authkey": self.msg91_auth_key,
                "content-type": "application/json"
            }
            
            payload = {
                "template_id": self.msg91_template_id,
                "recipients": [
                    {
                        "mobiles": clean_phone,
                        "var1": otp_code
                    }
                ]
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    print(f"MSG91 SMS Response: {json.dumps(data)}")
                    return True
                else:
                    print(f"MSG91 API error: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            print(f"Error sending SMS via MSG91: {e}")
            return False


# Singleton instance
sms_service = SMSService()

