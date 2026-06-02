from datetime import datetime, timezone, timedelta
from pydantic import ValidationError
import asyncio
import logging
from app.sockets.socketio_server import sio
from app.users.models import User, UserSession
from app.calls.models import Call, CallStatus
from app.messages.routers import save_message, build_message_response, delete_message_helper, update_message
from app.sockets.active import set_active, get_active_sid, is_active, remove_active
from ..users.routers import get_user_by_id, get_user
from app.security import (
    decode_access_token,
)
from ..dependencies import SessionDep
from ..database import engine, Session
from ..messages.models import MessageSend, MessageEdit, Message
from app.messages.models import Reaction, MessageType
from sqlmodel import Session, select
from app.notifications.services.notification_service import notification_service
from sqlalchemy.orm import scoped_session, sessionmaker
from contextvars import ContextVar
import functools
import json
from app.redis_client import redis_client

# In-memory fallback for pending call offers when Redis is unavailable (local dev)
_pending_offers_memory: dict = {}

logger = logging.getLogger(__name__)


def _session_scope_key():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


SessionLocal = scoped_session(
    sessionmaker(bind=engine, class_=Session, expire_on_commit=False),
    scopefunc=_session_scope_key,
)
_session_ctx: ContextVar[Session | None] = ContextVar("socketio_session", default=None)


class SessionProxy:
    def _get(self) -> Session:
        session = _session_ctx.get()
        if session is None:
            raise RuntimeError("Database session is not initialized for this context")
        return session

    def __getattr__(self, item):
        return getattr(self._get(), item)

    def __enter__(self):
        return self._get().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._get().__exit__(exc_type, exc_val, exc_tb)

session = SessionProxy()


def with_scoped_session(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        db = SessionLocal()
        token = _session_ctx.set(db)
        try:
            return await func(*args, **kwargs)
        finally:
            _session_ctx.reset(token)
            SessionLocal.remove()

    return wrapper


def user_room(user_id: str) -> str:
    """Build a per-user room name so events reach all of the user's sids across workers."""
    return f"user:{user_id}"


async def broadcast_online_status(user_id: str, is_online: bool):
    """Broadcast online/offline status to all chat partners."""
    try:
        from app.messages.models import Chat
        chats = session.exec(
            select(Chat).where(
                (Chat.participant_one_id == user_id) | (Chat.participant_two_id == user_id)
            )
        ).all()
        for chat in chats:
            partner_id = chat.participant_two_id if chat.participant_one_id == user_id else chat.participant_one_id
            await sio.emit("user:online_status", {
                "user_id": user_id,
                "is_online": is_online,
            }, room=user_room(partner_id))
    except Exception as e:
        logger.error(f"Error broadcasting online status for user {user_id}: {e}")


async def is_user_online(user_id: str) -> bool:
    """Check if a user has any connected sids across workers."""
    try:
        # Redis cache is shared across all workers
        if await is_active(user_id):
            return True

        # Fallback: ask the Socket.IO manager (Redis-backed rooms)
        if hasattr(sio.manager, 'get_participants'):
            room = user_room(user_id)
            participants = await sio.manager.get_participants("/", room)
            return bool(participants)
        return False
    except Exception:
        return await is_active(user_id)

async def emit_call_update(
    call: Call, initiator_id: str, status: CallStatus, extra: dict | None = None
):
    payload = {
        "type": "call_status",
        "status": status.value,
        "call_id": call.id,
        "is_video_call": call.is_video_call,
        "duration_seconds": call.duration_seconds,
        "call_from": call.call_from,
        "call_to": call.call_to,
        "updated_at": call.updated_at.isoformat(),
    }
    if extra:
        payload.update(extra)
    participants = {call.call_from, call.call_to}
    for participant_id in participants:
        await sio.emit(
            "call_update",
            {
                "from": initiator_id,
                "to": participant_id,
                "payload": payload,
            },
            room=user_room(participant_id),
        )


async def safe_parse(model, data: dict):
    try:
        return model(**data)
    except ValidationError as e:
        return {"error": e.errors()}


async def _maybe_mark_missed(
    call_id: str, initiator_id: str, timeout_seconds: int = 45
):
    """After timeout, mark unanswered ONGOING calls as MISSED and notify participants."""
    try:
        await asyncio.sleep(timeout_seconds)
        with Session(engine) as local_session:
            call_obj = local_session.get(Call, call_id)
            if not call_obj:
                return
            # If no one answered yet, mark as missed
            if call_obj.call_status == CallStatus.ONGOING.value:
                call_obj.call_status = CallStatus.MISSED.value
                local_session.add(call_obj)
                local_session.commit()
                await emit_call_update(call_obj, initiator_id, CallStatus.MISSED)
    except Exception as e:
        logger.error(f"Missed-call timeout error for call {call_id}: {e}")


async def get_user_from_socket(sid) -> User | None:
    # Kept for backward compatibility; prefers session lookup over local cache
    try:
        socket_session = await sio.get_session(sid)
    except Exception:
        return None
    user_id = None
    if socket_session and isinstance(socket_session, dict):
        user_id = socket_session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id, session)


