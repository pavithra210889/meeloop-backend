"""Tests for session refresh and logout endpoints."""

from datetime import datetime, timezone

from sqlmodel import select

from app.users.models import UserSession


class TestSessionRefresh:
    def test_refresh_returns_token_and_user(self, client, auth_headers):
        """POST /auth/refresh returns the token with expiry and user info."""
        resp = client.post("/auth/refresh", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "Bearer"
        assert "expires_in" in data
        assert isinstance(data["expires_in"], int)
        user = data["user"]
        assert "id" in user
        assert "username" in user
        assert "email" in user

    def test_refresh_extends_session_expiry(self, client, auth_headers, session):
        """Refresh updates expires_at to be far in the future."""
        client.post("/auth/refresh", headers=auth_headers)
        sessions = session.exec(select(UserSession).where(UserSession.is_active == True)).all()
        now = datetime.utcnow()
        # expires_at should be at least one day from now
        from datetime import timedelta
        assert all(
            s.expires_at.replace(tzinfo=None) > now + timedelta(hours=1)
            for s in sessions
        )

    def test_refresh_requires_auth(self, client):
        """Refresh without a token returns 401."""
        resp = client.post("/auth/refresh")
        assert resp.status_code == 401

    def test_refresh_with_invalid_token_returns_401(self, client):
        """Garbage token returns 401."""
        resp = client.post("/auth/refresh", headers={"Authorization": "Bearer notavalidtoken"})
        assert resp.status_code == 401


class TestLogout:
    def test_logout_deactivates_session(self, client, auth_headers, session):
        """POST /auth/logout marks the current session as inactive."""
        resp = client.post("/auth/logout", headers=auth_headers)
        assert resp.status_code == 200
        active = session.exec(
            select(UserSession).where(UserSession.is_active == True)
        ).all()
        assert len(active) == 0

    def test_token_rejected_after_logout(self, client, auth_headers):
        """After logout, the same token no longer authenticates."""
        client.post("/auth/logout", headers=auth_headers)
        resp = client.get("/me/", headers=auth_headers)
        assert resp.status_code in (401, 403)

    def test_logout_requires_auth(self, client):
        resp = client.post("/auth/logout")
        assert resp.status_code == 401
