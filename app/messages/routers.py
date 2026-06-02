from typing import Annotated
import logging
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel as PydanticBaseModel
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Request, HTTPException, Query
from sqlmodel import select, or_, func
from ..security import decode_access_token
from ..users.models import User, Block
from ..users.routers import get_current_active_user
from ..dependencies import SessionDep
import json
from .models import (
    Chat,
    ChatMute,
    ChatResponse,
    Message,
    MessageCreate,
    MessageKey,
    MessageResponse,
    MessageType,
    ReactionResponse,
    StarredMessage,
)
from ..users.models import UserBasic
from .models import MessageEdit, MessageStatus
from .models import Reaction as ReactionModel
from app.sockets.socketio_server import sio
from app.sockets.active import is_active
from app.redis_client import redis_client
import asyncio
import threading
from ..notifications.enums import NotificationType
from ..notifications.services.notification_service import notification_service

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependency - will be imported locally when needed
# from app.sockets.socketio_events import is_user_online

_IDEM_TTL = 3600  # 1 hour TTL for idempotency keys


async def _idem_get(key: str) -> str | None:
    """Look up an idempotency key in Redis."""
    return await redis_client.get(f"idem:{key}")


async def _idem_set(key: str, message_id: str) -> None:
    """Store an idempotency key → message_id mapping in Redis."""
    await redis_client.set(f"idem:{key}", message_id, ex=_IDEM_TTL)

router = APIRouter(tags=["messages"])

async def emit_message_async(sio, payload, receiver_id):
    """Async function to emit socket messages"""
    # Use room-based routing to reach ALL connections for this user across workers
    room = f"user:{receiver_id}"
    await sio.emit("message:new", payload, room=room)
    logger.debug(f"Message emitted to room {room}")

def emit_message_sync(sio, payload, receiver_id):
    """Sync wrapper to emit socket messages"""
    try:
        # Get the event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're in an async context, schedule the task
            asyncio.create_task(emit_message_async(sio, payload, receiver_id))
        else:
            # If no loop is running, run it
            asyncio.run(emit_message_async(sio, payload, receiver_id))
    except RuntimeError:
        # If no event loop exists, create a new one
        asyncio.run(emit_message_async(sio, payload, receiver_id))


# ---------------------------------------------------------------------------
# E2E encryption helpers: split / reconstruct encrypted blobs
# ---------------------------------------------------------------------------

def _is_encrypted_blob(text: str | None) -> bool:
    """Check if a string looks like an E2E encrypted JSON payload."""
    if not text or not text.strip().startswith("{"):
        return False
    try:
        obj = json.loads(text)
        return "iv" in obj and ("ciphertext" in obj or "keys" in obj)
    except (json.JSONDecodeError, TypeError):
        return False


def _strip_keys_from_blob(text: str) -> str:
    """Remove the 'keys' map from an encrypted blob, keeping only iv+ciphertext."""
    obj = json.loads(text)
    stripped = {"iv": obj["iv"]}
    if "ciphertext" in obj:
        stripped["ciphertext"] = obj["ciphertext"]
    return json.dumps(stripped, separators=(",", ":"))


def _extract_and_save_keys(
    blob_text: str,
    message_id: str,
    key_slot: str,
    session,
) -> None:
    """Parse keys from an encrypted blob and insert MessageKey rows."""
    obj = json.loads(blob_text)
    keys = obj.get("keys")
    if not keys or not isinstance(keys, dict):
        return
    for device_id, encrypted_key in keys.items():
        mk = MessageKey(
            message_id=message_id,
            device_id=device_id,
            encrypted_key=encrypted_key,
            key_slot=key_slot,
        )
        session.add(mk)


def _reconstruct_blob_for_device(
    stripped_text: str,
    message_id: str,
    device_id: str | None,
    key_slot: str,
    session,
) -> str:
    """Reconstruct an encrypted blob with only the requesting device's key.
    Falls back to returning the text as-is if no MessageKey rows found (old messages).
    """
    if not stripped_text or not device_id:
        return stripped_text

    try:
        obj = json.loads(stripped_text)
    except (json.JSONDecodeError, TypeError):
        return stripped_text

    # If blob already contains keys (old format), return as-is
    if "keys" in obj and obj["keys"]:
        return stripped_text

    # Look up this device's key
    mk = session.exec(
        select(MessageKey).where(
            MessageKey.message_id == message_id,
            MessageKey.device_id == device_id,
            MessageKey.key_slot == key_slot,
        )
    ).first()

    if mk:
        obj["keys"] = {device_id: mk.encrypted_key}
    else:
        obj["keys"] = {}

    return json.dumps(obj, separators=(",", ":"))


def _reconstruct_blob_all_keys(
    stripped_text: str,
    message_id: str,
    key_slot: str,
    session,
) -> str:
    """Reconstruct an encrypted blob with ALL device keys (for socket emission)."""
    if not stripped_text:
        return stripped_text

    try:
        obj = json.loads(stripped_text)
    except (json.JSONDecodeError, TypeError):
        return stripped_text

    # If blob already contains keys (old format), return as-is
    if "keys" in obj and obj["keys"]:
        return stripped_text

    mks = session.exec(
        select(MessageKey).where(
            MessageKey.message_id == message_id,
            MessageKey.key_slot == key_slot,
        )
    ).all()

    obj["keys"] = {mk.device_id: mk.encrypted_key for mk in mks}
    return json.dumps(obj, separators=(",", ":"))


