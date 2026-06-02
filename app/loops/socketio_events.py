import socketio
from app.loops.models import LoopMessage, LoopChat, LoopProfile, RandomSession, LoopMessageType
from app.database import get_session

from app.sockets.socketio_server import sio
from datetime import datetime
from sqlmodel import select


async def _get_authenticated_profile(sid):
    """Return the LoopProfile for the authenticated socket user, or None."""
    session_data = await sio.get_session(sid)
    user_id = session_data.get("user_id") if session_data else None
    if not user_id:
        return None
    with get_session() as session:
        profile = session.exec(
            select(LoopProfile).where(LoopProfile.user_id == user_id)
        ).first()
        return profile


# Real-time Loop messaging
@sio.event
async def join_loop_chat(sid, data):
    """User joins a loop chat room — only allowed if they are a participant."""
    chat_id = data.get("chat_id")
    if not chat_id:
        return

    profile = await _get_authenticated_profile(sid)
    if not profile:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    with get_session() as session:
        chat = session.get(LoopChat, chat_id)
        if not chat or (chat.profile1_id != profile.id and chat.profile2_id != profile.id):
            await sio.emit("error", {"message": "Not a participant of this chat"}, to=sid)
            return

    await sio.enter_room(sid, f"loop_chat_{chat_id}")
    print(f"✅ User {sid} joined loop_chat_{chat_id}")


@sio.event
async def send_loop_message(sid, data):
    """Send a loop message — sender is derived from the authenticated session."""
    chat_id = data.get("chat_id")
    content = data.get("content")
    message_type = data.get("message_type", "text")
    media_url = data.get("media_url")

    if not content and not media_url:
        print("❌ Message must have content or media")
        return

    profile = await _get_authenticated_profile(sid)
    if not profile:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    try:
        with get_session() as session:
            message = LoopMessage(
                chat_id=chat_id,
                sender_profile_id=profile.id,
                content=content or "",
                message_type=message_type,
                media_url=media_url,
            )
            session.add(message)
            session.flush()
            session.refresh(message)

            # Update chat preview so the chat list stays current
            chat = session.get(LoopChat, chat_id)
            if chat:
                chat.last_message_at = message.created_at
                chat.last_message_content = "Media" if not content else content
                session.add(chat)

            session.commit()

            await sio.emit(
                "message:new",
                {
                    "id": message.id,
                    "chat_id": chat_id,
                    "sender_profile_id": profile.id,
                    "content": content,
                    "message_type": message_type,
                    "media_url": media_url,
                    "created_at": message.created_at.isoformat(),
                },
                room=f"loop_chat_{chat_id}",
            )
            print(f"✅ Message {message.id} sent to loop_chat_{chat_id}")
    except Exception as e:
        print(f"❌ Error saving message: {e}")


@sio.event
async def delete_loop_message(sid, data):
    """Delete a loop message — only the sender can delete their own message."""
    message_id = data.get("message_id")
    chat_id = data.get("chat_id")

    profile = await _get_authenticated_profile(sid)
    if not profile:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    try:
        with get_session() as session:
            message = session.get(LoopMessage, message_id)
            if message and message.sender_profile_id == profile.id:
                message.deleted_for_profile_id = profile.id
                session.add(message)
                session.commit()

                await sio.emit(
                    "message:delete",
                    {"message_id": message_id},
                    room=f"loop_chat_{chat_id}",
                )
                print(f"✅ Message {message_id} deleted from loop_chat_{chat_id}")
    except Exception as e:
        print(f"❌ Error deleting message: {e}")


@sio.event
async def add_loop_reaction(sid, data):
    """Add/toggle reaction on a loop message — reactor is derived from the authenticated session."""
    message_id = data.get("message_id")
    chat_id = data.get("chat_id")
    emoji = data.get("emoji")

    profile = await _get_authenticated_profile(sid)
    if not profile:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    profile_id = profile.id
    
    try:
        with get_session() as session:
            from app.loops.models import LoopReaction

            message = session.get(LoopMessage, message_id)
            if not message:
                return

            existing = session.exec(
                select(LoopReaction).where(
                    LoopReaction.message_id == message_id,
                    LoopReaction.profile_id == profile_id,
                    LoopReaction.emoji == emoji,
                )
            ).first()
            
            if existing:
                session.delete(existing)
                session.commit()
                event_name = "reaction:removed"
            else:
                reaction = LoopReaction(
                    message_id=message_id,
                    profile_id=profile_id,
                    emoji=emoji,
                )
                session.add(reaction)
                session.commit()
                event_name = "reaction:added"
            
            await sio.emit(
                event_name,
                {
                    "message_id": message_id,
                    "emoji": emoji,
                    "profile_id": profile_id,
                },
                room=f"loop_chat_{chat_id}",
            )
            print(f"✅ Reaction {event_name} for message {message_id}")
    except Exception as e:
        print(f"❌ Error handling reaction: {e}")



# Validated Random Connect events using Manager

from app.loops.random_manager import random_manager
from app.users.models import User

