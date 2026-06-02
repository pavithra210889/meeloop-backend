"""Tests for Loop messaging: chats, messages, reactions, and chat list."""

from unittest.mock import patch

from app.loops.models import LoopChat, LoopMessage, LoopMessageType, LoopReaction


def _create_chat(session, profile1_id, profile2_id):
    chat = LoopChat(profile1_id=profile1_id, profile2_id=profile2_id)
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return chat


# ─────────────────────────────────────────────
# Start chat
# ─────────────────────────────────────────────

class TestStartLoopChat:
    def test_start_chat_creates_new_chat(self, client, auth_headers, loop_profile, second_loop_profile):
        """Starting a chat between two profiles creates a LoopChat."""
        resp = client.post(f"/loops/chat/start/{second_loop_profile.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["other_profile"]["id"] == second_loop_profile.id

    def test_start_chat_idempotent(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """Starting a chat that already exists returns the existing chat without duplication."""
        existing = _create_chat(session, loop_profile.id, second_loop_profile.id)
        resp = client.post(f"/loops/chat/start/{second_loop_profile.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == existing.id

    def test_start_chat_with_self_returns_400(self, client, auth_headers, loop_profile):
        """Cannot start a chat with yourself."""
        resp = client.post(f"/loops/chat/start/{loop_profile.id}", headers=auth_headers)
        assert resp.status_code == 400

    def test_requires_auth(self, client, second_loop_profile):
        resp = client.post(f"/loops/chat/start/{second_loop_profile.id}")
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# Send message
# ─────────────────────────────────────────────

class TestSendLoopMessage:
    def test_send_text_message(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """Sending a text message returns the created message."""
        chat = _create_chat(session, loop_profile.id, second_loop_profile.id)
        with patch("app.loops.routers.emit_loop_message_sync"):
            resp = client.post(f"/loops/chat/{chat.id}/message", json={
                "content": "Hello!",
                "message_type": "text",
            }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "Hello!"
        assert data["sender_profile_id"] == loop_profile.id

    def test_send_message_non_member_returns_403(self, client, second_auth_headers, loop_profile, second_loop_profile, session):
        """A user not in the chat cannot send messages."""
        from app.loops.models import LoopProfile as LP
        from app.users.models import User
        from app.security import get_password_hash
        third_user = User(
            name="Third", username="thirdu99", email="third99@meeloop.com",
            password=get_password_hash("pass"), is_active=True, is_verified=True,
        )
        session.add(third_user)
        session.commit()
        session.refresh(third_user)
        third_profile = LP(user_id=third_user.id, displayname="Third", bio="", gender="other")
        session.add(third_profile)
        session.commit()
        session.refresh(third_profile)

        # Chat only involves loop_profile (test_user) and third_profile
        chat = _create_chat(session, loop_profile.id, third_profile.id)
        with patch("app.loops.routers.emit_loop_message_sync"):
            resp = client.post(f"/loops/chat/{chat.id}/message", json={
                "content": "Hey",
                "message_type": "text",
            }, headers=second_auth_headers)
        assert resp.status_code == 403

    def test_send_to_nonexistent_chat_returns_404(self, client, auth_headers, loop_profile):
        """Sending to a non-existent chat_id returns 404."""
        with patch("app.loops.routers.emit_loop_message_sync"):
            resp = client.post("/loops/chat/nonexistent-id/message", json={
                "content": "Hello",
                "message_type": "text",
            }, headers=auth_headers)
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.post("/loops/chat/some-id/message", json={"content": "hi", "message_type": "text"})
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# Get messages
# ─────────────────────────────────────────────

class TestGetLoopMessages:
    def test_returns_messages_in_chat(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """GET /loops/chat/{id}/messages returns existing messages."""
        chat = _create_chat(session, loop_profile.id, second_loop_profile.id)
        msg = LoopMessage(
            chat_id=chat.id,
            sender_profile_id=loop_profile.id,
            content="Test message",
            message_type=LoopMessageType.TEXT,
        )
        session.add(msg)
        session.commit()

        resp = client.get(f"/loops/chat/{chat.id}/messages", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(m["content"] == "Test message" for m in data["items"])

    def test_non_member_cannot_read_messages(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """A user not in the chat gets 403 when listing messages."""
        from app.loops.models import LoopProfile as LP
        from app.users.models import User
        from app.security import get_password_hash
        third_user = User(
            name="T3", username="t3user99", email="t3_99@ml.com",
            password=get_password_hash("p"), is_active=True, is_verified=True,
        )
        session.add(third_user)
        session.commit()
        session.refresh(third_user)
        third_profile = LP(user_id=third_user.id, displayname="T3", bio="", gender="other")
        session.add(third_profile)
        session.commit()
        session.refresh(third_profile)

        # Chat between second and third — test_user (auth_headers) is NOT a member
        chat = _create_chat(session, second_loop_profile.id, third_profile.id)
        resp = client.get(f"/loops/chat/{chat.id}/messages", headers=auth_headers)
        assert resp.status_code == 403

    def test_nonexistent_chat_returns_404(self, client, auth_headers, loop_profile):
        resp = client.get("/loops/chat/nonexistent-id/messages", headers=auth_headers)
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/loops/chat/some-id/messages")
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# Delete message
# ─────────────────────────────────────────────

class TestDeleteLoopMessage:
    def test_sender_can_delete_own_message(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """Message sender can soft-delete their own message."""
        chat = _create_chat(session, loop_profile.id, second_loop_profile.id)
        msg = LoopMessage(
            chat_id=chat.id,
            sender_profile_id=loop_profile.id,
            content="Delete me",
            message_type=LoopMessageType.TEXT,
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)

        with patch("app.loops.routers.asyncio"):
            resp = client.delete(f"/loops/message/{msg.id}", headers=auth_headers)
        assert resp.status_code == 200

        # Soft-deleted: message still exists but deleted_for_profile_id is set
        session.refresh(msg)
        assert msg.deleted_for_profile_id == loop_profile.id

    def test_non_sender_cannot_delete(self, client, second_auth_headers, loop_profile, second_loop_profile, session):
        """Non-sender gets 403 when trying to delete a message."""
        chat = _create_chat(session, loop_profile.id, second_loop_profile.id)
        msg = LoopMessage(
            chat_id=chat.id,
            sender_profile_id=loop_profile.id,
            content="Not yours",
            message_type=LoopMessageType.TEXT,
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)

        with patch("app.loops.routers.asyncio"):
            resp = client.delete(f"/loops/message/{msg.id}", headers=second_auth_headers)
        assert resp.status_code == 403

    def test_nonexistent_message_returns_404(self, client, auth_headers, loop_profile):
        with patch("app.loops.routers.asyncio"):
            resp = client.delete("/loops/message/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.delete("/loops/message/some-id")
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# Reactions
# ─────────────────────────────────────────────

class TestLoopReactions:
    def test_add_reaction(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """Adding a reaction to a message returns the reaction data."""
        chat = _create_chat(session, loop_profile.id, second_loop_profile.id)
        msg = LoopMessage(
            chat_id=chat.id,
            sender_profile_id=second_loop_profile.id,
            content="React to me",
            message_type=LoopMessageType.TEXT,
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)

        with patch("app.loops.routers.emit_loop_message_sync"):
            resp = client.post(f"/loops/message/{msg.id}/reaction", json={"emoji": "👍"}, headers=auth_headers)
        assert resp.status_code == 200
        assert "detail" in resp.json()

    def test_remove_reaction(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """Removing an existing reaction succeeds."""
        chat = _create_chat(session, loop_profile.id, second_loop_profile.id)
        msg = LoopMessage(
            chat_id=chat.id,
            sender_profile_id=second_loop_profile.id,
            content="msg",
            message_type=LoopMessageType.TEXT,
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)
        reaction = LoopReaction(message_id=msg.id, profile_id=loop_profile.id, emoji="👍")
        session.add(reaction)
        session.commit()

        with patch("app.loops.routers.emit_loop_message_sync"):
            resp = client.delete(f"/loops/message/{msg.id}/reaction/👍", headers=auth_headers)
        assert resp.status_code == 200

    def test_reaction_on_nonexistent_message_returns_404(self, client, auth_headers, loop_profile):
        with patch("app.loops.routers.emit_loop_message_sync"):
            resp = client.post("/loops/message/nonexistent/reaction", json={"emoji": "❤️"}, headers=auth_headers)
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.post("/loops/message/some-id/reaction", json={"emoji": "👍"})
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# List chats
# ─────────────────────────────────────────────

class TestListLoopChats:
    def test_returns_own_chats(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """GET /loops/chats returns chats the current user is part of."""
        _create_chat(session, loop_profile.id, second_loop_profile.id)
        resp = client.get("/loops/chats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_excludes_unrelated_chats(self, client, auth_headers, loop_profile, second_loop_profile, session):
        """Chats that don't involve the current user are excluded."""
        from app.loops.models import LoopProfile as LP
        from app.users.models import User
        from app.security import get_password_hash
        u3 = User(
            name="U3", username="u3user99", email="u3_99@ml.com",
            password=get_password_hash("p"), is_active=True, is_verified=True,
        )
        session.add(u3)
        session.commit()
        session.refresh(u3)
        p3 = LP(user_id=u3.id, displayname="U3", bio="", gender="other")
        session.add(p3)
        session.commit()
        session.refresh(p3)

        other_chat = LoopChat(profile1_id=second_loop_profile.id, profile2_id=p3.id)
        session.add(other_chat)
        session.commit()

        resp = client.get("/loops/chats", headers=auth_headers)
        ids = [c["id"] for c in resp.json()["items"]]
        assert other_chat.id not in ids

    def test_requires_auth(self, client):
        resp = client.get("/loops/chats")
        assert resp.status_code == 401
