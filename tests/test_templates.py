"""
Tests for the Meme Templates endpoints.

Covers: pagination, search, type filtering, exclude type, empty results.
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.meme_templates.models import MemeTemplates, TemplateType


def _create_template(session, user, *, template_type=TemplateType.IMAGE, content="funny meme",
                     urls='["https://example.com/meme.jpg"]', hash_tags='["funny", "meme"]',
                     metadata_info='{}', created_at=None):
    """Helper to insert a MemeTemplates row."""
    template = MemeTemplates(
        template_type=template_type,
        content=content,
        urls=urls,
        hash_tags=hash_tags,
        metadata_info=metadata_info,
        created_by_id=user.id,
        updated_by_id=user.id,
    )
    if created_at:
        template.created_at = created_at
        template.updated_at = created_at
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


# ────────────────────────────────────────────
# Pagination
# ────────────────────────────────────────────


class TestTemplatePagination:
    def test_get_templates_default(self, client, auth_headers, test_user, session):
        """Default request should return up to 10 templates with pagination metadata."""
        for i in range(12):
            _create_template(session, test_user, content=f"meme {i}")

        response = client.get("/meme-templates/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 12
        assert data["limit"] == 10
        assert data["offset"] == 0
        assert data["has_next"] is True
        assert data["has_previous"] is False
        assert len(data["items"]) == 10

    def test_get_templates_second_page(self, client, auth_headers, test_user, session):
        """Requesting offset=10 should return the remaining templates."""
        for i in range(12):
            _create_template(session, test_user, content=f"meme {i}")

        response = client.get("/meme-templates/?offset=10", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 12
        assert data["offset"] == 10
        assert data["has_next"] is False
        assert data["has_previous"] is True
        assert len(data["items"]) == 2

    def test_get_templates_custom_limit(self, client, auth_headers, test_user, session):
        """Setting a custom limit should be respected."""
        for i in range(5):
            _create_template(session, test_user, content=f"meme {i}")

        response = client.get("/meme-templates/?limit=3", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 3
        assert data["has_next"] is True

    def test_get_templates_limit_max_20(self, client, auth_headers, test_user, session):
        """Limit above 20 should be rejected by query validation."""
        response = client.get("/meme-templates/?limit=50", headers=auth_headers)
        assert response.status_code == 422


# ────────────────────────────────────────────
# Search
# ────────────────────────────────────────────


class TestTemplateSearch:
    def test_search_by_content(self, client, auth_headers, test_user, session):
        """Search by q should match against content (case-insensitive)."""
        _create_template(session, test_user, content="Funny Cat")
        _create_template(session, test_user, content="Serious Dog")

        response = client.get("/meme-templates/?q=cat", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["content"] == "Funny Cat"

    def test_search_by_hashtag(self, client, auth_headers, test_user, session):
        """Search by q should also match against hash_tags."""
        _create_template(
            session, test_user, content="Something",
            hash_tags='["trending", "viral"]',
        )
        _create_template(
            session, test_user, content="Other",
            hash_tags='["boring"]',
        )

        response = client.get("/meme-templates/?q=viral", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["content"] == "Something"

    def test_search_no_match(self, client, auth_headers, test_user, session):
        """Search with a query that matches nothing should return empty items."""
        _create_template(session, test_user, content="Hello World")

        response = client.get("/meme-templates/?q=zzzznotfound", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_search_pagination(self, client, auth_headers, test_user, session):
        """Search results should also be paginated."""
        for i in range(5):
            _create_template(session, test_user, content=f"xylophone item {i}")
        _create_template(session, test_user, content="something else")

        response = client.get("/meme-templates/?q=xylophone&limit=2&offset=0", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["has_next"] is True


# ────────────────────────────────────────────
# Type Filtering
# ────────────────────────────────────────────


class TestTemplateTypeFilter:
    def test_filter_by_type(self, client, auth_headers, test_user, session):
        """Filtering by type should only return templates of that type."""
        _create_template(session, test_user, template_type=TemplateType.IMAGE, content="img")
        _create_template(session, test_user, template_type=TemplateType.VIDEO, content="vid")
        _create_template(session, test_user, template_type=TemplateType.AUDIO, content="aud")

        response = client.get("/meme-templates/?type=video", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["content"] == "vid"
        assert data["items"][0]["template_type"] == "video"

    def test_exclude_type(self, client, auth_headers, test_user, session):
        """Excluding a type should return all templates except that type."""
        _create_template(session, test_user, template_type=TemplateType.IMAGE, content="img")
        _create_template(session, test_user, template_type=TemplateType.VIDEO, content="vid")
        _create_template(session, test_user, template_type=TemplateType.AUDIO, content="aud")

        response = client.get("/meme-templates/?exclude_type=image", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        types = [item["template_type"] for item in data["items"]]
        assert "image" not in types

    def test_filter_type_with_search(self, client, auth_headers, test_user, session):
        """Type filter combined with search should apply both filters."""
        _create_template(session, test_user, template_type=TemplateType.IMAGE, content="cat image")
        _create_template(session, test_user, template_type=TemplateType.VIDEO, content="cat video")
        _create_template(session, test_user, template_type=TemplateType.IMAGE, content="dog image")

        response = client.get("/meme-templates/?q=cat&type=image", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["content"] == "cat image"


# ────────────────────────────────────────────
# Empty Results
# ────────────────────────────────────────────


class TestTemplateEmpty:
    def test_no_templates(self, client, auth_headers):
        """When no templates exist, should return empty list with total 0."""
        response = client.get("/meme-templates/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["has_next"] is False
        assert data["has_previous"] is False

    def test_filter_type_no_match(self, client, auth_headers, test_user, session):
        """Filtering by a type with no templates should return empty."""
        _create_template(session, test_user, template_type=TemplateType.IMAGE)

        response = client.get("/meme-templates/?type=audio", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []


# ────────────────────────────────────────────
# Auth Required
# ────────────────────────────────────────────


class TestTemplateAuth:
    def test_no_auth_returns_401(self, client):
        """Accessing meme-templates without auth should return 401."""
        response = client.get("/meme-templates/")
        assert response.status_code == 401
