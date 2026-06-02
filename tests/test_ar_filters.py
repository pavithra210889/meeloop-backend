"""
Tests for AR Filters and Game Config endpoints.

Covers:
- GET /ar/filters returns list of active filters
- GET /ar/games/{game_id}/config returns config
- Admin upsert requires authentication
- ETag caching returns 304 on matching ETag
"""

import pytest
from unittest.mock import patch

from app.ar_filters.models import ArFilter, ArGameConfig


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_filter(session, filter_key="dog", sort_order=0, is_active=True):
    """Insert an ArFilter row directly into the DB."""
    row = ArFilter(
        filter_key=filter_key,
        filter_data={"id": filter_key, "label": filter_key.capitalize(), "emoji": "🐶"},
        is_active=is_active,
        sort_order=sort_order,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _create_game_config(session, game_id="mouth_catch"):
    """Insert an ArGameConfig row directly into the DB."""
    row = ArGameConfig(
        game_id=game_id,
        config_data={"catchRadiusY": 0.14, "catchRadiusX": 0.22},
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _make_admin(username="testuser"):
    """Patch settings so testuser is an admin."""
    return patch("app.ar_filters.routers.settings.ADMIN_USERNAMES", username)


# ── GET /ar/filters ───────────────────────────────────────────────────────────


class TestGetArFilters:
    def test_get_filters_empty(self, client):
        """Returns empty list when no filters exist."""
        response = client.get("/ar/filters")
        assert response.status_code == 200
        data = response.json()
        assert "filters" in data
        assert data["filters"] == []

    def test_get_filters_returns_active_filters(self, client, session):
        """Active filters are returned in the list."""
        _create_filter(session, "dog", sort_order=0)
        _create_filter(session, "cat", sort_order=1)
        response = client.get("/ar/filters")
        assert response.status_code == 200
        data = response.json()
        assert len(data["filters"]) == 2

    def test_get_filters_excludes_inactive(self, client, session):
        """Inactive filters are not returned."""
        _create_filter(session, "dog", is_active=True)
        _create_filter(session, "hidden", is_active=False)
        response = client.get("/ar/filters")
        assert response.status_code == 200
        data = response.json()
        assert len(data["filters"]) == 1
        assert data["filters"][0]["id"] == "dog"

    def test_get_filters_no_auth_required(self, client, session):
        """GET /ar/filters is publicly accessible without auth."""
        _create_filter(session)
        response = client.get("/ar/filters")
        assert response.status_code == 200

    def test_get_filters_etag_304(self, client, session):
        """Second request with matching ETag returns 304 Not Modified."""
        _create_filter(session)
        first = client.get("/ar/filters")
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag is not None

        second = client.get("/ar/filters", headers={"If-None-Match": etag})
        assert second.status_code == 304

    def test_get_filters_etag_mismatch_returns_200(self, client, session):
        """Request with stale ETag returns 200 with fresh payload."""
        _create_filter(session)
        response = client.get("/ar/filters", headers={"If-None-Match": '"stale-etag"'})
        assert response.status_code == 200

    def test_get_filters_ordered_by_sort_order(self, client, session):
        """Filters come back in sort_order ascending."""
        _create_filter(session, "cat", sort_order=1)
        _create_filter(session, "dog", sort_order=0)
        response = client.get("/ar/filters")
        data = response.json()
        ids = [f["id"] for f in data["filters"]]
        assert ids == ["dog", "cat"]


# ── GET /ar/games/{game_id}/config ────────────────────────────────────────────


class TestGetGameConfig:
    def test_get_game_config_found(self, client, session):
        """Returns config for an existing game_id."""
        _create_game_config(session, "mouth_catch")
        response = client.get("/ar/games/mouth_catch/config")
        assert response.status_code == 200
        data = response.json()
        assert data["game_id"] == "mouth_catch"
        assert "config_data" in data
        assert "version" in data

    def test_get_game_config_not_found(self, client):
        """Returns 404 for unknown game_id."""
        response = client.get("/ar/games/nonexistent/config")
        assert response.status_code == 404

    def test_get_game_config_no_auth_required(self, client, session):
        """GET game config is publicly accessible."""
        _create_game_config(session)
        response = client.get("/ar/games/mouth_catch/config")
        assert response.status_code == 200


# ── POST /ar/admin/filters ────────────────────────────────────────────────────


class TestAdminUpsertFilter:
    def test_upsert_requires_auth(self, client):
        """Upsert endpoint requires authentication."""
        payload = {"filter_key": "dog", "filter_data": {"id": "dog"}}
        response = client.post("/ar/admin/filters", json=payload)
        # Unauthenticated → 401 or 403
        assert response.status_code in (401, 403)

    def test_upsert_requires_admin(self, client, auth_headers):
        """Non-admin authenticated user gets 403."""
        payload = {"filter_key": "dog", "filter_data": {"id": "dog"}}
        with patch("app.ar_filters.routers.settings.ADMIN_USERNAMES", "otheradmin"):
            response = client.post("/ar/admin/filters", json=payload, headers=auth_headers)
        assert response.status_code == 403

    def test_upsert_insert_new_filter(self, client, session, auth_headers):
        """Admin can insert a new filter."""
        payload = {"filter_key": "newdog", "filter_data": {"id": "newdog", "label": "New Dog"}}
        with _make_admin("testuser"):
            response = client.post("/ar/admin/filters", json=payload, headers=auth_headers)
        assert response.status_code == 200
        row = session.exec(
            __import__("sqlmodel").select(ArFilter).where(ArFilter.filter_key == "newdog")
        ).first()
        assert row is not None
        assert row.filter_data["label"] == "New Dog"

    def test_upsert_updates_existing_filter(self, client, session, auth_headers):
        """Admin upsert bumps version and updates data for existing filter."""
        existing = _create_filter(session, "dog")
        assert existing.version == 1

        payload = {"filter_key": "dog", "filter_data": {"id": "dog", "label": "Updated Dog"}}
        with _make_admin("testuser"):
            response = client.post("/ar/admin/filters", json=payload, headers=auth_headers)
        assert response.status_code == 200

        session.refresh(existing)
        assert existing.version == 2
        assert existing.filter_data["label"] == "Updated Dog"


# ── PUT /ar/admin/filters/{filter_key} ───────────────────────────────────────


class TestAdminUpdateFilter:
    def test_partial_update_filter(self, client, session, auth_headers):
        """Admin can partially update a filter."""
        _create_filter(session, "dog")
        with _make_admin("testuser"):
            response = client.put(
                "/ar/admin/filters/dog",
                json={"is_active": False},
                headers=auth_headers,
            )
        assert response.status_code == 200
        row = session.exec(
            __import__("sqlmodel").select(ArFilter).where(ArFilter.filter_key == "dog")
        ).first()
        assert row.is_active is False

    def test_update_nonexistent_filter_404(self, client, auth_headers):
        """Updating a filter that doesn't exist returns 404."""
        with _make_admin("testuser"):
            response = client.put(
                "/ar/admin/filters/doesnotexist",
                json={"is_active": False},
                headers=auth_headers,
            )
        assert response.status_code == 404