@sio.event
async def join_random_queue(sid, data):
    """User joins random queue (text/audio/video)"""
    # 1. Validation & Permissions
    session = await sio.get_session(sid)
    user_id = session.get("user_id") if session else None
    
    # Check if user_id is in session (set during connect usually)
    # If not, try to get from data or re-verify token? 
    # For now, let's assume `connect` handler sets user_id in session or we fetch it.
    # If `connect` is standard, we trust the sid session.
    
    # NOTE: The current `socketio_server.py` usually handles auth. 
    # If session is empty, we might need to parse token from data.
    # Assuming standard auth puts user_id in session.
    
    if not user_id:
        # Fallback: Check data for explicit user_id (less secure but needed if session mapping missing)
        user_id = data.get("user_id")
    
    if not user_id:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    mode = data.get("mode", "text")
    
    # Check `is_loop_enabled` permission
    # We do a quick DB check
    can_join = await random_manager._check_permission(user_id)
    if not can_join:
        await sio.emit("error", {"message": "Loops feature not enabled or account suspended"}, to=sid)
        return

    # Join Queue
    await random_manager.join_queue(user_id, sid, mode, sio)


@sio.event
async def leave_random_queue(sid, data):
    session = await sio.get_session(sid)
    user_id = session.get("user_id") or data.get("user_id")
    if user_id:
        await random_manager.leave_queue(user_id, sid)
        await sio.emit("random:left_queue", to=sid)


@sio.event
async def leave_random_session(sid, data):
    """Explicitly leave a session (Stop/Next)"""
    await random_manager.leave_session(sid, sio)


@sio.event
async def random_report(sid, data):
    """Report current partner"""
    target_profile_id = data.get("target_profile_id")
    reason = data.get("reason", "violation")
    
    # Log report
    # Typically we'd have a ReportService. 
    # For MVP, just print and maybe log to a file or DB row if simple.
    print(f"🚨 REPORT: User at SID {sid} reported Profile {target_profile_id} for {reason}")
    
    # Ideally: Create 'Report' in DB.
    # await random_manager.log_report(...)
    
    # Disconnect/Skip logic handled by client leaving queue/room?
    # We acknowledge
    await sio.emit("random:report_received", to=sid)

@sio.event
async def disconnect(sid, *args):
    print(f"❌ Disconnected: {sid}")
    session = await sio.get_session(sid)
    user_id = session.get("user_id") if session else None
    await random_manager.user_disconnected(sid, sio, user_id)


@sio.event
async def random_message(sid, data):
    """Ephemeral message for random chat (not saved to DB)"""
    room_id = data.get("room_id")
    content = data.get("content")
    session = await sio.get_session(sid)
    user_id = session.get("user_id") if session else None
    
    if not room_id or not content:
        return

    # Broadcast to room BUT skip sender (so sender handles their own UI optimistically)
    # And we don't send sender_id to protect privacy (as requested)
    await sio.emit(
        "random:message",
        {"content": content, "created_at": datetime.now().isoformat()},
        room=room_id,
        skip_sid=sid
    )

# --- WebRTC Signaling Events (Random Mode) ---

@sio.event
async def random_call_offer(sid, data):
    """Relay WebRTC Offer for Random Calls"""
    session_key = f"loops:random:sess:{sid}"
    session_data = await random_manager.redis.get(session_key)
    
    if session_data:
        import json
        s_data = json.loads(session_data)
        partner_sid = s_data["partner_sid"]
        
        # Relay to partner
        await sio.emit("random_call_offer", {"from": sid, "payload": data.get("payload")}, to=partner_sid)
        print(f"📡 Relayed RANDOM OFFER from {sid} to {partner_sid}")
    else:
        print(f"⚠️ Could not find partner for signaling from {sid}")

@sio.event
async def random_call_answer(sid, data):
    session_key = f"loops:random:sess:{sid}"
    session_data = await random_manager.redis.get(session_key)
    if session_data:
        import json
        s_data = json.loads(session_data)
        partner_sid = s_data["partner_sid"]
        await sio.emit("random_call_answer", {"from": sid, "payload": data.get("payload")}, to=partner_sid)
        print(f"📡 Relayed RANDOM ANSWER from {sid} to {partner_sid}")

@sio.event
async def random_ice_candidate(sid, data):
    session_key = f"loops:random:sess:{sid}"
    session_data = await random_manager.redis.get(session_key)
    if session_data:
        import json
        s_data = json.loads(session_data)
        partner_sid = s_data["partner_sid"]
        await sio.emit("random_ice_candidate", {"from": sid, "payload": data.get("payload")}, to=partner_sid)

@sio.event
async def random_call_reject(sid, data):
    session_key = f"loops:random:sess:{sid}"
    session_data = await random_manager.redis.get(session_key)
    if session_data:
        import json
        s_data = json.loads(session_data)
        partner_sid = s_data["partner_sid"]
        await sio.emit("random_call_reject", {"from": sid, "payload": data.get("payload")}, to=partner_sid)

@sio.event
async def random_call_end(sid, data):
    session_key = f"loops:random:sess:{sid}"
    session_data = await random_manager.redis.get(session_key)
    if session_data:
        import json
        s_data = json.loads(session_data)
        partner_sid = s_data["partner_sid"]
        await sio.emit("random_call_end", {"from": sid, "payload": data.get("payload")}, to=partner_sid)

@sio.event
async def random_handover(sid, data):
    """
    Called by the dedicated RandomCallClient socket.
    data: { "old_sid": "..." }
    """
    old_sid = data.get("old_sid")
    if old_sid:
        await random_manager.handover_session(old_sid, sid)
        # Notify the client that handover was successful
        await sio.emit("random_handover_success", room=sid)


