from pydantic import BaseModel
from sqlmodel import Field, SQLModel, Relationship
from ..users.models import User, UserBasic
from datetime import datetime, timezone
from ..datetime_utils import UTCDatetime
from typing import Optional
from enum import Enum
from ..uuid_utils import generate_uuid


class ChatType(str, Enum):
    DM = "dm"
    GROUP = "group"


class ChatMemberRole(str, Enum):
    ADMIN = "admin"
    MEMBER = "member"


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    FILE = "file"
    AUDIO = "audio"
    POST = "post"
    CALL = "call"
    LOCATION = "location"
    CONTACT = "contact"
    SYSTEM = "system"
    OTHER = "other"


class MessageStatus(str, Enum):
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class Chat(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    chat_type: str = Field(default=ChatType.DM)  # "dm" or "group"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_for_user_id: str | None = Field(default=None)

    # DM fields (nullable for groups)
    participant_one_id: str | None = Field(default=None, foreign_key="user.id")
    participant_two_id: str | None = Field(default=None, foreign_key="user.id")

    # Group fields (nullable for DMs)
    group_name: str | None = Field(default=None)
    group_icon_url: str | None = Field(default=None)
    group_description: str | None = Field(default=None)
    created_by_id: str | None = Field(default=None, foreign_key="user.id")
    max_members: int = Field(default=1024)
    # "private" = members only, "invite_only" = invite link, "public" = anyone can find/join
    join_mode: str = Field(default="private")

    last_message: str | None = Field(default=None)
    last_message_type: str | None = Field(default=None)
    last_message_datetime: datetime | None = Field(default=None)
    disappearing_timer: int | None = Field(default=None)

    messages: list["Message"] = Relationship(back_populates="chat", cascade_delete=True)
    members: list["ChatMember"] = Relationship(back_populates="chat", cascade_delete=True)
    participant_one: Optional[User] = Relationship(
        back_populates="chat_participant_one",
        sa_relationship_kwargs=dict(foreign_keys="[Chat.participant_one_id]"),
    )
    participant_two: Optional[User] = Relationship(
        back_populates="chat_participant_two",
        sa_relationship_kwargs=dict(foreign_keys="[Chat.participant_two_id]"),
    )
    created_by: Optional[User] = Relationship(
        sa_relationship_kwargs=dict(foreign_keys="[Chat.created_by_id]"),
    )

    def __str__(self) -> str:
        return self.group_name or f"DM:{self.id[:8]}"


class Message(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)

    message: str | None = None
    message_type: MessageType = Field(
        default=MessageType.TEXT, sa_column_kwargs={"nullable": False}
    )
    caption: str | None = None
    link_url: str | None = None

    media_url: str | None = None
    media_type: str | None = None
    media_thumbnail_url: str | None = None
    file_size: int | None = None
    file_size: int | None = None
    duration: int | None = None
    media_encryption: str | None = None

    # Call specific metadata when message_type == CALL
    is_video_call: bool | None = None
    call_status: str | None = None

    # Location message fields
    latitude: float | None = None
    longitude: float | None = None
    location_name: str | None = None

    # Contact sharing fields
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_user_id: str | None = Field(default=None)

    shared_post_id: str | None = Field(default=None, foreign_key="post.id")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    edited_at: datetime | None = None
    deleted_at: datetime | None = None

    expires_at: datetime | None = Field(default=None, index=True)  # for disappearing messages
    deleted_for_user_id: str | None = Field(default=None)
    is_edited: bool = Field(default=False)
    is_read: bool = Field(default=False)
    status: MessageStatus = Field(
        default=MessageStatus.SENT, sa_column_kwargs={"nullable": False}
    )  # sent, delivered, read, failed

    pinned: bool = Field(default=False)
    is_forwarded: bool = Field(default=False)
    forwarded_from_id: str | None = Field(default=None, foreign_key="user.id")

    is_system_message: bool = Field(default=False)  # "X added Y", "X left the group"

    sender_id: str = Field(foreign_key="user.id")
    receiver_id: str | None = Field(default=None, foreign_key="user.id")  # nullable for group messages
    chat_id: str = Field(foreign_key="chat.id")
    reply_to_id: str | None = Field(default=None, foreign_key="message.id")

    chat: "Chat" = Relationship(back_populates="messages")
    sender: "User" = Relationship(
        back_populates="sent_messages",
        sa_relationship_kwargs={"foreign_keys": "[Message.sender_id]"},
    )
    receiver: Optional["User"] = Relationship(
        back_populates="received_messages",
        sa_relationship_kwargs={"foreign_keys": "[Message.receiver_id]"},
    )
    forwarded_from: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[Message.forwarded_from_id]"}
    )
    reply_to: Optional["Message"] = Relationship(
        sa_relationship_kwargs=dict(foreign_keys="[Message.reply_to_id]")
    )
    shared_post: Optional["Post"] = Relationship(back_populates="shared_in_messages")
    reactions: list["Reaction"] = Relationship(back_populates="message")
    message_keys: list["MessageKey"] = Relationship(
        back_populates="message", cascade_delete=True
    )

    def __str__(self) -> str:
        return f"{self.message_type}:{self.id[:8]}"


