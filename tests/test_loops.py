"""
Tests for the Loops mini-app endpoints.

Covers: profile CRUD, photo management, nearby discovery, loop requests, friends.
"""

import pytest
from datetime import datetime, timedelta, timezone


# ────────────────────────────────────────────
# Profile Setup
# ────────────────────────────────────────────


class TestLoopProfileSetup:
    def test_setup_profile_success(self, client, auth_headers, test_user):
        """Creating a loop profile should return the profile with photos."""
        response = client.post(
            "/loops/profile/setup",
            json={
                "displayname": "My Loop Name",
                "bio": "Hello from loops",
                "gender": "male",
                "photo_urls": [
                    "https://example.com/pic1.jpg",
                    "https://example.com/pic2.jpg",
                ],
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["displayname"] == "My Loop Name"
        assert data["bio"] == "Hello from loops"
        assert len(data["photos"]) == 2
        assert data["photos"][0]["is_primary"] is True
        assert data["photos"][1]["is_primary"] is False

    def test_setup_profile_duplicate(self, client, auth_headers, loop_profile):
        """Setting up a profile when one already exists should fail."""
        response = client.post(
            "/loops/profile/setup",
            json={"displayname": "Duplicate"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_setup_profile_no_auth(self, client):
        """Unauthenticated request should be rejected."""
        response = client.post(
            "/loops/profile/setup",
            json={"displayname": "NoAuth"},
        )
        assert response.status_code == 401

    def test_setup_profile_max_photos(self, client, auth_headers, test_user):
        """Setup with more than 8 photos should only keep 8."""
        urls = [f"https://example.com/pic{i}.jpg" for i in range(10)]
        response = client.post(
            "/loops/profile/setup",
            json={"displayname": "Max Photos", "photo_urls": urls},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()["photos"]) == 8


# ────────────────────────────────────────────
# Profile Read / Update
# ────────────────────────────────────────────


class TestLoopProfileReadUpdate:
    def test_get_my_profile(self, client, auth_headers, loop_profile, loop_photos):
        """GET /loops/profile/me should return the profile with photos."""
        response = client.get("/loops/profile/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["displayname"] == loop_profile.displayname
        assert len(data["photos"]) == 3

    def test_get_my_profile_no_profile(self, client, auth_headers, test_user):
        """GET /loops/profile/me without a profile should 404."""
        # test_user has no loop_profile fixture here
        response = client.get("/loops/profile/me", headers=auth_headers)
        assert response.status_code == 404

    def test_update_profile(self, client, auth_headers, loop_profile):
        """PUT /loops/profile/me should update displayname and bio."""
        response = client.put(
            "/loops/profile/me",
            params={"displayname": "Updated Name", "bio": "Updated bio"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["displayname"] == "Updated Name"
        assert data["bio"] == "Updated bio"

    def test_update_profile_partial(self, client, auth_headers, loop_profile):
        """Updating only bio should leave displayname unchanged."""
        response = client.put(
            "/loops/profile/me",
            params={"bio": "New bio only"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["displayname"] == loop_profile.displayname
        assert data["bio"] == "New bio only"

    def test_get_profile_by_id(self, client, loop_profile, loop_photos):
        """GET /loops/profile/{id} should return a public profile."""
        response = client.get(f"/loops/profile/{loop_profile.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["displayname"] == loop_profile.displayname
        assert len(data["photos"]) == 3

    def test_get_profile_by_id_not_found(self, client):
        """GET /loops/profile/{id} with invalid ID should 404."""
        response = client.get("/loops/profile/nonexistent-id")
        assert response.status_code == 404

    def test_route_me_vs_id_no_conflict(self, client, auth_headers, loop_profile):
        """/profile/me should NOT be caught by /profile/{profile_id}."""
        response = client.get("/loops/profile/me", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["displayname"] == loop_profile.displayname


# ────────────────────────────────────────────
# Profile Photos
# ────────────────────────────────────────────


class TestLoopProfilePhotos:
    def test_add_photo(self, client, auth_headers, loop_profile):
        """Adding a photo should return the new photo object."""
        response = client.post(
            "/loops/profile/photos",
            json={
                "photo_url": "https://example.com/new.jpg",
                "order": 0,
                "is_primary": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["photo_url"] == "https://example.com/new.jpg"
        assert data["is_primary"] is True

    def test_add_photo_max_limit(self, client, auth_headers, loop_profile, session):
        """Adding more than 8 photos should fail."""
        from app.loops.models import LoopProfilePhoto

        for i in range(8):
            photo = LoopProfilePhoto(
                loop_profile_id=loop_profile.id,
                photo_url=f"https://example.com/fill{i}.jpg",
                order=i,
                is_primary=(i == 0),
            )
            session.add(photo)
        session.commit()

        response = client.post(
            "/loops/profile/photos",
            json={"photo_url": "https://example.com/ninth.jpg"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "Maximum" in response.json()["detail"]

    def test_delete_photo(self, client, auth_headers, loop_profile, loop_photos):
        """Deleting a photo should succeed and return detail message."""
        photo_id = loop_photos[1].id
        response = client.delete(
            f"/loops/profile/photos/{photo_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert "deleted" in response.json()["detail"].lower()

    def test_delete_primary_photo_reassigns(
        self, client, auth_headers, loop_profile, loop_photos
    ):
        """Deleting the primary photo should promote the next one."""
        primary_id = loop_photos[0].id
        response = client.delete(
            f"/loops/profile/photos/{primary_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Check that profile still has a primary
        profile_response = client.get("/loops/profile/me", headers=auth_headers)
        photos = profile_response.json()["photos"]
        primaries = [p for p in photos if p["is_primary"]]
        assert len(primaries) == 1

    def test_delete_photo_not_found(self, client, auth_headers, loop_profile):
        """Deleting a non-existent photo should 404."""
        response = client.delete(
            "/loops/profile/photos/nonexistent-id",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_delete_photo_wrong_user(
        self, client, second_auth_headers, loop_profile, loop_photos
    ):
        """A user shouldn't be able to delete another user's photo."""
        # second_auth_headers belongs to second_user who has no loop profile
        # so this should fail (404 for profile or photo)
        response = client.delete(
            f"/loops/profile/photos/{loop_photos[0].id}",
            headers=second_auth_headers,
        )
        assert response.status_code in (404, 403)

    def test_reorder_photos(self, client, auth_headers, loop_profile, loop_photos):
        """Reordering should change photo order and update primary."""
        reversed_ids = [p.id for p in reversed(loop_photos)]
        response = client.put(
            "/loops/profile/photos/reorder",
            json={"photo_ids": reversed_ids},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Verify new order
        profile_response = client.get("/loops/profile/me", headers=auth_headers)
        photos = profile_response.json()["photos"]
        assert photos[0]["id"] == reversed_ids[0]
        assert photos[0]["is_primary"] is True

    def test_set_primary_photo(self, client, auth_headers, loop_profile, loop_photos):
        """Setting a non-primary photo as primary should work."""
        non_primary = loop_photos[2]
        assert non_primary.is_primary is False

        response = client.put(
            f"/loops/profile/photos/{non_primary.id}/set-primary",
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Verify it's now primary
        profile_response = client.get("/loops/profile/me", headers=auth_headers)
        photos = profile_response.json()["photos"]
        primaries = [p for p in photos if p["is_primary"]]
        assert len(primaries) == 1
        assert primaries[0]["id"] == non_primary.id

    def test_set_primary_photo_not_found(self, client, auth_headers, loop_profile):
        """Setting primary on non-existent photo should 404."""
        response = client.put(
            "/loops/profile/photos/nonexistent-id/set-primary",
            headers=auth_headers,
        )
        assert response.status_code == 404


# ────────────────────────────────────────────
# Nearby Discovery
# ────────────────────────────────────────────


class TestNearbyDiscovery:
    def test_nearby_requires_location(
        self, client, auth_headers, loop_profile
    ):
        """Nearby should return 400 if user has no location set."""
        response = client.get("/loops/nearby", headers=auth_headers)
        assert response.status_code == 400
        assert "location" in response.json()["detail"].lower()

    def test_nearby_no_auth(self, client):
        """Nearby without auth should fail."""
        response = client.get("/loops/nearby")
        assert response.status_code == 401


# ────────────────────────────────────────────
# Loop Requests
# ────────────────────────────────────────────


class TestLoopRequests:
    def test_send_loop_request(
        self, client, auth_headers, loop_profile, second_loop_profile
    ):
        """Sending a loop request to another user should succeed."""
        response = client.post(
            "/loops/request",
            params={"receiver_id": second_loop_profile.id},
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_get_received_requests(
        self, client, auth_headers, second_auth_headers,
        loop_profile, second_loop_profile
    ):
        """After sending a request, the receiver should see it."""
        # Send request from user1 to user2
        client.post(
            "/loops/request",
            params={"receiver_id": second_loop_profile.id},
            headers=auth_headers,
        )
        # Check user2's received requests
        response = client.get("/loops/requests", headers=second_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 1
        assert data["items"][0]["requester_profile"]["id"] == loop_profile.id

    def test_get_sent_requests(
        self, client, auth_headers, loop_profile, second_loop_profile
    ):
        """The sender should see their sent requests."""
        client.post(
            "/loops/request",
            params={"receiver_id": second_loop_profile.id},
            headers=auth_headers,
        )
        response = client.get("/loops/requests/sent", headers=auth_headers)
        assert response.status_code == 200
        assert len(response.json()["items"]) >= 1

    def test_accept_request_creates_friendship(
        self, client, auth_headers, second_auth_headers,
        loop_profile, second_loop_profile
    ):
        """Accepting a request should create a friendship."""
        # Send
        client.post(
            "/loops/request",
            params={"receiver_id": second_loop_profile.id},
            headers=auth_headers,
        )
        # Get the request ID
        requests_resp = client.get("/loops/requests", headers=second_auth_headers)
        request_id = requests_resp.json()["items"][0]["id"]

        # Accept
        response = client.post(
            f"/loops/request/{request_id}",
            params={"action": "accepted"},
            headers=second_auth_headers,
        )
        assert response.status_code == 200

        # The acceptor (second_user) should see the requester as a friend
        friends_resp = client.get("/loops/friends", headers=second_auth_headers)
        assert friends_resp.status_code == 200
        friend_ids = [f["id"] for f in friends_resp.json()["items"]]
        assert loop_profile.id in friend_ids

        # The requester (first_user) should also see the acceptor as a friend
        requester_friends_resp = client.get("/loops/friends", headers=auth_headers)
        assert requester_friends_resp.status_code == 200
        requester_friend_ids = [f["id"] for f in requester_friends_resp.json()["items"]]
        assert second_loop_profile.id in requester_friend_ids

    def test_reject_request(
        self, client, auth_headers, second_auth_headers,
        loop_profile, second_loop_profile
    ):
        """Rejecting a request should not create a friendship."""
        client.post(
            "/loops/request",
            params={"receiver_id": second_loop_profile.id},
            headers=auth_headers,
        )
        requests_resp = client.get("/loops/requests", headers=second_auth_headers)
        request_id = requests_resp.json()["items"][0]["id"]

        response = client.post(
            f"/loops/request/{request_id}",
            params={"action": "rejected"},
            headers=second_auth_headers,
        )
        assert response.status_code == 200

        friends_resp = client.get("/loops/friends", headers=auth_headers)
        assert len(friends_resp.json()["items"]) == 0

    def test_delete_sent_request(
        self, client, auth_headers, loop_profile, second_loop_profile
    ):
        """The sender should be able to delete their own request."""
        client.post(
            "/loops/request",
            params={"receiver_id": second_loop_profile.id},
            headers=auth_headers,
        )
        sent = client.get("/loops/requests/sent", headers=auth_headers)
        request_id = sent.json()["items"][0]["id"]

        response = client.delete(
            f"/loops/request/{request_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Should be gone
        sent_after = client.get("/loops/requests/sent", headers=auth_headers)
        assert len(sent_after.json()["items"]) == 0


# ────────────────────────────────────────────
# Auth Edge Cases
# ────────────────────────────────────────────


class TestAuthEdgeCases:
    def test_expired_session_rejected(self, client, test_user, session):
        """An expired session token should be rejected."""
        from app.users.models import UserSession
        import secrets

        token = secrets.token_urlsafe(32)
        expired_session = UserSession(
            user_id=test_user.id,
            session_token=token,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            is_active=True,
        )
        session.add(expired_session)
        session.commit()

        response = client.get(
            "/loops/profile/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401

    def test_invalid_token_rejected(self, client):
        """A garbage token should be rejected."""
        response = client.get(
            "/loops/profile/me",
            headers={"Authorization": "Bearer garbage_token_here"},
        )
        assert response.status_code == 401


class TestLoopChatPagination:
    """Compound-cursor pagination for GET /loops/chats."""

    def _create_chats(self, session, my_profile, other_profiles, timestamps):
        """Insert LoopChat rows directly with controlled timestamps."""
        from app.loops.models import LoopChat
        chats = []
        for other, ts in zip(other_profiles, timestamps):
            chat = LoopChat(
                profile1_id=my_profile.id,
                profile2_id=other.id,
                last_message_at=ts,
                last_message_content="hi",
            )
            session.add(chat)
            chats.append(chat)
        session.commit()
        for c in chats:
            session.refresh(c)
        return chats

    def test_no_chats_skipped_with_identical_timestamps(
        self, client, auth_headers, session,
        loop_profile, second_loop_profile
    ):
        """Compound cursor must not drop chats that share a last_message_at timestamp."""
        from app.loops.models import LoopProfile

        # Create two extra loop profiles so we have 3 chats total
        extra1 = LoopProfile(user_id=loop_profile.user_id, displayname="Extra1")
        extra2 = LoopProfile(user_id=second_loop_profile.user_id, displayname="Extra2")

        # Use unique user accounts by creating throw-away profiles with distinct IDs
        # Instead, reuse second_loop_profile and create two bare LoopProfile rows
        from app.users.models import User
        from app.uuid_utils import generate_uuid
        from app.security import get_password_hash
        u1 = User(id=generate_uuid(), name="Pag1", username="pag_u1", email="pag1@test.com", password=get_password_hash("x"), is_active=True, is_loop_enabled=True)
        u2 = User(id=generate_uuid(), name="Pag2", username="pag_u2", email="pag2@test.com", password=get_password_hash("x"), is_active=True, is_loop_enabled=True)
        session.add_all([u1, u2])
        session.commit()
        p1 = LoopProfile(user_id=u1.id, displayname="PagP1")
        p2 = LoopProfile(user_id=u2.id, displayname="PagP2")
        session.add_all([p1, p2])
        session.commit()
        session.refresh(p1)
        session.refresh(p2)

        shared_ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._create_chats(
            session,
            loop_profile,
            [second_loop_profile, p1, p2],
            [shared_ts, shared_ts, shared_ts],  # all identical
        )

        # Page 1 — limit=2
        resp1 = client.get("/loops/chats?limit=2", headers=auth_headers)
        assert resp1.status_code == 200
        page1 = resp1.json()
        assert len(page1["items"]) == 2
        assert page1["has_more"] is True

        # Page 2 — cursor from last item of page 1
        last_id = page1["items"][-1]["id"]
        resp2 = client.get(f"/loops/chats?limit=2&before_id={last_id}", headers=auth_headers)
        assert resp2.status_code == 200
        page2 = resp2.json()
        assert len(page2["items"]) == 1

        # No chat should appear on both pages
        ids1 = {item["id"] for item in page1["items"]}
        ids2 = {item["id"] for item in page2["items"]}
        assert ids1.isdisjoint(ids2), "Duplicate chats across pages"
        assert len(ids1 | ids2) == 3, "One or more chats were skipped"

    def test_null_last_message_chats_appear_last(
        self, client, auth_headers, session, loop_profile, second_loop_profile
    ):
        """Chats with no messages (NULL last_message_at) must sort after all active chats."""
        from app.loops.models import LoopProfile
        from app.users.models import User
        from app.uuid_utils import generate_uuid
        from app.security import get_password_hash

        u_null = User(id=generate_uuid(), name="NullU", username="null_u", email="null@test.com", password=get_password_hash("x"), is_active=True, is_loop_enabled=True)
        session.add(u_null)
        session.commit()
        p_null = LoopProfile(user_id=u_null.id, displayname="NullChat")
        session.add(p_null)
        session.commit()
        session.refresh(p_null)

        active_ts = datetime(2025, 6, 1, tzinfo=timezone.utc)
        self._create_chats(
            session,
            loop_profile,
            [second_loop_profile, p_null],
            [active_ts, None],  # one active, one never messaged
        )

        resp = client.get("/loops/chats?limit=10", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        # The chat with a real timestamp must come first
        assert items[0]["last_message_at"] is not None
        assert items[1]["last_message_at"] is None
