"""
Tests for the Stories endpoints.

Covers: upload, delete, get stories feed, record view, get viewers, auth.
"""

import pytest
from datetime import datetime, timedelta

from app.stories.models import Story, StoryMedia, StoryView
from app.users.models import Follow, Block


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────


def _create_story(session, user_id, text=None, media_url="https://example.com/img.jpg"):
    """Create a Story + StoryMedia directly in the DB, bypassing r2_service."""
    story = Story(user_id=user_id, text=text)
    session.add(story)
    session.commit()
    session.refresh(story)

    media = StoryMedia(story_id=story.id, media_url=media_url, media_type="image")
    session.add(media)
    session.commit()
    session.refresh(story)
    return story


def _create_follow(session, follower_id, following_id):
    """Create a Follow relationship."""
    follow = Follow(follower_id=follower_id, following_id=following_id)
    session.add(follow)
    session.commit()
    return follow


def _create_block(session, blocker_id, blocked_id):
    """Create a Block relationship."""
    block = Block(blocker_id=blocker_id, blocked_id=blocked_id)
    session.add(block)
    session.commit()
    return block


# ────────────────────────────────────────────
# Upload Story
# ────────────────────────────────────────────


class TestUploadStory:
    def test_upload_story_success(self, client, auth_headers, test_user):
        """Uploading a story with media_url should succeed."""
        response = client.post(
            "/story/upload",
            data={"media_url": "https://example.com/photo.jpg", "text": "Hello world"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Story uploaded successfully"
        assert "story_id" in data

    def test_upload_story_without_text(self, client, auth_headers, test_user):
        """Uploading a story without text should still succeed."""
        response = client.post(
            "/story/upload",
            data={"media_url": "https://example.com/photo.jpg"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Story uploaded successfully"

    def test_upload_story_missing_media_url(self, client, auth_headers, test_user):
        """Uploading a story without media_url should fail with 422."""
        response = client.post(
            "/story/upload",
            data={"text": "no media"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_upload_story_no_auth(self, client):
        """Uploading without authentication should return 401."""
        response = client.post(
            "/story/upload",
            data={"media_url": "https://example.com/photo.jpg"},
        )
        assert response.status_code == 401


# ────────────────────────────────────────────
# Delete Story
# ────────────────────────────────────────────


class TestDeleteStory:
    def test_delete_own_story(self, client, auth_headers, test_user, session):
        """Owner should be able to delete their own story."""
        story = _create_story(session, test_user.id)
        response = client.delete(f"/story/{story.id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["message"] == "Story deleted successfully"

    def test_delete_story_not_owner(
        self, client, second_auth_headers, test_user, second_user, session
    ):
        """Non-owner should get 404 when trying to delete someone else's story."""
        story = _create_story(session, test_user.id)
        response = client.delete(f"/story/{story.id}", headers=second_auth_headers)
        assert response.status_code == 404

    def test_delete_nonexistent_story(self, client, auth_headers, test_user):
        """Deleting a story that doesn't exist should return 404."""
        response = client.delete("/story/nonexistent-id", headers=auth_headers)
        assert response.status_code == 404

    def test_delete_expired_story(self, client, auth_headers, test_user, session):
        """Deleting a story older than 24 hours should return 404."""
        story = _create_story(session, test_user.id)
        # Manually backdate the story
        story.created_at = datetime.now() - timedelta(hours=25)
        session.add(story)
        session.commit()

        response = client.delete(f"/story/{story.id}", headers=auth_headers)
        assert response.status_code == 404

    def test_delete_story_no_auth(self, client, test_user, session):
        """Deleting without auth should return 401."""
        story = _create_story(session, test_user.id)
        response = client.delete(f"/story/{story.id}")
        assert response.status_code == 401


# ────────────────────────────────────────────
# Get Stories Feed
# ────────────────────────────────────────────


class TestGetStories:
    def test_get_own_stories(self, client, auth_headers, test_user, session):
        """User should see their own stories in the feed."""
        _create_story(session, test_user.id, text="my story")
        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["user"]["id"] == test_user.id
        assert len(data[0]["stories"]) == 1

    def test_get_followed_user_stories(
        self, client, auth_headers, test_user, second_user, session
    ):
        """User should see stories from users they follow."""
        _create_follow(session, test_user.id, second_user.id)
        _create_story(session, second_user.id, text="followed story")

        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        user_ids = [entry["user"]["id"] for entry in data]
        assert second_user.id in user_ids

    def test_get_stories_excludes_unfollowed(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Stories from unfollowed users should not appear in the feed."""
        _create_story(session, second_user.id, text="not followed")

        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        user_ids = [entry["user"]["id"] for entry in data]
        assert second_user.id not in user_ids

    def test_get_stories_excludes_blocked_users(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Stories from blocked users should not appear even if followed."""
        _create_follow(session, test_user.id, second_user.id)
        _create_block(session, test_user.id, second_user.id)
        _create_story(session, second_user.id, text="blocked story")

        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        user_ids = [entry["user"]["id"] for entry in data]
        assert second_user.id not in user_ids

    def test_get_stories_excludes_user_who_blocked_you(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Stories from users who blocked the current user should not appear."""
        _create_follow(session, test_user.id, second_user.id)
        _create_block(session, second_user.id, test_user.id)  # second_user blocked test_user
        _create_story(session, second_user.id, text="they blocked me")

        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        user_ids = [entry["user"]["id"] for entry in data]
        assert second_user.id not in user_ids

    def test_get_stories_excludes_expired(
        self, client, auth_headers, test_user, session
    ):
        """Stories older than 24 hours should not appear."""
        story = _create_story(session, test_user.id, text="old story")
        story.created_at = datetime.now() - timedelta(hours=25)
        session.add(story)
        session.commit()

        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0

    def test_get_stories_includes_media(
        self, client, auth_headers, test_user, session
    ):
        """Stories should include media_file information."""
        _create_story(session, test_user.id, media_url="https://example.com/media.jpg")

        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        story_data = response.json()[0]["stories"][0]
        assert story_data["media_file"] is not None
        assert story_data["media_file"]["media_url"] == "https://example.com/media.jpg"
        assert story_data["media_file"]["media_type"] == "image"

    def test_get_stories_own_include_view_count(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Own stories should include view_count."""
        story = _create_story(session, test_user.id)
        # Add a view from second_user
        view = StoryView(story_id=story.id, viewer_id=second_user.id)
        session.add(view)
        session.commit()

        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        story_data = response.json()[0]["stories"][0]
        assert "view_count" in story_data
        assert story_data["view_count"] == 1

    def test_get_stories_others_no_view_count(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Other users' stories should not include view_count."""
        _create_follow(session, test_user.id, second_user.id)
        _create_story(session, second_user.id)

        response = client.get("/story/", headers=auth_headers)
        assert response.status_code == 200
        # Find the second_user's story entry
        for entry in response.json():
            if entry["user"]["id"] == second_user.id:
                assert "view_count" not in entry["stories"][0]
                break
        else:
            pytest.fail("Second user's stories not found in feed")

    def test_get_stories_no_auth(self, client):
        """Getting stories without auth should return 401."""
        response = client.get("/story/")
        assert response.status_code == 401


# ────────────────────────────────────────────
# Record Story View
# ────────────────────────────────────────────


class TestRecordStoryView:
    def test_view_own_story(self, client, auth_headers, test_user, session):
        """User should be able to view their own story."""
        story = _create_story(session, test_user.id)
        response = client.post(f"/story/{story.id}/view", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["message"] == "Story view recorded"

    def test_view_followed_user_story(
        self, client, auth_headers, test_user, second_user, session
    ):
        """User should be able to view stories from users they follow."""
        _create_follow(session, test_user.id, second_user.id)
        story = _create_story(session, second_user.id)

        response = client.post(f"/story/{story.id}/view", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["message"] == "Story view recorded"

    def test_view_story_not_following(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Viewing a story from someone you don't follow should return 403."""
        story = _create_story(session, second_user.id)
        response = client.post(f"/story/{story.id}/view", headers=auth_headers)
        assert response.status_code == 403

    def test_view_story_blocked(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Viewing a story from a blocked user should return 403."""
        _create_follow(session, test_user.id, second_user.id)
        _create_block(session, test_user.id, second_user.id)
        story = _create_story(session, second_user.id)

        response = client.post(f"/story/{story.id}/view", headers=auth_headers)
        assert response.status_code == 403

    def test_view_story_blocked_by_author(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Viewing a story where the author blocked you should return 403."""
        _create_follow(session, test_user.id, second_user.id)
        _create_block(session, second_user.id, test_user.id)
        story = _create_story(session, second_user.id)

        response = client.post(f"/story/{story.id}/view", headers=auth_headers)
        assert response.status_code == 403

    def test_view_story_idempotent(
        self, client, auth_headers, test_user, session
    ):
        """Viewing the same story again should update the view, not create a duplicate."""
        story = _create_story(session, test_user.id)

        resp1 = client.post(f"/story/{story.id}/view", headers=auth_headers)
        assert resp1.status_code == 200
        assert resp1.json()["message"] == "Story view recorded"

        resp2 = client.post(f"/story/{story.id}/view", headers=auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["message"] == "Story view updated"

    def test_view_nonexistent_story(self, client, auth_headers, test_user):
        """Viewing a story that doesn't exist should return 404."""
        response = client.post("/story/nonexistent-id/view", headers=auth_headers)
        assert response.status_code == 404

    def test_view_expired_story(self, client, auth_headers, test_user, session):
        """Viewing an expired story should return 404."""
        story = _create_story(session, test_user.id)
        story.created_at = datetime.now() - timedelta(hours=25)
        session.add(story)
        session.commit()

        response = client.post(f"/story/{story.id}/view", headers=auth_headers)
        assert response.status_code == 404

    def test_view_story_no_auth(self, client, test_user, session):
        """Viewing a story without auth should return 401."""
        story = _create_story(session, test_user.id)
        response = client.post(f"/story/{story.id}/view")
        assert response.status_code == 401


# ────────────────────────────────────────────
# Get Story Viewers
# ────────────────────────────────────────────


class TestGetStoryViewers:
    def test_get_viewers_as_owner(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Story owner should see the list of viewers."""
        story = _create_story(session, test_user.id)
        view = StoryView(story_id=story.id, viewer_id=second_user.id)
        session.add(view)
        session.commit()

        response = client.get(f"/story/{story.id}/viewers", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["story_id"] == story.id
        assert data["viewer_count"] == 1
        assert len(data["viewers"]) == 1
        assert data["viewers"][0]["viewer_id"] == second_user.id
        assert data["viewers"][0]["username"] == second_user.username

    def test_get_viewers_not_owner(
        self, client, second_auth_headers, test_user, second_user, session
    ):
        """Non-owner should get 403 when requesting viewers."""
        story = _create_story(session, test_user.id)
        response = client.get(f"/story/{story.id}/viewers", headers=second_auth_headers)
        assert response.status_code == 403

    def test_get_viewers_no_views(self, client, auth_headers, test_user, session):
        """Story with no views should return empty viewers list."""
        story = _create_story(session, test_user.id)
        response = client.get(f"/story/{story.id}/viewers", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["viewer_count"] == 0
        assert data["viewers"] == []

    def test_get_viewers_multiple(
        self, client, auth_headers, test_user, second_user, session
    ):
        """Multiple viewers should all appear in the list."""
        # Create a third user
        from app.security import get_password_hash

        third_user = __import__("app.users.models", fromlist=["User"]).User(
            name="Third User",
            username="thirduser",
            email="third@meeloop.com",
            password=get_password_hash("testpassword123"),
            is_active=True,
            is_verified=True,
        )
        session.add(third_user)
        session.commit()
        session.refresh(third_user)

        story = _create_story(session, test_user.id)
        for viewer in [second_user, third_user]:
            view = StoryView(story_id=story.id, viewer_id=viewer.id)
            session.add(view)
        session.commit()

        response = client.get(f"/story/{story.id}/viewers", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["viewer_count"] == 2
        viewer_ids = [v["viewer_id"] for v in data["viewers"]]
        assert second_user.id in viewer_ids
        assert third_user.id in viewer_ids

    def test_get_viewers_nonexistent_story(self, client, auth_headers, test_user):
        """Getting viewers of a nonexistent story should return 404."""
        response = client.get("/story/nonexistent-id/viewers", headers=auth_headers)
        assert response.status_code == 404

    def test_get_viewers_expired_story(
        self, client, auth_headers, test_user, session
    ):
        """Getting viewers of an expired story should return 404."""
        story = _create_story(session, test_user.id)
        story.created_at = datetime.now() - timedelta(hours=25)
        session.add(story)
        session.commit()

        response = client.get(f"/story/{story.id}/viewers", headers=auth_headers)
        assert response.status_code == 404

    def test_get_viewers_no_auth(self, client, test_user, session):
        """Getting viewers without auth should return 401."""
        story = _create_story(session, test_user.id)
        response = client.get(f"/story/{story.id}/viewers")
        assert response.status_code == 401