# Human-readable preview for Chat.last_message (never the encrypted blob)
_LAST_MESSAGE_PREVIEWS = {
    MessageType.TEXT: "Message",
    MessageType.IMAGE: "Photo",
    MessageType.VIDEO: "Video",
    MessageType.AUDIO: "Audio",
    MessageType.FILE: "File",
    MessageType.POST: "Shared a post",
    MessageType.CALL: "Call",
    MessageType.LOCATION: "Location",
    MessageType.CONTACT: "Shared a contact",
    MessageType.OTHER: "Message",
}


@router.get("/messages/{receiver_id}/", response_model=list[MessageResponse])
def view_messages(
    receiver_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    request: Request,
    limit: int = 50,
    before_id: str | None = None,
) -> list[MessageResponse]:
    device_id = request.headers.get("x-device-id")
    # Block enforcement: forbid viewing conversation if blocked either way
    if (
        session.exec(
            select(Block).where(
                (Block.blocker_id == current_user.id) & (Block.blocked_id == receiver_id)
                | (Block.blocker_id == receiver_id) & (Block.blocked_id == current_user.id)
            )
        ).first()
        is not None
    ):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="You cannot view messages with this user")
    stmt = select(Message).where(
        (Message.sender_id == current_user.id)
        | (Message.receiver_id == current_user.id),
        (Message.sender_id == receiver_id) | (Message.receiver_id == receiver_id),
        or_(
            Message.deleted_for_user_id.is_(None),
            Message.deleted_for_user_id != current_user.id,
        ),
    )
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    stmt = stmt.order_by(Message.created_at.desc()).limit(limit)

    messages = session.exec(stmt).all()
    logger.debug(f"Retrieved {len(messages)} messages for user {current_user.id} and receiver {receiver_id}")

    # Build proper response with per-device key reconstruction
    return [build_message_response(msg, device_id=device_id, session=session) for msg in messages]


def save_message(
    sender_id: str,
    receiver_id: str,
    message_text: str | None,
    session: SessionDep,
    caption: str | None = None,
    link_url: str | None = None,
    media_url: str | None = None,
    media_type: str | None = None,
    media_thumbnail_url: str | None = None,
    file_size: int | None = None,
    duration: int | None = None,
    shared_post_id: str | None = None,
    reply_to_id: str | None = None,
    forwarded_from_id: str | None = None,
    pinned: bool = False,
    is_forwarded: bool = False,
    is_video_call: bool | None = None,
    call_status: str | None = None,
    media_encryption: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    location_name: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    contact_user_id: str | None = None,
):
    # Block enforcement: do not create message if blocked either way
    if (
        session.exec(
            select(Block).where(
                (Block.blocker_id == sender_id) & (Block.blocked_id == receiver_id)
                | (Block.blocker_id == receiver_id) & (Block.blocked_id == sender_id)
            )
        ).first()
        is not None
    ):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="You cannot message this user")

    if call_status is not None or is_video_call is not None:
        message_type = MessageType.CALL
    elif latitude is not None and longitude is not None:
        message_type = MessageType.LOCATION
    elif contact_name or contact_phone or contact_user_id:
        message_type = MessageType.CONTACT
    elif shared_post_id:
        message_type = MessageType.POST
    elif media_url:
        if media_type == "image":
            message_type = MessageType.IMAGE
        elif media_type == "video":
            message_type = MessageType.VIDEO
        elif media_type == "audio":
            message_type = MessageType.AUDIO
        elif media_type == "file":
            message_type = MessageType.FILE
        else:
            message_type = MessageType.OTHER
    else:
        message_type = MessageType.TEXT
    statement = select(Chat).where(
        or_(
            (Chat.participant_one_id == receiver_id)
            & (Chat.participant_two_id == sender_id),
            (Chat.participant_one_id == sender_id)
            & (Chat.participant_two_id == receiver_id),
        )
    )
    chat = session.exec(statement).first()
    if not chat:
        chat = Chat()
        chat.participant_one_id = sender_id
        chat.participant_two_id = receiver_id
        session.add(chat)
        session.commit()
        session.refresh(chat)
    # Strip keys from encrypted blobs before storing
    stored_message_text = message_text
    stored_caption = caption
    stored_media_encryption = media_encryption
    body_encrypted = _is_encrypted_blob(message_text)
    caption_encrypted = _is_encrypted_blob(caption)
    media_encrypted = _is_encrypted_blob(media_encryption)

    if body_encrypted:
        stored_message_text = _strip_keys_from_blob(message_text)
    if caption_encrypted:
        stored_caption = _strip_keys_from_blob(caption)
    if media_encrypted:
        stored_media_encryption = _strip_keys_from_blob(media_encryption)

    message = Message(
        message=stored_message_text,
        message_type=message_type,
        caption=stored_caption,
        link_url=link_url,
        media_url=media_url,
        media_type=media_type,
        media_thumbnail_url=media_thumbnail_url,
        file_size=file_size,
        duration=duration,
        is_video_call=is_video_call,
        call_status=call_status,
        latitude=latitude,
        longitude=longitude,
        location_name=location_name,
        contact_name=contact_name,
        contact_phone=contact_phone,
        contact_user_id=contact_user_id,
        shared_post_id=shared_post_id,
        reply_to_id=reply_to_id,
        forwarded_from_id=forwarded_from_id,
        pinned=pinned,
        is_forwarded=is_forwarded,
        sender_id=sender_id,
        receiver_id=receiver_id,
        chat_id=chat.id,
        media_encryption=stored_media_encryption,
    )
    # Set expiry if chat has disappearing messages enabled
    if chat.disappearing_timer:
        message.expires_at = datetime.now(timezone.utc) + timedelta(seconds=chat.disappearing_timer)

    session.add(message)
    session.flush()  # get message.id before inserting keys

    # Save per-device keys into MessageKey table
    if body_encrypted:
        _extract_and_save_keys(message_text, message.id, "body", session)
    if caption_encrypted:
        _extract_and_save_keys(caption, message.id, "caption", session)
    if media_encrypted:
        _extract_and_save_keys(media_encryption, message.id, "media", session)

    # Store human-readable preview when encrypted, otherwise keep the actual text
    if body_encrypted:
        chat.last_message = _LAST_MESSAGE_PREVIEWS.get(message_type, "Message")
    else:
        chat.last_message = message.message
    chat.last_message_type = message.message_type.value if message.message_type else None
    chat.last_message_datetime = message.created_at
    # Clear soft-delete so the chat reappears for both users
    if chat.deleted_for_user_id is not None:
        chat.deleted_for_user_id = None

    logger.debug(f"Saving message - sender: {message.sender_id}, receiver: {message.receiver_id}, chat: {message.chat_id}")

    try:
        session.commit()
        session.refresh(chat)
        session.refresh(message)
        logger.debug(f"Message saved successfully with ID: {message.id}")
        return message
    except Exception as e:
        logger.error(f"Error committing message to database: {e}")
        session.rollback()
        raise e


