"""Tests for the forgot-password / reset-password 3-step flow."""

from unittest.mock import patch, AsyncMock

from app.security import create_reset_token


# ─────────────────────────────────────────────
# Step 1 — Request OTP
# ─────────────────────────────────────────────

class TestForgotPasswordRequest:
    def test_sends_otp_for_existing_user(self, client, test_user):
        """OTP is generated and sent when the account exists."""
        with patch("app.users.routers.otp_service.create_otp", return_value="111111"), \
             patch("app.users.routers.email_service.send_otp", new_callable=AsyncMock, return_value=True):
            resp = client.post("/auth/forgot-password/request", json={
                "contact": test_user.email,
                "channel": "email",
            })
        assert resp.status_code == 200
        assert "sent" in resp.json()["message"].lower() or "OTP" in resp.json()["message"]

    def test_nonexistent_account_still_returns_200(self, client):
        """Non-existent contact returns 200 to prevent account enumeration."""
        resp = client.post("/auth/forgot-password/request", json={
            "contact": "ghost@meeloop.com",
            "channel": "email",
        })
        assert resp.status_code == 200

    def test_rate_limited_returns_429(self, client, test_user):
        """OTP service returning None/falsy means rate-limited → 429."""
        with patch("app.users.routers.otp_service.create_otp", return_value=None):
            resp = client.post("/auth/forgot-password/request", json={
                "contact": test_user.email,
                "channel": "email",
            })
        assert resp.status_code == 429

    def test_send_failure_returns_500(self, client, test_user):
        """OTP delivery failure returns 500."""
        with patch("app.users.routers.otp_service.create_otp", return_value="111111"), \
             patch("app.users.routers.email_service.send_otp", new_callable=AsyncMock, return_value=False):
            resp = client.post("/auth/forgot-password/request", json={
                "contact": test_user.email,
                "channel": "email",
            })
        assert resp.status_code == 500


# ─────────────────────────────────────────────
# Step 2 — Verify OTP
# ─────────────────────────────────────────────

class TestForgotPasswordVerify:
    def test_valid_otp_returns_reset_token(self, client, test_user):
        """Correct OTP returns a reset_token to use in the reset step."""
        with patch("app.users.routers.otp_service.verify_otp", return_value=True):
            resp = client.post("/auth/forgot-password/verify", json={
                "contact": test_user.email,
                "otp_code": "111111",
            })
        assert resp.status_code == 200
        assert "reset_token" in resp.json()

    def test_invalid_otp_returns_401(self, client, test_user):
        """Wrong or expired OTP returns 401."""
        with patch("app.users.routers.otp_service.verify_otp", return_value=False):
            resp = client.post("/auth/forgot-password/verify", json={
                "contact": test_user.email,
                "otp_code": "999999",
            })
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# Step 3 — Reset password
# ─────────────────────────────────────────────

class TestForgotPasswordReset:
    def test_reset_password_succeeds(self, client, test_user):
        """Valid reset token + matching passwords updates the password."""
        token = create_reset_token(test_user.email)
        resp = client.post("/auth/forgot-password/reset", json={
            "reset_token": token,
            "new_password": "brandnewpassword",
            "confirm_password": "brandnewpassword",
        })
        assert resp.status_code == 200
        assert "reset successfully" in resp.json()["message"]

    def test_passwords_mismatch_returns_400(self, client, test_user):
        """Passwords that don't match returns 400."""
        token = create_reset_token(test_user.email)
        resp = client.post("/auth/forgot-password/reset", json={
            "reset_token": token,
            "new_password": "password1",
            "confirm_password": "password2",
        })
        assert resp.status_code == 400

    def test_invalid_token_returns_401(self, client):
        """Expired or garbage reset token returns 401."""
        resp = client.post("/auth/forgot-password/reset", json={
            "reset_token": "badtoken",
            "new_password": "password123",
            "confirm_password": "password123",
        })
        assert resp.status_code == 401

    def test_new_password_works_for_login(self, client, test_user):
        """After reset the user can log in with the new password."""
        token = create_reset_token(test_user.email)
        client.post("/auth/forgot-password/reset", json={
            "reset_token": token,
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        })
        resp = client.post("/login/", data={
            "username": test_user.username,
            "password": "newpassword123",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_old_password_rejected_after_reset(self, client, test_user):
        """Old password no longer works after a reset."""
        token = create_reset_token(test_user.email)
        client.post("/auth/forgot-password/reset", json={
            "reset_token": token,
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        })
        resp = client.post("/login/", data={
            "username": test_user.username,
            "password": "testpassword123",
        })
        assert resp.status_code == 401
