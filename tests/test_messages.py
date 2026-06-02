"""
Tests for the messaging and chat endpoints.

Covers: sending messages, retrieving conversations, editing, deleting,
read receipts, reactions, chat listing, mute/unmute, and idempotency.
"""

import json
import pytest
from datetime import datetime, timedelta, timezone


# ────────────────────────────────────────────
# Helper
# ────────────────────────────────────────────

def _send_message(client, receiver_id, headers, text="hello", **extra):
    """Shortcut to POST a text message and return the response."""
    payload = {"message": text, **extra}
    return client.post(
        f"/messages/{receiver_id}/",
        json=payload,
        headers=headers,
    )


# ────────────────────────────────────────────
# Send Message
# ────────────────────────────────────────────


class TestSendMessage:
    def test_send_text_message(self, client, auth_headers, test_user, second_user):
        """Sending a basic text message should succeed and return full response."""
        resp = _send_message(client, second_user.id, auth_headers, "Hi there")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Hi there"
        assert data["message_type"] == "text"
        assert data["sender"]["id"] == test_user.id
        assert data["receiver"]["id"] == second_user.id
        assert data["status"] == "sent"
        assert "chat_id" in data

    def test_send_message_to_self(self, client, auth_headers, test_user):
        """Sending a message to yourself should still work (notes-to-self)."""
        resp = _send_message(client, test_user.id, auth_headers, "note to self")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sender"]["id"] == test_user.id
        assert data["receiver"]["id"] == test_user.id

    def test_send_message_no_auth(self, client, second_user):
        """Sending a message without auth should be rejected."""
        resp = client.post(
            f"/messages/{second_user.id}/",
            json={"message": "anon"},
        )
        assert resp.status_code == 401

    def test_send_image_message(self, client, auth_headers, second_user):
        """Sending a message with media_url and media_type='image' should set message_type to IMAGE."""
        resp = _send_message(
            client,
            second_user.id,
            auth_headers,
            text=None,
            media_url="https://cdn.example.com/photo.jpg",
            media_type="image",
        )
        assert resp.status_code == 200
        assert resp.json()["message_type"] == "image"

    def test_send_message_with_caption(self, client, auth_headers, second_user):
        """A message can carry an optional caption."""
        resp = _send_message(
            client,
            second_user.id,
            auth_headers,
            text=None,
            media_url="https://cdn.example.com/vid.mp4",
            media_type="video",
            caption="Check this out",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["caption"] == "Check this out"
        assert data["message_type"] == "video"

    def test_send_message_with_media_encryption(self, client, auth_headers, second_user):
        """The media_encryption field should be persisted."""
        enc_json = '{"keys":{"dev1":"abc"}}'
        resp = _send_message(
            client,
            second_user.id,
            auth_headers,
            text="encrypted media",
            media_encryption=enc_json,
        )
        assert resp.status_code == 200
        assert resp.json()["media_encryption"] == enc_json


# ────────────────────────────────────────────
# Get Messages
# ────────────────────────────────────────────


class TestGetMessages:
    def test_get_messages_between_users(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Both participants should see messages in their conversation."""
        _send_message(client, second_user.id, auth_headers, "msg1")
        _send_message(client, test_user.id, second_auth_headers, "msg2")

        resp = client.get(f"/messages/{second_user.id}/", headers=auth_headers)
        assert resp.status_code == 200
        texts = [m["message"] for m in resp.json()]
        assert "msg1" in texts
        assert "msg2" in texts

    def test_get_messages_empty(self, client, auth_headers, second_user):
        """Getting messages with a user you have no conversation with returns empty list."""
        resp = client.get(f"/messages/{second_user.id}/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_messages_pagination(
        self, client, auth_headers, test_user, second_user
    ):
        """Pagination via limit and before_id should work."""
        # Send 5 messages
        ids = []
        for i in range(5):
            r = _send_message(client, second_user.id, auth_headers, f"page{i}")
            ids.append(r.json()["id"])

        # Fetch with limit=2
        resp = client.get(
            f"/messages/{second_user.id}/",
            params={"limit": 2},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_messages_no_auth(self, client, second_user):
        """Getting messages without auth should fail."""
        resp = client.get(f"/messages/{second_user.id}/")
        assert resp.status_code == 401


# ────────────────────────────────────────────
# Edit Message
# ────────────────────────────────────────────


class TestEditMessage:
    def test_edit_own_message(
        self, client, auth_headers, test_user, second_user
    ):
        """The sender should be able to edit their own message text."""
        send_resp = _send_message(client, second_user.id, auth_headers, "original")
        msg_id = send_resp.json()["id"]

        edit_resp = client.put(
            f"/messages/{msg_id}",
            json={
                "id": msg_id,
                "receiver_id": second_user.id,
                "message": "edited",
                "message_type": "text",
            },
            headers=auth_headers,
        )
        assert edit_resp.status_code == 200
        assert edit_resp.json()["message"] == "edited"

    def test_edit_message_not_sender(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """The receiver editing a message should only change pinned status, not text."""
        send_resp = _send_message(client, second_user.id, auth_headers, "original")
        msg_id = send_resp.json()["id"]

        edit_resp = client.put(
            f"/messages/{msg_id}",
            json={
                "id": msg_id,
                "receiver_id": test_user.id,
                "message": "hacked",
                "message_type": "text",
                "pinned": True,
            },
            headers=second_auth_headers,
        )
        assert edit_resp.status_code == 200
        data = edit_resp.json()
        # Receiver can only toggle pin; message text stays as original
        assert data["message"] == "original"
        assert data["pinned"] is True

    def test_edit_nonexistent_message(self, client, auth_headers, second_user):
        """Editing a message that doesn't exist should return 400."""
        resp = client.put(
            "/messages/nonexistent-id",
            json={
                "id": "nonexistent-id",
                "receiver_id": second_user.id,
                "message": "nope",
                "message_type": "text",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400


# ────────────────────────────────────────────
# Delete Message
# ────────────────────────────────────────────


class TestDeleteMessage:
    def test_delete_message_soft(
        self, client, auth_headers, test_user, second_user
    ):
        """First delete should soft-delete (mark deleted_for_user_id)."""
        send_resp = _send_message(client, second_user.id, auth_headers, "bye")
        msg_id = send_resp.json()["id"]

        del_resp = client.delete(f"/messages/{msg_id}", headers=auth_headers)
        assert del_resp.status_code == 200
        assert "deleted" in del_resp.json()["detail"]

    def test_deleted_message_hidden_from_sender(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """After soft-delete the message should not appear in sender's view."""
        send_resp = _send_message(client, second_user.id, auth_headers, "hidden")
        msg_id = send_resp.json()["id"]

        client.delete(f"/messages/{msg_id}", headers=auth_headers)

        msgs = client.get(f"/messages/{second_user.id}/", headers=auth_headers)
        ids = [m["id"] for m in msgs.json()]
        assert msg_id not in ids

    def test_deleted_message_still_visible_to_other(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """After one user soft-deletes, the other should still see it."""
        send_resp = _send_message(client, second_user.id, auth_headers, "visible")
        msg_id = send_resp.json()["id"]

        client.delete(f"/messages/{msg_id}", headers=auth_headers)

        msgs = client.get(f"/messages/{test_user.id}/", headers=second_auth_headers)
        ids = [m["id"] for m in msgs.json()]
        assert msg_id in ids

    def test_delete_nonexistent_message(self, client, auth_headers):
        """Deleting a non-existent message returns detail message (not a crash)."""
        resp = client.delete("/messages/fake-id", headers=auth_headers)
        assert resp.status_code == 200
        assert "detail" in resp.json()


# ────────────────────────────────────────────
# Mark Read
# ────────────────────────────────────────────


class TestMarkRead:
    def test_mark_message_read(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Receiver marking a message as read should update status."""
        send_resp = _send_message(client, second_user.id, auth_headers, "read me")
        msg_id = send_resp.json()["id"]

        read_resp = client.post(
            f"/messages/{msg_id}/read", headers=second_auth_headers
        )
        assert read_resp.status_code == 200
        assert read_resp.json()["status"] == "read"

    def test_mark_all_from_sender_read(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Marking all messages from a sender as read should update their count."""
        for i in range(3):
            _send_message(client, second_user.id, auth_headers, f"unread{i}")

        resp = client.post(
            f"/messages/from/{test_user.id}/read-all",
            headers=second_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

    def test_mark_all_read_no_unread(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """When there are no unread messages the count should be 0."""
        resp = client.post(
            f"/messages/from/{test_user.id}/read-all",
            headers=second_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ────────────────────────────────────────────
# Reactions
# ────────────────────────────────────────────


class TestReactions:
    def test_add_reaction(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Adding a reaction should succeed and return the message."""
        send_resp = _send_message(client, second_user.id, auth_headers, "react to me")
        msg_id = send_resp.json()["id"]

        react_resp = client.post(
            f"/messages/{msg_id}/reactions",
            params={"emoji": "❤️"},
            headers=second_auth_headers,
        )
        assert react_resp.status_code == 200

    def test_toggle_reaction(
        self, client, auth_headers, test_user, second_user
    ):
        """Adding the same reaction twice should toggle (remove) it."""
        send_resp = _send_message(client, second_user.id, auth_headers, "toggle")
        msg_id = send_resp.json()["id"]

        # First add
        client.post(
            f"/messages/{msg_id}/reactions",
            params={"emoji": "👍"},
            headers=auth_headers,
        )
        # Second add = toggle remove
        resp = client.post(
            f"/messages/{msg_id}/reactions",
            params={"emoji": "👍"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_remove_reaction(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Explicitly removing a reaction via DELETE should work."""
        send_resp = _send_message(client, second_user.id, auth_headers, "remove react")
        msg_id = send_resp.json()["id"]

        # Add reaction first
        client.post(
            f"/messages/{msg_id}/reactions",
            params={"emoji": "🔥"},
            headers=second_auth_headers,
        )

        # Remove it
        del_resp = client.delete(
            f"/messages/{msg_id}/reactions/🔥",
            headers=second_auth_headers,
        )
        assert del_resp.status_code == 200


# ────────────────────────────────────────────
# Chat Listing
# ────────────────────────────────────────────


class TestChatListing:
    def test_chat_created_after_message(
        self, client, auth_headers, test_user, second_user
    ):
        """A chat should appear in the list after a message is sent."""
        _send_message(client, second_user.id, auth_headers, "creates chat")

        resp = client.get("/chats/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        # The other user should be shown as the chat partner
        assert data[0]["user"]["id"] == second_user.id

    def test_chat_last_message_updated(
        self, client, auth_headers, test_user, second_user
    ):
        """The chat's last_message should reflect the most recent message."""
        _send_message(client, second_user.id, auth_headers, "first")
        _send_message(client, second_user.id, auth_headers, "second")

        resp = client.get("/chats/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()[0]["last_message"] == "second"

    def test_chat_unread_count(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Unread count should reflect messages not yet marked as read."""
        _send_message(client, second_user.id, auth_headers, "unread1")
        _send_message(client, second_user.id, auth_headers, "unread2")

        resp = client.get("/chats/", headers=second_auth_headers)
        assert resp.status_code == 200
        chat = resp.json()[0]
        assert chat["unread_count"] == 2

    def test_delete_chat(
        self, client, auth_headers, test_user, second_user
    ):
        """Deleting a chat should hide it from the user's list."""
        send_resp = _send_message(client, second_user.id, auth_headers, "delete chat")
        chat_id = send_resp.json()["chat_id"]

        del_resp = client.delete(f"/chats/{chat_id}", headers=auth_headers)
        assert del_resp.status_code == 200

        chats = client.get("/chats/", headers=auth_headers)
        chat_ids = [c["id"] for c in chats.json()]
        assert chat_id not in chat_ids

    def test_deleted_chat_visible_to_other(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """When one user deletes a chat, the other should still see it."""
        send_resp = _send_message(client, second_user.id, auth_headers, "still there")
        chat_id = send_resp.json()["chat_id"]

        client.delete(f"/chats/{chat_id}", headers=auth_headers)

        chats = client.get("/chats/", headers=second_auth_headers)
        chat_ids = [c["id"] for c in chats.json()]
        assert chat_id in chat_ids

    def test_chat_reappears_after_new_message(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """A deleted chat should reappear when a new message is sent."""
        send_resp = _send_message(client, second_user.id, auth_headers, "before delete")
        chat_id = send_resp.json()["chat_id"]

        client.delete(f"/chats/{chat_id}", headers=auth_headers)

        # Second user sends a new message — chat should reappear for first user
        _send_message(client, test_user.id, second_auth_headers, "comeback")

        chats = client.get("/chats/", headers=auth_headers)
        chat_ids = [c["id"] for c in chats.json()]
        assert chat_id in chat_ids

    def test_list_chats_no_auth(self, client):
        """Listing chats without auth should fail."""
        resp = client.get("/chats/")
        assert resp.status_code == 401

    def test_mark_chat_read(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """POST /chats/{chat_id}/read should mark all unread messages as read."""
        send_resp = _send_message(client, second_user.id, auth_headers, "chat read")
        chat_id = send_resp.json()["chat_id"]

        read_resp = client.post(
            f"/chats/{chat_id}/read", headers=second_auth_headers
        )
        assert read_resp.status_code == 200

        # Unread count should now be 0
        chats = client.get("/chats/", headers=second_auth_headers)
        chat = next(c for c in chats.json() if c["id"] == chat_id)
        assert chat["unread_count"] == 0


# ────────────────────────────────────────────
# Mute / Unmute Chat
# ────────────────────────────────────────────


class TestChatMute:
    def test_mute_chat_indefinitely(
        self, client, auth_headers, test_user, second_user
    ):
        """Muting without a duration should mute indefinitely."""
        send_resp = _send_message(client, second_user.id, auth_headers, "mute test")
        chat_id = send_resp.json()["chat_id"]

        mute_resp = client.post(f"/chats/{chat_id}/mute", headers=auth_headers)
        assert mute_resp.status_code == 200
        assert mute_resp.json()["is_muted"] is True

    def test_mute_chat_with_duration(
        self, client, auth_headers, test_user, second_user
    ):
        """Muting with a duration should set muted_until."""
        send_resp = _send_message(client, second_user.id, auth_headers, "timed mute")
        chat_id = send_resp.json()["chat_id"]

        mute_resp = client.post(
            f"/chats/{chat_id}/mute",
            params={"duration": 3600},
            headers=auth_headers,
        )
        assert mute_resp.status_code == 200
        assert mute_resp.json()["is_muted"] is True

    def test_get_mute_status(
        self, client, auth_headers, test_user, second_user
    ):
        """GET mute status should reflect current state."""
        send_resp = _send_message(client, second_user.id, auth_headers, "status check")
        chat_id = send_resp.json()["chat_id"]

        # Not muted initially
        status = client.get(f"/chats/{chat_id}/mute", headers=auth_headers)
        assert status.json()["is_muted"] is False

        # Mute
        client.post(f"/chats/{chat_id}/mute", headers=auth_headers)

        # Now muted
        status = client.get(f"/chats/{chat_id}/mute", headers=auth_headers)
        assert status.json()["is_muted"] is True

    def test_unmute_chat(
        self, client, auth_headers, test_user, second_user
    ):
        """Unmuting a muted chat should clear the mute."""
        send_resp = _send_message(client, second_user.id, auth_headers, "unmute test")
        chat_id = send_resp.json()["chat_id"]

        client.post(f"/chats/{chat_id}/mute", headers=auth_headers)
        unmute_resp = client.delete(f"/chats/{chat_id}/mute", headers=auth_headers)
        assert unmute_resp.status_code == 200
        assert unmute_resp.json()["is_muted"] is False

        status = client.get(f"/chats/{chat_id}/mute", headers=auth_headers)
        assert status.json()["is_muted"] is False

    def test_mute_nonexistent_chat(self, client, auth_headers):
        """Muting a chat that doesn't exist should 404."""
        resp = client.post("/chats/fake-chat-id/mute", headers=auth_headers)
        assert resp.status_code == 404

    def test_chat_list_shows_muted_flag(
        self, client, auth_headers, test_user, second_user
    ):
        """The is_muted flag should be true in the chat list after muting."""
        send_resp = _send_message(client, second_user.id, auth_headers, "mute flag")
        chat_id = send_resp.json()["chat_id"]

        client.post(f"/chats/{chat_id}/mute", headers=auth_headers)

        chats = client.get("/chats/", headers=auth_headers)
        chat = next(c for c in chats.json() if c["id"] == chat_id)
        assert chat["is_muted"] is True


# ────────────────────────────────────────────
# Idempotency
# ────────────────────────────────────────────


class TestIdempotency:
    def test_same_idempotency_key_returns_same_message(
        self, client, auth_headers, test_user, second_user
    ):
        """Sending with the same Idempotency-Key should return the same message ID."""
        headers = {**auth_headers, "Idempotency-Key": "unique-key-123"}

        resp1 = client.post(
            f"/messages/{second_user.id}/",
            json={"message": "idempotent"},
            headers=headers,
        )
        resp2 = client.post(
            f"/messages/{second_user.id}/",
            json={"message": "idempotent"},
            headers=headers,
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["id"] == resp2.json()["id"]

    def test_different_idempotency_keys_create_different_messages(
        self, client, auth_headers, test_user, second_user
    ):
        """Different Idempotency-Keys should create distinct messages."""
        h1 = {**auth_headers, "Idempotency-Key": "key-a"}
        h2 = {**auth_headers, "Idempotency-Key": "key-b"}

        resp1 = client.post(
            f"/messages/{second_user.id}/",
            json={"message": "msg-a"},
            headers=h1,
        )
        resp2 = client.post(
            f"/messages/{second_user.id}/",
            json={"message": "msg-b"},
            headers=h2,
        )
        assert resp1.json()["id"] != resp2.json()["id"]

    def test_no_idempotency_key_always_creates(
        self, client, auth_headers, test_user, second_user
    ):
        """Without an Idempotency-Key, every request creates a new message."""
        resp1 = _send_message(client, second_user.id, auth_headers, "dup1")
        resp2 = _send_message(client, second_user.id, auth_headers, "dup2")
        assert resp1.json()["id"] != resp2.json()["id"]


# ────────────────────────────────────────────
# MessageKey / E2E Encryption Blob Splitting
# ────────────────────────────────────────────

# Fake encrypted payload that looks like what clients send
_FAKE_BLOB = json.dumps({
    "iv": "dGVzdF9pdjEyMzQ1Ng==",
    "ciphertext": "ZW5jcnlwdGVkX2NvbnRlbnQ=",
    "keys": {
        "android_abc": "cmFuZG9tX2VuY3J5cHRlZF9rZXlfMQ==",
        "desktop_def": "cmFuZG9tX2VuY3J5cHRlZF9rZXlfMg==",
        "web_ghi": "cmFuZG9tX2VuY3J5cHRlZF9rZXlfMw==",
    }
})


class TestMessageKeyExtraction:
    """Tests for the E2E encryption blob splitting into MessageKey rows."""

    def test_encrypted_blob_keys_stripped_from_stored_message(
        self, client, auth_headers, second_user, session
    ):
        """When sending an encrypted blob, the stored message should have keys stripped."""
        resp = _send_message(client, second_user.id, auth_headers, _FAKE_BLOB)
        assert resp.status_code == 200
        data = resp.json()
        # The response should reconstruct the blob (but without x-device-id, keys may be empty)
        msg_obj = json.loads(data["message"])
        assert "iv" in msg_obj
        assert "ciphertext" in msg_obj

    def test_encrypted_blob_creates_messagekey_rows(
        self, client, auth_headers, second_user, session
    ):
        """Sending an encrypted blob should create MessageKey rows for each device."""
        from app.messages.models import MessageKey
        from sqlmodel import select

        resp = _send_message(client, second_user.id, auth_headers, _FAKE_BLOB)
        assert resp.status_code == 200
        msg_id = resp.json()["id"]

        keys = session.exec(
            select(MessageKey).where(MessageKey.message_id == msg_id)
        ).all()
        assert len(keys) == 3
        device_ids = {k.device_id for k in keys}
        assert device_ids == {"android_abc", "desktop_def", "web_ghi"}
        assert all(k.key_slot == "body" for k in keys)

    def test_per_device_retrieval_returns_only_own_key(
        self, client, auth_headers, test_user, second_user
    ):
        """GET messages with x-device-id header should return only that device's key."""
        _send_message(client, second_user.id, auth_headers, _FAKE_BLOB)

        headers = {**auth_headers, "x-device-id": "android_abc"}
        resp = client.get(f"/messages/{second_user.id}/", headers=headers)
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) >= 1
        msg = messages[0]
        msg_obj = json.loads(msg["message"])
        assert "keys" in msg_obj
        assert "android_abc" in msg_obj["keys"]
        # Only the requesting device's key should be present
        assert len(msg_obj["keys"]) == 1

    def test_per_device_retrieval_unknown_device_gets_empty_keys(
        self, client, auth_headers, test_user, second_user
    ):
        """GET messages with an unknown device_id should return empty keys map."""
        _send_message(client, second_user.id, auth_headers, _FAKE_BLOB)

        headers = {**auth_headers, "x-device-id": "unknown_device_999"}
        resp = client.get(f"/messages/{second_user.id}/", headers=headers)
        assert resp.status_code == 200
        msg = resp.json()[0]
        msg_obj = json.loads(msg["message"])
        assert msg_obj["keys"] == {}

    def test_plain_text_not_affected(
        self, client, auth_headers, second_user
    ):
        """Plain text messages should pass through unchanged."""
        resp = _send_message(client, second_user.id, auth_headers, "just plain text")
        assert resp.status_code == 200
        assert resp.json()["message"] == "just plain text"

    def test_chat_last_message_shows_preview_for_encrypted(
        self, client, auth_headers, test_user, second_user
    ):
        """Chat.last_message should be a human-readable preview, not the encrypted blob."""
        _send_message(client, second_user.id, auth_headers, _FAKE_BLOB)

        resp = client.get("/chats/", headers=auth_headers)
        assert resp.status_code == 200
        chats = resp.json()
        assert len(chats) >= 1
        # Should be a preview, not a JSON blob
        assert chats[0]["last_message"] == "Message"

    def test_chat_last_message_shows_text_for_plain(
        self, client, auth_headers, test_user, second_user
    ):
        """For unencrypted messages, Chat.last_message should be the actual text."""
        _send_message(client, second_user.id, auth_headers, "hello world")

        resp = client.get("/chats/", headers=auth_headers)
        chats = resp.json()
        assert chats[0]["last_message"] == "hello world"

    def test_caption_blob_keys_extracted(
        self, client, auth_headers, second_user, session
    ):
        """Encrypted caption blobs should also have keys extracted to MessageKey."""
        from app.messages.models import MessageKey
        from sqlmodel import select

        caption_blob = json.dumps({
            "iv": "Y2FwdGlvbl9pdg==",
            "ciphertext": "Y2FwdGlvbl9jaXBoZXJ0ZXh0",
            "keys": {"device_1": "a2V5MQ==", "device_2": "a2V5Mg=="}
        })
        resp = client.post(
            f"/messages/{second_user.id}/",
            json={"message": None, "caption": caption_blob, "media_url": "https://example.com/img.jpg", "media_type": "image"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        msg_id = resp.json()["id"]

        caption_keys = session.exec(
            select(MessageKey).where(
                MessageKey.message_id == msg_id,
                MessageKey.key_slot == "caption",
            )
        ).all()
        assert len(caption_keys) == 2
