import httpx
from typing import Optional
from ..config import settings
import logging
import json

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Service for sending WhatsApp messages via Meta Cloud API"""

    def __init__(self):
        self.phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.access_token = settings.WHATSAPP_ACCESS_TOKEN
        self.template_name = settings.WHATSAPP_TEMPLATE_NAME
        self.api_version = "v21.0"  # Updated to a recent version

    async def send_otp(self, phone_number: str, otp_code: str) -> bool:
        """
        Send OTP code via WhatsApp using Meta Cloud API.
        
        Args:
            phone_number: The phone number to send OTP to (with country code, no +)
            otp_code: The OTP code to send
            
        Returns:
            True if message sent successfully, False otherwise
        """
        if not settings.ENABLE_OTP:
            # Safe mode: Log OTP and return success without sending
            logger.info(f"[SAFEMODE - MOCK WHATSAPP] OTP for {phone_number}: {otp_code}")
            return True

        if not self.phone_number_id or not self.access_token:
            logger.error("WhatsApp configuration missing (ID or Token)")
            return False

        # Prepare phone number: remove + if present
        clean_phone = phone_number.replace("+", "").replace(" ", "").replace("-", "")
        
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}"
        }
        
        # Construct payload based on the user's provided example
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "template",
            "template": {
                "name": self.template_name,
                "language": {
                    "code": "en"
                },
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {
                                "type": "text",
                                "text": otp_code
                            }
                        ]
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [
                            {
                                "type": "text",
                                "text": otp_code
                            }
                        ]
                    }
                ]
            }
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    print(f"Meta API Response: {json.dumps(data)}")
                    print(f"WhatsApp OTP sent to {clean_phone[:3]}***. ID: {data.get('messages', [{}])[0].get('id')}")
                    return True
                else:
                    print(f"WhatsApp API error: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            print(f"Error sending WhatsApp message: {e}")
            return False


# Singleton instance
whatsapp_service = WhatsAppService()
