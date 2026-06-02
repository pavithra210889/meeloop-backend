import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from firebase_admin import credentials, messaging, initialize_app
from firebase_admin.exceptions import FirebaseError
import logging
from ..config import settings

logger = logging.getLogger(__name__)

class FirebaseService:
    def __init__(self):
        self.app = None
        self._initialize_firebase()

    def _initialize_firebase(self):
        """Initialize Firebase Admin SDK"""
        try:
            # Check if Firebase is already initialized
            try:
                # Try to get the default app to check if it's already initialized
                from firebase_admin import get_app
                get_app()  # This will raise ValueError if no app exists
                logger.info("Firebase already initialized")
                print("ℹ️ Firebase already initialized")
                return
            except ValueError:
                # No app exists, we need to initialize
                pass
            
            # Check if push notifications are enabled
            if not settings.ENABLE_PUSH_NOTIFICATIONS:
                logger.info("Push notifications are disabled in configuration")
                self.app = None
                return
            
            # Try to get credentials from config
            firebase_credentials_path = settings.FIREBASE_CREDENTIALS
            
            print(f"🔧 Firebase Configuration:")
            print(f"   ENABLE_PUSH_NOTIFICATIONS: {settings.ENABLE_PUSH_NOTIFICATIONS}")
            print(f"   FIREBASE_CREDENTIALS: {firebase_credentials_path}")
            
            if firebase_credentials_path and os.path.exists(firebase_credentials_path):
                # Initialize with service account file
                print(f"📁 Using Firebase service account file: {firebase_credentials_path}")
                cred = credentials.Certificate(firebase_credentials_path)
                self.app = initialize_app(cred)
                logger.info(f"Firebase initialized with service account file: {firebase_credentials_path}")
                print("✅ Firebase initialized successfully!")
            else:
                # Try to initialize with default credentials (for production)
                print("🔍 Trying to initialize Firebase with default credentials...")
                try:
                    self.app = initialize_app()
                    logger.info("Firebase initialized with default credentials")
                    print("✅ Firebase initialized successfully with default credentials")
                except Exception as e:
                    logger.warning(f"Firebase initialization failed: {e}")
                    logger.warning("Push notifications will be disabled")
                    print(f"❌ Firebase initialization failed: {e}")
                    self.app = None
        except Exception as e:
            logger.error(f"Failed to initialize Firebase: {e}")
            print(f"❌ Failed to initialize Firebase: {e}")
            self.app = None

    def is_available(self) -> bool:
        """Check if Firebase service is available"""
        return self.app is not None

    async def send_notification(
        self,
        fcm_tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict[str, str]] = None,
        image_url: Optional[str] = None,
        session=None
    ) -> Dict[str, Any]:
        """
        Send push notification to multiple FCM tokens
        
        Args:
            fcm_tokens: List of FCM registration tokens
            title: Notification title
            body: Notification body
            data: Additional data payload
            image_url: Optional image URL for rich notifications
            
        Returns:
            Dict with success count and failure details
        """
        if not self.is_available():
            logger.warning("Firebase not available, skipping push notification")
            print("❌ Firebase not available - check FIREBASE_SETUP.md for configuration help")
            return {"success_count": 0, "failure_count": len(fcm_tokens), "errors": ["Firebase not initialized"]}

        if not fcm_tokens:
            return {"success_count": 0, "failure_count": 0, "errors": []}

        try:
            # Determine notification strategy:
            # - calls: data-only, TTL=0 (handled by onMessageReceived always)
            # - messages: data-only so onMessageReceived is called even in background,
            #             allowing the app to add Reply/MarkAsRead/Mute action buttons
            # - other: standard notification payload
            notification_type_val = data.get("type") if data else None
            is_call = notification_type_val == "incoming_call"
            is_message = notification_type_val == "message"
            is_data_only = is_call or is_message

            # Add title/body to data so onMessageReceived can display them
            # Truncate to stay well within FCM's 4KB total data payload limit
            message_data = dict(data or {})
            if is_data_only:
                message_data["title"] = (title or "")[:200]
                message_data["body"] = (body or "")[:500]

            # No notification payload for data-only types — forces onMessageReceived
            notification = None
            if not is_data_only:
                notification = messaging.Notification(
                    title=title,
                    body=body,
                    image=image_url
                )

            # Android config
            if is_call:
                android_config = messaging.AndroidConfig(
                    priority="high",
                    ttl=0,
                )
            elif is_message:
                android_config = messaging.AndroidConfig(
                    priority="high",
                )
            else:
                android_config = messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        priority="high",
                        sound="default",
                        channel_id="general_notifications"
                    )
                )

            # Create iOS-specific configuration
            apns_config = messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(
                            title=title,
                            body=body
                        ),
                        badge=1,
                        sound="default"
                    )
                )
            )

            # Create the message
            message = messaging.MulticastMessage(
                notification=notification,
                data=message_data,
                android=android_config,
                apns=apns_config,
                tokens=fcm_tokens
            )

            # Send the message - use send_multicast if available, otherwise send individually
            try:
                # Try the newer send_multicast method first
                response = messaging.send_multicast(message)
                
                logger.info(f"Push notification sent: {response.success_count} successful, {response.failure_count} failed")
                
                # Log individual failures
                invalid_tokens = []
                for i, resp in enumerate(response.responses):
                    if not resp.success:
                        logger.error(f"Failed to send to token {i}: {resp.exception}")
                        if self._should_deactivate_token(resp.exception):
                            invalid_tokens.append(fcm_tokens[i])
                
                if invalid_tokens and session:
                    self._deactivate_tokens(invalid_tokens, session)
                
                return {
                    "success_count": response.success_count,
                    "failure_count": response.failure_count,
                    "errors": [str(resp.exception) for resp in response.responses if not resp.success]
                }
            except AttributeError:
                # Fallback for older Firebase Admin SDK versions
                print("🔄 Using fallback method for older Firebase Admin SDK")
                success_count = 0
                failure_count = 0
                errors = []
                invalid_tokens = []
                
                # Send to each token individually
                for i, token in enumerate(fcm_tokens):
                    try:
                        # Create individual message for each token
                        individual_message = messaging.Message(
                            notification=notification,
                            data=message_data,
                            android=android_config,
                            apns=apns_config,
                            token=token
                        )
                        
                        response = messaging.send(individual_message)
                        success_count += 1
                        logger.info(f"Successfully sent to token {i}")
                        
                    except Exception as e:
                        failure_count += 1
                        error_msg = f"Failed to send to token {i}: {str(e)}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        if self._should_deactivate_token(e):
                            invalid_tokens.append(token)
                
                logger.info(f"Push notification sent: {success_count} successful, {failure_count} failed")
                
                if invalid_tokens and session:
                    self._deactivate_tokens(invalid_tokens, session)
                
                return {
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "errors": errors
                }

        except FirebaseError as e:
            logger.error(f"Firebase error sending notification: {e}")
            return {"success_count": 0, "failure_count": len(fcm_tokens), "errors": [str(e)]}
        except Exception as e:
            logger.error(f"Unexpected error sending notification: {e}")
            return {"success_count": 0, "failure_count": len(fcm_tokens), "errors": [str(e)]}

    def _should_deactivate_token(self, exception: Optional[Exception]) -> bool:
        if not exception:
            return False
        code = getattr(exception, "code", "") or ""
        message = str(exception).lower()
        invalid_codes = {
            "messaging/registration-token-not-registered",
            "messaging/invalid-registration-token",
        }
        if code in invalid_codes:
            return True
        
        # Check for textual matches in the error message
        if (
            "registration-token-not-registered" in message or 
            "invalid-registration-token" in message or
            "requested entity was not found" in message
        ):
            return True
            
        return False

    def _deactivate_tokens(self, tokens: List[str], session) -> None:
        if not tokens or not session:
            return
        try:
            from app.users.models import FCMToken
            from sqlmodel import select

            updated = 0
            for token_value in tokens:
                record = session.exec(
                    select(FCMToken).where(FCMToken.token == token_value)
                ).first()
                if record:
                    session.delete(record)
                    updated += 1
            if updated:
                session.commit()
                logger.info(f"Deleted {updated} invalid FCM token(s)")
        except Exception as e:
            logger.error(f"Failed to delete invalid FCM tokens: {e}")
            session.rollback()

    async def send_to_user(
        self,
        user_id: str,
        title: str,
        body: str,
        data: Optional[Dict[str, str]] = None,
        image_url: Optional[str] = None,
        session=None
    ) -> Dict[str, Any]:
        """
        Send push notification to all active FCM tokens of a user

        Args:
            user_id: Target user ID
            title: Notification title
            body: Notification body
            data: Additional data payload
            image_url: Optional image URL
            session: Database session
            
        Returns:
            Dict with success count and failure details
        """
        if not session:
            logger.error("Database session required for send_to_user")
            return {"success_count": 0, "failure_count": 0, "errors": ["No database session"]}

        try:
            from app.users.models import FCMToken
            from sqlmodel import select

            # Get all active FCM tokens for the user
            fcm_tokens = session.exec(
                select(FCMToken.token).where(
                    FCMToken.user_id == user_id,
                    FCMToken.is_active == True
                )
            ).all()

            print(f"🔍 Looking for FCM tokens for user {user_id}")
            print(f"📱 Found {len(fcm_tokens)} active FCM tokens")

            if not fcm_tokens:
                logger.info(f"No active FCM tokens found for user {user_id}")
                return {"success_count": 0, "failure_count": 0, "errors": ["No active FCM tokens"]}

            return await self.send_notification(
                fcm_tokens=fcm_tokens,
                title=title,
                body=body,
                data=data,
                image_url=image_url,
                session=session
            )

        except Exception as e:
            logger.error(f"Error getting FCM tokens for user {user_id}: {e}")
            return {"success_count": 0, "failure_count": 0, "errors": [str(e)]}

    async def send_message_notification(
        self,
        sender_name: str,
        message_text: str,
        receiver_id: str,
        chat_id: str,
        message_type: str = "text",
        session=None
    ) -> Dict[str, Any]:
        """
        Send push notification for a new message
        
        Args:
            sender_name: Name of the message sender
            message_text: Content of the message
            receiver_id: ID of the message receiver
            chat_id: ID of the chat
            message_type: Type of message (text, image, video, etc.)
            session: Database session
            
        Returns:
            Dict with success count and failure details
        """
        # Truncate message if too long
        display_text = message_text[:100] + "..." if len(message_text) > 100 else message_text
        
        # Customize title based on message type
        if message_type == "image":
            title = f"📷 {sender_name} sent a photo"
            body = "Tap to view"
        elif message_type == "video":
            title = f"🎥 {sender_name} sent a video"
            body = "Tap to view"
        elif message_type == "audio":
            title = f"🎵 {sender_name} sent an audio"
            body = "Tap to play"
        elif message_type == "file":
            title = f"📎 {sender_name} sent a file"
            body = "Tap to download"
        elif message_type == "post":
            title = f"📝 {sender_name} shared a post"
            body = "Tap to view"
        else:
            title = f"💬 {sender_name}"
            body = display_text

        # Prepare notification data
        data = {
            "type": "message",
            "chat_id": str(chat_id),
            "sender_id": str(receiver_id),  # This will be updated with actual sender ID
            "message_type": message_type
        }

        return await self.send_to_user(
            user_id=receiver_id,
            title=title,
            body=body,
            data=data,
            session=session
        )


# Global instance
firebase_service = FirebaseService()
