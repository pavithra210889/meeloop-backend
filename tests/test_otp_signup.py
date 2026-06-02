"""Tests for the OTP-based signup flow: request-otp, verify-otp, complete."""

from unittest.mock import patch, AsyncMock

from app.security import create_registration_token


# ─────────────────────────────────────────────
# Request OTP
# ─────────────────────────────────────────────

class TestRequestOTP:
    def test_email_otp_sent_for_new_contact(self, client):
        """Valid new email triggers OTP creation and email delivery."""
        with patch("app.users.routers.otp_service.create_otp", return_value="123456"), \
             patch("app.users.routers.email_service.send_otp", new_callable=AsyncMock, return_value=True):
            resp = client.post("/signup/request-otp", json={
                "contact": "newuser@meeloop.com",
                "channel": "email",
            })
        assert resp.status_code == 200
        assert resp.json()["message"] == "OTP sent successfully"

    def test_sms_otp_sent_for_new_phone(self, client):
        """Valid new phone number triggers OTP creation and SMS delivery."""
        with patch("app.users.routers.otp_service.create_otp", return_value="654321"), \
             patch("app.users.routers.sms_service.send_otp", new_callable=AsyncMock, return_value=True):
            resp = client.post("/signup/request-otp", json={
                "contact": "+919876543210",
                "channel": "sms",
            })
        assert resp.status_code == 200

    def test_conflict_if_email_already_registered(self, client, test_user):
        """Returns 409 if the email already has an account."""
        resp = client.post("/signup/request-otp", json={
            "contact": test_user.email,
            "channel": "email",
        })
        assert resp.status_code == 409

    def test_rate_limited_returns_429(self, client):
        """Returns 429 when OTP service indicates rate limit."""
        with patch("app.users.routers.otp_service.create_otp", return_value="RATE_LIMITED"):
            resp = client.post("/signup/request-otp", json={
                "contact": "newuser@meeloop.com",
                "channel": "email",
            })
        assert resp.status_code == 429


# ─────────────────────────────────────────────
# Verify OTP
# ─────────────────────────────────────────────

class TestVerifyOTP:
    def test_valid_otp_returns_registration_token(self, client):
        """Valid OTP returns a registration_token for the complete step."""
        with patch("app.users.routers.otp_service.verify_otp", return_value=True):
            resp = client.post("/signup/verify-otp", json={
                "contact": "newuser@meeloop.com",
                "otp_code": "123456",
            })
        assert resp.status_code == 200
        assert "registration_token" in resp.json()

    def test_invalid_otp_returns_400(self, client):
        """Wrong or expired OTP returns 400."""
        with patch("app.users.routers.otp_service.verify_otp", return_value=False):
            resp = client.post("/signup/verify-otp", json={
                "contact": "newuser@meeloop.com",
                "otp_code": "000000",
            })
        assert resp.status_code == 400


# ─────────────────────────────────────────────
# Complete signup
# ─────────────────────────────────────────────

class TestCompleteSignup:
    def test_valid_token_creates_user_and_returns_session(self, client):
        """Valid registration token + profile data creates user and returns access_token."""
        token = create_registration_token("newuser@meeloop.com")
        resp = client.post("/signup/complete", json={
            "registration_token": token,
            "name": "New User",
            "username": "newuser123",
            "password": "securepassword",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["username"] == "newuser123"

    def test_invalid_token_returns_401(self, client):
        """Expired or garbage registration token returns 401."""
        resp = client.post("/signup/complete", json={
            "registration_token": "notavalidtoken",
            "name": "New User",
            "username": "newuser123",
            "password": "securepassword",
        })
        assert resp.status_code == 401

    def test_duplicate_username_returns_409(self, client, test_user):
        """Username already taken returns 409."""
        token = create_registration_token("another@meeloop.com")
        resp = client.post("/signup/complete", json={
            "registration_token": token,
            "name": "Another User",
            "username": test_user.username,
            "password": "securepassword",
        })
        assert resp.status_code == 409

    def test_new_user_can_log_in_after_signup(self, client):
        """User created via complete-signup can authenticate with their credentials."""
        token = create_registration_token("brand@meeloop.com")
        client.post("/signup/complete", json={
            "registration_token": token,
            "name": "Brand New",
            "username": "brandnewuser",
            "password": "mypassword123",
        })
        resp = client.post("/login/", data={
            "username": "brandnewuser",
            "password": "mypassword123",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()