@sio.event
@with_scoped_session
async def connect(sid, environ, auth=None):
    logger.info(f"---- DEBUG: Connect request sid={sid} ----")
    query_string = environ.get("QUERY_STRING", "")
    query_params = query_string or ""
    token = None

    # Extract token from query string
    for param in query_params.split("&"):
        if param.startswith("token="):
            token = param.split("=", 1)[1]
            break

    # Socket.IO v4 may send token in auth payload on connect.
    if not token and isinstance(auth, dict):
        auth_token = auth.get("token")
        if isinstance(auth_token, str) and auth_token.strip():
            token = auth_token.strip()

    token_preview = f"{token[:10]}..." if token else "None"
    logger.info(f"DEBUG: extracted token: {token_preview}")

    if not token:
        logger.info(f"Anonymous connection for sid={sid} (possibly for QR login)")
        # Do not reject, but also do not authenticate as a user
        # Proceed with empty session/user
        pass
    else:

        try:
            user: User | None = None

            # Try server-side session first (new approach)
            now = datetime.now(timezone.utc)
            user_session = session.exec(
                select(UserSession).where(
                    UserSession.session_token == token,
                    UserSession.is_active == True,
                    UserSession.expires_at > now,
                )
            ).first()

            if user_session:
                logger.info(f"DEBUG: Found valid UserSession for user_id={user_session.user_id}")
                # Valid session token - get user from session
                from ..users.routers import get_user_by_id
                user = get_user_by_id(user_session.user_id, session)
                # Update last activity
                user_session.last_activity = now
                session.add(user_session)
                session.commit()
            else:
                logger.info("DEBUG: No UserSession found, trying JWT decode...")
                # Fallback to JWT for backward compatibility
                try:
                    payload = decode_access_token(token)
                    sub = payload.get("sub")
                    if sub is None:
                        logger.warning(f"Invalid token (no sub) for sid={sid}")
                        return False

                    # sub is a UUID string (user_id) or legacy username
                    from ..users.routers import get_user_by_id, get_user
                    user = get_user_by_id(sub, session)
                    if user is None:
                        # Fallback: try as username for backward compatibility
                        user = get_user(sub, session)
                    
                    logger.info(f"DEBUG: JSON decode success, user found: {user.id if user else 'None'}")
                except Exception as jwt_error:
                    logger.error(f"JWT decode error for sid={sid}: {jwt_error}")
                    return False

            if user is None:
                logger.warning(f"User not found for sid={sid} after auth attempt")
                return False

            await set_active(user.id, sid)  # Shared across all workers via Redis
            await sio.save_session(sid, {"user_id": user.id})
            await sio.enter_room(sid, user_room(user.id))

            # Join all group chat rooms
            try:
                from ..messages.models import ChatMember
                group_memberships = session.exec(
                    select(ChatMember).where(
                        ChatMember.user_id == user.id,
                        ChatMember.is_active == True,
                    )
                ).all()
                for membership in group_memberships:
                    await sio.enter_room(sid, f"group:{membership.chat_id}")
                if group_memberships:
                    logger.debug(f"User {user.id} joined {len(group_memberships)} group rooms")
            except Exception as e:
                logger.error(f"Failed to join group rooms for {user.id}: {e}")

            logger.info(f"User {user.id} ({user.username}) connected with sid={sid}")

            # Broadcast online status to chat partners
            await broadcast_online_status(user.id, True)

            # Check for pending call offers (Redis first, memory fallback).
            # Do NOT delete the offer on delivery — keep it in Redis until the call is
            # resolved (answered, rejected, or cancelled). If the socket briefly disconnects
            # after delivery and reconnects with a new SID, the backend will re-deliver the
            # offer so the callee never gets stuck waiting forever.
            pending_offer_json = None
            try:
                pending_offer_json = await redis_client.get(f"pending_offer:{user.id}")
            except Exception:
                pass
            if not pending_offer_json:
                pending_offer_json = _pending_offers_memory.get(user.id)
            if pending_offer_json:
                try:
                    offer_data = json.loads(pending_offer_json)
                    await sio.emit("call_offer", offer_data, to=sid)
                    logger.info(f"Delivered pending call offer to {user.id} (sid={sid})")
                except Exception as e:
                    logger.error(f"Error delivering pending call offer to {user.id}: {e}")

        except Exception as e:
            logger.error(f"Auth error for sid={sid}: {e}", exc_info=True)
            return False


@sio.event
@with_scoped_session
async def disconnect(sid):
    try:
        socket_session = await sio.get_session(sid)
        user_id = socket_session.get("user_id") if socket_session else None
        if user_id:
            await sio.leave_room(sid, user_room(user_id))
            # Clean Redis cache (only if this sid still owns the slot)
            await remove_active(user_id, sid)
            # Clear active chat state
            try:
                from app.redis_client import redis_client
                await redis_client.delete(f"current_chat:{user_id}")
            except Exception as re:
                logger.error(f"Failed to clear current_chat for user {user_id}: {re}")
            # Broadcast offline status to chat partners
            await broadcast_online_status(user_id, False)
            logger.info(f"User {user_id} disconnected")
    except Exception as e:
        logger.error(f"Disconnect handling failed: {e}")


@sio.on("chat:join")
async def chat_join(sid, data):
    try:
        socket_session = await sio.get_session(sid)
        user_id = socket_session.get("user_id") if socket_session else None
        if not user_id:
            return
            
        viewing_user_id = data.get("viewing_user_id")
        if viewing_user_id:
            from app.redis_client import redis_client
            # user_id is actively viewing their chat with viewing_user_id
            await redis_client.set(f"current_chat:{user_id}", str(viewing_user_id))
            logger.debug(f"User {user_id} joined chat with {viewing_user_id}")
    except Exception as e:
        logger.error(f"Error in chat:join: {e}")

@sio.on("chat:leave")
async def chat_leave(sid, data=None):
    try:
        socket_session = await sio.get_session(sid)
        user_id = socket_session.get("user_id") if socket_session else None
        if user_id:
            from app.redis_client import redis_client
            await redis_client.delete(f"current_chat:{user_id}")
            logger.debug(f"User {user_id} left chat screen")
    except Exception as e:
        logger.error(f"Error in chat:leave: {e}")


@sio.on("user:check_online")
async def user_check_online(sid, data):
    """Client asks if a specific user is online. Responds with user:online_status."""
    try:
        if not isinstance(data, dict):
            return
        target_user_id = data.get("user_id")
        if target_user_id:
            online = await is_user_online(target_user_id)
            await sio.emit("user:online_status", {
                "user_id": target_user_id,
                "is_online": online,
            }, to=sid)
    except Exception as e:
        logger.error(f"Error in user:check_online: {e}")


