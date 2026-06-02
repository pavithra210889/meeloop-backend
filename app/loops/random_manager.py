from datetime import datetime
import json
import logging
import asyncio
from typing import Optional, Tuple
from redis.asyncio import Redis
from sqlmodel import select, Session as SQLSession

from app.database import engine
from app.config import settings
from app.loops.models import RandomSession, LoopProfile
from app.users.models import User

logger = logging.getLogger(__name__)

class RandomCallManager:
    def __init__(self, redis_url: str):
        self.redis = Redis.from_url(redis_url, decode_responses=True)
        self.mode_queues = {
            "text": "loops:random:queue:text",
            "audio": "loops:random:queue:audio",
            "video": "loops:random:queue:video",
        }
        # Track which user is in which room/session to handle disconnects
        # Key: user_sid, Value: session_data_json
        self.active_sessions_key = "loops:random:sessions" 

    async def join_queue(self, user_id: str, sid: str, mode: str, sio_server) -> None:
        """
        Add user to queue and try to match.
        mode: 'text', 'audio', 'video'
        """
        if mode not in self.mode_queues:
            logger.error(f"Invalid mode {mode} for user {user_id}")
            return

        queue_key = self.mode_queues[mode]

        # 1. Check if anyone else is waiting
        # We use LPOP to get the longest waiting user (FIFO)
        partner_data_json = await self.redis.lpop(queue_key)

        if partner_data_json:
            # MATCH FOUND!
            partner_data = json.loads(partner_data_json)
            partner_sid = partner_data["sid"]
            partner_user_id = partner_data["user_id"]
            
            # Use partner's SID if they are still connected? 
            # We assume they are. If not, match handling might fail on emit, 
            # but we can handle that later. For MVP, assume goodness.

            if partner_user_id == user_id:
                # Prevent self-match (edge case if user disconnects/reconnects fast)
                await self.redis.rpush(queue_key, partner_data_json)
                user_data = json.dumps({"user_id": user_id, "sid": sid})
                await self.redis.rpush(queue_key, user_data)
                await sio_server.emit("random:waiting", {"mode": mode}, to=sid)
                return

            logger.info(f"MATCH: {user_id} <-> {partner_user_id} in {mode}")

            # Create a unique room ID
            room_id = f"random_{mode}_{partner_user_id}_{user_id}_{int(datetime.utcnow().timestamp())}"

            # Join both to room
            await sio_server.enter_room(sid, room_id)
            await sio_server.enter_room(partner_sid, room_id)

            # Fetch profiles (to send partial info like name/avatar if allowed, or just verify)
            # Creating DB record
            session_id = await self._create_db_session(partner_user_id, user_id)
            
            # Emit match event
            # We need to send partner profile info.
            initiator_profile = await self._get_profile_public(user_id)
            receiver_profile = await self._get_profile_public(partner_user_id)

            # Notify Initiator (User who just joined) -> They are "offer" role usually in WebRTC logic
            # But here, let's say the one popped from queue (Partner) was waiting, so maybe they are "waiting".
            # Actually, standard WebRTC: One sends offer. Let's make the NEW comer (User) send offer.
            await sio_server.emit(
                "random:matched",
                {
                    "room_id": room_id,
                    "session_id": session_id,
                    "mode": mode,
                    "role": "initiator",
                    "partner_profile": receiver_profile,
                    "partner_sid": partner_sid,
                    "partner_user_id": partner_user_id,
                },
                to=sid
            )

            # Notify Receiver (Partner who was waiting)
            await sio_server.emit(
                "random:matched",
                {
                    "room_id": room_id,
                    "session_id": session_id,
                    "mode": mode,
                    "role": "receiver",
                    "partner_profile": initiator_profile,
                    "partner_sid": sid,
                    "partner_user_id": user_id,
                },
                to=partner_sid
            )
            
            # Track session for disconnect handling
            await self._track_session(sid, room_id, partner_sid)
            await self._track_session(partner_sid, room_id, sid)

        else:
            # NO MATCH, QUEUE UP
            user_data = json.dumps({"user_id": user_id, "sid": sid})
            # Push to right (newest)
            await self.redis.rpush(queue_key, user_data)
            await sio_server.emit("random:waiting", {"mode": mode}, to=sid)
            logger.info(f"User {user_id} joined queue {mode}")

    async def leave_queue(self, user_id: str, sid: str) -> None:
        """
        Remove user from all queues (expensive O(N) but queues shouldn't be massive for MVP).
        For scalable Redis list remove, usually we need to know WHICH queue.
        We'll try to remove current SID from all queues.
        """
        # We generally expect the client to know which mode they are in, 
        # but for safety we scan all.
        user_data = json.dumps({"user_id": user_id, "sid": sid})
        
        for mode, key in self.mode_queues.items():
            # LREM removes elements equal to value
            removed = await self.redis.lrem(key, 0, user_data)
            if removed > 0:
                logger.info(f"Removed {user_id} from {mode} queue")

    async def leave_session(self, sid: str, sio_server) -> None:
        """
        User explicitly leaves the session (clicked Stop/Next)
        """
        await self._handle_session_termination(sid, sio_server, "random:partner_disconnected")

    async def user_disconnected(self, sid: str, sio_server, user_id: str = None) -> None:
        """
        Handle unexpected disconnect.
        1. Remove from queues.
        2. If in active session, notify partner.
        """
        if user_id:
            await self.leave_queue(user_id, sid)
        else:
            await self._remove_from_queues_by_sid(sid)

        # Handle active session
        await self._handle_session_termination(sid, sio_server, "random:partner_disconnected")

    async def _remove_from_queues_by_sid(self, sid: str) -> None:
        """Scan all queues and remove any entry matching this sid (fallback when user_id is unknown)."""
        for mode, key in self.mode_queues.items():
            entries = await self.redis.lrange(key, 0, -1)
            for entry in entries:
                try:
                    data = json.loads(entry)
                    if data.get("sid") == sid:
                        await self.redis.lrem(key, 0, entry)
                        logger.info(f"Removed disconnected sid {sid} from {mode} queue")
                except Exception:
                    pass

    async def _handle_session_termination(self, sid: str, sio_server, event_name: str):
        session_tracker_key = f"loops:random:sess:{sid}"
        session_data_json = await self.redis.get(session_tracker_key)
        
        if session_data_json:
            # User was in a match
            data = json.loads(session_data_json)
            # room_id = data["room_id"]
            partner_sid = data["partner_sid"]
            
            # Notify partner
            await sio_server.emit(event_name, to=partner_sid)
            
            # Clear my tracking
            await self.redis.delete(session_tracker_key)
            # Clear partner tracking (free them up)
            partner_key = f"loops:random:sess:{partner_sid}"
            await self.redis.delete(partner_key)

    async def _track_session(self, sid: str, room_id: str, partner_sid: str):
        key = f"loops:random:sess:{sid}"
        data = json.dumps({"room_id": room_id, "partner_sid": partner_sid})
        await self.redis.set(key, data, ex=3600) # 1 hour TTL safety

    async def _create_db_session(self, user1_id: str, user2_id: str) -> str:
        """Log the session start in DB"""
        try:
            # We need to find LoopProfile IDs for these users actually.
            # RandomSession uses foreign_key="loopprofile.id"
            
            # Sync wrapper for async context if needed, or just standard SQLModel
            # Since we are in async method but SQLModel is usually sync unless using AsyncEngine
            # The app seems to use standard synchronous engine in `database.py`.
            # We should run this in a thread or use specific async handling.
            # For simplicity in this codebase structure:
            return await asyncio.to_thread(self._sync_create_db_session, user1_id, user2_id)
        except Exception as e:
            logger.error(f"Failed to create DB session: {e}")
            return ""

    def _sync_create_db_session(self, user1_id: str, user2_id: str) -> str:
        from sqlmodel import Session
        
        with Session(engine) as session:
            # Get Profiles
            p1 = session.exec(select(LoopProfile).where(LoopProfile.user_id == user1_id)).first()
            p2 = session.exec(select(LoopProfile).where(LoopProfile.user_id == user2_id)).first()
            
            if p1 and p2:
                # RandomSession model expects profile_id, connected_profile_id
                # user1 is profile_id, user2 is connected
                rs = RandomSession(
                    profile_id=p1.id,
                    connected_profile_id=p2.id,
                    started_at=datetime.utcnow()
                )
                session.add(rs)
                session.commit()
                session.refresh(rs)
                return str(rs.id)
        return ""

    async def _get_profile_public(self, user_id: str) -> Optional[dict]:
        return await asyncio.to_thread(self._sync_get_profile, user_id)

    def _sync_get_profile(self, user_id: str) -> Optional[dict]:
        from sqlmodel import Session
        with Session(engine) as session:
            profile = session.exec(select(LoopProfile).where(LoopProfile.user_id == user_id)).first()
            if profile:
                return {
                    "id": profile.id,
                    "displayname": profile.displayname,
                    "profile_pic": profile.profile_pic,
                    "gender": profile.gender
                }
        return None

    async def _check_permission(self, user_id: str) -> bool:
        return await asyncio.to_thread(self._sync_check_permission, user_id)

    def _sync_check_permission(self, user_id: str) -> bool:
        from sqlmodel import Session
        with Session(engine) as session:
            user = session.get(User, user_id)
            if user and user.is_loop_enabled:
                 if user.suspended_until and user.suspended_until > datetime.utcnow():
                     return False
                 return True
            return False

    async def handover_session(self, old_sid: str, new_sid: str):
        """
        Transfer session ownership from old_sid (Main App) to new_sid (Random Call Client).
        This updates the Redis keys so that signaling messages routed to the partner
        correctly point to the user's new socket, and vice-versa.
        """
        # 1. Get my session data (stored under old_sid)
        my_key = f"loops:random:sess:{old_sid}"
        data_json = await self.redis.get(my_key)
        
        if not data_json:
            logger.warning(f"Handover failed: No session found for {old_sid}")
            return

        data = json.loads(data_json)
        partner_sid = data["partner_sid"]
        
        # 2. Save my session data under NEW SID
        new_key = f"loops:random:sess:{new_sid}"
        await self.redis.set(new_key, data_json, ex=3600)
        
        # 3. Update Partner's session data to point to ME (New SID)
        partner_key = f"loops:random:sess:{partner_sid}"
        partner_data_json = await self.redis.get(partner_key)
        
        if partner_data_json:
            p_data = json.loads(partner_data_json)
            # Verify that the partner was indeed pointing to my old_sid
            if p_data["partner_sid"] == old_sid:
                p_data["partner_sid"] = new_sid # Point to my New SID
                await self.redis.set(partner_key, json.dumps(p_data), ex=3600)
                logger.info(f"Updated partner {partner_sid} to point to {new_sid}")
        
        # 4. Clean up old key
        await self.redis.delete(my_key)
        
        logger.info(f"Handover complete: {old_sid} -> {new_sid}")

# Singleton-ish instance
random_manager = RandomCallManager(settings.REDIS_URL)
