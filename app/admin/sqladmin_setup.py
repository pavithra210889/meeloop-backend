from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from sqlmodel import Session, select

from app.database import engine
from app.security import verify_password

# ── Models ────────────────────────────────────────────────────────────────────
from app.users.models import (
    User, FCMToken, Follow, Block, UserPreference,
    UserPasskey, UserDevice, LoginActivity, OTP, UserSession,
)
from app.posts.models import Post, Media, Like, Comment, BookmarkFolder, Bookmark
from app.messages.models import (
    Chat, Message, MessageKey, ChatMute, Reaction,
    StarredMessage, ChatMember, SenderKey, SenderKeyDistribution,
    MessageReadReceipt,
)
from app.stories.models import Story, StoryMedia, StoryView
from app.calls.models import Call
from app.scheduled_calls.models import ScheduledCall
from app.contacts.models import Contact
from app.notifications.models import Notification, NotificationPreference
from app.reports.models import Report, ModerationAction
from app.loops.models import (
    LoopProfile, LoopFriend, LoopRequest, LoopChat,
    LoopMessage, LoopReaction, LoopProfilePhoto, RandomSession,
)
from app.meme_templates.models import MemeTemplates
from app.ar_filters.models import ArFilter, ArGameConfig
from app.admin.models import AdminAuditLog
from app.geo.models import IpInfoCache


def _sid(val) -> str:
    """Show first 8 chars of a UUID for compact FK display in list views."""
    return str(val)[:8] + "…" if val else "—"


# ── Auth ──────────────────────────────────────────────────────────────────────

class SuperAdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        with Session(engine) as session:
            user = session.exec(select(User).where(User.username == username)).first()
            if not user or not user.is_superadmin or not user.is_active:
                return False
            if not user.password or not verify_password(password, user.password):
                return False
        request.session["admin_user_id"] = user.id
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        user_id = request.session.get("admin_user_id")
        if not user_id:
            return False
        with Session(engine) as session:
            user = session.get(User, user_id)
            if not user or not user.is_superadmin or not user.is_active:
                return False
        return True


# ── Users ─────────────────────────────────────────────────────────────────────