@sio.on("typing:start")
async def typing_start(sid, data):
    try:
        socket_session = await sio.get_session(sid)
        user_id = socket_session.get("user_id") if socket_session else None
        if not user_id or not isinstance(data, dict):
            return
        to_user_id = data.get("to_user_id")
        if to_user_id:
            await sio.emit("typing:start", {"user_id": user_id}, room=user_room(to_user_id))
    except Exception as e:
        logger.error(f"Error in typing:start: {e}")


@sio.on("typing:stop")
async def typing_stop(sid, data):
    try:
        socket_session = await sio.get_session(sid)
        user_id = socket_session.get("user_id") if socket_session else None
        if not user_id or not isinstance(data, dict):
            return
        to_user_id = data.get("to_user_id")
        if to_user_id:
            await sio.emit("typing:stop", {"user_id": user_id}, room=user_room(to_user_id))
    except Exception as e:
        logger.error(f"Error in typing:stop: {e}")


@sio.on("message:send")
@with_scoped_session
async def message_send(sid, data):
    try:
        socket_session = await sio.get_session(sid)
        sender_id = socket_session.get("user_id") if socket_session else None
        if not sender_id:
            logger.warning(f"Message send attempt without user_id from sid={sid}")
            return

        parsed = await safe_parse(MessageSend, data)
        if isinstance(parsed, dict) and "error" in parsed:
            await sio.emit(
                "error",
                {"type": "validation_error", "details": parsed["error"]},
                to=sid,
            )
            return
        message_payload: MessageSend = parsed
        receiver_id = message_payload.receiver_id

        # Get sender and receiver user objects for notifications
        sender = get_user_by_id(sender_id, session)
        receiver = get_user_by_id(receiver_id, session)

        message_response = save_message(
            sender_id,
            receiver_id,
            message_payload.message,
            session,
            caption=message_payload.caption,
            link_url=message_payload.link_url,
            media_url=message_payload.media_url,
            media_type=message_payload.media_type,
            media_thumbnail_url=message_payload.media_thumbnail_url,
            file_size=message_payload.file_size,
            duration=message_payload.duration,
            shared_post_id=message_payload.shared_post_id,
            reply_to_id=message_payload.reply_to_id,
            forwarded_from_id=message_payload.forwarded_from_id,
            pinned=message_payload.pinned,
            is_forwarded=message_payload.is_forwarded,
        )

        payload = build_message_response(message_response, include_all_keys=True, session=session).model_dump(mode="json")

        receiver_online = await is_user_online(receiver_id)
        is_actively_viewing = False
        
        if receiver_online:
            try:
                from app.redis_client import redis_client
                active_chat = await redis_client.get(f"current_chat:{receiver_id}")
                is_actively_viewing = active_chat == str(sender_id)
                
                await sio.emit("message:new", payload, room=user_room(receiver_id))
                logger.debug(f"Message delivered to online user {receiver_id}")
            except Exception as e:
                logger.warning(f"Failed to emit message to user {receiver_id}: {e}")
                # Fallback to offline handling if emit fails (e.g., due to ghost connection)
                receiver_online = False
                
        if not receiver_online or not is_actively_viewing:
            logger.debug(f"User {receiver_id} offline or viewing different chat, sending notification")

            # Send notification (in-app or push)
            if sender and receiver:
                try:
                    notification_result = (
                        await notification_service.send_message_notification(
                            message=message_response,
                            sender=sender,
                            receiver=receiver,
                            session=session,
                        )
                    )
                    logger.debug(f"Notification result: {notification_result}")
                except Exception as e:
                    logger.error(f"Error sending push notification: {e}")

        # Sync to sender's other devices (skip the sending socket)
        await sio.emit("message:new", payload, room=user_room(sender_id), skip_sid=sid)
    except Exception as e:
        logger.error(f"Error in message_send: {e}", exc_info=True)
        await sio.emit(
            "error",
            {"type": "server_error", "details": "Failed to send message"},
            to=sid,
        )


@sio.on("message:edit")
@with_scoped_session
async def message_edit(sid, data):
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id") if isinstance(socket_session, dict) else None
    if not sender_id:
        return

    parsed = await safe_parse(MessageEdit, data)
    if isinstance(parsed, dict) and "error" in parsed:
        await sio.emit(
            "error", {"type": "validation_error", "details": parsed["error"]}, to=sid
        )
        return
    message_payload: MessageEdit = parsed
    receiver_id = message_payload.receiver_id
    message_response = update_message(
        sender_id,
        receiver_id,
        message_payload.id,
        message_payload.message,
        session,
        message_type=message_payload.message_type,
        caption=message_payload.caption,
        link_url=message_payload.link_url,
        media_url=message_payload.media_url,
        media_type=message_payload.media_type,
        media_thumbnail_url=message_payload.media_thumbnail_url,
        file_size=message_payload.file_size,
        duration=message_payload.duration,
        shared_post_id=message_payload.shared_post_id,
        reply_to_id=message_payload.reply_to_id,
        forwarded_from_id=message_payload.forwarded_from_id,
        pinned=message_payload.pinned,
        is_forwarded=message_payload.is_forwarded,
    )
    payload = build_message_response(message_response, include_all_keys=True, session=session).model_dump(mode="json")
    if await is_user_online(receiver_id):
        await sio.emit("message:edit", payload, room=user_room(receiver_id))
    # Sync to sender's other devices
    await sio.emit("message:edit", payload, room=user_room(sender_id), skip_sid=sid)


@sio.on("message:delete")
@with_scoped_session
async def message_delete(sid, data):
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id") if isinstance(socket_session, dict) else None
    if not sender_id:
        return

    message_id = data.get("message_id")
    if not message_id:
        await sio.emit(
            "error",
            {"type": "validation_error", "details": "message_id required"},
            to=sid,
        )
        return

    # Look up message to find the other party before deleting
    msg = session.exec(select(Message).where(Message.id == message_id)).first()
    other_user_id = None
    if msg and (msg.sender_id == sender_id or msg.receiver_id == sender_id):
        other_user_id = msg.receiver_id if msg.sender_id == sender_id else msg.sender_id

    response = delete_message_helper(message_id, sender_id, session)
    logger.debug(f"Message delete response: {response}")
    delete_payload = {"message_id": message_id}
    await sio.emit("message:delete", delete_payload, to=sid)
    # Notify the other party
    if other_user_id:
        await sio.emit("message:delete", delete_payload, room=user_room(other_user_id))
    # Sync to sender's other devices
    await sio.emit("message:delete", delete_payload, room=user_room(sender_id), skip_sid=sid)


