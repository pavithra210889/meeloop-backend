"""
Tests for the scheduled calls endpoints.

Covers: CRUD operations, authorization, validation, filtering, and status transitions.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock

from app.scheduled_calls.models import ScheduledCall, ScheduledCallStatus


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────

def _future_time(minutes=30):
    """Return an ISO datetime string N minutes in the future."""
    return (datetime.now() + timedelta(minutes=minutes)).isoformat()


def _create_scheduled_call(session, *, scheduler_id, participant_id,
                           minutes_from_now=30, is_video=False, note=None,
                           status=ScheduledCallStatus.PENDING):
    """Insert a ScheduledCall record and return it."""
    sc = ScheduledCall(
        scheduler_id=scheduler_id,
        participant_id=participant_id,
        scheduled_at=datetime.now() + timedelta(minutes=minutes_from_now),
        is_video_call=is_video,
        note=note,
        status=status,
    )
    session.add(sc)
    session.commit()
    session.refresh(sc)
    return sc


# ────────────────────────────────────────────
# Authentication
# ────────────────────────────────────────────

class TestScheduledCallsAuth:
    def test_unauthenticated_create_returns_401(self, client):
        resp = client.post("/scheduled-calls/", json={
            "participant_id": "fake-id",
            "scheduled_at": _future_time(),
        })
        assert resp.status_code == 401

    def test_unauthenticated_list_returns_401(self, client):
        resp = client.get("/scheduled-calls/")
        assert resp.status_code == 401


# ────────────────────────────────────────────
# Create
# ────────────────────────────────────────────

class TestCreateScheduledCall:
    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_create_success(self, mock_emit, mock_notif, client, auth_headers,
                            test_user, second_user):
        resp = client.post("/scheduled-calls/", headers=auth_headers, json={
            "participant_id": second_user.id,
            "scheduled_at": _future_time(60),
            "is_video_call": True,
            "note": "Let's catch up",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["scheduler"]["id"] == test_user.id
        assert data["participant"]["id"] == second_user.id
        assert data["is_video_call"] is True
        assert data["note"] == "Let's catch up"
        assert data["status"] == "pending"

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_create_rejects_past_time(self, mock_emit, mock_notif, client,
                                      auth_headers, second_user):
        resp = client.post("/scheduled-calls/", headers=auth_headers, json={
            "participant_id": second_user.id,
            "scheduled_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        })
        assert resp.status_code == 400
        assert "5 minutes" in resp.json()["detail"]

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_create_rejects_too_soon(self, mock_emit, mock_notif, client,
                                     auth_headers, second_user):
        resp = client.post("/scheduled-calls/", headers=auth_headers, json={
            "participant_id": second_user.id,
            "scheduled_at": (datetime.now() + timedelta(minutes=2)).isoformat(),
        })
        assert resp.status_code == 400

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_create_rejects_self_call(self, mock_emit, mock_notif, client,
                                      auth_headers, test_user):
        resp = client.post("/scheduled-calls/", headers=auth_headers, json={
            "participant_id": test_user.id,
            "scheduled_at": _future_time(),
        })
        assert resp.status_code == 400
        assert "yourself" in resp.json()["detail"]

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_create_rejects_nonexistent_participant(self, mock_emit, mock_notif,
                                                     client, auth_headers):
        resp = client.post("/scheduled-calls/", headers=auth_headers, json={
            "participant_id": "nonexistent-user-id",
            "scheduled_at": _future_time(),
        })
        assert resp.status_code == 404


# ────────────────────────────────────────────
# List
# ────────────────────────────────────────────

class TestListScheduledCalls:
    def test_empty_list(self, client, auth_headers, test_user):
        resp = client.get("/scheduled-calls/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_as_scheduler(self, client, auth_headers, test_user,
                               second_user, session):
        _create_scheduled_call(session, scheduler_id=test_user.id,
                               participant_id=second_user.id)
        resp = client.get("/scheduled-calls/", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_as_participant(self, client, auth_headers, test_user,
                                 second_user, session):
        _create_scheduled_call(session, scheduler_id=second_user.id,
                               participant_id=test_user.id)
        resp = client.get("/scheduled-calls/", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_filter_by_status(self, client, auth_headers, test_user,
                              second_user, session):
        _create_scheduled_call(session, scheduler_id=test_user.id,
                               participant_id=second_user.id,
                               status=ScheduledCallStatus.PENDING)
        _create_scheduled_call(session, scheduler_id=test_user.id,
                               participant_id=second_user.id,
                               status=ScheduledCallStatus.CANCELLED)
        resp = client.get("/scheduled-calls/?status=pending", headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "pending"

    def test_filter_by_user_id(self, client, auth_headers, test_user,
                               second_user, session):
        from app.users.models import User
        from app.security import get_password_hash
        third = User(
            name="Third User", username="thirduser",
            email="third@meeloop.com",
            password=get_password_hash("testpassword123"),
            is_active=True, is_verified=True,
        )
        session.add(third)
        session.commit()
        session.refresh(third)

        _create_scheduled_call(session, scheduler_id=test_user.id,
                               participant_id=second_user.id)
        _create_scheduled_call(session, scheduler_id=test_user.id,
                               participant_id=third.id)

        resp = client.get(f"/scheduled-calls/?with_user_id={second_user.id}",
                          headers=auth_headers)
        assert len(resp.json()) == 1

    def test_unrelated_calls_excluded(self, client, auth_headers, test_user,
                                      second_user, session):
        from app.users.models import User
        from app.security import get_password_hash
        third = User(
            name="Third User", username="thirduser",
            email="third@meeloop.com",
            password=get_password_hash("testpassword123"),
            is_active=True, is_verified=True,
        )
        session.add(third)
        session.commit()
        session.refresh(third)

        _create_scheduled_call(session, scheduler_id=second_user.id,
                               participant_id=third.id)
        resp = client.get("/scheduled-calls/", headers=auth_headers)
        assert resp.json() == []


# ────────────────────────────────────────────
# Get single
# ────────────────────────────────────────────

class TestGetScheduledCall:
    def test_get_as_scheduler(self, client, auth_headers, test_user,
                              second_user, session):
        sc = _create_scheduled_call(session, scheduler_id=test_user.id,
                                    participant_id=second_user.id)
        resp = client.get(f"/scheduled-calls/{sc.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == sc.id

    def test_get_as_participant(self, client, auth_headers, test_user,
                                second_user, session):
        sc = _create_scheduled_call(session, scheduler_id=second_user.id,
                                    participant_id=test_user.id)
        resp = client.get(f"/scheduled-calls/{sc.id}", headers=auth_headers)
        assert resp.status_code == 200

    def test_get_unauthorized(self, client, auth_headers, test_user,
                              second_user, session):
        from app.users.models import User
        from app.security import get_password_hash
        third = User(
            name="Third User", username="thirduser",
            email="third@meeloop.com",
            password=get_password_hash("testpassword123"),
            is_active=True, is_verified=True,
        )
        session.add(third)
        session.commit()
        session.refresh(third)

        sc = _create_scheduled_call(session, scheduler_id=second_user.id,
                                    participant_id=third.id)
        resp = client.get(f"/scheduled-calls/{sc.id}", headers=auth_headers)
        assert resp.status_code == 403

    def test_get_not_found(self, client, auth_headers):
        resp = client.get("/scheduled-calls/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404


# ────────────────────────────────────────────
# Update (reschedule)
# ────────────────────────────────────────────

class TestUpdateScheduledCall:
    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_reschedule_success(self, mock_emit, mock_notif, client, auth_headers,
                                test_user, second_user, session):
        sc = _create_scheduled_call(session, scheduler_id=test_user.id,
                                    participant_id=second_user.id)
        new_time = _future_time(120)
        resp = client.patch(f"/scheduled-calls/{sc.id}", headers=auth_headers,
                            json={"scheduled_at": new_time})
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_only_scheduler_can_update(self, mock_emit, mock_notif, client,
                                       second_auth_headers, test_user,
                                       second_user, session):
        sc = _create_scheduled_call(session, scheduler_id=test_user.id,
                                    participant_id=second_user.id)
        resp = client.patch(f"/scheduled-calls/{sc.id}", headers=second_auth_headers,
                            json={"scheduled_at": _future_time(120)})
        assert resp.status_code == 403

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_cannot_update_cancelled(self, mock_emit, mock_notif, client,
                                     auth_headers, test_user, second_user, session):
        sc = _create_scheduled_call(session, scheduler_id=test_user.id,
                                    participant_id=second_user.id,
                                    status=ScheduledCallStatus.CANCELLED)
        resp = client.patch(f"/scheduled-calls/{sc.id}", headers=auth_headers,
                            json={"scheduled_at": _future_time(120)})
        assert resp.status_code == 400

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_reschedule_resets_status(self, mock_emit, mock_notif, client,
                                     auth_headers, test_user, second_user, session):
        sc = _create_scheduled_call(session, scheduler_id=test_user.id,
                                    participant_id=second_user.id,
                                    status=ScheduledCallStatus.REMINDED)
        resp = client.patch(f"/scheduled-calls/{sc.id}", headers=auth_headers,
                            json={"scheduled_at": _future_time(120)})
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"


# ────────────────────────────────────────────
# Cancel
# ────────────────────────────────────────────

class TestCancelScheduledCall:
    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_scheduler_can_cancel(self, mock_emit, mock_notif, client, auth_headers,
                                  test_user, second_user, session):
        sc = _create_scheduled_call(session, scheduler_id=test_user.id,
                                    participant_id=second_user.id)
        resp = client.delete(f"/scheduled-calls/{sc.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert "cancelled" in resp.json()["detail"].lower()

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_participant_can_cancel(self, mock_emit, mock_notif, client,
                                    second_auth_headers, test_user, second_user,
                                    session):
        sc = _create_scheduled_call(session, scheduler_id=test_user.id,
                                    participant_id=second_user.id)
        resp = client.delete(f"/scheduled-calls/{sc.id}", headers=second_auth_headers)
        assert resp.status_code == 200

    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_cannot_cancel_already_cancelled(self, mock_emit, mock_notif, client,
                                              auth_headers, test_user, second_user,
                                              session):
        sc = _create_scheduled_call(session, scheduler_id=test_user.id,
                                    participant_id=second_user.id,
                                    status=ScheduledCallStatus.CANCELLED)
        resp = client.delete(f"/scheduled-calls/{sc.id}", headers=auth_headers)
        assert resp.status_code == 400

    def test_unauthorized_cancel(self, client, auth_headers, test_user,
                                 second_user, session):
        from app.users.models import User
        from app.security import get_password_hash
        third = User(
            name="Third User", username="thirduser",
            email="third@meeloop.com",
            password=get_password_hash("testpassword123"),
            is_active=True, is_verified=True,
        )
        session.add(third)
        session.commit()
        session.refresh(third)

        sc = _create_scheduled_call(session, scheduler_id=second_user.id,
                                    participant_id=third.id)
        resp = client.delete(f"/scheduled-calls/{sc.id}", headers=auth_headers)
        assert resp.status_code == 403


# ────────────────────────────────────────────
# Response fields
# ────────────────────────────────────────────

class TestScheduledCallResponseFields:
    @patch("app.scheduled_calls.routers.notification_service.create_notification", new_callable=AsyncMock)
    @patch("app.scheduled_calls.routers.sio.emit", new_callable=AsyncMock)
    def test_response_includes_all_fields(self, mock_emit, mock_notif, client,
                                           auth_headers, test_user, second_user):
        resp = client.post("/scheduled-calls/", headers=auth_headers, json={
            "participant_id": second_user.id,
            "scheduled_at": _future_time(60),
            "is_video_call": True,
            "note": "Test note",
        })
        data = resp.json()
        assert "id" in data
        assert "scheduler" in data
        assert "participant" in data
        assert data["scheduler"]["username"] == "testuser"
        assert data["participant"]["username"] == "seconduser"
        assert data["is_video_call"] is True
        assert data["note"] == "Test note"
        assert data["status"] == "pending"
        assert data["call_id"] is None
        assert "created_at" in data
        assert "updated_at" in data
        assert "scheduled_at" in data