class MessageKey(SQLModel, table=True):
    """Per-device encrypted AES key for a message.

    Extracted from the E2E encrypted payload so each device can retrieve
    only its own key instead of the full keys map.
    """
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    device_id: str = Field(index=True)
    encrypted_key: str  # Base64 RSA-OAEP encrypted AES key
    key_slot: str = Field(default="body")  # "body", "caption", or "media"

    message: "Message" = Relationship(back_populates="message_keys")


class ChatMute(SQLModel, table=True):
    """Per-user mute setting for a chat. Push notifications are suppressed while muted."""
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    chat_id: str = Field(foreign_key="chat.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    muted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    muted_until: datetime | None = Field(default=None)  # None = muted indefinitely


class Reaction(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    emoji: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str = Field(foreign_key="user.id")
    message_id: str = Field(foreign_key="message.id")
    user: "User" = Relationship(back_populates="reactions")
    message: "Message" = Relationship(back_populates="reactions")


class StarredMessage(SQLModel, table=True):
    """User-starred messages for quick access."""
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatMember(SQLModel, table=True):
    """Membership record for group chats. Not used for DMs."""
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    chat_id: str = Field(foreign_key="chat.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    role: str = Field(default=ChatMemberRole.MEMBER)  # "admin" or "member"
    joined_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    added_by_id: str | None = Field(default=None, foreign_key="user.id")
    is_active: bool = Field(default=True)  # soft-leave without deleting history
    left_at: datetime | None = Field(default=None)

    chat: "Chat" = Relationship(back_populates="members")
    user: "User" = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[ChatMember.user_id]"},
    )
    added_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[ChatMember.added_by_id]"},
    )


class SenderKey(SQLModel, table=True):
    """Sender Keys for efficient group E2E encryption.

    Each sender generates a symmetric chain key and distributes it
    (RSA-encrypted) to all group members' devices once. Subsequent
    messages use this key with a ratcheting counter, so each message
    only requires a single AES encryption regardless of group size.

    Key rotation happens when:
    - A member is removed (all remaining senders re-key)
    - A sender's device changes
    - Manual rotation for forward secrecy
    """
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    chat_id: str = Field(foreign_key="chat.id", index=True)
    sender_id: str = Field(foreign_key="user.id", index=True)
    sender_device_id: str = Field(index=True)
    chain_key: str  # Base64 AES-256 chain key (encrypted per recipient in SenderKeyDistribution)
    iteration: int = Field(default=0)  # Ratchet counter
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = Field(default=True)


class SenderKeyDistribution(SQLModel, table=True):
    """Per-device distribution of a sender's key to a group member.

    When a sender creates/rotates their SenderKey, they RSA-encrypt
    the chain_key for each member's device and store it here. Members
    fetch their distributions to decrypt group messages from that sender.
    """
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    sender_key_id: str = Field(foreign_key="senderkey.id", index=True)
    recipient_device_id: str = Field(index=True)  # The device that can decrypt this
    recipient_user_id: str = Field(foreign_key="user.id", index=True)
    encrypted_chain_key: str  # RSA-OAEP encrypted chain key for this device
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MessageReadReceipt(SQLModel, table=True):
    """Per-user read tracking for group messages.
    DMs continue to use Message.is_read / Message.status.
    """
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    read_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MessageCreate(BaseModel):
    message: str | None = None
    message_type: MessageType = MessageType.TEXT
    caption: str | None = None
    link_url: str | None = None
    media_url: str | None = None
    media_type: str | None = None
    media_thumbnail_url: str | None = None
    file_size: int | None = None
    duration: int | None = None
    shared_post_id: str | None = None
    reply_to_id: str | None = None
    forwarded_from_id: str | None = None
    pinned: bool = False
    is_forwarded: bool = False
    # Optional call fields to allow creating CALL-type system messages
    is_video_call: bool | None = None
    is_video_call: bool | None = None
    call_status: str | None = None
    media_encryption: str | None = None
    # Location
    latitude: float | None = None
    longitude: float | None = None
    location_name: str | None = None
    # Contact sharing
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_user_id: str | None = None


class ChatResponse(BaseModel):
    id: str
    user: UserBasic
    last_message: str | None = None
    last_message_type: str | None = None
    last_message_datetime: UTCDatetime | None = None
    unread_count: int = 0
    is_muted: bool = False
    disappearing_timer: int | None = None


class ReactionResponse(BaseModel):
    id: str
    emoji: str
    user_id: str
    message_id: str
    user: Optional[UserBasic] = None


class MessageResponse(BaseModel):
    id: str
    message: str | None = None
    message_type: MessageType = MessageType.TEXT
    caption: str | None = None
    link_url: str | None = None
    media_url: str | None = None
    media_type: str | None = None
    media_thumbnail_url: str | None = None
    file_size: int | None = None
    duration: int | None = None
    # Call fields (for message_type == CALL)
    is_video_call: bool | None = None
    is_video_call: bool | None = None
    call_status: str | None = None
    media_encryption: str | None = None
    # Location
    latitude: float | None = None
    longitude: float | None = None
    location_name: str | None = None
    # Contact sharing
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_user_id: str | None = None
    # Starring
    is_starred: bool = False
    expires_at: UTCDatetime | None = None
    shared_post_id: str | None = None
    shared_post: Optional[dict] = None  # Enriched post details
    reply_to_id: str | None = None
    forwarded_from_id: str | None = None
    pinned: bool = False
    is_forwarded: bool = False
    created_at: UTCDatetime
    updated_at: UTCDatetime
    is_system_message: bool = False
    sender: UserBasic
    receiver: Optional[UserBasic] = None  # None for group messages
    chat_id: str
    chat_type: str = "dm"
    status: MessageStatus = MessageStatus.SENT
    reactions: list[ReactionResponse] = []


class MessageSend(BaseModel):
    message: str | None = None
    receiver_id: str
    message_type: MessageType = MessageType.TEXT
    caption: str | None = None
    link_url: str | None = None
    media_url: str | None = None
    media_type: str | None = None
    media_thumbnail_url: str | None = None
    file_size: int | None = None
    duration: int | None = None
    shared_post_id: str | None = None
    reply_to_id: str | None = None
    forwarded_from_id: str | None = None
    pinned: bool = False
    is_forwarded: bool = False
    # Optional call fields to create system call messages if needed
    is_video_call: bool | None = None
    is_video_call: bool | None = None
    call_status: str | None = None
    media_encryption: str | None = None
    # Location
    latitude: float | None = None
    longitude: float | None = None
    location_name: str | None = None
    # Contact sharing
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_user_id: str | None = None


class MessageEdit(MessageSend):
    id: str


# --- Group Chat Schemas ---

class GroupCreate(BaseModel):
    name: str
    member_ids: list[str]  # Initial members (creator auto-added as admin)
    icon_url: str | None = None
    description: str | None = None
    join_mode: str = "private"  # "private", "invite_only", "public"


class GroupUpdate(BaseModel):
    name: str | None = None
    icon_url: str | None = None
    description: str | None = None


class GroupMemberAdd(BaseModel):
    user_ids: list[str]


class GroupMemberUpdate(BaseModel):
    role: str  # "admin" or "member"


class GroupMemberResponse(BaseModel):
    user: UserBasic
    role: str
    joined_at: UTCDatetime
    is_active: bool = True


class GroupInfoResponse(BaseModel):
    id: str
    chat_type: str = "group"
    group_name: str
    group_icon_url: str | None = None
    group_description: str | None = None
    created_by: UserBasic
    members: list[GroupMemberResponse]
    member_count: int
    max_members: int = 1024
    join_mode: str = "private"
    created_at: UTCDatetime
    last_message: str | None = None
    last_message_type: str | None = None
    last_message_datetime: UTCDatetime | None = None
    disappearing_timer: int | None = None


class ChatResponseV2(BaseModel):
    """Unified response for both DMs and groups in the chat list."""
    id: str
    chat_type: str  # "dm" or "group"
    # DM fields
    user: Optional[UserBasic] = None
    # Group fields
    group_name: str | None = None
    group_icon_url: str | None = None
    member_count: int | None = None
    # Common fields
    last_message: str | None = None
    last_message_type: str | None = None
    last_message_datetime: UTCDatetime | None = None
    unread_count: int = 0
    is_muted: bool = False
    disappearing_timer: int | None = None


class GroupMessageSend(BaseModel):
    """Send a message to a group chat."""
    chat_id: str | None = None  # optional — already provided in URL path
    message: str | None = None
    message_type: MessageType = MessageType.TEXT
    caption: str | None = None
    link_url: str | None = None
    media_url: str | None = None
    media_type: str | None = None
    media_thumbnail_url: str | None = None
    file_size: int | None = None
    duration: int | None = None
    shared_post_id: str | None = None
    reply_to_id: str | None = None
    forwarded_from_id: str | None = None
    is_forwarded: bool = False
    media_encryption: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_name: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_user_id: str | None = None


class SenderKeyDistributionRequest(BaseModel):
    """Client sends their sender key encrypted for each member's device."""
    chat_id: str
    sender_device_id: str
    chain_key: str  # Base64 raw chain key (stored server-side for reference)
    distributions: list[dict]  # [{"device_id": "...", "user_id": "...", "encrypted_chain_key": "..."}]
    iteration: int = 0
