"""
Integration tests for auth and user endpoints.

Covers login, profile CRUD, password change, search, follow/unfollow,
block/unblock, and loop toggle.
"""

import pytest
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class TestLogin:
    """POST /login/ and POST /login-user/ flows."""

    def test_login_valid_credentials(self, client, test_user):
        resp = client.post(
            "/login/",
            data={"username": "testuser", "password": "testpassword123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_login_wrong_password(self, client, test_user):
        resp = client.post(
            "/login/",
            data={"username": "testuser", "password": "wrongpassword"},
        )
        assert resp.status_code == 401
        assert "Incorrect username or password" in resp.json()["detail"]

    def test_login_nonexistent_user(self, client):
        resp = client.post(
            "/login/",
            data={"username": "nosuchuser", "password": "whatever123"},
        )
        assert resp.status_code == 401

    def test_login_with_email(self, client, test_user):
        """Login using email instead of username should also work."""
        resp = client.post(
            "/login/",
            data={"username": "test@meeloop.com", "password": "testpassword123"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login_user_endpoint(self, client, test_user):
        """POST /login-user/ returns enriched LoginResponse."""
        resp = client.post(
            "/login-user/",
            data={"username": "testuser", "password": "testpassword123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "testuser"
        assert "access_token" in body

    def test_login_user_wrong_password(self, client, test_user):
        resp = client.post(
            "/login-user/",
            data={"username": "testuser", "password": "wrong"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

class TestUserProfile:
    """GET /me/, PUT /me/, GET /user/{user_id}."""

    def test_get_me_authenticated(self, client, auth_headers, test_user):
        resp = client.get("/me/", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "testuser"
        assert body["email"] == "test@meeloop.com"
        # Password must never be returned
        assert "password" not in body or body.get("password") is None

    def test_get_me_unauthenticated(self, client):
        resp = client.get("/me/")
        assert resp.status_code in (401, 403)

    def test_update_name(self, client, auth_headers):
        resp = client.put("/me/", json={"name": "New Name"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    def test_update_bio(self, client, auth_headers):
        resp = client.put("/me/", json={"bio": "Hello world"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["bio"] == "Hello world"

    def test_partial_update_preserves_other_fields(self, client, auth_headers):
        """Updating only bio should not change the name."""
        client.put("/me/", json={"name": "Keep Me"}, headers=auth_headers)
        resp = client.put("/me/", json={"bio": "new bio"}, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Keep Me"
        assert body["bio"] == "new bio"

    def test_update_username(self, client, auth_headers):
        resp = client.put("/me/", json={"username": "newusername"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["username"] == "newusername"

    def test_update_username_taken(self, client, auth_headers, second_user):
        """Cannot change to a username that already exists."""
        resp = client.put(
            "/me/", json={"username": "seconduser"}, headers=auth_headers
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"].lower()

    def test_get_user_by_id(self, client, test_user):
        resp = client.get(f"/user/{test_user.id}")
        assert resp.status_code == 200
        assert resp.json()["username"] == "testuser"

    def test_get_user_by_id_not_found(self, client):
        resp = client.get("/user/nonexistent-uuid")
        # Endpoint returns None -> 200 with null body is typical for this endpoint
        assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------

class TestPasswordChange:
    """POST /me/password."""

    def test_change_password_success(self, client, auth_headers):
        resp = client.post(
            "/me/password",
            json={
                "old_password": "testpassword123",
                "new_password": "newpass456",
                "confirm_password": "newpass456",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Password updated successfully"

        # Verify new password works for login
        login_resp = client.post(
            "/login/",
            data={"username": "testuser", "password": "newpass456"},
        )
        assert login_resp.status_code == 200

    def test_change_password_wrong_old(self, client, auth_headers):
        resp = client.post(
            "/me/password",
            json={
                "old_password": "wrongold",
                "new_password": "newpass456",
                "confirm_password": "newpass456",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "incorrect old password" in resp.json()["detail"].lower()

    def test_change_password_mismatch_confirm(self, client, auth_headers):
        resp = client.post(
            "/me/password",
            json={
                "old_password": "testpassword123",
                "new_password": "newpass456",
                "confirm_password": "different789",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "do not match" in resp.json()["detail"].lower()

    def test_change_password_unauthenticated(self, client):
        resp = client.post(
            "/me/password",
            json={
                "old_password": "x",
                "new_password": "newpass456",
                "confirm_password": "newpass456",
            },
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestUserSearch:
    """GET /search/?q=..."""

    def test_search_finds_user(self, client, auth_headers, second_user):
        resp = client.get("/search/?q=second", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        usernames = [u["username"] for u in data["items"]]
        assert "seconduser" in usernames

    def test_search_excludes_self(self, client, auth_headers, test_user):
        resp = client.get("/search/?q=test", headers=auth_headers)
        assert resp.status_code == 200
        ids = [u["id"] for u in resp.json()["items"]]
        assert test_user.id not in ids

    def test_search_no_results(self, client, auth_headers):
        resp = client.get("/search/?q=zzzznonexistent", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_search_unauthenticated(self, client, second_user):
        resp = client.get("/search/?q=second")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        usernames = [u["username"] for u in data["items"]]
        assert "seconduser" in usernames
        assert all(u["is_following"] is False for u in data["items"])


# ---------------------------------------------------------------------------
# Follow / Unfollow
# ---------------------------------------------------------------------------

class TestFollows:
    """POST /user/{id}/follow and /user/{id}/unfollow."""

    def test_follow_user(self, client, auth_headers, second_user):
        resp = client.post(f"/user/{second_user.id}/follow", headers=auth_headers)
        assert resp.status_code == 200
        assert "followed" in resp.json()["detail"].lower()

    def test_follow_already_following(self, client, auth_headers, second_user):
        client.post(f"/user/{second_user.id}/follow", headers=auth_headers)
        resp = client.post(f"/user/{second_user.id}/follow", headers=auth_headers)
        assert resp.status_code == 400
        assert "already following" in resp.json()["detail"].lower()

    def test_follow_self(self, client, auth_headers, test_user):
        resp = client.post(f"/user/{test_user.id}/follow", headers=auth_headers)
        assert resp.status_code == 400

    def test_follow_nonexistent_user(self, client, auth_headers):
        resp = client.post("/user/nonexistent-uuid/follow", headers=auth_headers)
        assert resp.status_code == 404

    def test_unfollow_user(self, client, auth_headers, second_user):
        client.post(f"/user/{second_user.id}/follow", headers=auth_headers)
        resp = client.post(f"/user/{second_user.id}/unfollow", headers=auth_headers)
        assert resp.status_code == 200
        assert "unfollowed" in resp.json()["detail"].lower()

    def test_unfollow_not_following(self, client, auth_headers, second_user):
        resp = client.post(f"/user/{second_user.id}/unfollow", headers=auth_headers)
        assert resp.status_code == 400

    def test_follow_unauthenticated(self, client, second_user):
        resp = client.post(f"/user/{second_user.id}/follow")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Block / Unblock
# ---------------------------------------------------------------------------

class TestBlocks:
    """POST /users/{id}/block, DELETE /users/{id}/block, GET /users/blocked."""

    def test_block_user(self, client, auth_headers, second_user):
        resp = client.post(f"/users/{second_user.id}/block", headers=auth_headers)
        assert resp.status_code == 200
        assert "blocked" in resp.json()["detail"].lower()

    def test_block_self(self, client, auth_headers, test_user):
        resp = client.post(f"/users/{test_user.id}/block", headers=auth_headers)
        assert resp.status_code == 400

    def test_block_already_blocked(self, client, auth_headers, second_user):
        client.post(f"/users/{second_user.id}/block", headers=auth_headers)
        resp = client.post(f"/users/{second_user.id}/block", headers=auth_headers)
        assert resp.status_code == 200
        assert "already blocked" in resp.json()["detail"].lower()

    def test_unblock_user(self, client, auth_headers, second_user):
        client.post(f"/users/{second_user.id}/block", headers=auth_headers)
        resp = client.delete(f"/users/{second_user.id}/block", headers=auth_headers)
        assert resp.status_code == 200
        assert "unblocked" in resp.json()["detail"].lower()

    def test_unblock_not_blocked(self, client, auth_headers, second_user):
        resp = client.delete(f"/users/{second_user.id}/block", headers=auth_headers)
        assert resp.status_code == 200
        assert "not blocked" in resp.json()["detail"].lower()

    def test_list_blocked_users(self, client, auth_headers, second_user):
        client.post(f"/users/{second_user.id}/block", headers=auth_headers)
        resp = client.get("/users/blocked", headers=auth_headers)
        assert resp.status_code == 200
        ids = [u["id"] for u in resp.json()["items"]]
        assert second_user.id in ids

    def test_list_blocked_empty(self, client, auth_headers):
        resp = client.get("/users/blocked", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_follow_blocked_user_rejected(self, client, auth_headers, second_user):
        """Following someone you blocked (or who blocked you) should fail."""
        client.post(f"/users/{second_user.id}/block", headers=auth_headers)
        resp = client.post(f"/user/{second_user.id}/follow", headers=auth_headers)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Loop toggle
# ---------------------------------------------------------------------------

class TestLoopToggle:
    """POST /user/enable-loop/ and /user/disable-loop/."""

    def test_enable_loop_without_dob(self, client, auth_headers):
        """Should fail if date_of_birth is not set."""
        # The test_user fixture does not set date_of_birth, so enabling should fail
        resp = client.post("/user/enable-loop/", headers=auth_headers)
        assert resp.status_code == 400
        assert "date of birth" in resp.json()["detail"].lower()

    def test_enable_loop_underage(self, client, auth_headers):
        """Under-18 user should be rejected."""
        # Set DOB to 10 years ago
        ten_years_ago = (datetime.now(timezone.utc) - timedelta(days=365 * 10)).isoformat()
        client.put("/me/", json={"date_of_birth": ten_years_ago}, headers=auth_headers)
        resp = client.post("/user/enable-loop/", headers=auth_headers)
        assert resp.status_code == 403
        assert "18+" in resp.json()["detail"]

    def test_enable_loop_adult(self, client, auth_headers):
        """Adult user can enable loop."""
        twenty_years_ago = (datetime.now(timezone.utc) - timedelta(days=365 * 25)).isoformat()
        client.put("/me/", json={"date_of_birth": twenty_years_ago}, headers=auth_headers)
        resp = client.post("/user/enable-loop/", headers=auth_headers)
        assert resp.status_code == 200
        assert "enabled" in resp.json()["detail"].lower()

    def test_disable_loop(self, client, auth_headers):
        """Disable loop should succeed regardless."""
        resp = client.post("/user/disable-loop/", headers=auth_headers)
        assert resp.status_code == 200
        assert "disabled" in resp.json()["detail"].lower()

    def test_disable_loop_reflects_in_profile(self, client, auth_headers):
        """After disabling loop, /me/ should show is_loop_enabled=False."""
        client.post("/user/disable-loop/", headers=auth_headers)
        resp = client.get("/me/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["is_loop_enabled"] is False

    def test_enable_loop_unauthenticated(self, client):
        resp = client.post("/user/enable-loop/")
        assert resp.status_code in (401, 403)