def update_message(
    sender_id: str,
    receiver_id: str,
    message_id: str,
    message_text: str | None,
    session: SessionDep,
    message_type: str = "text",
    caption: str | None = None,
    link_url: str | None = None,
    media_url: str | None = None,
    media_type: str | None = None,
    media_thumbnail_url: str | None = None,
    file_size: int | None = None,
    duration: int | None = None,
    shared_post_id: str | None = None,
    reply_to_id: str | None = None,
    forwarded_from_id: str | None = None,
    pinned: bool = False,
    is_forwarded: bool = False,
    media_encryption: str | None = None,
):
    statement = select(Message).where(Message.id == message_id)
    message = session.exec(statement).first()
    if message:
        if message.sender_id == sender_id:
            # Strip keys from encrypted blobs
            stored_message_text = message_text
            stored_caption = caption
            stored_media_enc = media_encryption
            if _is_encrypted_blob(message_text):
                stored_message_text = _strip_keys_from_blob(message_text)
                # Delete old keys and insert new ones
                session.exec(select(MessageKey).where(MessageKey.message_id == message_id, MessageKey.key_slot == "body")).all()
                for old_key in session.exec(select(MessageKey).where(MessageKey.message_id == message_id, MessageKey.key_slot == "body")).all():
                    session.delete(old_key)
                _extract_and_save_keys(message_text, message_id, "body", session)
            if _is_encrypted_blob(caption):
                stored_caption = _strip_keys_from_blob(caption)
                for old_key in session.exec(select(MessageKey).where(MessageKey.message_id == message_id, MessageKey.key_slot == "caption")).all():
                    session.delete(old_key)
                _extract_and_save_keys(caption, message_id, "caption", session)
            if _is_encrypted_blob(media_encryption):
                stored_media_enc = _strip_keys_from_blob(media_encryption)
                for old_key in session.exec(select(MessageKey).where(MessageKey.message_id == message_id, MessageKey.key_slot == "media")).all():
                    session.delete(old_key)
                _extract_and_save_keys(media_encryption, message_id, "media", session)

            message.message = stored_message_text
            message.message_type = message_type
            message.caption = stored_caption
            message.link_url = link_url
            message.media_url = media_url
            message.media_type = media_type
            message.media_thumbnail_url = media_thumbnail_url
            message.file_size = file_size
            message.duration = duration
            message.shared_post_id = shared_post_id
            message.reply_to_id = reply_to_id
            message.forwarded_from_id = forwarded_from_id
            message.pinned = pinned
            message.is_forwarded = is_forwarded
            message.media_encryption = stored_media_enc
            # Mark as edited and set edited timestamp
            message.is_edited = True
            message.edited_at = datetime.now(timezone.utc)
            message.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(message)
            return message
        elif message.receiver_id == sender_id:
            # Receiver can only update pinned status (don't mark as edited)
            message.pinned = pinned
            message.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(message)
            return message

    return {"detail": "message does not exists or you dont have permission"}


def delete_message_helper(
    message_id: str,
    user_id: str,
    session: SessionDep,
):
    statement = select(Message).where(Message.id == message_id)
    message = session.exec(statement).first()
    if message and (message.sender_id == user_id or message.receiver_id == user_id):
        if message.deleted_for_user_id:
            session.delete(message)
        else:
            message.deleted_for_user_id = user_id
            session.add(message)
        session.commit()
        return {"detail": "message is deleted"}
    return {"detail": "message does not exists or you dont have permission"}


