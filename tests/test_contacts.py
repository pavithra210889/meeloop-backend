"""
Tests for the contacts module.

Covers: bulk upload, deduplication, invalid phone numbers, max limit,
contact matching, self-exclusion, and authentication requirements.
"""

import pytest


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────

def _make_contact(name: str, phone: str, email: str | None = None) -> dict:
    """Build a contact payload dict."""
    payload = {"name": name, "phone_num": phone}
    if email is not None:
        payload["email"] = email
    return payload


def _bulk_upload(client, contacts: list[dict], headers: dict):
    """POST contacts to the bulk endpoint."""
    return client.post("/contacts/bulk", json=contacts, headers=headers)


# ────────────────────────────────────────────
# Bulk Upload
# ────────────────────────────────────────────


class TestBulkUpload:
    def test_upload_valid_contacts(self, client, auth_headers, test_user):
        """Uploading valid Indian phone numbers should insert them all."""
        contacts = [
            _make_contact("Alice", "+919876543210"),
            _make_contact("Bob", "+919876543211", email="bob@example.com"),
            _make_contact("Charlie", "+919876543212"),
        ]
        resp = _bulk_upload(client, contacts, auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["inserted"] == 3
        assert data["skipped"] == 0
        assert data["invalid"] == 0
        assert data["total"] == 3
        assert data["message"] == "Contacts processed"

    def test_upload_contacts_with_local_format(self, client, auth_headers, test_user):
        """Phone numbers in local Indian format (without +91) should normalize and insert."""
        contacts = [
            _make_contact("Local Format", "09876543210"),
        ]
        resp = _bulk_upload(client, contacts, auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["inserted"] == 1
        assert data["invalid"] == 0

    def test_skip_duplicates_within_same_request(self, client, auth_headers, test_user):
        """Duplicate normalized numbers in the same request should be deduplicated."""
        contacts = [
            _make_contact("Alice", "+919876543210"),
            _make_contact("Alice Copy", "+919876543210"),
        ]
        resp = _bulk_upload(client, contacts, auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["inserted"] == 1
        assert data["skipped"] == 1
        assert data["total"] == 2

    def test_skip_duplicates_across_requests(self, client, auth_headers, test_user):
        """Contacts already uploaded should be skipped in subsequent requests."""
        contacts = [_make_contact("Alice", "+919876543210")]
        resp1 = _bulk_upload(client, contacts, auth_headers)
        assert resp1.status_code == 200
        assert resp1.json()["inserted"] == 1

        # Upload same contact again
        resp2 = _bulk_upload(client, contacts, auth_headers)
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["inserted"] == 0
        assert data["skipped"] == 1

    def test_skip_duplicates_different_format_same_number(
        self, client, auth_headers, test_user
    ):
        """Same number in different formats should be recognized as duplicate."""
        contacts_first = [_make_contact("Alice", "+919876543210")]
        resp1 = _bulk_upload(client, contacts_first, auth_headers)
        assert resp1.status_code == 200
        assert resp1.json()["inserted"] == 1

        # Same number in local format
        contacts_second = [_make_contact("Alice Local", "09876543210")]
        resp2 = _bulk_upload(client, contacts_second, auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["skipped"] == 1
        assert resp2.json()["inserted"] == 0

    def test_invalid_phone_numbers_counted(self, client, auth_headers, test_user):
        """Invalid phone numbers should be counted and skipped, not cause errors."""
        contacts = [
            _make_contact("Valid", "+919876543210"),
            _make_contact("Empty", ""),
        ]
        resp = _bulk_upload(client, contacts, auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["inserted"] == 1
        assert data["invalid"] == 1
        assert data["total"] == 2

    def test_max_100_limit(self, client, auth_headers, test_user):
        """Uploading more than 100 contacts should return 400."""
        contacts = [
            _make_contact(f"Contact {i}", f"+9198765{i:05d}")
            for i in range(101)
        ]
        resp = _bulk_upload(client, contacts, auth_headers)
        assert resp.status_code == 400
        assert "100" in resp.json()["detail"]

    def test_exactly_100_contacts_allowed(self, client, auth_headers, test_user):
        """Uploading exactly 100 contacts should succeed."""
        contacts = [
            _make_contact(f"Contact {i}", f"+9198765{i:05d}")
            for i in range(100)
        ]
        resp = _bulk_upload(client, contacts, auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 100

    def test_empty_list(self, client, auth_headers, test_user):
        """Uploading an empty list should succeed with zero counts."""
        resp = _bulk_upload(client, [], auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["inserted"] == 0
        assert data["skipped"] == 0
        assert data["invalid"] == 0
        assert data["total"] == 0

    def test_different_users_can_upload_same_number(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Two different users should each be able to upload the same phone number."""
        contacts = [_make_contact("Shared Contact", "+919876543210")]

        resp1 = _bulk_upload(client, contacts, auth_headers)
        assert resp1.status_code == 200
        assert resp1.json()["inserted"] == 1

        resp2 = _bulk_upload(client, contacts, second_auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["inserted"] == 1


# ────────────────────────────────────────────
# Contact Matches
# ────────────────────────────────────────────


class TestContactMatches:
    def test_returns_matched_users(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Users whose phone numbers match uploaded contacts should be returned."""
        # Give the second user a phone number
        second_user.phone_number = "+919876543210"
        session.add(second_user)
        session.commit()

        # Upload a contact with the matching number
        contacts = [_make_contact("My Friend", "+919876543210")]
        _bulk_upload(client, contacts, auth_headers)

        resp = client.get("/contacts/matches", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == second_user.id
        assert data["items"][0]["username"] == second_user.username
        assert data["items"][0]["name"] == second_user.name
        assert data["total"] == 1
        assert data["has_more"] is False

    def test_excludes_self(
        self, client, auth_headers, test_user, session
    ):
        """The current user should not appear in their own matches."""
        # Give the test user a phone number
        test_user.phone_number = "+919876543210"
        session.add(test_user)
        session.commit()

        # Upload a contact with the user's own number
        contacts = [_make_contact("Myself", "+919876543210")]
        _bulk_upload(client, contacts, auth_headers)

        resp = client.get("/contacts/matches", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        matched_ids = [u["id"] for u in data["items"]]
        assert test_user.id not in matched_ids

    def test_empty_when_no_contacts_uploaded(
        self, client, auth_headers, test_user
    ):
        """Matches should return empty list when user has no uploaded contacts."""
        resp = client.get("/contacts/matches", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["has_more"] is False

    def test_empty_when_no_numbers_match(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Matches should be empty when no users have matching phone numbers."""
        # second_user has no phone_number set
        contacts = [_make_contact("Unknown", "+919876543210")]
        _bulk_upload(client, contacts, auth_headers)

        resp = client.get("/contacts/matches", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_match_includes_is_following_field(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Matched user should have is_following=False when not followed."""
        second_user.phone_number = "+919876543210"
        session.add(second_user)
        session.commit()

        contacts = [_make_contact("Friend", "+919876543210")]
        _bulk_upload(client, contacts, auth_headers)

        resp = client.get("/contacts/matches", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["is_following"] is False

    def test_multiple_matches(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Multiple users matching uploaded contacts should all be returned."""
        # Create a third user with a phone number
        from app.security import get_password_hash
        from app.users.models import User

        third_user = User(
            name="Third User",
            username="thirduser",
            email="third@meeloop.com",
            password=get_password_hash("testpassword123"),
            is_active=True,
            is_verified=True,
            phone_number="+919876543222",
        )
        session.add(third_user)

        second_user.phone_number = "+919876543211"
        session.add(second_user)
        session.commit()

        contacts = [
            _make_contact("Friend A", "+919876543211"),
            _make_contact("Friend B", "+919876543222"),
        ]
        _bulk_upload(client, contacts, auth_headers)

        resp = client.get("/contacts/matches", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        matched_ids = {u["id"] for u in data["items"]}
        assert second_user.id in matched_ids
        assert third_user.id in matched_ids
        assert len(data["items"]) == 2


# ────────────────────────────────────────────
# Authentication Required
# ────────────────────────────────────────────


class TestContactsAuth:
    def test_bulk_upload_requires_auth(self, client):
        """POST /contacts/bulk without auth should return 401."""
        contacts = [_make_contact("Alice", "+919876543210")]
        resp = client.post("/contacts/bulk", json=contacts)
        assert resp.status_code == 401

    def test_matches_requires_auth(self, client):
        """GET /contacts/matches without auth should return 401."""
        resp = client.get("/contacts/matches")
        assert resp.status_code == 401
