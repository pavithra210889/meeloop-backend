from enum import Enum


class NotificationType(str, Enum):
    """Types of notifications that can be sent"""
    # Messages
    MESSAGE = "message"  # Includes all message types: text, image, video, audio, file, shared post, reactions
    
    # Calls
    MISSED_CALL = "missed_call"
    SCHEDULED_CALL_CREATED = "scheduled_call_created"
    SCHEDULED_CALL_REMINDER = "scheduled_call_reminder"
    SCHEDULED_CALL_STARTING = "scheduled_call_starting"
    SCHEDULED_CALL_CANCELLED = "scheduled_call_cancelled"
    SCHEDULED_CALL_RESCHEDULED = "scheduled_call_rescheduled"
    
    # Posts
    POST_LIKED = "post_liked"
    POST_COMMENTED = "post_commented"
    COMMENT_REPLY = "comment_reply"
    
    # Social
    NEW_FOLLOWER = "new_follower"  # When someone follows you
    
    # Loop
    LOOP_FRIEND_REQUEST = "loop_friend_request"
    LOOP_FRIEND_ACCEPTED = "loop_friend_accepted"
    LOOP_MESSAGE = "loop_message"  # Includes all loop message types including reactions
    LOOP_MATCH_FOUND = "loop_match_found"
    
    # Account & Security
    ACCOUNT_VERIFIED = "account_verified"
    ACCOUNT_SUSPENDED = "account_suspended"
    LOGIN_NEW_DEVICE = "login_new_device"
    SECURITY_ALERT = "security_alert"
    PASSWORD_CHANGED = "password_changed"
    
    # Reports
    REPORT_STATUS_UPDATE = "report_status_update"
    CONTENT_REMOVED = "content_removed"


class NotificationCategory(str, Enum):
    """Categories for grouping notifications"""
    MESSAGES = "messages"
    CALLS = "calls"
    POSTS = "posts"
    SOCIAL = "social"
    LOOP = "loop"
    ACCOUNT = "account"
    REPORTS = "reports"