@router.post("/messages/{receiver_id}/", response_model=MessageResponse)
async def send_message(
    receiver_id: str,
    message_create: MessageCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    request: Request,
):
    # Lazy import to avoid circular dependency
    from app.sockets.socketio_events import is_user_online
    device_id = request.headers.get("x-device-id")
    idem_key = request.headers.get("Idempotency-Key")
    if idem_key:
        existing_id = await _idem_get(idem_key)
        if existing_id:
            existing = session.get(Message, existing_id)
            if existing:
                return build_message_response(existing, device_id=device_id, session=session)
    # Be tolerant of clients that don't send new optional fields
    _is_video_call = getattr(message_create, "is_video_call", None)
    _call_status = getattr(message_create, "call_status", None)
    message = save_message(
        sender_id=current_user.id,
        receiver_id=receiver_id,
        message_text=message_create.message,
        session=session,
        caption=message_create.caption,
        link_url=message_create.link_url,
        media_url=message_create.media_url,
        media_type=message_create.media_type,
        media_thumbnail_url=message_create.media_thumbnail_url,
        file_size=message_create.file_size,
        duration=message_create.duration,
        shared_post_id=message_create.shared_post_id,
        reply_to_id=message_create.reply_to_id,
        forwarded_from_id=message_create.forwarded_from_id,
        pinned=message_create.pinned,
        is_forwarded=message_create.is_forwarded,
        is_video_call=_is_video_call,
        call_status=_call_status,
        media_encryption=message_create.media_encryption,
        latitude=message_create.latitude,
        longitude=message_create.longitude,
        location_name=message_create.location_name,
        contact_name=message_create.contact_name,
        contact_phone=message_create.contact_phone,
        contact_user_id=message_create.contact_user_id,
    )
    if idem_key:
        await _idem_set(idem_key, message.id)
    # Emit WS event to receiver if online (include ALL keys so all receiver devices can decrypt)
    payload = build_message_response(message, include_all_keys=True, session=session).model_dump(mode="json")
    logger.debug(f"Emitting message:new to receiver_id: {receiver_id}")
    
    # Use room-based routing to reach ALL connections for this user
    receiver_room = f"user:{receiver_id}"
    is_online = await is_user_online(receiver_id) if hasattr(sio, 'manager') else await is_active(receiver_id)
    is_actively_viewing = False
    
    if is_online:
        from app.redis_client import redis_client
        try:
            active_chat = await redis_client.get(f"current_chat:{receiver_id}")
            is_actively_viewing = active_chat == str(current_user.id)
            
            logger.debug(f"User {receiver_id} is online, sending via socket")
            await sio.emit("message:new", payload, room=receiver_room)
        except Exception as e:
            logger.warning(f"Failed to emit message to user {receiver_id}: {e}")
            is_online = False

    # Sync to sender's other devices
    sender_room = f"user:{current_user.id}"
    await sio.emit("message:new", payload, room=sender_room)

    # Check if the receiver has muted this chat — skip push notification if so
    is_chat_muted = False
    mute_record = session.exec(
        select(ChatMute).where(ChatMute.chat_id == message.chat_id, ChatMute.user_id == receiver_id)
    ).first()
    if mute_record:
        if mute_record.muted_until and datetime.now(timezone.utc) > mute_record.muted_until:
            session.delete(mute_record)
            session.commit()
        else:
            is_chat_muted = True

    if not is_chat_muted and (not is_online or not is_actively_viewing):
        logger.debug(f"User {receiver_id} offline or viewing different chat, sending notification")
        try:
            # Format preview based on message type
            # Note: TEXT messages are E2E encrypted — never include raw ciphertext in push
            if message.message_type == MessageType.IMAGE: message_preview = "📷 Photo"
            elif message.message_type == MessageType.VIDEO: message_preview = "🎥 Video"
            elif message.message_type == MessageType.AUDIO: message_preview = "🎵 Audio"
            elif message.message_type == MessageType.FILE: message_preview = "📎 File"
            elif message.message_type == MessageType.POST: message_preview = "📌 Shared a post"
            else: message_preview = "New message"

            await notification_service.create_notification(
                notification_type=NotificationType.MESSAGE,
                recipient_id=receiver_id,
                sender_id=current_user.id,
                title=current_user.username,
                message=message_preview,
                image_url=current_user.profile_pic,
                redirect_to=f"/chat/{message.chat_id}",
                redirect_type="chat",
                redirect_id=message.chat_id,
                session=session,
                meta={
                    "chat_id": message.chat_id,
                    "message_id": message.id,
                    "message_type": message.message_type.value if message.message_type else "",
                },
                group_key=f"message_{current_user.id}_{receiver_id}",
                aggregation_message_template="{sender_name} sent {count} messages"
            )
        except Exception as e:
            logger.error(f"Error sending push notification: {e}")

    return build_message_response(message, device_id=device_id, session=session)


@router.delete("/messages/{message_id}")
def delete_message(
    message_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    request: Request,
):
    statement = select(Message).where(Message.id == message_id)
    message = session.exec(statement).first()
    if message and (
        message.sender_id == current_user.id or message.receiver_id == current_user.id
    ):
        if message.deleted_for_user_id:
            session.delete(message)
        else:
            message.deleted_for_user_id = current_user.id
            session.add(message)
        session.commit()
        # Emit delete event via room-based routing
        try:
            target = message.receiver_id if message.sender_id == current_user.id else message.sender_id
            target_room = f"user:{target}"
            asyncio.create_task(sio.emit("message:delete", {"message_id": message_id}, room=target_room))
        except Exception:
            pass
        return {"detail": "message is deleted"}
    return {"detail": "message does not exists or you dont have permission"}