@sio.on("message:delivered")
@with_scoped_session
async def message_delivered(sid, data):
    """
    Handle delivery receipts from clients.
    Update message status to DELIVERED and notify sender.
    """
    socket_session = await sio.get_session(sid)
    user_id = socket_session.get("user_id")
    if not user_id:
        return

    message_id = data.get("message_id")
    if not message_id:
        return

    # specific logic to avoid race conditions with read receipts
    # Only update if current status is SENT (don't overwrite READ)
    # logic:
    # 1. Fetch message
    # 2. Verify receiver_id == user_id
    # 3. If status == SENT, update to DELIVERED
    # 4. Emit event to sender

    statement = select(Message).where(Message.id == message_id)
    message = session.exec(statement).first()

    if not message:
        return

    if message.receiver_id != user_id:
        # User is not the receiver, ignore
        return

    from app.messages.models import MessageStatus

    if message.status == MessageStatus.SENT:
        message.status = MessageStatus.DELIVERED
        message.updated_at = datetime.now(timezone.utc)
        session.add(message)
        session.commit()
        session.refresh(message)

        # Notify sender
        try:
            sender_room = f"user:{message.sender_id}"
            await sio.emit(
                "message:delivered",
                {"message_id": message.id, "delivered_to": user_id},
                room=sender_room,
            )
            logger.debug(f"Marked message {message_id} as DELIVERED by {user_id}")
        except Exception as e:
            logger.error(f"Error emitting delivery receipt: {e}")
    elif message.status == MessageStatus.DELIVERED:
        # Already delivered, but maybe sender missed the ack?
        # Re-emit just in case? Or just ignore to save bandwidth.
        # For now, ignore.
        pass


@sio.on("call_offer")
@with_scoped_session
async def call_offer(sid, data):
    """Handle actual call offers - creates call record and sends notifications"""
    target = data.get("to")
    payload = data.get("payload") or {}
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id")

    logger.debug(f"Call offer data: {data}")

    if not sender_id:
        await sio.emit(
            "error",
            {"type": "unauthorized", "details": "Missing sender information"},
            to=sid,
        )
        return

    if not (target and payload):
        await sio.emit(
            "error",
            {"type": "invalid_data", "details": "Missing target or payload"},
            to=sid,
        )
        return

    call_type = payload.get("type")
    is_video_call = payload.get("is_video_call", False)
    sdp = payload.get("sdp")

    logger.debug(
        f"Call offer - type: {call_type}, video: {is_video_call}, has_sdp: {sdp is not None}"
    )

    if call_type != "offer":
        logger.warning(f"Invalid call offer type: {call_type}")
        await sio.emit(
            "error",
            {
                "type": "invalid_call_type",
                "details": f"Only 'offer' type allowed for call_offer event, got: {call_type}",
            },
            to=sid,
        )
        return

    if not sdp:
        logger.warning("Missing SDP in call offer")
        await sio.emit(
            "error",
            {"type": "invalid_sdp", "details": "Missing SDP in call offer"},
            to=sid,
        )
        return

    call_info = None
    call_id = None
    created_new_call = False
    try:
        # Deduplicate repeated offers for the same caller/callee while an invite is still ongoing.
        now = datetime.now(timezone.utc)
        duplicate_window_start = now - timedelta(seconds=30)
        existing_call = session.exec(
            select(Call)
            .where(
                Call.call_from == sender_id,
                Call.call_to == target,
                Call.call_status == CallStatus.ONGOING.value,
                Call.created_at >= duplicate_window_start,
            )
            .order_by(Call.created_at.desc())
        ).first()

        if existing_call:
            call_info = existing_call
            call_id = existing_call.id
            logger.warning(
                f"Deduped duplicate call_offer from {sender_id} to {target}; reusing call_id={call_id}"
            )
        else:
            call_info = Call(
                call_from=sender_id,
                call_to=target,
                is_video_call=is_video_call,
                call_status=CallStatus.ONGOING.value,
            )
            session.add(call_info)
            session.commit()
            session.refresh(call_info)
            call_id = call_info.id
            created_new_call = True
            logger.info(f"Call offer from {sender_id} to {target} (call_id: {call_id})")

        # Link to scheduled call if this call was initiated from one
        scheduled_call_id = payload.get("scheduled_call_id")
        if scheduled_call_id and created_new_call:
            try:
                from app.scheduled_calls.models import ScheduledCall, ScheduledCallStatus
                sc = session.get(ScheduledCall, scheduled_call_id)
                if sc and sc.status in (ScheduledCallStatus.TRIGGERED, ScheduledCallStatus.REMINDED, ScheduledCallStatus.PENDING):
                    sc.status = ScheduledCallStatus.COMPLETED
                    sc.call_id = call_id
                    sc.updated_at = datetime.now(timezone.utc)
                    session.add(sc)
                    session.commit()
                    logger.info(f"Linked scheduled call {scheduled_call_id} to call {call_id}")
            except Exception as sc_err:
                logger.error(f"Failed to update scheduled call {scheduled_call_id}: {sc_err}")
    except Exception as e:
        logger.error(f"Failed to create call record from {sender_id} to {target}: {e}")
        session.rollback()
        await sio.emit(
            "error",
            {"type": "call_error", "details": f"Failed to create call: {str(e)}"},
            to=sid,
        )
        return

    # Schedule a missed-call timeout only for newly-created calls.
    if created_new_call:
        try:
            asyncio.create_task(_maybe_mark_missed(call_id, sender_id, timeout_seconds=45))
        except Exception as e:
            logger.error(f"Could not schedule missed-call check for {call_id}: {e}")

    payload_with_call = dict(payload)
    payload_with_call.setdefault("call_id", call_id)
    payload_with_call.setdefault("call_status", CallStatus.ONGOING.value)

    # ALWAYS try to emit to the room first
    # This ensures delivery even if is_user_online returns a false negative
    message = {
        "from": sender_id,
        "to": target,
        "payload": payload_with_call,
    }
    await sio.emit(
        "call_offer",
        message,
        room=user_room(target),
    )
    logger.debug(f"Call offer emitted to room {user_room(target)}")

    # Send call_id back to caller so they can cancel/end the call
    await sio.emit(
        "call_offer_ack",
        {"type": "call_offer_ack", "call_id": call_id, "to": target},
        to=sid,
    )
    logger.debug(f"call_offer_ack sent to caller sid={sid} call_id={call_id}")

    # Always store the pending offer so it can be replayed when the callee connects.
    # Falls back to in-memory dict when Redis is unavailable (local dev without Redis).
    offer_json = json.dumps(message)
    stored = False
    try:
        await redis_client.setex(f"pending_offer:{target}", 300, offer_json)
        stored = True
        logger.info(f"Stored pending call offer in Redis for {target}")
    except Exception as e:
        logger.warning(f"Redis unavailable, using in-memory fallback: {e}")
    if not stored:
        _pending_offers_memory[target] = offer_json
        logger.info(f"Stored pending call offer in memory for {target}")

    # Send push notification only for newly-created calls, not duplicate offers.
    if created_new_call:
        try:
            sender = get_user_by_id(sender_id, session)
            receiver = get_user_by_id(target, session)
            if sender and receiver:
                notification_result = await notification_service.send_call_notification(
                    caller=sender,
                    receiver=receiver,
                    is_video_call=is_video_call,
                    call_id=call_id,
                    session=session,
                )
                logger.debug(f"Call notification sent: {notification_result}")
        except Exception as e:
            logger.error(f"Error sending call notification: {e}")

    await emit_call_update(call_info, sender_id, CallStatus.ONGOING)

    # Create one CALL system message per created call.
    if created_new_call:
        try:
            call_message = save_message(
                sender_id=sender_id,
                receiver_id=target,
                message_text=None,
                session=session,
                is_video_call=is_video_call,
                call_status=CallStatus.ONGOING.value,
            )
            payload = build_message_response(call_message).model_dump(mode="json")
            if await is_user_online(target):
                await sio.emit("message:new", payload, room=user_room(target))
        except Exception as e:
            logger.error(f"Could not create CALL message on offer: {e}")


