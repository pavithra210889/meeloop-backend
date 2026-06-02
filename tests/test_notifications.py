"""
Tests for the notifications endpoints.

Covers: listing notifications, unread count, mark as read, mark all as read,
delete notification, preferences (get/update), filtering, pagination, and auth.
"""

import pytest
from datetime import datetime, timedelta
from app.notifications.models import Notification, NotificationPreference


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────

def _make_notification(
    session,
    recipient_id,
    *,
    sender_id=None,
    notification_type="message",
    notification_category="messages",
    title="Test Notification",
    message="Test message",
    is_read=False,
    priority=0,
    created_at=None,
    deleted_at=None,
):
    """Insert a Notification row directly and return it."""
    notif = Notification(
        notification_type=notification_type,
        notification_category=notification_category,
        recipient_id=recipient_id,
        sender_id=sender_id,
        title=title,
        message=message,
        is_read=is_read,
        read_at=datetime.now() if is_read else None,
        priority=priority,
        meta={},
    )
    if created_at:
        notif.created_at = created_at
    if deleted_at:
        notif.deleted_at = deleted_at
    session.add(notif)
    session.commit()
    session.refresh(notif)
    return notif


# ────────────────────────────────────────────
# GET /notifications/
# ────────────────────────────────────────────


class TestGetNotifications:
    def test_empty_list(self, client, auth_headers, test_user):
        """Returns empty list when user has no notifications."""
        resp = client.get("/notifications/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_own_notifications(self, client, auth_headers, test_user, second_user, session):
        """Returns notifications belonging to the authenticated user."""
        n1 = _make_notification(session, test_user.id, sender_id=second_user.id, title="For me")
        _make_notification(session, second_user.id, title="Not for me")

        resp = client.get("/notifications/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == n1.id
        assert data[0]["title"] == "For me"

    def test_excludes_soft_deleted(self, client, auth_headers, test_user, session):
        """Soft-deleted notifications should not appear."""
        _make_notification(session, test_user.id, deleted_at=datetime.now())
        _make_notification(session, test_user.id, title="Visible")

        resp = client.get("/notifications/", headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Visible"

    def test_filter_by_category(self, client, auth_headers, test_user, session):
        """Filter notifications by category query param."""
        _make_notification(session, test_user.id, notification_category="messages", title="Msg")
        _make_notification(session, test_user.id, notification_category="calls", notification_type="missed_call", title="Call")

        resp = client.get("/notifications/?category=messages", headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Msg"

    def test_filter_by_is_read(self, client, auth_headers, test_user, session):
        """Filter notifications by read status."""
        _make_notification(session, test_user.id, is_read=False, title="Unread")
        _make_notification(session, test_user.id, is_read=True, title="Read")

        resp = client.get("/notifications/?is_read=false", headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Unread"

        resp = client.get("/notifications/?is_read=true", headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Read"

    def test_pagination_limit_offset(self, client, auth_headers, test_user, session):
        """Limit and offset work correctly."""
        base = datetime.now()
        for i in range(5):
            _make_notification(
                session, test_user.id,
                title=f"Notif {i}",
                created_at=base + timedelta(seconds=i),
            )

        # Default ordering is newest first
        resp = client.get("/notifications/?limit=2&offset=0", headers=auth_headers)
        data = resp.json()
        assert len(data) == 2
        assert data[0]["title"] == "Notif 4"

        resp = client.get("/notifications/?limit=2&offset=2", headers=auth_headers)
        data = resp.json()
        assert len(data) == 2
        assert data[0]["title"] == "Notif 2"

    def test_ordered_by_created_at_desc(self, client, auth_headers, test_user, session):
        """Notifications are returned newest-first."""
        base = datetime.now()
        _make_notification(session, test_user.id, title="Old", created_at=base - timedelta(hours=1))
        _make_notification(session, test_user.id, title="New", created_at=base)

        resp = client.get("/notifications/", headers=auth_headers)
        data = resp.json()
        assert data[0]["title"] == "New"
        assert data[1]["title"] == "Old"


# ────────────────────────────────────────────
# GET /notifications/unread-count/
# ────────────────────────────────────────────


class TestUnreadCount:
    def test_zero_when_empty(self, client, auth_headers, test_user):
        resp = client.get("/notifications/unread-count/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_counts_only_unread(self, client, auth_headers, test_user, session):
        _make_notification(session, test_user.id, is_read=False)
        _make_notification(session, test_user.id, is_read=False)
        _make_notification(session, test_user.id, is_read=True)

        resp = client.get("/notifications/unread-count/", headers=auth_headers)
        assert resp.json()["count"] == 2

    def test_excludes_deleted(self, client, auth_headers, test_user, session):
        _make_notification(session, test_user.id, is_read=False, deleted_at=datetime.now())
        _make_notification(session, test_user.id, is_read=False)

        resp = client.get("/notifications/unread-count/", headers=auth_headers)
        assert resp.json()["count"] == 1

    def test_filter_by_category(self, client, auth_headers, test_user, session):
        _make_notification(session, test_user.id, notification_category="messages")
        _make_notification(session, test_user.id, notification_category="calls", notification_type="missed_call")

        resp = client.get("/notifications/unread-count/?category=messages", headers=auth_headers)
        assert resp.json()["count"] == 1

    def test_counts_only_own(self, client, auth_headers, test_user, second_user, session):
        """Should not count notifications for other users."""
        _make_notification(session, test_user.id)
        _make_notification(session, second_user.id)

        resp = client.get("/notifications/unread-count/", headers=auth_headers)
        assert resp.json()["count"] == 1


# ────────────────────────────────────────────
# POST /notifications/{id}/read/
# ────────────────────────────────────────────


class TestMarkAsRead:
    def test_mark_as_read_success(self, client, auth_headers, test_user, session):
        notif = _make_notification(session, test_user.id)
        assert notif.is_read is False

        resp = client.post(f"/notifications/{notif.id}/read/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_read"] is True
        assert data["read_at"] is not None

    def test_already_read_is_idempotent(self, client, auth_headers, test_user, session):
        notif = _make_notification(session, test_user.id, is_read=True)

        resp = client.post(f"/notifications/{notif.id}/read/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["is_read"] is True

    def test_not_found(self, client, auth_headers, test_user):
        resp = client.post("/notifications/nonexistent-id/read/", headers=auth_headers)
        assert resp.status_code == 404

    def test_wrong_user(self, client, second_auth_headers, test_user, second_user, session):
        """Cannot mark another user's notification as read."""
        notif = _make_notification(session, test_user.id)

        resp = client.post(f"/notifications/{notif.id}/read/", headers=second_auth_headers)
        assert resp.status_code == 404


# ────────────────────────────────────────────
# POST /notifications/read-all/
# ────────────────────────────────────────────


class TestMarkAllAsRead:
    def test_marks_all_unread(self, client, auth_headers, test_user, session):
        _make_notification(session, test_user.id, is_read=False)
        _make_notification(session, test_user.id, is_read=False)
        _make_notification(session, test_user.id, is_read=True)

        resp = client.post("/notifications/read-all/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["marked_count"] == 2

    def test_marks_only_given_category(self, client, auth_headers, test_user, session):
        _make_notification(session, test_user.id, notification_category="messages")
        _make_notification(session, test_user.id, notification_category="calls", notification_type="missed_call")

        resp = client.post("/notifications/read-all/?category=messages", headers=auth_headers)
        assert resp.json()["marked_count"] == 1

        # The calls notification should still be unread
        resp = client.get("/notifications/unread-count/?category=calls", headers=auth_headers)
        assert resp.json()["count"] == 1

    def test_does_not_touch_other_users(self, client, auth_headers, test_user, second_user, session):
        _make_notification(session, test_user.id)
        _make_notification(session, second_user.id)

        resp = client.post("/notifications/read-all/", headers=auth_headers)
        assert resp.json()["marked_count"] == 1

    def test_zero_when_all_already_read(self, client, auth_headers, test_user, session):
        _make_notification(session, test_user.id, is_read=True)

        resp = client.post("/notifications/read-all/", headers=auth_headers)
        assert resp.json()["marked_count"] == 0

    def test_excludes_soft_deleted(self, client, auth_headers, test_user, session):
        _make_notification(session, test_user.id, is_read=False, deleted_at=datetime.now())
        _make_notification(session, test_user.id, is_read=False)

        resp = client.post("/notifications/read-all/", headers=auth_headers)
        assert resp.json()["marked_count"] == 1


# ────────────────────────────────────────────
# DELETE /notifications/{id}/
# ────────────────────────────────────────────


class TestDeleteNotification:
    def test_soft_delete_success(self, client, auth_headers, test_user, session):
        notif = _make_notification(session, test_user.id)

        resp = client.delete(f"/notifications/{notif.id}/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Notification deleted"

        # Should no longer appear in listing
        resp = client.get("/notifications/", headers=auth_headers)
        assert len(resp.json()) == 0

    def test_not_found(self, client, auth_headers, test_user):
        resp = client.delete("/notifications/nonexistent-id/", headers=auth_headers)
        assert resp.status_code == 404

    def test_wrong_user(self, client, second_auth_headers, test_user, second_user, session):
        """Cannot delete another user's notification."""
        notif = _make_notification(session, test_user.id)

        resp = client.delete(f"/notifications/{notif.id}/", headers=second_auth_headers)
        assert resp.status_code == 404


# ────────────────────────────────────────────
# GET /notifications/preferences/
# ────────────────────────────────────────────


class TestGetPreferences:
    def test_creates_default_if_not_exists(self, client, auth_headers, test_user):
        """First call should auto-create default preferences."""
        resp = client.get("/notifications/preferences/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == test_user.id
        assert data["notifications_enabled"] is True
        assert data["messages_enabled"] is True
        assert data["calls_enabled"] is True
        assert data["quiet_hours_enabled"] is False
        assert data["quiet_hours_start"] is None

    def test_returns_existing(self, client, auth_headers, test_user, session):
        """If preferences exist, return them without creating a new row."""
        pref = NotificationPreference(
            user_id=test_user.id,
            notifications_enabled=False,
        )
        session.add(pref)
        session.commit()
        session.refresh(pref)

        resp = client.get("/notifications/preferences/", headers=auth_headers)
        data = resp.json()
        assert data["id"] == pref.id
        assert data["notifications_enabled"] is False

    def test_idempotent(self, client, auth_headers, test_user):
        """Calling twice should return the same preferences object."""
        resp1 = client.get("/notifications/preferences/", headers=auth_headers)
        resp2 = client.get("/notifications/preferences/", headers=auth_headers)
        assert resp1.json()["id"] == resp2.json()["id"]


# ────────────────────────────────────────────
# PUT /notifications/preferences/
# ────────────────────────────────────────────


class TestUpdatePreferences:
    def test_partial_update(self, client, auth_headers, test_user):
        """Only provided fields should change."""
        # Ensure defaults exist
        client.get("/notifications/preferences/", headers=auth_headers)

        resp = client.put(
            "/notifications/preferences/",
            headers=auth_headers,
            json={"messages_enabled": False, "quiet_hours_enabled": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages_enabled"] is False
        assert data["quiet_hours_enabled"] is True
        # Other fields unchanged
        assert data["calls_enabled"] is True
        assert data["notifications_enabled"] is True

    def test_update_quiet_hours(self, client, auth_headers, test_user):
        resp = client.put(
            "/notifications/preferences/",
            headers=auth_headers,
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": "22:00",
                "quiet_hours_end": "07:00",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quiet_hours_enabled"] is True
        assert data["quiet_hours_start"] == "22:00"
        assert data["quiet_hours_end"] == "07:00"

    def test_disable_all_notifications(self, client, auth_headers, test_user):
        resp = client.put(
            "/notifications/preferences/",
            headers=auth_headers,
            json={"notifications_enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["notifications_enabled"] is False

    def test_creates_preferences_if_missing(self, client, auth_headers, test_user):
        """PUT should also auto-create preferences if they don't exist yet."""
        resp = client.put(
            "/notifications/preferences/",
            headers=auth_headers,
            json={"likes_enabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["likes_enabled"] is False
        assert data["notifications_enabled"] is True  # default


# ────────────────────────────────────────────
# Auth required (401)
# ────────────────────────────────────────────


class TestAuthRequired:
    """All notification endpoints require authentication."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/notifications/"),
            ("GET", "/notifications/unread-count/"),
            ("POST", "/notifications/some-id/read/"),
            ("POST", "/notifications/read-all/"),
            ("DELETE", "/notifications/some-id/"),
            ("GET", "/notifications/preferences/"),
            ("PUT", "/notifications/preferences/"),
        ],
    )
    def test_returns_401_without_token(self, client, method, path, test_user):
        resp = client.request(method, path)
        assert resp.status_code == 401