@router.get("/chats/", response_model=list[ChatResponse])
def view_chats(
    current_user: Annotated[User, Depends(get_current_active_user)], session: SessionDep,
    limit: int = 50,
    before_id: str | None = None,
):
    statement = select(Chat).where(
        (Chat.participant_one_id == current_user.id)
        | (Chat.participant_two_id == current_user.id),
        or_(
            Chat.deleted_for_user_id.is_(None),
            Chat.deleted_for_user_id != current_user.id,
        ),
    )
    if before_id is not None:
        statement = statement.where(Chat.id < before_id)
    statement = statement.order_by(Chat.id.desc()).limit(limit)
    chats = session.exec(statement).all()
    # Compute block sets for filtering
    you_blocked = set(
        b.blocked_id for b in session.exec(select(Block).where(Block.blocker_id == current_user.id)).all()
    )
    blocked_you = set(
        b.blocker_id for b in session.exec(select(Block).where(Block.blocked_id == current_user.id)).all()
    )
    chat_responses = []
    for chat in chats:
        other_user = (
            chat.participant_two
            if chat.participant_one_id == current_user.id
            else chat.participant_one
        )

        # Skip chats with blocked relationships
        if other_user.id in you_blocked or other_user.id in blocked_you:
            continue

        # Count unread messages (messages sent to current user that are not READ)
        unread_stmt = select(func.count(Message.id)).where(
            Message.chat_id == chat.id,
            Message.receiver_id == current_user.id,
            Message.status != MessageStatus.READ,
            or_(
                Message.deleted_for_user_id.is_(None),
                Message.deleted_for_user_id != current_user.id,
            ),
        )
        unread_count = session.exec(unread_stmt).one()

        # Check if this chat is muted for the current user
        mute_record = session.exec(
            select(ChatMute).where(
                ChatMute.chat_id == chat.id,
                ChatMute.user_id == current_user.id,
            )
        ).first()
        is_muted = False
        if mute_record:
            # Check if mute has expired
            if mute_record.muted_until and datetime.now() > mute_record.muted_until:
                session.delete(mute_record)
                session.commit()
            else:
                is_muted = True

        chat_responses.append(
            ChatResponse(
                id=chat.id,
                user=UserBasic(
                    id=other_user.id,
                    username=other_user.username,
                    name=other_user.name,
                    profile_pic=other_user.profile_pic,
                    bio=other_user.bio,
                ),
                last_message=chat.last_message,
                last_message_type=chat.last_message_type,
                last_message_datetime=chat.last_message_datetime,
                unread_count=unread_count,
                is_muted=is_muted,
                disappearing_timer=chat.disappearing_timer,
            )
        )

    return chat_responses


