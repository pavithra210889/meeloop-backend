import httpx
from typing import Optional, Dict, Any
from ..config import settings
import logging

logger = logging.getLogger(__name__)


class GoogleAuthService:
    """Service for verifying Google OAuth tokens and fetching user information"""
    
    GOOGLE_TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"
    GOOGLE_USER_INFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
    
    def __init__(self):
        self.client_id = settings.GOOGLE_CLIENT_ID
        extra = settings.GOOGLE_EXTRA_CLIENT_IDS
        self.accepted_client_ids: set = (
            {self.client_id} if self.client_id else set()
        ) | {cid.strip() for cid in extra.split(",") if cid.strip()}
        if not self.client_id:
            logger.warning("GOOGLE_CLIENT_ID not configured")
    
    async def verify_id_token(self, id_token: str) -> Optional[Dict[str, Any]]:
        """
        Verify a Google ID token and return the token payload.
        
        Args:
            id_token: The Google ID token to verify
            
        Returns:
            Dictionary containing token information (sub, email, name, etc.) or None if invalid
        """
        if not self.accepted_client_ids:
            logger.error("Google Client ID not configured")
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                # Verify token with Google
                response = await client.get(
                    self.GOOGLE_TOKEN_INFO_URL,
                    params={"id_token": id_token},
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    logger.error(f"Google token verification failed: {response.status_code} - {response.text}")
                    return None
                
                token_info = response.json()
                
                # Verify the audience (client_id) is one of the accepted client IDs
                if token_info.get("aud") not in self.accepted_client_ids:
                    logger.error(f"Token audience mismatch. Accepted {self.accepted_client_ids}, got {token_info.get('aud')}")
                    return None
                
                # Check if token is expired (Google includes 'exp' claim)
                # The tokeninfo endpoint already validates expiration, but we can double-check
                
                return token_info
                
        except httpx.TimeoutException:
            logger.error("Timeout while verifying Google token")
            return None
        except httpx.RequestError as e:
            logger.error(f"Error verifying Google token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error verifying Google token: {e}")
            return None
    
    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Fetch user information from Google using an access token.
        
        Args:
            access_token: Google OAuth access token
            
        Returns:
            Dictionary containing user information (id, email, name, picture, etc.) or None if failed
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.GOOGLE_USER_INFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    logger.error(f"Failed to fetch user info: {response.status_code} - {response.text}")
                    return None
                
                return response.json()
                
        except httpx.TimeoutException:
            logger.error("Timeout while fetching user info from Google")
            return None
        except httpx.RequestError as e:
            logger.error(f"Error fetching user info from Google: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching user info: {e}")
            return None
    
    async def exchange_code_for_tokens(self, code: str, redirect_uri: str) -> Optional[Dict[str, Any]]:
        """
        Exchange an authorization code for access and ID tokens.
        
        Args:
            code: Authorization code from Google OAuth callback
            redirect_uri: The redirect URI used in the OAuth flow
            
        Returns:
            Dictionary containing access_token, id_token, etc. or None if failed
        """
        if not self.client_id or not settings.GOOGLE_CLIENT_SECRET:
            logger.error("Google OAuth credentials not configured")
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "code": code,
                        "client_id": self.client_id,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET,
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code"
                    },
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
                    return None
                
                return response.json()
                
        except httpx.TimeoutException:
            logger.error("Timeout while exchanging authorization code")
            return None
        except httpx.RequestError as e:
            logger.error(f"Error exchanging authorization code: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error exchanging authorization code: {e}")
            return None


# Singleton instance
google_auth_service = GoogleAuthService()