@sio.on("call_offer_pull")
@with_scoped_session
async def call_offer_pull(sid, data):
    """Explicitly re-deliver pending call offer for cold-start recovery."""
    socket_session = await sio.get_session(sid)
    user_id = socket_session.get("user_id") if socket_session else None
    if not user_id:
        logger.warning(f"call_offer_pull: unauthorized request sid={sid}")
        await sio.emit(
            "error",
            {"type": "unauthorized", "details": "Missing user session for call_offer_pull"},
            to=sid,
        )
        return

    requested_call_id = data.get("call_id") if isinstance(data, dict) else None
    logger.debug(
        f"call_offer_pull: request received sid={sid} user_id={user_id} requested_call_id={requested_call_id}"
    )

    pending_offer_json = None
    try:
        pending_offer_json = await redis_client.get(f"pending_offer:{user_id}")
    except Exception:
        pass
    if not pending_offer_json:
        pending_offer_json = _pending_offers_memory.get(user_id)

    if not pending_offer_json:
        logger.debug(f"call_offer_pull: no pending offer for user={user_id}")
        return

    try:
        offer_data = json.loads(pending_offer_json)
    except Exception as e:
        logger.error(f"call_offer_pull: invalid pending offer payload for user={user_id}: {e}")
        return

    offer_call_id = (
        offer_data.get("payload", {}).get("call_id")
        if isinstance(offer_data, dict)
        else None
    )
    offer_to = offer_data.get("to") if isinstance(offer_data, dict) else None
    logger.debug(
        f"call_offer_pull: loaded pending offer user_id={user_id} offer_call_id={offer_call_id} offer_to={offer_to}"
    )

    if offer_to and str(offer_to) != str(user_id):
        logger.warning(
            f"call_offer_pull: pending offer ownership mismatch user_id={user_id} offer_to={offer_to}"
        )
        return

    if requested_call_id and offer_call_id and requested_call_id != offer_call_id:
        logger.debug(
            f"call_offer_pull: call_id mismatch user={user_id} requested={requested_call_id} pending={offer_call_id}"
        )
        return

    await sio.emit("call_offer", offer_data, to=sid)
    logger.info(
        f"call_offer_pull: re-delivered pending offer to user={user_id} sid={sid} call_id={offer_call_id}"
    )


@sio.on("call_answer")
@with_scoped_session
async def call_answer(sid, data):
    """Handle call answers - update call status and forward SDP"""
    target = data.get("to")
    payload = data.get("payload") or {}
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id")

    logger.debug(f"Call answer data: {data}")

    call_id = payload.get("call_id")
    if not (target and payload and call_id):
        await sio.emit(
            "error",
            {"type": "invalid_data", "details": "Missing target, payload, or call_id"},
            to=sid,
        )
        return

    if not call_id or not isinstance(call_id, str):
        await sio.emit(
            "error",
            {"type": "invalid_call_id", "details": "call_id must be a valid string"},
            to=sid,
        )
        return

    call_info = session.get(Call, call_id)
    if not call_info:
        await sio.emit(
            "error",
            {"type": "not_found", "details": f"Call {call_id} not found"},
            to=sid,
        )
        return

    call_info.call_status = CallStatus.ANSWERED.value
    call_info.updated_at = datetime.now(timezone.utc)
    session.add(call_info)
    session.commit()
    session.refresh(call_info)

    # Call answered — clean up the pending offer that was kept for reconnect retries.
    # sender_id is the callee (answerer), so the offer was buffered for sender_id.
    try:
        await redis_client.delete(f"pending_offer:{sender_id}")
        _pending_offers_memory.pop(sender_id, None)
    except Exception:
        pass

    payload_with_call = dict(payload)
    payload_with_call["call_id"] = call_id

    # ALWAYS emit to room first
    message = {
        "from": sender_id,
        "to": target,
        "payload": payload_with_call,
    }
    await sio.emit(
        "call_answer",
        message,
        room=user_room(target),
    )

    if await is_user_online(target):
        logger.debug(f"Call answer delivered to online user {target}")
    else:
        logger.warning(f"User {target} is offline, emitted call answer to room anyway")

    await emit_call_update(call_info, sender_id, CallStatus.ANSWERED)
    # Update latest CALL message to ANSWERED for this pair if needed (optional)


