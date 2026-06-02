"""Tests for OAuth login flows: Google, Facebook, Truecaller mobile endpoints."""

from unittest.mock import patch, AsyncMock

from app.users.models import User
from app.security import get_password_hash


_GOOGLE_TOKEN_INFO = {
    "sub": "google-user-123",
    "email": "googleuser@gmail.com",
    "name": "Google User",
    "picture": None,
}

_FACEBOOK_TOKEN_INFO = {
    "id": "facebook-user-456",
    "email": "fbuser@gmail.com",
    "name": "Facebook User",
    "picture": None,
}

_TC_VERIFICATION = {"truecaller_id": "tc-789", "phone_number": "+919876543210", "name": "TC User"}
_TC_USER_INFO = {"truecaller_id": "tc-789", "phone_number": "+919876543210", "name": "TC User", "email": None}


# ─────────────────────────────────────────────
# Google
# ─────────────────────────────────────────────

class TestGoogleMobileAuth:
    def test_new_user_created_on_first_login(self, client):
        """First login with a valid Google token creates account and returns session."""
        with patch("app.users.routers.google_auth_service.verify_id_token", new_callable=AsyncMock, return_value=_GOOGLE_TOKEN_INFO), \
             patch("app.users.routers.r2_service.upload_from_url", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/google/mobile/", json={"id_token": "valid-google-id-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "username" in data

    def test_returning_google_user_gets_new_session(self, client, session):
        """Returning Google user (same google_id) receives a fresh session."""
        user = User(
            name="Google User",
            username="googleuser",
            email="googleuser@gmail.com",
            google_id="google-user-123",
            auth_provider="google",
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        session.commit()

        with patch("app.users.routers.google_auth_service.verify_id_token", new_callable=AsyncMock, return_value=_GOOGLE_TOKEN_INFO), \
             patch("app.users.routers.r2_service.upload_from_url", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/google/mobile/", json={"id_token": "valid-google-id-token"})
        assert resp.status_code == 200

    def test_existing_email_links_google_id(self, client, test_user):
        """If email already exists (password user), the Google ID gets linked."""
        token_info = {**_GOOGLE_TOKEN_INFO, "email": test_user.email}
        with patch("app.users.routers.google_auth_service.verify_id_token", new_callable=AsyncMock, return_value=token_info), \
             patch("app.users.routers.r2_service.upload_from_url", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/google/mobile/", json={"id_token": "valid-google-id-token"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_invalid_token_returns_401(self, client):
        """Invalid/expired Google ID token returns 401."""
        with patch("app.users.routers.google_auth_service.verify_id_token", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/google/mobile/", json={"id_token": "bad-token"})
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# Facebook
# ─────────────────────────────────────────────

class TestFacebookMobileAuth:
    def test_new_user_created_on_first_login(self, client):
        """First Facebook login creates a new user account."""
        with patch("app.users.routers.facebook_auth_service.verify_access_token", new_callable=AsyncMock, return_value=_FACEBOOK_TOKEN_INFO), \
             patch("app.users.routers.r2_service.upload_from_url", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/facebook/mobile/", json={"access_token": "valid-fb-token"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_returning_facebook_user_gets_session(self, client, session):
        """Returning Facebook user (same facebook_id) gets a fresh session."""
        user = User(
            name="FB User",
            username="fbuser",
            email="fbuser@gmail.com",
            facebook_id="facebook-user-456",
            auth_provider="facebook",
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        session.commit()

        with patch("app.users.routers.facebook_auth_service.verify_access_token", new_callable=AsyncMock, return_value=_FACEBOOK_TOKEN_INFO), \
             patch("app.users.routers.r2_service.upload_from_url", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/facebook/mobile/", json={"access_token": "valid-fb-token"})
        assert resp.status_code == 200

    def test_invalid_token_returns_401(self, client):
        """Invalid Facebook access token returns 401."""
        with patch("app.users.routers.facebook_auth_service.verify_access_token", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/facebook/mobile/", json={"access_token": "bad-token"})
        assert resp.status_code == 401

    def test_connect_facebook_to_existing_account(self, client, auth_headers, test_user):
        """Logged-in user can link their Facebook account."""
        fb_info = {**_FACEBOOK_TOKEN_INFO, "email": test_user.email}
        with patch("app.users.routers.facebook_auth_service.verify_access_token", new_callable=AsyncMock, return_value=fb_info), \
             patch("app.users.routers.r2_service.upload_from_url", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/facebook/connect/", json={"access_token": "valid-fb-token"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json().get("connected") is True

    def test_connect_facebook_requires_auth(self, client):
        resp = client.post("/auth/facebook/connect/", json={"access_token": "token"})
        assert resp.status_code in (401, 403)


# ─────────────────────────────────────────────
# Truecaller
# ─────────────────────────────────────────────

class TestTruecallerMobileAuth:
    def test_new_user_created_on_first_login(self, client):
        """First Truecaller login creates a user account from phone info."""
        with patch("app.users.routers.truecaller_auth_service.verify_request_id", new_callable=AsyncMock, return_value=_TC_VERIFICATION), \
             patch("app.users.routers.truecaller_auth_service.extract_user_info", return_value=_TC_USER_INFO):
            resp = client.post("/auth/truecaller/mobile/", json={
                "request_id": "req-123",
                "access_token": "tc-token",
            })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_returning_truecaller_user_gets_session(self, client, session):
        """Returning Truecaller user (same truecaller_id) receives a fresh session."""
        user = User(
            name="TC User",
            username="tcuser99",
            email="tc99@truecaller.temp",
            truecaller_id="tc-789",
            auth_provider="truecaller",
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        session.commit()

        with patch("app.users.routers.truecaller_auth_service.verify_request_id", new_callable=AsyncMock, return_value=_TC_VERIFICATION), \
             patch("app.users.routers.truecaller_auth_service.extract_user_info", return_value=_TC_USER_INFO):
            resp = client.post("/auth/truecaller/mobile/", json={
                "request_id": "req-123",
                "access_token": "tc-token",
            })
        assert resp.status_code == 200

    def test_invalid_token_returns_401(self, client):
        """Invalid Truecaller token returns 401."""
        with patch("app.users.routers.truecaller_auth_service.verify_request_id", new_callable=AsyncMock, return_value=None):
            resp = client.post("/auth/truecaller/mobile/", json={
                "request_id": "req-bad",
                "access_token": "bad-token",
            })
        assert resp.status_code == 401
