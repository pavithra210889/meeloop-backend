from typing import Optional
from sqlmodel import Session, select
from datetime import datetime, time
from app.notifications.models import NotificationPreference
import logging

logger = logging.getLogger(__name__)


class NotificationPreferenceService:
    """Manages user notification preferences"""
    
    async def get_preferences(
        self,
        user_id: str,
        session: Session
    ) -> NotificationPreference:
        """Get user preferences, create default if not exists"""
        preference = session.exec(
            select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        ).first()
        
        if not preference:
            preference = NotificationPreference(user_id=user_id)
            session.add(preference)
            session.commit()
            session.refresh(preference)
            logger.info(f"Created default preferences for user {user_id}")
        
        return preference
    
    async def update_preferences(
        self,
        user_id: str,
        session: Session,
        **updates
    ) -> NotificationPreference:
        """Update user preferences"""
        preference = await self.get_preferences(user_id, session)
        
        for key, value in updates.items():
            if hasattr(preference, key):
                setattr(preference, key, value)
        
        preference.updated_at = datetime.now()
        session.add(preference)
        session.commit()
        session.refresh(preference)
        
        logger.info(f"Updated preferences for user {user_id}")
        return preference
    
    async def is_notification_enabled(
        self,
        user_id: str,
        notification_type: str,
        session: Session
    ) -> bool:
        """Check if specific notification type is enabled for user"""
        preference = await self.get_preferences(user_id, session)
        
        if not preference.notifications_enabled:
            return False
        
        # Map notification types to preference fields
        # Note: missed_call requires both calls_enabled and missed_calls_enabled
        type_mapping = {
            "message": preference.messages_enabled,  # Includes all message types (text, media, reactions, etc.)
            "incoming_call": preference.calls_enabled,
            "missed_call": preference.calls_enabled and preference.missed_calls_enabled,  # Calls must be enabled first
            "scheduled_call_created": preference.calls_enabled,
            "scheduled_call_reminder": preference.calls_enabled,
            "scheduled_call_starting": preference.calls_enabled,
            "scheduled_call_cancelled": preference.calls_enabled,
            "scheduled_call_rescheduled": preference.calls_enabled,
            "post_liked": preference.likes_enabled,
            "post_commented": preference.comments_enabled,
            "comment_reply": preference.comment_replies_enabled,
            "new_follower": preference.followers_enabled,
            "loop_friend_request": preference.loop_friend_requests_enabled,
            "loop_friend_accepted": preference.loop_friend_requests_enabled,  # Same as request
            "loop_message": preference.loop_messages_enabled,  # Includes all loop message types (text, reactions, etc.)
            "loop_match_found": preference.loop_matches_enabled,
            "account_verified": preference.account_security_enabled,
            "account_suspended": preference.account_status_enabled,
            "login_new_device": preference.login_alerts_enabled,
            "security_alert": preference.account_security_enabled,
            "password_changed": preference.account_security_enabled,
            "report_status_update": preference.reports_enabled,
            "content_removed": preference.reports_enabled,
        }
        
        return type_mapping.get(notification_type, True)  # Default to enabled if not mapped
    
    async def is_quiet_hours(
        self,
        user_id: str,
        session: Session
    ) -> bool:
        """Check if current time is within quiet hours"""
        preference = await self.get_preferences(user_id, session)
        
        if not preference.quiet_hours_enabled:
            return False
        
        if not preference.quiet_hours_start or not preference.quiet_hours_end:
            return False
        
        try:
            start_time = time.fromisoformat(preference.quiet_hours_start)
            end_time = time.fromisoformat(preference.quiet_hours_end)
            current_time = datetime.now().time()
            
            if start_time <= end_time:
                return start_time <= current_time <= end_time
            else:  # Spans midnight
                return current_time >= start_time or current_time <= end_time
        except Exception as e:
            logger.error(f"Error checking quiet hours for user {user_id}: {e}")
            return False


# Global instance
preference_service = NotificationPreferenceService()