@sio.on("call_renegotiate")
@with_scoped_session
async def call_renegotiate(sid, data):
    """Relay mid-call renegotiation offers/answers (e.g. video upgrade, screen share) without creating new call records"""
    target = data.get("to")
    payload = data.get("payload") or {}
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id") if isinstance(socket_session, dict) else None
    if not sender_id:
        logger.warning(f"call_renegotiate from unauthenticated sid={sid}, ignoring")
        return

    if not (target and payload):
        await sio.emit(
            "error",
            {"type": "invalid_data", "details": "Missing target or payload"},
            to=sid,
        )
        return

    logger.debug(f"call_renegotiate from {sender_id} to {target}: {payload.get('type')}")
    message = {"from": sender_id, "to": target, "payload": payload}
    await sio.emit("call_renegotiate", message, room=user_room(target))


@sio.on("ice_candidate")
@with_scoped_session
async def ice_candidate(sid, data):
    """Handle ICE candidates - notify callee via push if offline"""
    target = data.get("to")
    payload = data.get("payload")
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id") if isinstance(socket_session, dict) else None
    if not sender_id:
        logger.warning(f"ICE candidate from unauthenticated sid={sid}, ignoring")
        return

    logger.debug(f"ICE candidate data: {data}")

    if not (target and payload):
        await sio.emit(
            "error",
            {"type": "invalid_data", "details": "Missing target or payload"},
            to=sid,
        )
        return

    call_type = payload.get("type")
    is_video_call = payload.get("is_video_call", False)
    candidate = payload.get("candidate")
    call_id = payload.get("call_id")

    logger.debug(
        f"ICE candidate - type: {call_type}, video: {is_video_call}, has_candidate: {candidate is not None}"
    )

    # Forward ICE candidate to target user
    # ALWAYS emit to room first
    message = {"from": sender_id, "to": target, "payload": payload}
    await sio.emit(
        "ice_candidate",
        message,
        room=user_room(target),
    )

    if await is_user_online(target):
        logger.debug(f"ICE candidate delivered to online user {target}")
    else:
        logger.warning(f"User {target} appears offline, emitted ICE candidate anyway")
        if call_id:
            try:
                # Avoid notification spam: many ICE candidates arrive per call.
                # Send at most one ICE-fallback push per (call_id, target).
                fallback_key = f"ice_fallback_push:{call_id}:{target}"
                should_send = True
                try:
                    # Redis NX-style lock with short TTL so transient offline windows are covered.
                    should_send = bool(await redis_client.set(fallback_key, "1", ex=60, nx=True))
                except Exception:
                    # If Redis is unavailable, still send one best-effort notification.
                    should_send = True

                if should_send:
                    sender = get_user_by_id(sender_id, session)
                    receiver = get_user_by_id(target, session)
                    if sender and receiver:
                        notification_result = await notification_service.send_call_notification(
                            caller=sender,
                            receiver=receiver,
                            is_video_call=is_video_call,
                            call_id=call_id,
                            session=session,
                        )
                        logger.debug(f"ICE fallback push result: {notification_result}")
                else:
                    logger.debug(
                        f"Skipping duplicate ICE fallback push for call_id={call_id} target={target}"
                    )
            except Exception as e:
                logger.error(f"Failed to send ICE fallback notification: {e}")


@sio.on("call_reject")
@with_scoped_session
async def call_reject(sid, data):
    """Handle call rejection - update call status and notify participants"""
    target = data.get("to")
    payload = data.get("payload") or {}
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id")

    logger.debug(f"Call reject data: {data}")

    call_id = payload.get("call_id")
    if not (target and call_id):
        await sio.emit(
            "error",
            {"type": "invalid_data", "details": "Missing target or call_id"},
            to=sid,
        )
        return

    if not call_id or not isinstance(call_id, str):
        await sio.emit(
            "error",
            {"type": "invalid_call_id", "details": "call_id must be a valid string"},
            to=sid,
        )
        return

    call_info = session.get(Call, call_id)
    if not call_info:
        await sio.emit(
            "error",
            {"type": "not_found", "details": f"Call {call_id} not found"},
            to=sid,
        )
        return

    call_info.call_status = CallStatus.DECLINED.value
    call_info.duration_seconds = 0
    call_info.updated_at = datetime.now(timezone.utc)
    session.add(call_info)
    session.commit()
    session.refresh(call_info)

    # Call rejected — clean up the pending offer kept for reconnect retries.
    # sender_id is the callee (rejecter), so the offer was buffered for sender_id.
    try:
        await redis_client.delete(f"pending_offer:{sender_id}")
        _pending_offers_memory.pop(sender_id, None)
    except Exception:
        pass

    payload_with_call = dict(payload)
    payload_with_call["call_id"] = call_id

    # ALWAYS emit to room first
    message = {
        "from": sender_id,
        "to": target,
        "payload": payload_with_call,
    }
    await sio.emit(
        "call_reject",
        message,
        room=user_room(target),
    )

    if await is_user_online(target):
        logger.debug(f"Call reject delivered to online user {target}")
    else:
        logger.warning(f"User {target} is offline, emitted call reject anyway")

    await emit_call_update(call_info, sender_id, CallStatus.DECLINED)
    # Create/update a CALL message reflecting declined
    try:
        call_message = save_message(
            sender_id=sender_id,
            receiver_id=target,
            message_text=None,
            session=session,
            is_video_call=call_info.is_video_call,
            call_status=CallStatus.DECLINED.value,
        )
        payload = build_message_response(call_message).model_dump(mode="json")
        if await is_user_online(target):
            await sio.emit("message:new", payload, room=user_room(target))
    except Exception as e:
        logger.error(f"Could not upsert CALL message on reject: {e}")


