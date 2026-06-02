from typing import Dict, Any, Optional, List
from sqlmodel import Session, select, func
from datetime import datetime
from app.users.models import User
from app.notifications.models import Notification, NotificationPreference
from app.notifications.enums import NotificationType, NotificationCategory
from app.messages.models import Message, MessageType
from app.services.firebase_service import firebase_service
from .preference_service import preference_service
from app.sockets.socketio_server import sio
from app.sockets.active import get_active_sid, pop_active
import logging

logger = logging.getLogger(__name__)


class NotificationService:
    """Main service for creating and managing notifications"""
    
    def __init__(self):
        self.firebase = firebase_service
    
    async def create_notification(
        self,
        notification_type: NotificationType,
        recipient_id: str,
        sender_id: str | None,
        title: str,
        message: str,
        session: Session,
        category: NotificationCategory | None = None,
        redirect_to: Optional[str] = None,
        redirect_type: Optional[str] = None,
        redirect_id: Optional[str] = None,
        image_url: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        priority: int = 0,
        skip_preference_check: bool = False,
        skip_push: bool = False,
        group_key: Optional[str] = None,
        aggregation_message_template: Optional[str] = None
    ) -> Notification:
        """
        Create a notification with preference checking and aggregation support
        
        Args:
            notification_type: Type of notification
            recipient_id: User receiving notification
            sender_id: User sending (if applicable)
            title: Notification title
            message: Notification message
            session: Database session
            category: Notification category (auto-detected if not provided)
            redirect_to: Redirect path
            redirect_type: Type of redirect
            redirect_id: ID for redirect
            image_url: Optional image URL
            meta: Additional metadata
            priority: Priority level (0=normal, 1=high, 2=urgent)
            skip_preference_check: Skip preference checking (for system notifications)
            group_key: Key for grouping/aggregating notifications
            aggregation_message_template: Template string for aggregated message, e.g. "{sender_name} and {count-1} others liked your post"
            
        Returns:
            Created or updated Notification object
        """
        try:
            # Auto-detect category if not provided
            if not category:
                category = self._get_category_from_type(notification_type)
            
            # Check for existing unread notification if group_key is provided
            if group_key:
                existing_notification = session.exec(
                    select(Notification).where(
                        Notification.recipient_id == recipient_id,
                        Notification.notification_type == notification_type.value,
                        Notification.group_key == group_key,
                        Notification.is_read == False,
                        Notification.deleted_at.is_(None)
                    )
                ).first()
                
                if existing_notification:
                    # Update existing notification
                    existing_notification.aggregated_count += 1
                    existing_notification.updated_at = datetime.now()
                    existing_notification.sender_id = sender_id  # Update to latest sender
                    
                    # Update message if template provided
                    if aggregation_message_template and sender_id:
                        sender = session.get(User, sender_id)
                        if sender:
                            # Use count-1 because "others" implies excluding the named person
                            others_count = existing_notification.aggregated_count - 1
                            new_message = aggregation_message_template.format(
                                sender_name=sender.username,
                                count=others_count,
                                **(meta or {})
                            )
                            existing_notification.message = new_message
                            # Also update title to just sender name if needed, or keep original?
                            # Usually title is "New Like" or sender name. 
                            # If typical usage passes title=sender.name, we might want to update it.
                            # For now, let's update title to new sender name if it looks like a name (heuristic)
                            # or just trust the caller's initial title strategy.
                            # Let's assume title is "New Like" or similar generic, or just the username.
                            # We'll update title to the new sender's name to match the message anchor.
                            if title and title != existing_notification.title:
                                existing_notification.title = title
                    
                    if image_url:
                        existing_notification.image_url = image_url
                    
                    if meta:
                         # Merge or overwrite? Overwrite often better for "latest context"
                        existing_notification.meta = meta

                    session.add(existing_notification)
                    session.commit()
                    session.refresh(existing_notification)
                    
                    recipient_sid = await get_active_sid(recipient_id)
                    if recipient_sid:
                        try:
                            socket_payload = {
                                "id": existing_notification.id,
                                "title": existing_notification.title,
                                "message": existing_notification.message,
                                "notification_type": notification_type.value,
                                "created_at": existing_notification.created_at.isoformat(),
                                "image_url": image_url or existing_notification.image_url,
                                "is_read": False,
                                "redirect_to": redirect_to or existing_notification.redirect_to,
                                "redirect_type": redirect_type or existing_notification.redirect_type,
                                "redirect_id": redirect_id or existing_notification.redirect_id,
                                "aggregated_count": existing_notification.aggregated_count,
                                "meta": existing_notification.meta
                            }
                            await sio.emit(
                                "notification:new",
                                socket_payload,
                                to=recipient_sid
                            )
                            logger.info(f"Socket update sent to user {recipient_id}")
                        except Exception as e:
                            logger.error(f"Error sending socket update: {e}, falling back to push notification")
                            await pop_active(recipient_id)
                    
                    #  Always try push for aggregated updates too (user may be on web but want phone ping)
                    print(f"🔄 Notification updated (aggregated), sending push for user {recipient_id}")
                    
                    # Check preferences (unless skipped)
                    should_send_push_update = False
                    if not skip_preference_check:
                        is_enabled = await preference_service.is_notification_enabled(
                            user_id=recipient_id,
                            notification_type=notification_type.value,
                            session=session
                        )
                        if is_enabled:
                            in_quiet_hours = await preference_service.is_quiet_hours(
                                user_id=recipient_id,
                                session=session
                            )
                            should_send_push_update = not in_quiet_hours
                    else:
                        should_send_push_update = True

                    print(f"🚀 Aggregated should send push: {should_send_push_update}")
                    
                    if should_send_push_update and not skip_push:
                        try:
                            print("🚀 Calling firebase.send_to_user for aggregated update...")
                            agg_push_data = {
                                    "type": notification_type.value,
                                    "notification_id": str(existing_notification.id),
                                    "redirect_type": redirect_type or existing_notification.redirect_to or "",
                                    "redirect_id": str(redirect_id) if redirect_id else "",
                                    "redirect_path": redirect_to or existing_notification.redirect_to or "",
                                    "sender_id": str(sender_id) if sender_id else "",
                                    "chat_id": str(meta.get("chat_id", "")) if meta else "",
                                    "msg_type": str(meta.get("message_type", "")) if meta else "",
                                    "message_id": str(meta.get("message_id", "")) if meta else "",
                            }
                            push_result = await self.firebase.send_to_user(
                                user_id=recipient_id,
                                title=existing_notification.title,
                                body=message,  # Use current message text, not the aggregated count
                                data=agg_push_data,
                                image_url=image_url or existing_notification.image_url,
                                session=session
                            )
                            print(f"🚀 Aggregated Push result: {push_result}")
                            
                            existing_notification.is_pushed = push_result.get("success_count", 0) > 0
                            existing_notification.push_sent_at = datetime.now()
                            existing_notification.push_failed = push_result.get("failure_count", 0) > 0
                            if existing_notification.push_failed:
                                existing_notification.push_error = str(push_result.get("errors", []))
                            
                            session.add(existing_notification)
                            session.commit()
                        except Exception as e:
                            logger.error(f"Error sending aggregated push: {e}")
                            print(f"❌ Error sending aggregated push: {e}")

                    return existing_notification

            # Check preferences (unless skipped)
            if not skip_preference_check:
                print(f"🔍 Checking preferences for user {recipient_id}, type {notification_type.value}")
                is_enabled = await preference_service.is_notification_enabled(
                    user_id=recipient_id,
                    notification_type=notification_type.value,
                    session=session
                )
                print(f"🔍 Preferences enabled: {is_enabled}")
                
                if not is_enabled:
                    logger.info(f"Notification {notification_type.value} disabled for user {recipient_id}")
                    print(f"⚠️ Notification disabled by preference")
                    # Still create notification but don't send push
                    should_send_push = False
                else:
                    # Check quiet hours
                    in_quiet_hours = await preference_service.is_quiet_hours(
                        user_id=recipient_id,
                        session=session
                    )
                    print(f"🔍 In quiet hours: {in_quiet_hours}")
                    should_send_push = not in_quiet_hours
            else:
                print("⏩ Skipping preference check")
                should_send_push = True
            
            # Create notification record
            notification = Notification(
                notification_type=notification_type.value,
                notification_category=category.value,
                recipient_id=recipient_id,
                sender_id=sender_id,
                title=title,
                message=message,
                redirect_to=redirect_to,
                redirect_type=redirect_type,
                redirect_id=redirect_id,
                image_url=image_url,
                meta=meta or {},
                priority=priority,
                is_pushed=False,
                group_key=group_key
            )
            
            session.add(notification)
            session.commit()
            session.refresh(notification)
            print(f"💾 Notification created in DB: {notification.id}")
            
            # Send socket event if user is online
            recipient_sid = await get_active_sid(recipient_id)
            if recipient_sid:
                try:
                    socket_payload = {
                        "id": notification.id,
                        "title": title,
                        "message": message,
                        "notification_type": notification_type.value,
                        "created_at": notification.created_at.isoformat(),
                        "image_url": image_url,
                        "is_read": False,
                        "redirect_to": redirect_to,
                        "redirect_type": redirect_type,
                        "redirect_id": redirect_id,
                        "meta": meta or {}
                    }
                    await sio.emit(
                        "notification:new",
                        socket_payload,
                        to=recipient_sid
                    )
                    logger.info(f"Socket notification sent to user {recipient_id}")
                except Exception as e:
                    logger.error(f"Error sending socket notification: {e}")
                    await pop_active(recipient_id)
                    should_send_push = True
            
            # Send push notification if enabled and not skipped
            # NOTE: We always try push independently of socket delivery.
            # A user can be online on web AND receive a push to their phone simultaneously.
            print(f"🚀 Should send push: {should_send_push}, Skip push: {skip_push}")
            if should_send_push and not skip_push:
                try:
                    print("🚀 Calling firebase.send_to_user...")
                    push_data = {
                            "type": notification_type.value,
                            "notification_id": str(notification.id),
                            "redirect_type": redirect_type or "",
                            "redirect_id": str(redirect_id) if redirect_id else "",
                            "redirect_path": redirect_to or "",
                            "sender_id": str(sender_id) if sender_id else "",
                            "chat_id": str(meta.get("chat_id", "")) if meta else "",
                            "msg_type": str(meta.get("message_type", "")) if meta else "",
                            "message_id": str(meta.get("message_id", "")) if meta else "",
                    }
                    push_result = await self.firebase.send_to_user(
                        user_id=recipient_id,
                        title=title,
                        body=message,
                        data=push_data,
                        image_url=image_url,
                        session=session
                    )
                    print(f"🚀 Push result: {push_result}")

                    notification.is_pushed = push_result.get("success_count", 0) > 0
                    notification.push_sent_at = datetime.now()
                    notification.push_failed = push_result.get("failure_count", 0) > 0

                    if notification.push_failed:
                        notification.push_error = str(push_result.get("errors", []))

                    session.add(notification)
                    session.commit()
                except Exception as e:
                    logger.error(f"Error sending push notification: {e}")
                    print(f"❌ Error sending push notification: {e}")
                    notification.push_failed = True
                    notification.push_error = str(e)
                    session.add(notification)
                    session.commit()
            
            logger.info(f"Notification created: {notification.id}, type: {notification_type.value}, user: {recipient_id}")
            return notification
            
        except Exception as e:
            logger.error(f"Error creating notification: {e}")
            session.rollback()
            raise
    
    def _get_category_from_type(self, notification_type: NotificationType) -> NotificationCategory:
        """Map notification type to category"""
        category_mapping = {
            NotificationType.MESSAGE: NotificationCategory.MESSAGES,
            NotificationType.MISSED_CALL: NotificationCategory.CALLS,
            NotificationType.SCHEDULED_CALL_CREATED: NotificationCategory.CALLS,
            NotificationType.SCHEDULED_CALL_REMINDER: NotificationCategory.CALLS,
            NotificationType.SCHEDULED_CALL_STARTING: NotificationCategory.CALLS,
            NotificationType.SCHEDULED_CALL_CANCELLED: NotificationCategory.CALLS,
            NotificationType.SCHEDULED_CALL_RESCHEDULED: NotificationCategory.CALLS,
            NotificationType.POST_LIKED: NotificationCategory.POSTS,
            NotificationType.POST_COMMENTED: NotificationCategory.POSTS,
            NotificationType.COMMENT_REPLY: NotificationCategory.POSTS,
            NotificationType.NEW_FOLLOWER: NotificationCategory.SOCIAL,
            NotificationType.LOOP_FRIEND_REQUEST: NotificationCategory.LOOP,
            NotificationType.LOOP_FRIEND_ACCEPTED: NotificationCategory.LOOP,
            NotificationType.LOOP_MESSAGE: NotificationCategory.LOOP,
            NotificationType.LOOP_MATCH_FOUND: NotificationCategory.LOOP,
            NotificationType.ACCOUNT_VERIFIED: NotificationCategory.ACCOUNT,
            NotificationType.ACCOUNT_SUSPENDED: NotificationCategory.ACCOUNT,
            NotificationType.LOGIN_NEW_DEVICE: NotificationCategory.ACCOUNT,
            NotificationType.SECURITY_ALERT: NotificationCategory.ACCOUNT,
            NotificationType.PASSWORD_CHANGED: NotificationCategory.ACCOUNT,
            NotificationType.REPORT_STATUS_UPDATE: NotificationCategory.REPORTS,
            NotificationType.CONTENT_REMOVED: NotificationCategory.REPORTS,
        }
        return category_mapping.get(notification_type, NotificationCategory.MESSAGES)
    
    async def mark_as_read(
        self,
        notification_id: str,
        user_id: str,
        session: Session
    ) -> Notification:
        """Mark notification as read"""
        notification = session.get(Notification, notification_id)
        if not notification:
            raise ValueError(f"Notification {notification_id} not found")
        
        if notification.recipient_id != user_id:
            raise ValueError("Notification does not belong to user")
        
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = datetime.now()
            session.add(notification)
            session.commit()
            session.refresh(notification)
        
        return notification
    
    async def mark_all_as_read(
        self,
        user_id: str,
        session: Session,
        category: Optional[str] = None
    ) -> int:
        """Mark all notifications as read for user"""
        query = select(Notification).where(
            Notification.recipient_id == user_id,
            Notification.is_read == False,
            Notification.deleted_at.is_(None)
        )
        
        if category:
            query = query.where(Notification.notification_category == category)
        
        notifications = session.exec(query).all()
        count = 0
        
        for notification in notifications:
            notification.is_read = True
            notification.read_at = datetime.now()
            session.add(notification)
            count += 1
        
        session.commit()
        return count
    
    async def get_notifications(
        self,
        user_id: str,
        session: Session,
        category: Optional[str] = None,
        is_read: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Notification]:
        """Get paginated notifications with filters"""
        query = select(Notification).where(
            Notification.recipient_id == user_id,
            Notification.deleted_at.is_(None),
            # Exclude legacy incoming-call records stored before push-only was enforced
            ~(
                (Notification.notification_type == "missed_call") &
                Notification.message.startswith("Incoming ")
            )
        )
        
        if category:
            query = query.where(Notification.notification_category == category)
        
        if is_read is not None:
            query = query.where(Notification.is_read == is_read)
        
        query = query.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        
        return list(session.exec(query).all())
    
    async def get_unread_count(
        self,
        user_id: str,
        session: Session,
        category: Optional[str] = None
    ) -> int:
        """Get count of unread notifications"""
        query = select(func.count(Notification.id)).where(
            Notification.recipient_id == user_id,
            Notification.is_read == False,
            Notification.deleted_at.is_(None)
        )
        
        if category:
            query = query.where(Notification.notification_category == category)
        
        return session.exec(query).one() or 0
    
    async def delete_notification(
        self,
        notification_id: str,
        user_id: str,
        session: Session
    ) -> bool:
        """Soft delete notification"""
        notification = session.get(Notification, notification_id)
        if not notification:
            return False
        
        if notification.recipient_id != user_id:
            return False
        
        notification.deleted_at = datetime.now()
        session.add(notification)
        session.commit()
        return True
    
    async def clear_all_notifications(
        self,
        user_id: str,
        session: Session,
        category: Optional[str] = None
    ) -> int:
        """Soft delete all notifications for user"""
        query = select(Notification).where(
            Notification.recipient_id == user_id,
            Notification.deleted_at.is_(None)
        )

        if category:
            query = query.where(Notification.notification_category == category)

        notifications = session.exec(query).all()
        count = 0

        for notification in notifications:
            notification.deleted_at = datetime.now()
            session.add(notification)
            count += 1

        session.commit()
        return count

    # Legacy methods for backward compatibility
    async def send_message_notification(
        self,
        message: Message,
        sender: User,
        receiver: User,
        session: Session
    ) -> Dict[str, Any]:
        """Send push notification for a new message (legacy method)"""
        try:
            is_online = await self._is_user_online(receiver.id)
            skip_push = is_online  # Skip push if user is online
            
            if is_online:
                logger.info(f"User {receiver.id} is online, skipping push notification")
            
            title = f"New message from {sender.name}"
            message_preview = self._format_message_preview(message)
            
            notification = await self.create_notification(
                notification_type=NotificationType.MESSAGE,
                recipient_id=receiver.id,
                sender_id=sender.id,
                title=title,
                message=message_preview,
                session=session,
                redirect_to=f"/chat/{message.chat_id}",
                redirect_type="chat",
                redirect_id=message.chat_id,
                meta={
                    "message_id": message.id,
                    "chat_id": message.chat_id,
                    "message_type": message.message_type.value,
                    "sender_name": sender.name,
                },
                skip_preference_check=False,
                skip_push=skip_push
            )
            
            return {
                "sent": notification.is_pushed,
                "notification_id": notification.id,
                "push_success": notification.is_pushed
            }
            
        except Exception as e:
            logger.error(f"Error sending message notification: {e}")
            return {"sent": False, "error": str(e)}
    
    async def send_call_notification(
        self,
        caller: User,
        receiver: User,
        is_video_call: bool = False,
        call_id: str | None = None,
        session: Session = None
    ) -> Dict[str, Any]:
        """Send push notification for incoming call (legacy method)"""
        try:
            call_type = "video call" if is_video_call else "voice call"
            title = f"📞 {caller.name} is calling"
            body = f"Incoming {call_type}"
            
            if call_id is None:
                call_id = str(hash(f"{caller.id}_{receiver.id}_{is_video_call}") % 1000000)
            
            # Respect calls preference — but not quiet hours (calls are urgent)
            if session:
                calls_enabled = await preference_service.is_notification_enabled(
                    user_id=receiver.id,
                    notification_type="incoming_call",
                    session=session
                )
                if not calls_enabled:
                    logger.info(f"Call notifications disabled for user {receiver.id}, skipping push")
                    return {"sent": False, "push_success": False}

            # 1. Send immediate high-priority data-only push for the call
            push_result = await self.firebase.send_to_user(
                user_id=receiver.id,
                title=title,
                body=body,
                data={
                    "type": "incoming_call",
                    "caller_name": caller.name,
                    "call_id": str(call_id),
                    "caller_id": str(caller.id),
                    "is_video_call": "true" if is_video_call else "false",
                    "caller_profile_pic": caller.profile_pic or "",
                },
                session=session
            )

            # Call notifications are transient — push only, not stored in notification history.
            push_success = push_result.get("success_count", 0) > 0
            return {
                "sent": push_success,
                "push_success": push_success
            }
            
        except Exception as e:
            logger.error(f"Error sending call notification: {e}")
            return {"sent": False, "error": str(e)}
    
    async def _is_user_online(self, user_id: str) -> bool:
        """Check if user is currently online"""
        # TODO: Implement proper online status checking
        return False
    
    def _format_message_preview(self, message: Message) -> str:
        """Format message content for notification preview"""
        if message.message_type == MessageType.TEXT:
            # Message content is E2E encrypted ciphertext — don't include it in the payload
            return "New message"
        elif message.message_type == MessageType.IMAGE:
            return "📷 Photo"
        elif message.message_type == MessageType.VIDEO:
            return "🎥 Video"
        elif message.message_type == MessageType.AUDIO:
            return "🎵 Audio"
        elif message.message_type == MessageType.FILE:
            return "📎 File"
        elif message.message_type == MessageType.POST:
            return "📝 Shared a post"
        else:
            return "New message"


# Global instance
notification_service = NotificationService()