class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-user"
    column_list = [
        User.id, User.username, User.name, User.email,
        User.phone_number, User.is_active, User.is_verified,
        User.is_superadmin, User.auth_provider,
    ]
    column_searchable_list = [User.username, User.email, User.name, User.phone_number]
    column_sortable_list = [User.username, User.email, User.is_active, User.is_verified]
    column_details_exclude_list = [
        User.password, User.mfa_secret, User.backup_codes,
    ]
    form_excluded_columns = [
        User.password, User.mfa_secret, User.backup_codes, "location",
    ]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class UserSessionAdmin(ModelView, model=UserSession):
    name = "Session"
    name_plural = "User Sessions"
    icon = "fa-solid fa-key"
    column_list = [
        UserSession.id, "user", UserSession.device_id,
        UserSession.ip_address, UserSession.is_active,
        UserSession.created_at, UserSession.expires_at, UserSession.last_activity,
    ]
    column_sortable_list = [UserSession.created_at, UserSession.is_active, UserSession.expires_at]
    column_details_exclude_list = [UserSession.session_token]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class UserDeviceAdmin(ModelView, model=UserDevice):
    name = "Device"
    name_plural = "User Devices"
    icon = "fa-solid fa-mobile"
    column_list = [
        UserDevice.id, UserDevice.user_id, UserDevice.device_id,
        UserDevice.user_agent, UserDevice.last_ip,
        UserDevice.is_active, UserDevice.first_seen, UserDevice.last_seen,
    ]
    column_formatters = {
        "user_id": lambda m, a: _sid(m.user_id),
    }
    column_searchable_list = [UserDevice.device_id, UserDevice.user_agent, UserDevice.last_ip]
    column_sortable_list = [UserDevice.last_seen, UserDevice.is_active]
    column_details_exclude_list = [UserDevice.public_key]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class FCMTokenAdmin(ModelView, model=FCMToken):
    name = "FCM Token"
    name_plural = "FCM Tokens"
    icon = "fa-solid fa-bell"
    column_list = [
        FCMToken.id, "user", FCMToken.device_id,
        FCMToken.device_type, FCMToken.is_active, FCMToken.created_at,
    ]
    column_sortable_list = [FCMToken.created_at, FCMToken.is_active]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class FollowAdmin(ModelView, model=Follow):
    name = "Follow"
    name_plural = "Follows"
    icon = "fa-solid fa-user-plus"
    column_list = [Follow.id, "follower", "following", Follow.created_at]
    column_sortable_list = [Follow.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class BlockAdmin(ModelView, model=Block):
    name = "Block"
    name_plural = "Blocks"
    icon = "fa-solid fa-ban"
    column_list = [Block.id, "blocker", "blocked", Block.created_at]
    column_sortable_list = [Block.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class UserPreferenceAdmin(ModelView, model=UserPreference):
    name = "User Preference"
    name_plural = "User Preferences"
    icon = "fa-solid fa-sliders"
    column_list = [
        UserPreference.id, UserPreference.user_id,
        UserPreference.ui_mode, UserPreference.theme_mode, UserPreference.language,
    ]
    column_formatters = {
        "user_id": lambda m, a: _sid(m.user_id),
    }
    can_create = False
    can_edit = True
    can_delete = False
    can_view_details = True


class UserPasskeyAdmin(ModelView, model=UserPasskey):
    name = "Passkey"
    name_plural = "Passkeys"
    icon = "fa-solid fa-fingerprint"
    column_list = [
        UserPasskey.id, "user", UserPasskey.name,
        UserPasskey.sign_count, UserPasskey.created_at, UserPasskey.last_used_at,
    ]
    column_sortable_list = [UserPasskey.created_at, UserPasskey.last_used_at]
    column_details_exclude_list = [UserPasskey.public_key, UserPasskey.credential_id]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class LoginActivityAdmin(ModelView, model=LoginActivity):
    name = "Login Activity"
    name_plural = "Login Activities"
    icon = "fa-solid fa-right-to-bracket"
    column_list = [
        LoginActivity.id, LoginActivity.user_id, LoginActivity.device_id,
        LoginActivity.ip_address, LoginActivity.user_agent,
        LoginActivity.success, LoginActivity.reason, LoginActivity.login_at,
    ]
    column_formatters = {
        "user_id": lambda m, a: _sid(m.user_id),
    }
    column_sortable_list = [LoginActivity.login_at, LoginActivity.success]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class OTPAdmin(ModelView, model=OTP):
    name = "OTP"
    name_plural = "OTPs"
    icon = "fa-solid fa-lock"
    column_list = [
        OTP.id, OTP.phone_number, OTP.attempts,
        OTP.is_verified, OTP.expires_at, OTP.created_at,
    ]
    column_sortable_list = [OTP.created_at, OTP.expires_at]
    column_details_exclude_list = [OTP.otp_code]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class ContactAdmin(ModelView, model=Contact):
    name = "Contact"
    name_plural = "Contacts"
    icon = "fa-solid fa-address-book"
    column_list = [
        Contact.id, Contact.contact_owner_id, Contact.name,
        Contact.phone_num, Contact.email, Contact.created_at,
    ]
    column_formatters = {
        "contact_owner_id": lambda m, a: _sid(m.contact_owner_id),
    }
    column_searchable_list = [Contact.name, Contact.phone_num, Contact.normalized_number]
    column_sortable_list = [Contact.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


# ── Posts ─────────────────────────────────────────────────────────────────────

class PostAdmin(ModelView, model=Post):
    name = "Post"
    name_plural = "Posts"
    icon = "fa-solid fa-file-image"
    column_list = [
        Post.id, "user", Post.caption,
        Post.is_hidden, Post.created_at, Post.deleted_at,
    ]
    column_searchable_list = [Post.caption]
    column_sortable_list = [Post.created_at, Post.is_hidden]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class MediaAdmin(ModelView, model=Media):
    name = "Media"
    name_plural = "Media"
    icon = "fa-solid fa-photo-film"
    column_list = [Media.id, "post", Media.file_type, Media.file_path]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class LikeAdmin(ModelView, model=Like):
    name = "Like"
    name_plural = "Likes"
    icon = "fa-solid fa-heart"
    column_list = [Like.id, Like.user_id, Like.post_id, Like.liked, Like.created_at]
    column_formatters = {
        "user_id": lambda m, a: _sid(m.user_id),
        "post_id": lambda m, a: _sid(m.post_id),
    }
    column_sortable_list = [Like.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class CommentAdmin(ModelView, model=Comment):
    name = "Comment"
    name_plural = "Comments"
    icon = "fa-solid fa-comment"
    column_list = [
        Comment.id, "commented_by", Comment.post_id,
        Comment.comment, Comment.is_hidden, Comment.created_at,
    ]
    column_formatters = {
        "post_id": lambda m, a: _sid(m.post_id),
    }
    column_searchable_list = [Comment.comment]
    column_sortable_list = [Comment.created_at, Comment.is_hidden]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class BookmarkFolderAdmin(ModelView, model=BookmarkFolder):
    name = "Bookmark Folder"
    name_plural = "Bookmark Folders"
    icon = "fa-solid fa-folder-bookmark"
    column_list = [
        BookmarkFolder.id, BookmarkFolder.created_by,
        BookmarkFolder.name, BookmarkFolder.created_at,
    ]
    column_formatters = {
        "created_by": lambda m, a: _sid(m.created_by),
    }
    column_searchable_list = [BookmarkFolder.name]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class BookmarkAdmin(ModelView, model=Bookmark):
    name = "Bookmark"
    name_plural = "Bookmarks"
    icon = "fa-solid fa-bookmark"
    column_list = [
        Bookmark.id, "bookmark_folder", "post", Bookmark.created_at,
    ]
    column_sortable_list = [Bookmark.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


# ── Stories ───────────────────────────────────────────────────────────────────

class StoryAdmin(ModelView, model=Story):
    name = "Story"
    name_plural = "Stories"
    icon = "fa-solid fa-circle-play"
    column_list = [
        Story.id, Story.user_id, Story.text,
        Story.created_at, Story.expires_on,
    ]
    column_formatters = {
        "user_id": lambda m, a: _sid(m.user_id),
    }
    column_sortable_list = [Story.created_at, Story.expires_on]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class StoryMediaAdmin(ModelView, model=StoryMedia):
    name = "Story Media"
    name_plural = "Story Media"
    icon = "fa-solid fa-film"
    column_list = [StoryMedia.id, "story", StoryMedia.media_type, StoryMedia.media_url]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class StoryViewAdmin(ModelView, model=StoryView):
    name = "Story View"
    name_plural = "Story Views"
    icon = "fa-solid fa-eye"
    column_list = [StoryView.id, "story", StoryView.viewer_id, StoryView.viewed_at]
    column_formatters = {
        "viewer_id": lambda m, a: _sid(m.viewer_id),
    }
    column_sortable_list = [StoryView.viewed_at]
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True


# ── Messages ──────────────────────────────────────────────────────────────────

class ChatAdmin(ModelView, model=Chat):
    name = "Chat"
    name_plural = "Chats"
    icon = "fa-solid fa-comments"
    column_list = [
        Chat.id, Chat.chat_type, Chat.group_name,
        "participant_one", "participant_two",
        Chat.last_message_datetime, Chat.created_at,
    ]
    column_searchable_list = [Chat.group_name]
    column_sortable_list = [Chat.created_at, Chat.last_message_datetime]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class ChatMemberAdmin(ModelView, model=ChatMember):
    name = "Chat Member"
    name_plural = "Chat Members"
    icon = "fa-solid fa-users"
    column_list = [
        ChatMember.id, "chat", "user",
        ChatMember.role, ChatMember.is_active, ChatMember.joined_at, ChatMember.left_at,
    ]
    column_sortable_list = [ChatMember.joined_at, ChatMember.is_active]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class MessageAdmin(ModelView, model=Message):
    name = "Message"
    name_plural = "Messages"
    icon = "fa-solid fa-envelope"
    column_list = [
        Message.id, "chat", "sender",
        Message.message_type, Message.status, Message.is_read,
        Message.pinned, Message.created_at,
    ]
    column_sortable_list = [Message.created_at, Message.status]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class MessageKeyAdmin(ModelView, model=MessageKey):
    name = "Message Key"
    name_plural = "Message Keys"
    icon = "fa-solid fa-key"
    column_list = [MessageKey.id, "message", MessageKey.device_id, MessageKey.key_slot]
    column_formatters = {
        "device_id": lambda m, a: _sid(m.device_id),
    }
    column_details_exclude_list = [MessageKey.encrypted_key]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class ReactionAdmin(ModelView, model=Reaction):
    name = "Reaction"
    name_plural = "Reactions"
    icon = "fa-solid fa-face-smile"
    column_list = [Reaction.id, "message", "user", Reaction.emoji, Reaction.created_at]
    column_sortable_list = [Reaction.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class StarredMessageAdmin(ModelView, model=StarredMessage):
    name = "Starred Message"
    name_plural = "Starred Messages"
    icon = "fa-solid fa-star"
    column_list = [StarredMessage.id, StarredMessage.message_id, StarredMessage.user_id, StarredMessage.created_at]
    column_formatters = {
        "message_id": lambda m, a: _sid(m.message_id),
        "user_id": lambda m, a: _sid(m.user_id),
    }
    column_sortable_list = [StarredMessage.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class ChatMuteAdmin(ModelView, model=ChatMute):
    name = "Chat Mute"
    name_plural = "Chat Mutes"
    icon = "fa-solid fa-bell-slash"
    column_list = [ChatMute.id, ChatMute.chat_id, ChatMute.user_id, ChatMute.muted_at, ChatMute.muted_until]
    column_formatters = {
        "chat_id": lambda m, a: _sid(m.chat_id),
        "user_id": lambda m, a: _sid(m.user_id),
    }
    column_sortable_list = [ChatMute.muted_at, ChatMute.muted_until]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class SenderKeyAdmin(ModelView, model=SenderKey):
    name = "Sender Key"
    name_plural = "Sender Keys"
    icon = "fa-solid fa-shield-halved"
    column_list = [
        SenderKey.id, SenderKey.chat_id, SenderKey.sender_id,
        SenderKey.sender_device_id, SenderKey.iteration,
        SenderKey.is_active, SenderKey.created_at,
    ]
    column_formatters = {
        "chat_id": lambda m, a: _sid(m.chat_id),
        "sender_id": lambda m, a: _sid(m.sender_id),
    }
    column_sortable_list = [SenderKey.created_at, SenderKey.is_active]
    column_details_exclude_list = [SenderKey.chain_key]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class SenderKeyDistributionAdmin(ModelView, model=SenderKeyDistribution):
    name = "Sender Key Distribution"
    name_plural = "Sender Key Distributions"
    icon = "fa-solid fa-share-nodes"
    column_list = [
        SenderKeyDistribution.id, SenderKeyDistribution.sender_key_id,
        SenderKeyDistribution.recipient_user_id,
        SenderKeyDistribution.recipient_device_id, SenderKeyDistribution.created_at,
    ]
    column_formatters = {
        "sender_key_id": lambda m, a: _sid(m.sender_key_id),
        "recipient_user_id": lambda m, a: _sid(m.recipient_user_id),
    }
    column_sortable_list = [SenderKeyDistribution.created_at]
    column_details_exclude_list = [SenderKeyDistribution.encrypted_chain_key]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class MessageReadReceiptAdmin(ModelView, model=MessageReadReceipt):
    name = "Read Receipt"
    name_plural = "Read Receipts"
    icon = "fa-solid fa-check-double"
    column_list = [MessageReadReceipt.id, MessageReadReceipt.message_id, MessageReadReceipt.user_id, MessageReadReceipt.read_at]
    column_formatters = {
        "message_id": lambda m, a: _sid(m.message_id),
        "user_id": lambda m, a: _sid(m.user_id),
    }
    column_sortable_list = [MessageReadReceipt.read_at]
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True


# ── Calls ─────────────────────────────────────────────────────────────────────

class CallAdmin(ModelView, model=Call):
    name = "Call"
    name_plural = "Calls"
    icon = "fa-solid fa-phone"
    column_list = [
        Call.id, Call.call_from, Call.call_to,
        Call.call_status, Call.is_video_call,
        Call.duration_seconds, Call.created_at,
    ]
    column_formatters = {
        "call_from": lambda m, a: _sid(m.call_from),
        "call_to": lambda m, a: _sid(m.call_to),
    }
    column_sortable_list = [Call.created_at, Call.call_status, Call.duration_seconds]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class ScheduledCallAdmin(ModelView, model=ScheduledCall):
    name = "Scheduled Call"
    name_plural = "Scheduled Calls"
    icon = "fa-solid fa-calendar-check"
    column_list = [
        ScheduledCall.id, ScheduledCall.scheduler_id, ScheduledCall.participant_id,
        ScheduledCall.scheduled_at, ScheduledCall.status,
        ScheduledCall.is_video_call, ScheduledCall.note,
    ]
    column_formatters = {
        "scheduler_id": lambda m, a: _sid(m.scheduler_id),
        "participant_id": lambda m, a: _sid(m.participant_id),
    }
    column_sortable_list = [ScheduledCall.scheduled_at, ScheduledCall.status, ScheduledCall.created_at]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


# ── Notifications ─────────────────────────────────────────────────────────────

class NotificationAdmin(ModelView, model=Notification):
    name = "Notification"
    name_plural = "Notifications"
    icon = "fa-solid fa-bell"
    column_list = [
        Notification.id, Notification.recipient_id, Notification.sender_id,
        Notification.notification_type, Notification.notification_category,
        Notification.title, Notification.is_read, Notification.is_pushed,
        Notification.created_at,
    ]
    column_formatters = {
        "recipient_id": lambda m, a: _sid(m.recipient_id),
        "sender_id": lambda m, a: _sid(m.sender_id),
    }
    column_sortable_list = [Notification.created_at, Notification.is_read, Notification.notification_type]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class NotificationPreferenceAdmin(ModelView, model=NotificationPreference):
    name = "Notification Preference"
    name_plural = "Notification Preferences"
    icon = "fa-solid fa-sliders"
    column_list = [
        NotificationPreference.id, NotificationPreference.user_id,
        NotificationPreference.notifications_enabled,
        NotificationPreference.messages_enabled,
        NotificationPreference.calls_enabled,
        NotificationPreference.quiet_hours_enabled,
    ]
    column_formatters = {
        "user_id": lambda m, a: _sid(m.user_id),
    }
    can_create = False
    can_edit = True
    can_delete = False
    can_view_details = True


# ── Reports ───────────────────────────────────────────────────────────────────

class ReportAdmin(ModelView, model=Report):
    name = "Report"
    name_plural = "Reports"
    icon = "fa-solid fa-flag"
    column_list = [
        Report.id, Report.reporter_id, Report.reported_user_id,
        Report.target_type, Report.target_id,
        Report.reason, Report.status, Report.created_at,
    ]
    column_formatters = {
        "reporter_id": lambda m, a: _sid(m.reporter_id),
        "reported_user_id": lambda m, a: _sid(m.reported_user_id),
    }
    column_searchable_list = [Report.reason, Report.details]
    column_sortable_list = [Report.status, Report.created_at]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class ModerationActionAdmin(ModelView, model=ModerationAction):
    name = "Moderation Action"
    name_plural = "Moderation Actions"
    icon = "fa-solid fa-gavel"
    column_list = [
        ModerationAction.id, ModerationAction.report_id, ModerationAction.moderator_id,
        ModerationAction.action, ModerationAction.action_meta, ModerationAction.created_at,
    ]
    column_formatters = {
        "report_id": lambda m, a: _sid(m.report_id),
        "moderator_id": lambda m, a: _sid(m.moderator_id),
    }
    column_sortable_list = [ModerationAction.created_at]
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True


# ── Loops ─────────────────────────────────────────────────────────────────────

class LoopProfileAdmin(ModelView, model=LoopProfile):
    name = "Loop Profile"
    name_plural = "Loop Profiles"
    icon = "fa-solid fa-circle-user"
    column_list = [
        LoopProfile.id, LoopProfile.user_id, LoopProfile.displayname,
        LoopProfile.gender, LoopProfile.location_name,
        LoopProfile.is_suspended, LoopProfile.created_at,
    ]
    column_formatters = {
        "user_id": lambda m, a: _sid(m.user_id),
    }
    column_searchable_list = [LoopProfile.displayname, LoopProfile.location_name]
    column_sortable_list = [LoopProfile.created_at, LoopProfile.is_suspended]
    form_excluded_columns = ["location"]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class LoopFriendAdmin(ModelView, model=LoopFriend):
    name = "Loop Friend"
    name_plural = "Loop Friends"
    icon = "fa-solid fa-user-group"
    column_list = [
        LoopFriend.id, LoopFriend.loop_profile_id,
        LoopFriend.friend_profile_id, LoopFriend.created_at,
    ]
    column_formatters = {
        "loop_profile_id": lambda m, a: _sid(m.loop_profile_id),
        "friend_profile_id": lambda m, a: _sid(m.friend_profile_id),
    }
    column_sortable_list = [LoopFriend.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class LoopRequestAdmin(ModelView, model=LoopRequest):
    name = "Loop Request"
    name_plural = "Loop Requests"
    icon = "fa-solid fa-user-check"
    column_list = [
        LoopRequest.id, LoopRequest.requester_profile_id,
        LoopRequest.receiver_profile_id, LoopRequest.status, LoopRequest.created_at,
    ]
    column_formatters = {
        "requester_profile_id": lambda m, a: _sid(m.requester_profile_id),
        "receiver_profile_id": lambda m, a: _sid(m.receiver_profile_id),
    }
    column_sortable_list = [LoopRequest.created_at, LoopRequest.status]
    can_create = False
    can_edit = True
    can_delete = True
    can_view_details = True


class LoopChatAdmin(ModelView, model=LoopChat):
    name = "Loop Chat"
    name_plural = "Loop Chats"
    icon = "fa-solid fa-comment-dots"
    column_list = [
        LoopChat.id, LoopChat.profile1_id, LoopChat.profile2_id,
        LoopChat.last_message_at, LoopChat.created_at,
    ]
    column_formatters = {
        "profile1_id": lambda m, a: _sid(m.profile1_id),
        "profile2_id": lambda m, a: _sid(m.profile2_id),
    }
    column_sortable_list = [LoopChat.created_at, LoopChat.last_message_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class LoopMessageAdmin(ModelView, model=LoopMessage):
    name = "Loop Message"
    name_plural = "Loop Messages"
    icon = "fa-solid fa-message"
    column_list = [
        LoopMessage.id, "chat", LoopMessage.sender_profile_id,
        LoopMessage.message_type, LoopMessage.created_at,
    ]
    column_formatters = {
        "sender_profile_id": lambda m, a: _sid(m.sender_profile_id),
    }
    column_sortable_list = [LoopMessage.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class LoopReactionAdmin(ModelView, model=LoopReaction):
    name = "Loop Reaction"
    name_plural = "Loop Reactions"
    icon = "fa-solid fa-face-grin-hearts"
    column_list = [LoopReaction.id, "message", LoopReaction.profile_id, LoopReaction.emoji, LoopReaction.created_at]
    column_formatters = {
        "profile_id": lambda m, a: _sid(m.profile_id),
    }
    column_sortable_list = [LoopReaction.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class LoopProfilePhotoAdmin(ModelView, model=LoopProfilePhoto):
    name = "Loop Profile Photo"
    name_plural = "Loop Profile Photos"
    icon = "fa-solid fa-image"
    column_list = [
        LoopProfilePhoto.id, LoopProfilePhoto.loop_profile_id,
        LoopProfilePhoto.photo_url, LoopProfilePhoto.order,
        LoopProfilePhoto.is_primary, LoopProfilePhoto.created_at,
    ]
    column_formatters = {
        "loop_profile_id": lambda m, a: _sid(m.loop_profile_id),
    }
    column_sortable_list = [LoopProfilePhoto.order, LoopProfilePhoto.created_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


class RandomSessionAdmin(ModelView, model=RandomSession):
    name = "Random Session"
    name_plural = "Random Sessions"
    icon = "fa-solid fa-shuffle"
    column_list = [
        RandomSession.id, RandomSession.profile_id,
        RandomSession.connected_profile_id,
        RandomSession.started_at, RandomSession.ended_at,
    ]
    column_formatters = {
        "profile_id": lambda m, a: _sid(m.profile_id),
        "connected_profile_id": lambda m, a: _sid(m.connected_profile_id),
    }
    column_sortable_list = [RandomSession.started_at, RandomSession.ended_at]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


# ── Content & Misc ────────────────────────────────────────────────────────────

class MemeTemplateAdmin(ModelView, model=MemeTemplates):
    name = "Meme Template"
    name_plural = "Meme Templates"
    icon = "fa-solid fa-face-laugh"
    column_list = [
        MemeTemplates.id, MemeTemplates.template_type,
        MemeTemplates.content, "created_by", MemeTemplates.created_at,
    ]
    column_searchable_list = [MemeTemplates.content]
    column_sortable_list = [MemeTemplates.created_at, MemeTemplates.template_type]
    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True


class ArFilterAdmin(ModelView, model=ArFilter):
    name = "AR Filter"
    name_plural = "AR Filters"
    icon = "fa-solid fa-wand-magic-sparkles"
    column_list = [
        ArFilter.id, ArFilter.filter_key, ArFilter.is_active,
        ArFilter.sort_order, ArFilter.version, ArFilter.updated_at,
    ]
    column_searchable_list = [ArFilter.filter_key]
    column_sortable_list = [ArFilter.sort_order, ArFilter.is_active, ArFilter.updated_at]
    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True


class ArGameConfigAdmin(ModelView, model=ArGameConfig):
    name = "AR Game Config"
    name_plural = "AR Game Configs"
    icon = "fa-solid fa-gamepad"
    column_list = [ArGameConfig.id, ArGameConfig.game_id, ArGameConfig.version, ArGameConfig.updated_at]
    column_searchable_list = [ArGameConfig.game_id]
    column_sortable_list = [ArGameConfig.updated_at, ArGameConfig.version]
    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True


class IpInfoCacheAdmin(ModelView, model=IpInfoCache):
    name = "IP Info Cache"
    name_plural = "IP Info Cache"
    icon = "fa-solid fa-network-wired"
    column_list = [
        IpInfoCache.id, IpInfoCache.ip, IpInfoCache.country,
        IpInfoCache.region, IpInfoCache.city, IpInfoCache.org, IpInfoCache.updated_at,
    ]
    column_searchable_list = [IpInfoCache.ip, IpInfoCache.country, IpInfoCache.city]
    column_sortable_list = [IpInfoCache.updated_at, IpInfoCache.country]
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True


# ── Admin ─────────────────────────────────────────────────────────────────────

class AuditLogAdmin(ModelView, model=AdminAuditLog):
    name = "Audit Log"
    name_plural = "Audit Logs"
    icon = "fa-solid fa-clipboard-list"
    column_list = [
        AdminAuditLog.id, AdminAuditLog.admin_id, AdminAuditLog.action,
        AdminAuditLog.target_type, AdminAuditLog.target_id,
        AdminAuditLog.ip_address, AdminAuditLog.created_at,
    ]
    column_formatters = {
        "admin_id": lambda m, a: _sid(m.admin_id),
    }
    column_searchable_list = [AdminAuditLog.action, AdminAuditLog.target_type]
    column_sortable_list = [AdminAuditLog.created_at, AdminAuditLog.action]
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True


# ── Mount ─────────────────────────────────────────────────────────────────────

def create_admin(app) -> Admin:
    import os
    from app.config import settings
    auth_backend = SuperAdminAuth(secret_key=settings.SECRET_KEY)
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    admin = Admin(
        app, engine,
        authentication_backend=auth_backend,
        base_url="/sqladmin",
        templates_dir=templates_dir,
    )

    # Users
    admin.add_view(UserAdmin)
    admin.add_view(UserSessionAdmin)
    admin.add_view(UserDeviceAdmin)
    admin.add_view(FCMTokenAdmin)
    admin.add_view(FollowAdmin)
    admin.add_view(BlockAdmin)
    admin.add_view(ContactAdmin)
    admin.add_view(UserPreferenceAdmin)
    admin.add_view(UserPasskeyAdmin)
    admin.add_view(LoginActivityAdmin)
    admin.add_view(OTPAdmin)

    # Posts
    admin.add_view(PostAdmin)
    admin.add_view(MediaAdmin)
    admin.add_view(LikeAdmin)
    admin.add_view(CommentAdmin)
    admin.add_view(BookmarkFolderAdmin)
    admin.add_view(BookmarkAdmin)

    # Stories
    admin.add_view(StoryAdmin)
    admin.add_view(StoryMediaAdmin)
    admin.add_view(StoryViewAdmin)

    # Messages
    admin.add_view(ChatAdmin)
    admin.add_view(ChatMemberAdmin)
    admin.add_view(MessageAdmin)
    admin.add_view(MessageKeyAdmin)
    admin.add_view(ReactionAdmin)
    admin.add_view(StarredMessageAdmin)
    admin.add_view(ChatMuteAdmin)
    admin.add_view(SenderKeyAdmin)
    admin.add_view(SenderKeyDistributionAdmin)
    admin.add_view(MessageReadReceiptAdmin)

    # Calls
    admin.add_view(CallAdmin)
    admin.add_view(ScheduledCallAdmin)

    # Notifications
    admin.add_view(NotificationAdmin)
    admin.add_view(NotificationPreferenceAdmin)

    # Reports & Moderation
    admin.add_view(ReportAdmin)
    admin.add_view(ModerationActionAdmin)

    # Loops
    admin.add_view(LoopProfileAdmin)
    admin.add_view(LoopFriendAdmin)
    admin.add_view(LoopRequestAdmin)
    admin.add_view(LoopChatAdmin)
    admin.add_view(LoopMessageAdmin)
    admin.add_view(LoopReactionAdmin)
    admin.add_view(LoopProfilePhotoAdmin)
    admin.add_view(RandomSessionAdmin)

    # Content & Misc
    admin.add_view(MemeTemplateAdmin)
    admin.add_view(ArFilterAdmin)
    admin.add_view(ArGameConfigAdmin)
    admin.add_view(IpInfoCacheAdmin)

    # Admin
    admin.add_view(AuditLogAdmin)

    return admin