@sio.on("call_end")
@with_scoped_session
async def call_end(sid, data):
    """Handle call end - update call duration/status and notify participants"""
    target = data.get("to")
    payload = data.get("payload") or {}
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id")

    logger.debug(f"Call end data: {data}")

    call_id = payload.get("call_id")
    if not (target and call_id):
        await sio.emit(
            "error",
            {"type": "invalid_data", "details": "Missing target or call_id"},
            to=sid,
        )
        return

    if not call_id or not isinstance(call_id, str):
        await sio.emit(
            "error",
            {"type": "invalid_call_id", "details": "call_id must be a valid string"},
            to=sid,
        )
        return

    call_info = session.get(Call, call_id)
    if not call_info:
        await sio.emit(
            "error",
            {"type": "not_found", "details": f"Call {call_id} not found"},
            to=sid,
        )
        return

    now = datetime.now(timezone.utc)
    previous_status = call_info.call_status
    start_time = call_info.updated_at

    if previous_status == CallStatus.ANSWERED.value and start_time:
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        duration = int((now - start_time).total_seconds())
        call_info.duration_seconds = max(duration, 0)
        call_info.call_status = CallStatus.ENDED.value
    else:
        call_info.duration_seconds = call_info.duration_seconds or 0
        call_info.call_status = CallStatus.MISSED.value

    call_info.updated_at = now
    session.add(call_info)
    session.commit()
    session.refresh(call_info)

    payload_with_call = dict(payload)
    payload_with_call["call_id"] = call_id

    # Notify users - Emit unconditionally to ensure delivery
    await sio.emit(
        "call_end",
        {"from": sender_id, "to": target, "payload": payload_with_call},
        room=user_room(target),
    )
    # If callee is not currently connected, send a data push so Android can stop
    # any ringing incoming-call notification tied to this call_id.
    try:
        if not await is_user_online(target):
            await notification_service.firebase.send_to_user(
                user_id=target,
                title="Call ended",
                body="",
                data={
                    "type": "incoming_call_end",
                    "call_id": str(call_id),
                },
                session=session,
            )
    except Exception as e:
        logger.error(f"Failed to send call_end cancel push: {e}")
    
    await emit_call_update(call_info, sender_id, CallStatus.ENDED)


@sio.event
async def random_handover(sid, data):
    """
    Called by the dedicated RandomCallClient socket.
    data: { "old_sid": "..." }
    """
    old_sid = data.get("old_sid")
    if old_sid:
        await random_manager.handover_session(old_sid, sid)
        await sio.emit("random_handover_success", to=sid)


@sio.on("login_qr_init")
async def login_qr_init(sid, data):
    """
    Web client initiates a QR login session.
    It provides a unique 'session_id' (displayed in QR) and joins a room.
    """
    session_id = data.get("session_id")
    if session_id:
        await sio.enter_room(sid, f"qr_session:{session_id}")
        logger.info(f"Socket {sid} waiting for QR login on session {session_id}")


@sio.on("login_qr_authenticate")
@with_scoped_session
async def login_qr_authenticate(sid, data):
    """
    Mobile client (authenticated) scans QR and sends 'session_id'.
    Server verifies sender and emits 'auth_success' to the Web client's room.
    """
    socket_session = await sio.get_session(sid)
    logger.info(f"QR Auth: sid={sid}, socket_session={socket_session}")
    sender_id = socket_session.get("user_id") if isinstance(socket_session, dict) else None

    if not sender_id:
        logger.warning(f"QR Auth: unauthorized sid={sid}, session={socket_session}")
        await sio.emit("error", {"type": "unauthorized", "details": "Not authenticated"}, to=sid)
        return

    target_session_id = data.get("session_id")
    logger.info(f"QR Auth Attempt: sid={sid}, sender_id={sender_id}, target_session_id={target_session_id}")
    
    if not target_session_id:
        print("❌ Missing session_id in QR Auth")
        await sio.emit("error", {"type": "invalid_data", "details": "Missing session_id"}, to=sid)
        return

    try:
        from app.users.routers import create_user_session

        user = get_user_by_id(sender_id, session)
        if not user:
            logger.error(f"QR Auth: user {sender_id} not found in DB")
            await sio.emit("error", {"type": "user_not_found", "details": "User not found"}, to=sid)
            return

        # Create a proper server-side session (same as normal login)
        user_session = create_user_session(user, session)

        payload = {
            "access_token": user_session.session_token,
            "token_type": "Bearer",
            "user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "email": user.email,
                "profile_pic": user.profile_pic,
                "bio": user.bio
            }
        }

        # Emit success to the specific QR session room
        await sio.emit("auth_success", payload, room=f"qr_session:{target_session_id}")

        # Confirm to mobile that it worked
        await sio.emit("login_qr_success", {"success": True}, to=sid)

        logger.info(f"User {sender_id} authorized QR session {target_session_id}")

    except Exception as e:
        logger.error(f"QR Auth failed: {e}")
        await sio.emit("error", {"type": "server_error", "details": "QR Auth failed"}, to=sid)

@sio.on("key_transfer_init")
async def key_transfer_init(sid, data):
    """
    Desktop client initiates a key transfer session.
    It provides a unique 'session_id' and joins a room to wait for the mobile key.
    """
    session_id = data.get("session_id")
    if session_id:
        await sio.enter_room(sid, f"key_transfer:{session_id}")
        logger.info(f"Socket {sid} waiting for key transfer on session {session_id}")


