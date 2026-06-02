"""Tests for TURN credential generation endpoints."""

import base64
import hashlib
import hmac
import time
from unittest.mock import patch

import pytest


class TestTurnCredentials:
    """Tests for GET /turn/credentials"""

    def test_turn_credentials_requires_auth(self, client):
        """Unauthenticated requests should be rejected."""
        response = client.get("/turn/credentials")
        assert response.status_code in (401, 403)

    @patch("app.config.settings.TURN_SECRET", "test-secret-key")
    @patch("app.config.settings.TURN_SERVER", "1.2.3.4:3478")
    @patch("app.config.settings.TURN_TTL", 3600)
    def test_turn_credentials_returns_valid_hmac(
        self, client, auth_headers, test_user
    ):
        """Authenticated request should return valid HMAC credentials."""
        response = client.get("/turn/credentials", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert "username" in data
        assert "credential" in data
        assert "ttl" in data
        assert "uris" in data
        assert data["ttl"] == 3600

        # Username should be {expiry}:{user_id}
        parts = data["username"].split(":")
        assert len(parts) == 2
        expiry = int(parts[0])
        user_id = parts[1]
        assert user_id == test_user.id
        assert expiry > int(time.time())  # should be in the future

        # Verify HMAC-SHA1 credential
        expected_hmac = hmac.new(
            b"test-secret-key",
            data["username"].encode("utf-8"),
            hashlib.sha1,
        ).digest()
        expected_credential = base64.b64encode(expected_hmac).decode("utf-8")
        assert data["credential"] == expected_credential

        # URIs should contain turn and stun for the configured server
        assert any("turn:1.2.3.4:3478" in uri for uri in data["uris"])
        assert any("stun:1.2.3.4:3478" in uri for uri in data["uris"])
        assert any("transport=udp" in uri for uri in data["uris"])
        assert any("transport=tcp" in uri for uri in data["uris"])


class TestIceServers:
    """Tests for GET /webrtc/ice-servers (legacy format for mobile clients)"""

    def test_ice_servers_requires_auth(self, client):
        """Unauthenticated requests should be rejected."""
        response = client.get("/webrtc/ice-servers")
        assert response.status_code in (401, 403)

    @patch("app.config.settings.TURN_SECRET", "test-secret-key")
    @patch("app.config.settings.TURN_SERVER", "1.2.3.4:3478")
    @patch("app.config.settings.TURN_TTL", 3600)
    def test_ice_servers_returns_list(self, client, auth_headers, test_user):
        """Should return a list of IceServerDTO objects."""
        response = client.get("/webrtc/ice-servers", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 3  # stun + 2 turn (udp + tcp)

        # First should be STUN (no credentials)
        stun = [s for s in data if s["url"].startswith("stun:")]
        assert len(stun) == 1
        assert stun[0]["username"] is None
        assert stun[0]["credential"] is None

        # TURN entries should have HMAC credentials
        turns = [s for s in data if s["url"].startswith("turn:")]
        assert len(turns) == 2
        for turn in turns:
            assert turn["username"] is not None
            assert turn["credential"] is not None
            assert test_user.id in turn["username"]

            # Verify HMAC
            expected_hmac = hmac.new(
                b"test-secret-key",
                turn["username"].encode("utf-8"),
                hashlib.sha1,
            ).digest()
            expected_credential = base64.b64encode(expected_hmac).decode("utf-8")
            assert turn["credential"] == expected_credential
