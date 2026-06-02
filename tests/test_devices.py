"""Tests for device registration, key management, and public key retrieval."""

from unittest.mock import patch, AsyncMock

from app.users.models import UserDevice, Block


# ─────────────────────────────────────────────
# Device key upload
# ─────────────────────────────────────────────

class TestDeviceKeyUpload:
    def test_upload_creates_new_device(self, client, auth_headers):
        """Uploading a key for an unknown device_id creates a new UserDevice entry."""
        resp = client.post(
            "/user/devices/keys",
            json={"public_key": "base64encodedpublickey=="},
            headers={**auth_headers, "x-device-id": "device-abc-123"},
        )
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Public key updated"

    def test_upload_updates_existing_device(self, client, auth_headers, test_user, session):
        """Re-uploading with the same device_id updates the key in place."""
        device = UserDevice(
            user_id=test_user.id,
            device_id="device-abc-123",
            public_key="oldkey==",
            is_active=True,
        )
        session.add(device)
        session.commit()

        resp = client.post(
            "/user/devices/keys",
            json={"public_key": "newkey=="},
            headers={**auth_headers, "x-device-id": "device-abc-123"},
        )
        assert resp.status_code == 200
        session.refresh(device)
        assert device.public_key == "newkey=="

    def test_missing_device_id_header_returns_400(self, client, auth_headers):
        """Missing x-device-id header should return 400."""
        resp = client.post(
            "/user/devices/keys",
            json={"public_key": "base64encodedpublickey=="},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_requires_auth(self, client):
        """Unauthenticated key upload returns 401."""
        resp = client.post(
            "/user/devices/keys",
            json={"public_key": "key=="},
            headers={"x-device-id": "device-abc"},
        )
        assert resp.status_code == 401

    def test_ghost_device_claimed_by_user_agent(self, client, auth_headers, test_user, session):
        """A ghost device (no device_id) is claimed when user-agent matches."""
        device = UserDevice(
            user_id=test_user.id,
            device_id=None,
            user_agent="TestAgent/1.0",
            is_active=False,
        )
        session.add(device)
        session.commit()
        session.refresh(device)

        resp = client.post(
            "/user/devices/keys",
            json={"public_key": "newkey=="},
            headers={
                **auth_headers,
                "x-device-id": "new-device-id",
                "user-agent": "TestAgent/1.0",
            },
        )
        assert resp.status_code == 200
        session.refresh(device)
        assert device.device_id == "new-device-id"
        assert device.public_key == "newkey=="
        assert device.is_active is True


# ─────────────────────────────────────────────
# List devices
# ─────────────────────────────────────────────

class TestListUserDevices:
    def test_returns_user_devices(self, client, auth_headers, test_user, session):
        """GET /user/devices returns the current user's devices."""
        device = UserDevice(user_id=test_user.id, device_id="device-xyz", is_active=True)
        session.add(device)
        session.commit()

        with patch("app.geo.service.ipinfo_service.resolve", new_callable=AsyncMock, return_value=None):
            resp = client.get("/user/devices", headers=auth_headers)
        assert resp.status_code == 200
        assert any(d["device_id"] == "device-xyz" for d in resp.json())

    def test_excludes_other_users_devices(self, client, auth_headers, second_user, session):
        """Devices belonging to other users don't appear in the list."""
        device = UserDevice(user_id=second_user.id, device_id="other-device", is_active=True)
        session.add(device)
        session.commit()

        with patch("app.geo.service.ipinfo_service.resolve", new_callable=AsyncMock, return_value=None):
            resp = client.get("/user/devices", headers=auth_headers)
        assert all(d["device_id"] != "other-device" for d in resp.json())

    def test_requires_auth(self, client):
        resp = client.get("/user/devices")
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# Revoke device
# ─────────────────────────────────────────────

class TestRevokeDevice:
    def test_owner_can_revoke_device(self, client, auth_headers, test_user, session):
        """Owner can revoke their own device (sets is_active=False)."""
        device = UserDevice(user_id=test_user.id, device_id="device-to-revoke", is_active=True)
        session.add(device)
        session.commit()
        session.refresh(device)

        resp = client.delete(f"/user/devices/{device.id}", headers=auth_headers)
        assert resp.status_code == 200
        session.refresh(device)
        assert device.is_active is False

    def test_cannot_revoke_another_users_device(self, client, auth_headers, second_user, session):
        """Cannot revoke another user's device — returns 404."""
        device = UserDevice(user_id=second_user.id, device_id="other-device", is_active=True)
        session.add(device)
        session.commit()
        session.refresh(device)

        resp = client.delete(f"/user/devices/{device.id}", headers=auth_headers)
        assert resp.status_code == 404

    def test_nonexistent_device_returns_404(self, client, auth_headers):
        resp = client.delete("/user/devices/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.delete("/user/devices/some-id")
        assert resp.status_code == 401


# ─────────────────────────────────────────────
# Get public keys for a user
# ─────────────────────────────────────────────

class TestGetUserKeys:
    def test_returns_active_keys(self, client, auth_headers, second_user, session):
        """GET /users/{id}/keys returns active public keys for the target user."""
        device = UserDevice(
            user_id=second_user.id,
            device_id="device-with-key",
            public_key="publickey==",
            is_active=True,
        )
        session.add(device)
        session.commit()

        resp = client.get(f"/users/{second_user.id}/keys", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert any(d["device_id"] == "device-with-key" and d["public_key"] == "publickey==" for d in data)

    def test_excludes_inactive_devices(self, client, auth_headers, second_user, session):
        """Revoked (is_active=False) devices are excluded."""
        device = UserDevice(
            user_id=second_user.id,
            device_id="revoked-device",
            public_key="revokedkey==",
            is_active=False,
        )
        session.add(device)
        session.commit()

        resp = client.get(f"/users/{second_user.id}/keys", headers=auth_headers)
        assert all(d["device_id"] != "revoked-device" for d in resp.json())

    def test_excludes_devices_without_key(self, client, auth_headers, second_user, session):
        """Devices that have no public_key set are excluded."""
        device = UserDevice(
            user_id=second_user.id,
            device_id="no-key-device",
            public_key=None,
            is_active=True,
        )
        session.add(device)
        session.commit()

        resp = client.get(f"/users/{second_user.id}/keys", headers=auth_headers)
        assert all(d["device_id"] != "no-key-device" for d in resp.json())

    def test_blocked_user_returns_empty_list(self, client, auth_headers, test_user, second_user, session):
        """Cannot retrieve keys of a user who has blocked you — returns empty list."""
        block = Block(blocker_id=second_user.id, blocked_id=test_user.id)
        session.add(block)
        session.commit()

        device = UserDevice(
            user_id=second_user.id,
            device_id="blocked-device",
            public_key="key==",
            is_active=True,
        )
        session.add(device)
        session.commit()

        resp = client.get(f"/users/{second_user.id}/keys", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_requires_auth(self, client, second_user):
        resp = client.get(f"/users/{second_user.id}/keys")
        assert resp.status_code == 401
