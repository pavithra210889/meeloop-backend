"""
Tests for the calls endpoints.

Covers: call history retrieval, filtering by user, incoming/outgoing calls,
video call flag, ordering, and authentication requirements.
"""

import pytest
from datetime import datetime, timedelta

from app.calls.models import Call, CallStatus


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────

def _create_call(session, *, call_from, call_to, status=CallStatus.ENDED,
                 duration=60, is_video=False, minutes_ago=0):
    """Insert a Call record and return it."""
    call = Call(
        call_from=call_from,
        call_to=call_to,
        call_status=status,
        duration_seconds=duration,
        is_video_call=is_video,
        created_at=datetime.now() - timedelta(minutes=minutes_ago),
        updated_at=datetime.now() - timedelta(minutes=minutes_ago),
    )
    session.add(call)
    session.commit()
    session.refresh(call)
    return call


# ────────────────────────────────────────────
# Authentication
# ────────────────────────────────────────────

class TestCallsAuth:
    def test_unauthenticated_returns_401(self, client):
        """GET /calls/ without auth headers should return 401."""
        resp = client.get("/calls/")
        assert resp.status_code == 401


# ────────────────────────────────────────────
# Empty history
# ────────────────────────────────────────────

class TestEmptyCallHistory:
    def test_no_calls_returns_empty_list(self, client, auth_headers, test_user):
        """A user with no calls should get an empty list."""
        resp = client.get("/calls/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []


# ────────────────────────────────────────────
# Call history retrieval
# ────────────────────────────────────────────

class TestGetCallHistory:
    def test_outgoing_call_appears(self, client, auth_headers, test_user,
                                   second_user, session):
        """An outgoing call (call_from=current_user) should appear in history."""
        _create_call(session, call_from=test_user.id, call_to=second_user.id)
        resp = client.get("/calls/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["call_from"]["id"] == test_user.id
        assert data[0]["call_to"]["id"] == second_user.id

    def test_incoming_call_appears(self, client, auth_headers, test_user,
                                   second_user, session):
        """An incoming call (call_to=current_user) should appear in history."""
        _create_call(session, call_from=second_user.id, call_to=test_user.id)
        resp = client.get("/calls/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["call_from"]["id"] == second_user.id
        assert data[0]["call_to"]["id"] == test_user.id

    def test_both_directions_appear(self, client, auth_headers, test_user,
                                    second_user, session):
        """Both outgoing and incoming calls should appear in history."""
        _create_call(session, call_from=test_user.id, call_to=second_user.id,
                     minutes_ago=10)
        _create_call(session, call_from=second_user.id, call_to=test_user.id,
                     minutes_ago=5)
        resp = client.get("/calls/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_calls_ordered_by_created_at_desc(self, client, auth_headers,
                                               test_user, second_user, session):
        """Calls should be returned newest first."""
        old = _create_call(session, call_from=test_user.id,
                           call_to=second_user.id, minutes_ago=30)
        new = _create_call(session, call_from=second_user.id,
                           call_to=test_user.id, minutes_ago=1)
        resp = client.get("/calls/", headers=auth_headers)
        data = resp.json()
        assert data[0]["id"] == new.id
        assert data[1]["id"] == old.id

    def test_unrelated_calls_excluded(self, client, auth_headers, test_user,
                                      second_user, session):
        """Calls between other users should not appear in the current user's history."""
        # Create a third user directly
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

        # Call between second and third -- should not appear for test_user
        _create_call(session, call_from=second_user.id, call_to=third.id)
        # Call involving test_user -- should appear
        _create_call(session, call_from=test_user.id, call_to=second_user.id)

        resp = client.get("/calls/", headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["call_from"]["id"] == test_user.id


# ────────────────────────────────────────────
# Filter by user_id
# ────────────────────────────────────────────

class TestFilterByUserId:
    def test_filter_returns_only_calls_with_target(self, client, auth_headers,
                                                    test_user, second_user,
                                                    session):
        """Passing user_id should return only calls between current user and that user."""
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

        _create_call(session, call_from=test_user.id, call_to=second_user.id)
        _create_call(session, call_from=test_user.id, call_to=third.id)

        resp = client.get(f"/calls/?user_id={second_user.id}",
                          headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["call_to"]["id"] == second_user.id

    def test_filter_includes_both_directions(self, client, auth_headers,
                                              test_user, second_user, session):
        """Filter should include calls in both directions with the target user."""
        _create_call(session, call_from=test_user.id, call_to=second_user.id,
                     minutes_ago=10)
        _create_call(session, call_from=second_user.id, call_to=test_user.id,
                     minutes_ago=5)

        resp = client.get(f"/calls/?user_id={second_user.id}",
                          headers=auth_headers)
        data = resp.json()
        assert len(data) == 2

    def test_filter_nonexistent_user_returns_empty(self, client, auth_headers,
                                                    test_user, session):
        """Filtering by a non-existent user_id should return an empty list."""
        resp = client.get("/calls/?user_id=nonexistent-id",
                          headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []


# ────────────────────────────────────────────
# Response fields
# ────────────────────────────────────────────

class TestCallResponseFields:
    def test_response_includes_all_fields(self, client, auth_headers,
                                           test_user, second_user, session):
        """Response should contain all expected CallResponse fields."""
        call = _create_call(session, call_from=test_user.id,
                            call_to=second_user.id,
                            status=CallStatus.ANSWERED, duration=120)
        resp = client.get("/calls/", headers=auth_headers)
        data = resp.json()[0]
        assert data["id"] == call.id
        assert data["call_status"] == "answered"
        assert data["duration_seconds"] == 120
        assert data["is_video_call"] is False
        assert "created_at" in data
        assert "updated_at" in data
        # call_from / call_to are UserBasic objects
        assert data["call_from"]["username"] == "testuser"
        assert data["call_to"]["username"] == "seconduser"

    def test_video_call_flag(self, client, auth_headers, test_user,
                             second_user, session):
        """Video call flag should be correctly reflected in the response."""
        _create_call(session, call_from=test_user.id, call_to=second_user.id,
                     is_video=True)
        resp = client.get("/calls/", headers=auth_headers)
        data = resp.json()[0]
        assert data["is_video_call"] is True

    def test_missed_call_has_no_duration(self, client, auth_headers,
                                         test_user, second_user, session):
        """A missed call should have null duration."""
        _create_call(session, call_from=test_user.id, call_to=second_user.id,
                     status=CallStatus.MISSED, duration=None)
        resp = client.get("/calls/", headers=auth_headers)
        data = resp.json()[0]
        assert data["call_status"] == "missed"
        assert data["duration_seconds"] is None

    def test_call_statuses(self, client, auth_headers, test_user,
                           second_user, session):
        """All call status values should round-trip correctly."""
        for status in [CallStatus.MISSED, CallStatus.ANSWERED,
                       CallStatus.DECLINED, CallStatus.ONGOING,
                       CallStatus.ENDED]:
            _create_call(session, call_from=test_user.id,
                         call_to=second_user.id, status=status)

        resp = client.get("/calls/", headers=auth_headers)
        statuses = {c["call_status"] for c in resp.json()}
        assert statuses == {"missed", "answered", "declined", "ongoing", "ended"}


# ────────────────────────────────────────────
# Pagination
# ────────────────────────────────────────────

class TestPagination:
    def test_limit_restricts_results(self, client, auth_headers, test_user,
                                     second_user, session):
        """limit parameter should restrict the number of returned calls."""
        for i in range(5):
            _create_call(session, call_from=test_user.id, call_to=second_user.id,
                         minutes_ago=i)
        resp = client.get("/calls/?limit=3", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_before_id_cursor(self, client, auth_headers, test_user,
                              second_user, session):
        """before_id should return only calls with id < the given cursor."""
        calls = [
            _create_call(session, call_from=test_user.id, call_to=second_user.id,
                         minutes_ago=i)
            for i in range(4)
        ]
        # Sort by id descending to find the cursor (second newest)
        sorted_ids = sorted([c.id for c in calls], reverse=True)
        cursor = sorted_ids[1]  # second newest

        resp = client.get(f"/calls/?before_id={cursor}", headers=auth_headers)
        assert resp.status_code == 200
        returned_ids = [c["id"] for c in resp.json()]
        assert all(rid < cursor for rid in returned_ids)
        assert cursor not in returned_ids


# ────────────────────────────────────────────
# Delete single call
# ────────────────────────────────────────────

class TestDeleteSingleCall:
    def test_caller_can_delete(self, client, auth_headers, test_user,
                               second_user, session):
        """The caller (call_from) can delete their call record."""
        call = _create_call(session, call_from=test_user.id, call_to=second_user.id)
        resp = client.delete(f"/calls/{call.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert "deleted" in resp.json()["detail"].lower()

        resp2 = client.get("/calls/", headers=auth_headers)
        assert all(c["id"] != call.id for c in resp2.json())

    def test_callee_can_delete(self, client, auth_headers, test_user,
                               second_user, second_auth_headers, session):
        """The callee (call_to) can also delete the call record."""
        call = _create_call(session, call_from=second_user.id, call_to=test_user.id)
        resp = client.delete(f"/calls/{call.id}", headers=auth_headers)
        assert resp.status_code == 200

    def test_unrelated_user_gets_403(self, client, auth_headers, test_user,
                                     second_user, session):
        """A user not in the call cannot delete it."""
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

        call = _create_call(session, call_from=second_user.id, call_to=third.id)
        resp = client.delete(f"/calls/{call.id}", headers=auth_headers)
        assert resp.status_code == 403

    def test_nonexistent_call_returns_404(self, client, auth_headers):
        """Deleting a non-existent call id should return 404."""
        resp = client.delete("/calls/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404

    def test_requires_auth(self, client, test_user, second_user, session):
        """Delete without auth should return 401."""
        call = _create_call(session, call_from=test_user.id, call_to=second_user.id)
        resp = client.delete(f"/calls/{call.id}")
        assert resp.status_code == 401


# ────────────────────────────────────────────
# Clear call history
# ────────────────────────────────────────────

class TestClearCallHistory:
    def test_clear_all_history(self, client, auth_headers, test_user,
                               second_user, session):
        """DELETE /calls/ removes all calls for the current user."""
        _create_call(session, call_from=test_user.id, call_to=second_user.id)
        _create_call(session, call_from=second_user.id, call_to=test_user.id)

        resp = client.delete("/calls/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Deleted 2 call records"

        resp2 = client.get("/calls/", headers=auth_headers)
        assert resp2.json() == []

    def test_clear_history_scoped_to_user(self, client, auth_headers, test_user,
                                          second_user, session):
        """Clearing history does not affect calls between other users."""
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

        _create_call(session, call_from=test_user.id, call_to=second_user.id)
        other_call = _create_call(session, call_from=second_user.id, call_to=third.id)

        client.delete("/calls/", headers=auth_headers)

        # other_call (between second and third) must still exist
        from app.calls.models import Call
        still_exists = session.get(Call, other_call.id)
        assert still_exists is not None

    def test_clear_with_user_id_filter(self, client, auth_headers, test_user,
                                       second_user, session):
        """DELETE /calls/?user_id= removes only calls with that specific user."""
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

        _create_call(session, call_from=test_user.id, call_to=second_user.id)
        kept = _create_call(session, call_from=test_user.id, call_to=third.id)

        resp = client.delete(f"/calls/?user_id={second_user.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Deleted 1 call records"

        resp2 = client.get("/calls/", headers=auth_headers)
        ids = [c["id"] for c in resp2.json()]
        assert kept.id in ids

    def test_clear_empty_history_returns_zero(self, client, auth_headers):
        """Clearing when there are no calls should return 0 deleted."""
        resp = client.delete("/calls/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Deleted 0 call records"

    def test_requires_auth(self, client):
        """DELETE /calls/ without auth should return 401."""
        resp = client.delete("/calls/")
        assert resp.status_code == 401
