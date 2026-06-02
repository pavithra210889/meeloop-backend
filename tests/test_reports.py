"""
Tests for the Reports endpoints.

Covers: create report, self-report prevention, idempotency, target validation,
get my reports, admin list/filter, admin status update, admin moderation action,
and auth requirements.
"""

import pytest
import uuid

from app.config import settings


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────


def _make_report_payload(target_type="user", target_id="some-id", reason="spam", **kwargs):
    payload = {
        "target_type": target_type,
        "target_id": target_id,
        "reason": reason,
    }
    payload.update(kwargs)
    return payload


@pytest.fixture(autouse=True)
def _reset_admin_setting():
    """Ensure ADMIN_USERNAMES is reset after each test."""
    original = settings.ADMIN_USERNAMES
    yield
    settings.ADMIN_USERNAMES = original


# ────────────────────────────────────────────
# Create Report
# ────────────────────────────────────────────


class TestCreateReport:
    def test_create_report_user_target(self, client, auth_headers, test_user, second_user):
        """Reporting another user should succeed and return the report."""
        payload = _make_report_payload(
            target_type="user",
            target_id=second_user.id,
            reason="spam",
            details="This user is spamming",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["target_type"] == "user"
        assert data["target_id"] == second_user.id
        assert data["reporter_id"] == test_user.id
        assert data["reported_user_id"] == second_user.id
        assert data["reason"] == "spam"
        assert data["details"] == "This user is spamming"
        assert data["status"] == "open"
        assert data["id"] is not None

    def test_create_report_with_attachments(self, client, auth_headers, test_user, second_user):
        """Report with attachments should store them correctly."""
        attachments = ["https://example.com/screenshot1.png", "https://example.com/screenshot2.png"]
        payload = _make_report_payload(
            target_type="user",
            target_id=second_user.id,
            reason="harassment",
            attachments=attachments,
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["attachments"] == attachments

    def test_self_report_rejected(self, client, auth_headers, test_user):
        """Users cannot report themselves."""
        payload = _make_report_payload(
            target_type="user",
            target_id=test_user.id,
            reason="spam",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 400
        assert "cannot report yourself" in response.json()["detail"].lower()

    def test_idempotent_report(self, client, auth_headers, test_user, second_user):
        """Submitting the same report twice should return the existing open report."""
        payload = _make_report_payload(
            target_type="user",
            target_id=second_user.id,
            reason="spam",
        )
        first = client.post("/reports/", json=payload, headers=auth_headers)
        assert first.status_code == 200
        first_id = first.json()["id"]

        second = client.post("/reports/", json=payload, headers=auth_headers)
        assert second.status_code == 200
        assert second.json()["id"] == first_id

    def test_target_not_found_user(self, client, auth_headers, test_user):
        """Reporting a non-existent user should return 404."""
        payload = _make_report_payload(
            target_type="user",
            target_id=str(uuid.uuid4()),
            reason="spam",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_target_not_found_post(self, client, auth_headers, test_user):
        """Reporting a non-existent post should return 404."""
        payload = _make_report_payload(
            target_type="post",
            target_id=str(uuid.uuid4()),
            reason="spam",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_target_not_found_comment(self, client, auth_headers, test_user):
        """Reporting a non-existent comment should return 404."""
        payload = _make_report_payload(
            target_type="comment",
            target_id=str(uuid.uuid4()),
            reason="harassment",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 404

    def test_target_not_found_loop_profile(self, client, auth_headers, test_user):
        """Reporting a non-existent loop profile should return 404."""
        payload = _make_report_payload(
            target_type="loop_profile",
            target_id=str(uuid.uuid4()),
            reason="impersonation",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 404

    def test_target_not_found_loop_message(self, client, auth_headers, test_user):
        """Reporting a non-existent loop message should return 404."""
        payload = _make_report_payload(
            target_type="loop_message",
            target_id=str(uuid.uuid4()),
            reason="violence",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 404

    def test_create_report_all_reason_types(self, client, auth_headers, test_user, second_user):
        """All valid reason enum values should be accepted."""
        reasons = [
            "spam", "impersonation", "hate", "harassment", "sexual",
            "self_harm", "violence", "misinformation", "illegal", "other",
        ]
        for reason in reasons:
            # Each reason creates a distinct report (different reason but same target).
            # Due to idempotency on (reporter, target_type, target_id), only the first
            # will create new; subsequent return existing. We just verify no 422 errors.
            payload = _make_report_payload(
                target_type="user",
                target_id=second_user.id,
                reason=reason,
            )
            response = client.post("/reports/", json=payload, headers=auth_headers)
            assert response.status_code == 200, f"Reason '{reason}' failed with {response.status_code}"

    def test_create_report_invalid_reason(self, client, auth_headers, test_user, second_user):
        """An invalid reason value should be rejected with 422."""
        payload = _make_report_payload(
            target_type="user",
            target_id=second_user.id,
            reason="not_a_valid_reason",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 422

    def test_create_report_invalid_target_type(self, client, auth_headers, test_user):
        """An invalid target_type value should be rejected with 422."""
        payload = _make_report_payload(
            target_type="invalid_type",
            target_id=str(uuid.uuid4()),
            reason="spam",
        )
        response = client.post("/reports/", json=payload, headers=auth_headers)
        assert response.status_code == 422


# ────────────────────────────────────────────
# Get My Reports
# ────────────────────────────────────────────


class TestMyReports:
    def test_get_my_reports_empty(self, client, auth_headers, test_user):
        """Returns empty list when user has no reports."""
        response = client.get("/reports/my", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["has_more"] is False

    def test_get_my_reports_returns_own(self, client, auth_headers, test_user, second_user):
        """Returns only reports created by the current user."""
        # Create a report first
        payload = _make_report_payload(
            target_type="user",
            target_id=second_user.id,
            reason="spam",
        )
        client.post("/reports/", json=payload, headers=auth_headers)

        response = client.get("/reports/my", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["reporter_id"] == test_user.id
        assert data["items"][0]["target_id"] == second_user.id

    def test_get_my_reports_excludes_others(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Each user only sees their own reports."""
        # test_user reports second_user
        client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=second_user.id, reason="spam"),
            headers=auth_headers,
        )
        # second_user reports test_user
        client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=test_user.id, reason="harassment"),
            headers=second_auth_headers,
        )

        # test_user should see only their report
        resp1 = client.get("/reports/my", headers=auth_headers)
        assert len(resp1.json()["items"]) == 1
        assert resp1.json()["items"][0]["reporter_id"] == test_user.id

        # second_user should see only their report
        resp2 = client.get("/reports/my", headers=second_auth_headers)
        assert len(resp2.json()["items"]) == 1
        assert resp2.json()["items"][0]["reporter_id"] == second_user.id


# ────────────────────────────────────────────
# Admin: List Reports
# ────────────────────────────────────────────


class TestListReports:
    def test_list_reports_non_admin_forbidden(self, client, auth_headers, test_user):
        """Non-admin users should get 403 on the admin list endpoint."""
        settings.ADMIN_USERNAMES = ""
        response = client.get("/reports/", headers=auth_headers)
        assert response.status_code == 403
        assert "admin" in response.json()["detail"].lower()

    def test_list_reports_admin_success(self, client, auth_headers, test_user, second_user):
        """Admin can list all reports."""
        settings.ADMIN_USERNAMES = "testuser"

        # Create a report first
        client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=second_user.id, reason="spam"),
            headers=auth_headers,
        )

        response = client.get("/reports/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    def test_list_reports_filter_by_status(self, client, auth_headers, test_user, second_user):
        """Admin can filter reports by status."""
        settings.ADMIN_USERNAMES = "testuser"

        client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=second_user.id, reason="spam"),
            headers=auth_headers,
        )

        # Filter for open reports
        response = client.get("/reports/?status=open", headers=auth_headers)
        assert response.status_code == 200
        for report in response.json():
            assert report["status"] == "open"

        # Filter for dismissed reports (should be empty)
        response = client.get("/reports/?status=dismissed", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    def test_list_reports_filter_by_target_type(self, client, auth_headers, test_user, second_user):
        """Admin can filter reports by target_type."""
        settings.ADMIN_USERNAMES = "testuser"

        client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=second_user.id, reason="spam"),
            headers=auth_headers,
        )

        response = client.get("/reports/?target_type=user", headers=auth_headers)
        assert response.status_code == 200
        assert len(response.json()) >= 1

        response = client.get("/reports/?target_type=post", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    def test_list_reports_filter_by_reported_user(self, client, auth_headers, test_user, second_user):
        """Admin can filter reports by reported_user_id."""
        settings.ADMIN_USERNAMES = "testuser"

        client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=second_user.id, reason="spam"),
            headers=auth_headers,
        )

        response = client.get(f"/reports/?reported_user_id={second_user.id}", headers=auth_headers)
        assert response.status_code == 200
        assert len(response.json()) >= 1
        for report in response.json():
            assert report["reported_user_id"] == second_user.id

    def test_list_reports_pagination(self, client, auth_headers, test_user, second_user):
        """Admin list respects limit and offset."""
        settings.ADMIN_USERNAMES = "testuser"

        # Create a report
        client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=second_user.id, reason="spam"),
            headers=auth_headers,
        )

        response = client.get("/reports/?limit=1&offset=0", headers=auth_headers)
        assert response.status_code == 200
        assert len(response.json()) <= 1

        response = client.get("/reports/?limit=1&offset=100", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []


# ────────────────────────────────────────────
# Admin: Update Report Status
# ────────────────────────────────────────────


class TestUpdateReportStatus:
    def _create_report(self, client, auth_headers, second_user):
        """Helper to create a report and return its id."""
        resp = client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=second_user.id, reason="spam"),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_update_status_non_admin_forbidden(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Non-admin cannot update report status."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_report(client, auth_headers, second_user)

        # second_user is not admin
        settings.ADMIN_USERNAMES = ""
        response = client.patch(
            f"/reports/{report_id}",
            json={"status": "under_review"},
            headers=second_auth_headers,
        )
        assert response.status_code == 403

    def test_update_status_admin_success(self, client, auth_headers, test_user, second_user):
        """Admin can update report status."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_report(client, auth_headers, second_user)

        response = client.patch(
            f"/reports/{report_id}",
            json={"status": "under_review"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["detail"] == "Report updated"

    def test_update_status_to_dismissed(self, client, auth_headers, test_user, second_user):
        """Admin can dismiss a report."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_report(client, auth_headers, second_user)

        response = client.patch(
            f"/reports/{report_id}",
            json={"status": "dismissed"},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Verify via admin list
        list_resp = client.get(f"/reports/?status=dismissed", headers=auth_headers)
        assert any(r["id"] == report_id for r in list_resp.json())

    def test_update_status_to_action_taken(self, client, auth_headers, test_user, second_user):
        """Admin can mark a report as action_taken."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_report(client, auth_headers, second_user)

        response = client.patch(
            f"/reports/{report_id}",
            json={"status": "action_taken"},
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_update_nonexistent_report(self, client, auth_headers, test_user):
        """Updating a non-existent report should return 404."""
        settings.ADMIN_USERNAMES = "testuser"
        response = client.patch(
            f"/reports/{str(uuid.uuid4())}",
            json={"status": "under_review"},
            headers=auth_headers,
        )
        assert response.status_code == 404


# ────────────────────────────────────────────
# Admin: Moderation Action
# ────────────────────────────────────────────


class TestModerationAction:
    def _create_user_report(self, client, auth_headers, second_user):
        """Create a report targeting a user and return its id."""
        resp = client.post(
            "/reports/",
            json=_make_report_payload(target_type="user", target_id=second_user.id, reason="spam"),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_action_non_admin_forbidden(
        self, client, auth_headers, second_auth_headers, test_user, second_user
    ):
        """Non-admin cannot apply moderation actions."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_user_report(client, auth_headers, second_user)

        settings.ADMIN_USERNAMES = ""
        response = client.patch(
            f"/reports/{report_id}/action",
            json={"status": "action_taken", "action": "suspend_user_temp"},
            headers=second_auth_headers,
        )
        assert response.status_code == 403

    def test_action_suspend_user_temp(self, client, auth_headers, test_user, second_user):
        """Admin can temporarily suspend a reported user."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_user_report(client, auth_headers, second_user)

        response = client.patch(
            f"/reports/{report_id}/action",
            json={
                "status": "action_taken",
                "action": "suspend_user_temp",
                "action_meta": {"days": 3},
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["detail"] == "Action applied"

    def test_action_suspend_user_perm(self, client, auth_headers, test_user, second_user):
        """Admin can permanently suspend a reported user."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_user_report(client, auth_headers, second_user)

        response = client.patch(
            f"/reports/{report_id}/action",
            json={
                "status": "action_taken",
                "action": "suspend_user_perm",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_action_missing_action_field(self, client, auth_headers, test_user, second_user):
        """Action field is required; omitting it should return 400."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_user_report(client, auth_headers, second_user)

        response = client.patch(
            f"/reports/{report_id}/action",
            json={"status": "action_taken"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "action is required" in response.json()["detail"].lower()

    def test_action_unsupported_action(self, client, auth_headers, test_user, second_user):
        """Unsupported action type should return 400."""
        settings.ADMIN_USERNAMES = "testuser"
        report_id = self._create_user_report(client, auth_headers, second_user)

        response = client.patch(
            f"/reports/{report_id}/action",
            json={"status": "action_taken", "action": "delete_everything"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "unsupported" in response.json()["detail"].lower()

    def test_action_nonexistent_report(self, client, auth_headers, test_user):
        """Action on a non-existent report should return 404."""
        settings.ADMIN_USERNAMES = "testuser"
        response = client.patch(
            f"/reports/{str(uuid.uuid4())}/action",
            json={"status": "action_taken", "action": "suspend_user_temp"},
            headers=auth_headers,
        )
        assert response.status_code == 404


# ────────────────────────────────────────────
# Authentication
# ────────────────────────────────────────────


class TestReportsAuth:
    def test_create_report_no_auth(self, client):
        """Creating a report without auth should return 401."""
        payload = _make_report_payload(target_type="user", target_id="some-id", reason="spam")
        response = client.post("/reports/", json=payload)
        assert response.status_code == 401

    def test_my_reports_no_auth(self, client):
        """Getting my reports without auth should return 401."""
        response = client.get("/reports/my")
        assert response.status_code == 401

    def test_list_reports_no_auth(self, client):
        """Listing all reports without auth should return 401."""
        response = client.get("/reports/")
        assert response.status_code == 401

    def test_update_status_no_auth(self, client):
        """Updating report status without auth should return 401."""
        response = client.patch(
            f"/reports/{str(uuid.uuid4())}",
            json={"status": "under_review"},
        )
        assert response.status_code == 401

    def test_action_no_auth(self, client):
        """Applying moderation action without auth should return 401."""
        response = client.patch(
            f"/reports/{str(uuid.uuid4())}/action",
            json={"status": "action_taken", "action": "suspend_user_temp"},
        )
        assert response.status_code == 401
