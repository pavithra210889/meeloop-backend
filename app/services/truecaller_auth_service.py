import httpx
from typing import Optional, Dict, Any
from ..config import settings
import logging

logger = logging.getLogger(__name__)


class TruecallerAuthService:
    """Service for verifying Truecaller SDK tokens and fetching user information"""
    
    TRUECALLER_VERIFY_URL = "https://api4.truecaller.com/v1/verify"
    
    def __init__(self):
        self.app_key = settings.TRUECALLER_APP_KEY
        if not self.app_key:
            logger.warning("TRUECALLER_APP_KEY not configured")
    
    async def verify_request_id(
        self, 
        request_id: str,
        access_token: str
    ) -> Optional[Dict[str, Any]]:
        """
        Verify a Truecaller request ID and access token.
        
        According to Truecaller SDK documentation, after the user authenticates,
        the SDK returns a requestId and accessToken that need to be verified
        on the backend.
        
        Args:
            request_id: The request ID returned by Truecaller SDK
            access_token: The access token returned by Truecaller SDK
            
        Returns:
            Dictionary containing verified user information or None if invalid
        """
        if not self.app_key:
            logger.error("Truecaller App Key not configured")
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.TRUECALLER_VERIFY_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "requestId": request_id,
                        "appKey": self.app_key
                    },
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    logger.error(
                        f"Truecaller verification failed: {response.status_code} - {response.text}"
                    )
                    return None
                
                verification_data = response.json()
                
                # Check if verification was successful
                if not verification_data.get("status") == "success":
                    logger.error(f"Truecaller verification failed: {verification_data}")
                    return None
                
                return verification_data
                
        except httpx.TimeoutException:
            logger.error("Timeout while verifying Truecaller token")
            return None
        except httpx.RequestError as e:
            logger.error(f"Error verifying Truecaller token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error verifying Truecaller token: {e}")
            return None
    
    def extract_user_info(self, verification_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract user information from Truecaller verification response.
        
        Args:
            verification_data: The verified response from Truecaller API
            
        Returns:
            Dictionary containing user information (phoneNumber, name, etc.)
        """
        if not verification_data:
            return None
        
        # Extract user profile from verification data
        # The structure may vary, but typically includes:
        # - phoneNumber
        # - name
        # - email (if available)
        # - profilePicture (if available)
        
        user_info = {
            "phone_number": verification_data.get("phoneNumber"),
            "name": verification_data.get("name", ""),
            "email": verification_data.get("email"),
            "profile_picture": verification_data.get("profilePicture"),
            "truecaller_id": verification_data.get("requestId"),  # Use requestId as unique identifier
        }
        
        # Ensure we have at least phone number
        if not user_info.get("phone_number"):
            logger.error("No phone number in Truecaller verification response")
            return None
        
        return user_info


# Singleton instance
truecaller_auth_service = TruecallerAuthService()

