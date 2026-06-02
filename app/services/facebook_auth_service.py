import httpx
from typing import Optional, Dict, Any
from ..config import settings
import logging

logger = logging.getLogger(__name__)


class FacebookAuthService:
    """Service for verifying Facebook OAuth tokens and fetching user information"""
    
    FACEBOOK_GRAPH_API_URL = "https://graph.facebook.com/v18.0"
    FACEBOOK_TOKEN_VERIFY_URL = "https://graph.facebook.com/me"
    FACEBOOK_TOKEN_EXCHANGE_URL = "https://graph.facebook.com/v18.0/oauth/access_token"
    
    def __init__(self):
        self.app_id = settings.FACEBOOK_APP_ID
        self.app_secret = settings.FACEBOOK_APP_SECRET
        if not self.app_id:
            logger.warning("FACEBOOK_APP_ID not configured")
        if not self.app_secret:
            logger.warning("FACEBOOK_APP_SECRET not configured")
    
    async def verify_access_token(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Verify a Facebook access token and return user information.
        
        Args:
            access_token: The Facebook access token to verify
            
        Returns:
            Dictionary containing user information (id, name, email, picture, etc.) or None if invalid
        """
        if not self.app_id:
            logger.error("Facebook App ID not configured")
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                # Verify token and get user info in one call
                response = await client.get(
                    self.FACEBOOK_TOKEN_VERIFY_URL,
                    params={
                        "access_token": access_token,
                        "fields": "id,name,email,picture"
                    },
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    logger.error(f"Facebook token verification failed: {response.status_code} - {response.text}")
                    return None
                
                user_info = response.json()
                
                # Check if there's an error in the response
                if "error" in user_info:
                    logger.error(f"Facebook API error: {user_info.get('error')}")
                    return None
                
                # Verify the app_id matches (Facebook includes app_id in token debug info)
                # We must verify the token before returning user info for security
                debug_response = await client.get(
                    f"{self.FACEBOOK_GRAPH_API_URL}/debug_token",
                    params={
                        "input_token": access_token,
                        "access_token": f"{self.app_id}|{self.app_secret}"
                    },
                    timeout=10.0
                )
                
                # Token verification is required - fail if debug fails
                if debug_response.status_code != 200:
                    logger.error(f"Facebook token debug failed: {debug_response.status_code} - {debug_response.text}")
                    return None
                
                debug_data = debug_response.json()
                
                # Check for errors in debug response
                if "error" in debug_data:
                    logger.error(f"Facebook token debug error: {debug_data.get('error')}")
                    return None
                
                if "data" not in debug_data:
                    logger.error("Facebook token debug response missing data")
                    return None
                
                token_data = debug_data["data"]
                
                # Verify app_id matches
                if token_data.get("app_id") != self.app_id:
                    logger.error(f"Token app_id mismatch. Expected {self.app_id}, got {token_data.get('app_id')}")
                    return None
                
                # Check if token is expired
                if token_data.get("expires_at") and token_data.get("expires_at") > 0:
                    import time
                    if time.time() > token_data.get("expires_at"):
                        logger.error("Facebook token has expired")
                        return None
                
                # Check if token is valid
                if not token_data.get("is_valid", False):
                    logger.error("Facebook token is not valid")
                    return None
                
                return user_info
                
        except httpx.TimeoutException:
            logger.error("Timeout while verifying Facebook token")
            return None
        except httpx.RequestError as e:
            logger.error(f"Error verifying Facebook token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error verifying Facebook token: {e}")
            return None
    
    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Fetch user information from Facebook using an access token.
        
        Args:
            access_token: Facebook OAuth access token
            
        Returns:
            Dictionary containing user information (id, name, email, picture, etc.) or None if failed
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.FACEBOOK_GRAPH_API_URL}/me",
                    params={
                        "access_token": access_token,
                        "fields": "id,name,email,picture.type(large)"
                    },
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    logger.error(f"Failed to fetch user info: {response.status_code} - {response.text}")
                    return None
                
                user_info = response.json()
                
                # Check for errors
                if "error" in user_info:
                    logger.error(f"Facebook API error: {user_info.get('error')}")
                    return None
                
                # Extract picture URL from nested structure
                if "picture" in user_info and isinstance(user_info["picture"], dict):
                    if "data" in user_info["picture"]:
                        user_info["picture_url"] = user_info["picture"]["data"].get("url")
                
                return user_info
                
        except httpx.TimeoutException:
            logger.error("Timeout while fetching user info from Facebook")
            return None
        except httpx.RequestError as e:
            logger.error(f"Error fetching user info from Facebook: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching user info: {e}")
            return None
    
    async def exchange_code_for_tokens(self, code: str, redirect_uri: str) -> Optional[Dict[str, Any]]:
        """
        Exchange an authorization code for access token.
        
        Args:
            code: Authorization code from Facebook OAuth callback
            redirect_uri: The redirect URI used in the OAuth flow
            
        Returns:
            Dictionary containing access_token, etc. or None if failed
        """
        if not self.app_id or not self.app_secret:
            logger.error("Facebook OAuth credentials not configured")
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.FACEBOOK_TOKEN_EXCHANGE_URL,
                    params={
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                        "redirect_uri": redirect_uri,
                        "code": code
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
facebook_auth_service = FacebookAuthService()