@sio.on("key_transfer_send")
async def key_transfer_send(sid, data):
    """
    Mobile client (authenticated) sends its encrypted private key to the desktop.
    data: { session_id, encrypted_key (base64 AES-GCM blob), iv (base64) }
    """
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id")

    if not sender_id:
        await sio.emit("error", {"type": "unauthorized", "details": "Not authenticated"}, to=sid)
        return

    session_id = data.get("session_id")
    encrypted_key = data.get("encrypted_key")
    iv = data.get("iv")

    if not session_id or not encrypted_key or not iv:
        await sio.emit("error", {"type": "invalid_data", "details": "Missing fields"}, to=sid)
        return

    # Relay the encrypted private key to the desktop room
    await sio.emit(
        "key_transfer_received",
        {"encrypted_key": encrypted_key, "iv": iv},
        room=f"key_transfer:{session_id}",
    )

    # Confirm to mobile
    await sio.emit("key_transfer_success", {"success": True}, to=sid)
    logger.info(f"User {sender_id} sent key transfer for session {session_id}")


@sio.on("call_cancel")
@with_scoped_session
async def call_cancel(sid, data):
    """Handle call cancellation - update call status and notify participants"""
    target = data.get("to")
    payload = data.get("payload") or {}
    socket_session = await sio.get_session(sid)
    sender_id = socket_session.get("user_id")

    logger.debug(f"Call cancel data: {data}")

    call_id = payload.get("call_id")
    if not (target and call_id):
        await sio.emit(
            "error",
            {"type": "invalid_data", "details": "Missing target or call_id"},
            to=sid,
        )
        return

    if not call_id or not isinstance(call_id, str):
        await sio.emit(
            "error",
            {"type": "invalid_call_id", "details": "call_id must be a valid string"},
            to=sid,
        )
        return

    call_info = session.get(Call, call_id)
    if not call_info:
        await sio.emit(
            "error",
            {"type": "not_found", "details": f"Call {call_id} not found"},
            to=sid,
        )
        return

    call_info.call_status = CallStatus.DECLINED.value
    call_info.duration_seconds = 0
    call_info.updated_at = datetime.now(timezone.utc)
    session.add(call_info)
    session.commit()
    session.refresh(call_info)

    # Call cancelled — clean up the pending offer kept for reconnect retries.
    # target is the callee, so the offer was buffered for target.
    try:
        await redis_client.delete(f"pending_offer:{target}")
        _pending_offers_memory.pop(target, None)
    except Exception:
        pass

    payload_with_call = dict(payload)
    payload_with_call["call_id"] = call_id

    if await is_user_online(target):
        message = {
            "from": sender_id,
            "to": target,
            "payload": payload_with_call,
        }
        await sio.emit(
            "call_cancel",
            message,
            room=user_room(target),
        )
        logger.debug(f"Call cancellation delivered to online user {target}")
    else:
        logger.warning(f"User {target} is offline, cannot deliver call cancellation")
        # Offline callee still needs to stop the ringing notification.
        try:
            await notification_service.firebase.send_to_user(
                user_id=target,
                title="Call cancelled",
                body="",
                data={
                    "type": "incoming_call_cancel",
                    "call_id": str(call_id),
                },
                session=session,
            )
        except Exception as e:
            logger.error(f"Failed to send call_cancel cancel push: {e}")

    await emit_call_update(call_info, sender_id, CallStatus.DECLINED)


@sio.on("reaction:add")
@with_scoped_session
async def reaction_add(sid, data):
    socket_session = await sio.get_session(sid)
    user_id = socket_session.get("user_id") if isinstance(socket_session, dict) else None
    message_id = data.get("message_id")
    emoji = data.get("emoji")
    if not (user_id and message_id and emoji):
        await sio.emit(
            "error", {"type": "validation_error", "details": "Missing fields"}, to=sid
        )
        return
    reaction = Reaction(user_id=user_id, message_id=message_id, emoji=emoji)
    session.add(reaction)
    session.commit()
    session.refresh(reaction)
    user = session.get(User, user_id)
    user_data = {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "profile_pic": user.profile_pic,
        "bio": user.bio or "",
    } if user else None
    reaction_payload = {
        "id": reaction.id,
        "message_id": message_id,
        "emoji": emoji,
        "user_id": user_id,
        "user": user_data,
    }
    await sio.emit("reaction:added", reaction_payload, to=sid)
    # Notify the other party (look up message to find them)
    msg = session.get(Message, message_id)
    if msg:
        other_id = msg.receiver_id if msg.sender_id == user_id else msg.sender_id
        await sio.emit("reaction:added", reaction_payload, room=user_room(other_id))
    # Sync to sender's other devices
    await sio.emit("reaction:added", reaction_payload, room=user_room(user_id), skip_sid=sid)


@sio.on("reaction:remove")
@with_scoped_session
async def reaction_remove(sid, data):
    socket_session = await sio.get_session(sid)
    user_id = socket_session.get("user_id") if isinstance(socket_session, dict) else None
    message_id = data.get("message_id")
    emoji = data.get("emoji")
    if not (user_id and message_id and emoji):
        await sio.emit(
            "error", {"type": "validation_error", "details": "Missing fields"}, to=sid
        )
        return
    reaction = (
        session.query(Reaction)
        .filter_by(user_id=user_id, message_id=message_id, emoji=emoji)
        .first()
    )
    if reaction:
        reaction_id = reaction.id
        # Look up message to find the other party before deleting
        msg = session.get(Message, message_id)
        session.delete(reaction)
        session.commit()
        remove_payload = {"id": reaction_id, "message_id": message_id, "emoji": emoji, "user_id": user_id}
        await sio.emit("reaction:removed", remove_payload, to=sid)
        if msg:
            other_id = msg.receiver_id if msg.sender_id == user_id else msg.sender_id
            await sio.emit("reaction:removed", remove_payload, room=user_room(other_id))
        # Sync to sender's other devices
        await sio.emit("reaction:removed", remove_payload, room=user_room(user_id), skip_sid=sid)
    else:
        await sio.emit(
            "error", {"type": "not_found", "details": "Reaction not found"}, to=sid
        )