@router.delete("/chats/{chat_id}")
def delete_chats(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    statement = select(Chat).where(Chat.id == chat_id)
    chat = session.exec(statement).first()
    if (
        chat
        and (
            chat.participant_one_id == current_user.id
            or chat.participant_two_id == current_user.id
        )
        and (
            chat.deleted_for_user_id == None
            or chat.deleted_for_user_id != current_user.id
        )
    ):
        # Soft-delete all messages in this chat for the current user
        messages = session.exec(
            select(Message).where(
                Message.chat_id == chat.id,
                or_(
                    Message.deleted_for_user_id.is_(None),
                    Message.deleted_for_user_id != current_user.id,
                ),
            )
        ).all()
        for message in messages:
            if message.deleted_for_user_id:
                # Already deleted for the other user — hard delete
                session.delete(message)
            else:
                message.deleted_for_user_id = current_user.id
                session.add(message)

        if chat.deleted_for_user_id:
            session.delete(chat)
        else:
            chat.deleted_for_user_id = current_user.id
            session.add(chat)
        session.commit()
        return {"detail": "Chat is deleted"}
    return {"detail": "The chat doesn't exist"}


@router.post("/chats/{chat_id}/mute")
def mute_chat(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    duration: int | None = None,  # Optional: seconds until auto-unmute
):
    """Mute a chat for the current user. Optionally specify duration in seconds."""
    chat = session.get(Chat, chat_id)
    if not chat or (chat.participant_one_id != current_user.id and chat.participant_two_id != current_user.id):
        raise HTTPException(status_code=404, detail="Chat not found")

    existing = session.exec(
        select(ChatMute).where(ChatMute.chat_id == chat_id, ChatMute.user_id == current_user.id)
    ).first()

    from datetime import timedelta

    if existing:
        # Update existing mute
        existing.muted_at = datetime.now()
        existing.muted_until = datetime.now() + timedelta(seconds=duration) if duration else None
        session.add(existing)
    else:
        mute = ChatMute(
            chat_id=chat_id,
            user_id=current_user.id,
            muted_at=datetime.now(),
            muted_until=datetime.now() + timedelta(seconds=duration) if duration else None,
        )
        session.add(mute)

    session.commit()
    return {"detail": "Chat muted", "is_muted": True}


@router.delete("/chats/{chat_id}/mute")
def unmute_chat(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Unmute a chat for the current user."""
    existing = session.exec(
        select(ChatMute).where(ChatMute.chat_id == chat_id, ChatMute.user_id == current_user.id)
    ).first()

    if existing:
        session.delete(existing)
        session.commit()

    return {"detail": "Chat unmuted", "is_muted": False}


@router.get("/chats/{chat_id}/mute")
def get_mute_status(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Check if a chat is muted for the current user."""
    existing = session.exec(
        select(ChatMute).where(ChatMute.chat_id == chat_id, ChatMute.user_id == current_user.id)
    ).first()

    if existing:
        if existing.muted_until and datetime.now() > existing.muted_until:
            session.delete(existing)
            session.commit()
            return {"is_muted": False, "muted_until": None}
        return {
            "is_muted": True,
            "muted_until": existing.muted_until.isoformat() if existing.muted_until else None,
        }

    return {"is_muted": False, "muted_until": None}


def _is_starred(message_id: str, user_id: str | None, session) -> bool:
    if not user_id or not session:
        return False
    return session.exec(
        select(StarredMessage).where(
            StarredMessage.message_id == message_id,
            StarredMessage.user_id == user_id,
        )
    ).first() is not None


def build_message_response(
    message: Message,
    device_id: str | None = None,
    session=None,
    include_all_keys: bool = False,
    current_user_id: str | None = None,
) -> MessageResponse:
    """Build a MessageResponse, optionally reconstructing per-device encrypted blobs.

    Args:
        message: The Message ORM object.
        device_id: Requesting device's ID (from x-device-id header). When set,
                   only this device's encrypted AES key is included in the blob.
        session: DB session (required if device_id is set).
        include_all_keys: If True, include ALL device keys (used for socket emission).
    """
    shared_post_data = None
    if message.shared_post_id and message.shared_post:
        post = message.shared_post
        shared_post_data = {
            "id": post.id,
            "caption": post.caption,
            "posted_by": post.posted_by,
            "created_at": post.created_at,
            "updated_at": post.updated_at,
            "user": {
                "id": post.user.id,
                "username": post.user.username,
                "name": post.user.name,
                "profile_pic": post.user.profile_pic,
                "bio": getattr(post.user, "bio", "") or "",
            } if post.user else None,
            "media_files": [
                {"id": mf.id, "file_path": mf.file_path, "file_type": mf.file_type}
                for mf in (post.media_files or [])
            ],
            "likes_count": 0,
            "comments_count": 0,
            "is_liked": False,
            "is_bookmarked": False,
            "bookmarked_folders": [],
        }

    # Reconstruct encrypted blobs with appropriate keys
    msg_text = message.message
    caption_text = message.caption
    media_enc = message.media_encryption

    if session and (device_id or include_all_keys):
        if _is_encrypted_blob(msg_text):
            if include_all_keys:
                msg_text = _reconstruct_blob_all_keys(msg_text, message.id, "body", session)
            else:
                msg_text = _reconstruct_blob_for_device(msg_text, message.id, device_id, "body", session)
        if _is_encrypted_blob(caption_text):
            if include_all_keys:
                caption_text = _reconstruct_blob_all_keys(caption_text, message.id, "caption", session)
            else:
                caption_text = _reconstruct_blob_for_device(caption_text, message.id, device_id, "caption", session)
        if _is_encrypted_blob(media_enc):
            if include_all_keys:
                media_enc = _reconstruct_blob_all_keys(media_enc, message.id, "media", session)
            else:
                media_enc = _reconstruct_blob_for_device(media_enc, message.id, device_id, "media", session)

    return MessageResponse(
        id=message.id,
        message=msg_text,
        message_type=message.message_type,
        caption=caption_text,
        link_url=message.link_url,
        media_url=message.media_url,
        media_type=message.media_type,
        media_thumbnail_url=message.media_thumbnail_url,
        file_size=message.file_size,
        duration=message.duration,
        is_video_call=message.is_video_call,
        call_status=message.call_status,
        media_encryption=media_enc,
        latitude=message.latitude,
        longitude=message.longitude,
        location_name=message.location_name,
        contact_name=message.contact_name,
        contact_phone=message.contact_phone,
        contact_user_id=message.contact_user_id,
        is_starred=_is_starred(message.id, current_user_id, session) if current_user_id and session else False,
        expires_at=message.expires_at,
        shared_post_id=message.shared_post_id,
        shared_post=shared_post_data,
        reply_to_id=message.reply_to_id,
        forwarded_from_id=message.forwarded_from_id,
        pinned=message.pinned,
        is_forwarded=message.is_forwarded,
        created_at=message.created_at,
        updated_at=message.updated_at,
        is_system_message=message.is_system_message,
        chat_id=message.chat_id,
        chat_type=message.chat.chat_type if message.chat else "dm",
        status=message.status,
        sender=UserBasic(
            id=message.sender.id,
            username=message.sender.username,
            name=message.sender.name,
            profile_pic=message.sender.profile_pic,
            bio=message.sender.bio,
        ),
        receiver=UserBasic(
            id=message.receiver.id,
            username=message.receiver.username,
            name=message.receiver.name,
            profile_pic=message.receiver.profile_pic,
            bio=message.receiver.bio,
        ) if message.receiver else None,
        reactions=[
            ReactionResponse(
                id=r.id,
                emoji=r.emoji,
                user_id=r.user_id,
                message_id=r.message_id,
                user=UserBasic(
                    id=r.user.id,
                    username=r.user.username,
                    name=r.user.name,
                    profile_pic=r.user.profile_pic,
                    bio=r.user.bio,
                ) if r.user else None,
            )
            for r in (message.reactions or [])
        ],
    )


@router.put("/messages/{message_id}", response_model=MessageResponse)
async def edit_message(
    message_id: str,
    payload: MessageEdit,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    request: Request,
):
    device_id = request.headers.get("x-device-id")
    idem_key = request.headers.get("Idempotency-Key")
    if idem_key:
        existing_id = await _idem_get(idem_key)
        if existing_id:
            existing = session.get(Message, existing_id)
            if existing:
                return build_message_response(existing, device_id=device_id, session=session)
    updated = update_message(
        sender_id=current_user.id,
        receiver_id=payload.receiver_id,
        message_id=message_id,
        message_text=payload.message,
        session=session,
        message_type=payload.message_type,
        caption=payload.caption,
        link_url=payload.link_url,
        media_url=payload.media_url,
        media_type=payload.media_type,
        media_thumbnail_url=payload.media_thumbnail_url,
        file_size=payload.file_size,
        duration=payload.duration,
        shared_post_id=payload.shared_post_id,
        reply_to_id=payload.reply_to_id,
        forwarded_from_id=payload.forwarded_from_id,
        pinned=payload.pinned,
        is_forwarded=payload.is_forwarded,
        media_encryption=payload.media_encryption,
    )
    if isinstance(updated, dict) and "detail" in updated:
        raise HTTPException(status_code=400, detail=updated["detail"])
    if idem_key:
        await _idem_set(idem_key, updated.id)
    # Emit WS event via room-based routing
    try:
        target = updated.receiver_id if updated.sender_id == current_user.id else updated.sender_id
        target_room = f"user:{target}"
        edit_payload = build_message_response(updated, include_all_keys=True, session=session).model_dump(mode="json")
        asyncio.create_task(sio.emit("message:edit", edit_payload, room=target_room))
    except Exception:
        pass
    return build_message_response(updated, device_id=device_id, session=session)


@router.post("/messages/{message_id}/read", response_model=MessageResponse)
async def mark_message_read(
    message_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    statement = select(Message).where(Message.id == message_id)
    message = session.exec(statement).first()
    if not message:
        return {"detail": "message not found"}
    if message.receiver_id != current_user.id:
        return {"detail": "not allowed"}
    message.status = MessageStatus.READ
    session.add(message)
    session.commit()
    session.refresh(message)
    # Emit read receipt via room-based routing
    try:
        sender_room = f"user:{message.sender_id}"
        asyncio.create_task(
            sio.emit(
                "message:read",
                {"message_id": message.id, "reader_id": current_user.id},
                room=sender_room,
            )
        )
    except Exception:
        pass
    return build_message_response(message)


@router.post("/messages/from/{sender_id}/read-all")
async def mark_all_from_sender_read(
    sender_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Mark all unread messages from `sender_id` to the current user as READ.
    Called by the Android 'Mark as Read' notification action.
    """
    statement = select(Message).where(
        Message.sender_id == sender_id,
        Message.receiver_id == current_user.id,
        Message.status != MessageStatus.READ,
    )
    messages = session.exec(statement).all()

    if not messages:
        return {"detail": "No unread messages found", "count": 0}

    for msg in messages:
        msg.status = MessageStatus.READ
        session.add(msg)
    session.commit()

    # Emit read receipts back to sender via WebSocket
    try:
        sender_room = f"user:{sender_id}"
        for msg in messages:
            session.refresh(msg)
            await sio.emit(
                "message:read",
                {"message_id": msg.id, "reader_id": current_user.id},
                room=sender_room,
            )
    except Exception as e:
        logger.warning(f"Failed to emit read receipts to user {sender_id}: {e}")

    return {"detail": "Messages marked as read", "count": len(messages)}


@router.post("/chats/{chat_id}/read")
def mark_chat_read(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    # Select all unread messages in this chat where the current user is the receiver
    statement = select(Message).where(
        Message.chat_id == chat_id,
        Message.receiver_id == current_user.id,
        Message.status != MessageStatus.READ,
        or_(
            Message.deleted_for_user_id.is_(None),
            Message.deleted_for_user_id != current_user.id,
        ),
    )
    messages = session.exec(statement).all()
    
    if not messages:
        return {"detail": "No unread messages found"}

    # Update status for all messages
    for message in messages:
        message.status = MessageStatus.READ
        session.add(message)
    
    session.commit()
    
    # Emit a single event for the chat read
    # We need to find the other participant (sender) to notify them
    # Since all messages are in the same chat and we represent the receiver, 
    # the sender must be the other participant. 
    # We can take the sender_id from the first message found.
    if messages:
        sender_id = messages[0].sender_id
        sender_room = f"user:{sender_id}"
        try:
             asyncio.create_task(
                sio.emit(
                    "chat:read",
                    {"chat_id": chat_id, "reader_id": current_user.id},
                    room=sender_room,
                )
            )
        except Exception:
            pass

    return {"detail": f"Marked {len(messages)} messages as read"}


@router.post("/messages/{message_id}/reactions", response_model=MessageResponse)
def add_reaction(
    message_id: str,
    emoji: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    message = session.get(Message, message_id)
    if not message:
        return {"detail": "message not found"}
    # Toggle behavior: if same reaction exists for this user, remove; else add
    existing = session.exec(
        select(ReactionModel).where(
            (ReactionModel.message_id == message_id)
            & (ReactionModel.user_id == current_user.id)
            & (ReactionModel.emoji == emoji)
        )
    ).first()
    if existing:
        session.delete(existing)
    else:
        reaction = ReactionModel(
            emoji=emoji,
            user_id=current_user.id,
            message_id=message_id,
        )
        session.add(reaction)
    session.commit()
    session.refresh(message)
    # Emit reaction event via room-based routing
    try:
        target = message.receiver_id if message.sender_id == current_user.id else message.sender_id
        target_room = f"user:{target}"
        event = "reaction:removed" if existing else "reaction:added"
        asyncio.create_task(
            sio.emit(
                event,
                {"message_id": message_id, "emoji": emoji, "user_id": current_user.id},
                room=target_room,
            )
        )
    except Exception:
        pass
    return build_message_response(message)


@router.delete("/messages/{message_id}/reactions/{emoji}", response_model=MessageResponse)
def remove_reaction(
    message_id: str,
    emoji: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    message = session.get(Message, message_id)
    if not message:
        return {"detail": "message not found"}
    existing = session.exec(
        select(ReactionModel).where(
            (ReactionModel.message_id == message_id)
            & (ReactionModel.user_id == current_user.id)
            & (ReactionModel.emoji == emoji)
        )
    ).first()
    if existing:
        session.delete(existing)
        session.commit()
    session.refresh(message)
    # Emit reaction removed via room-based routing
    try:
        target = message.receiver_id if message.sender_id == current_user.id else message.sender_id
        target_room = f"user:{target}"
        asyncio.create_task(
            sio.emit(
                "reaction:removed",
                {"message_id": message_id, "emoji": emoji, "user_id": current_user.id},
                room=target_room,
            )
        )
    except Exception:
        pass
    return build_message_response(message)


@router.get("/messages/search/", response_model=list[MessageResponse])
def search_messages(
    q: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    request: Request,
    chat_with: str | None = None,
    message_type: str | None = Query(None, description="Comma-separated message types to filter (e.g. text,image)"),
    limit: int = 50,
    offset: int = 0,
) -> list[MessageResponse]:
    """
    Search messages by content. Only searches messages the current user is part of.
    Optionally filter by a specific conversation using chat_with (other user's ID).
    """
    device_id = request.headers.get("x-device-id")
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty")

    stmt = select(Message).where(
        or_(
            Message.sender_id == current_user.id,
            Message.receiver_id == current_user.id,
        ),
        or_(
            Message.deleted_for_user_id.is_(None),
            Message.deleted_for_user_id != current_user.id,
        ),
        Message.message.icontains(q.strip()),
    )

    if chat_with:
        stmt = stmt.where(
            or_(
                (Message.sender_id == chat_with) & (Message.receiver_id == current_user.id),
                (Message.sender_id == current_user.id) & (Message.receiver_id == chat_with),
            )
        )

    if message_type:
        types = [t.strip().lower() for t in message_type.split(",") if t.strip()]
        if types:
            stmt = stmt.where(Message.message_type.in_(types))

    stmt = stmt.order_by(Message.created_at.desc()).offset(offset).limit(limit)
    messages = session.exec(stmt).all()
    return [build_message_response(msg, device_id=device_id, session=session) for msg in messages]


# --- Starred Messages ---

@router.post("/messages/{message_id}/star")
def star_message(
    message_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != current_user.id and message.receiver_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your message")
    existing = session.exec(
        select(StarredMessage).where(
            StarredMessage.message_id == message_id,
            StarredMessage.user_id == current_user.id,
        )
    ).first()
    if existing:
        return {"detail": "Already starred"}
    star = StarredMessage(message_id=message_id, user_id=current_user.id)
    session.add(star)
    session.commit()
    return {"detail": "Starred"}


@router.delete("/messages/{message_id}/star")
def unstar_message(
    message_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    existing = session.exec(
        select(StarredMessage).where(
            StarredMessage.message_id == message_id,
            StarredMessage.user_id == current_user.id,
        )
    ).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Not starred")
    session.delete(existing)
    session.commit()
    return {"detail": "Unstarred"}


@router.get("/messages/starred/", response_model=list[MessageResponse])
def get_starred_messages(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    request: Request,
    chat_with: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    device_id = request.headers.get("x-device-id")
    stmt = (
        select(Message)
        .join(StarredMessage, StarredMessage.message_id == Message.id)
        .where(StarredMessage.user_id == current_user.id)
    )
    if chat_with:
        stmt = stmt.where(
            or_(
                (Message.sender_id == chat_with) & (Message.receiver_id == current_user.id),
                (Message.sender_id == current_user.id) & (Message.receiver_id == chat_with),
            )
        )
    stmt = stmt.order_by(StarredMessage.created_at.desc()).offset(offset).limit(limit)
    messages = session.exec(stmt).all()
    return [
        build_message_response(msg, device_id=device_id, session=session, current_user_id=current_user.id)
        for msg in messages
    ]


# --- Disappearing Messages ---

class DisappearingTimerUpdate(PydanticBaseModel):
    timer: int | None = None  # seconds (None=disable, 86400=24h, 604800=7d, 2592000=30d, 7776000=90d)


@router.put("/chats/{chat_id}/disappearing")
def set_disappearing_timer(
    chat_id: str,
    payload: DisappearingTimerUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    chat = session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat.participant_one_id != current_user.id and chat.participant_two_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your chat")

    chat.disappearing_timer = payload.timer
    session.add(chat)
    session.commit()

    # Emit socket event to both participants
    other_id = chat.participant_two_id if chat.participant_one_id == current_user.id else chat.participant_one_id
    timer_label = "off" if payload.timer is None else f"{payload.timer // 86400}d" if payload.timer >= 86400 else f"{payload.timer // 3600}h"

    asyncio.get_event_loop().create_task(
        sio.emit("chat:disappearing_updated", {
            "chat_id": chat_id,
            "timer": payload.timer,
            "set_by": current_user.id,
        }, room=f"user:{other_id}")
    )

    return {"detail": f"Disappearing messages set to {timer_label}", "timer": payload.timer}


@router.get("/chats/{chat_id}/disappearing")
def get_disappearing_timer(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    chat = session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat.participant_one_id != current_user.id and chat.participant_two_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your chat")
    return {"timer": chat.disappearing_timer}


@router.delete("/messages/expired")
def cleanup_expired_messages(
    session: SessionDep,
):
    """Delete messages that have expired (disappearing messages). Can be called by a cron job."""
    now = datetime.now(timezone.utc)
    expired = session.exec(
        select(Message).where(
            Message.expires_at.is_not(None),
            Message.expires_at <= now,
        )
    ).all()
    count = len(expired)
    for msg in expired:
        session.delete(msg)
    if count > 0:
        session.commit()
    return {"deleted": count}
