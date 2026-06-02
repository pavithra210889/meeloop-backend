"""
Tests for the Posts endpoints.

Covers: create, get, delete, like/unlike, comments, feed, user posts, auth.
"""

import pytest
from unittest.mock import patch


# ────────────────────────────────────────────
# Post CRUD
# ────────────────────────────────────────────


class TestPostCRUD:
    @patch("app.posts.routers.r2_service")
    def test_create_post(self, mock_r2, client, auth_headers, test_user):
        """Creating a post should return the post with caption and media."""
        mock_r2.extract_file_key_from_url.return_value = None

        response = client.post(
            "/posts/",
            json={
                "caption": "Hello world",
                "media_urls": ["https://example.com/photo.jpg"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["caption"] == "Hello world"
        assert data["user"]["id"] == test_user.id
        assert data["likes_count"] == 0
        assert data["comments_count"] == 0
        assert data["is_liked"] is False
        assert len(data["media_files"]) == 1
        assert data["media_files"][0]["file_path"] == "https://example.com/photo.jpg"

    @patch("app.posts.routers.r2_service")
    def test_create_post_no_caption(self, mock_r2, client, auth_headers):
        """A post can be created with a null caption."""
        mock_r2.extract_file_key_from_url.return_value = None

        response = client.post(
            "/posts/",
            json={"caption": None, "media_urls": ["https://example.com/img.jpg"]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["caption"] is None

    @patch("app.posts.routers.r2_service")
    def test_get_post(self, mock_r2, client, auth_headers, test_user):
        """GET /posts/{id}/ should return the post details."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Test", "media_urls": ["https://example.com/a.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        response = client.get(f"/posts/{post_id}/", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == post_id
        assert response.json()["caption"] == "Test"

    def test_get_post_not_found(self, client, auth_headers):
        """Getting a non-existent post should return 404."""
        response = client.get("/posts/nonexistent-id/", headers=auth_headers)
        assert response.status_code == 404

    @patch("app.posts.routers.r2_service")
    def test_delete_post(self, mock_r2, client, auth_headers):
        """Author should be able to delete their own post."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "To delete", "media_urls": ["https://example.com/d.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        response = client.delete(f"/posts/{post_id}/", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["detail"] == "Post deleted successfully"

        # Confirm it's gone
        get_resp = client.get(f"/posts/{post_id}/", headers=auth_headers)
        assert get_resp.status_code == 404

    @patch("app.posts.routers.r2_service")
    def test_delete_post_forbidden_for_other_user(
        self, mock_r2, client, auth_headers, second_auth_headers
    ):
        """A user cannot delete another user's post."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "My post", "media_urls": ["https://example.com/x.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        response = client.delete(f"/posts/{post_id}/", headers=second_auth_headers)
        assert response.status_code == 403

    def test_delete_post_not_found(self, client, auth_headers):
        """Deleting a non-existent post should return 404."""
        response = client.delete("/posts/nonexistent-id/", headers=auth_headers)
        assert response.status_code == 404


# ────────────────────────────────────────────
# Likes
# ────────────────────────────────────────────


class TestPostLikes:
    @patch("app.posts.routers.r2_service")
    def test_like_post(self, mock_r2, client, auth_headers, second_auth_headers):
        """Liking a post should increment the like count."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Likeable", "media_urls": ["https://example.com/l.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        response = client.post(f"/posts/{post_id}/like/", headers=second_auth_headers)
        assert response.status_code == 200
        assert response.json()["likes_count"] == 1
        assert response.json()["is_liked"] is True

    @patch("app.posts.routers.r2_service")
    def test_unlike_post(self, mock_r2, client, auth_headers, second_auth_headers):
        """Unliking a previously liked post should decrement the count."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Unlike me", "media_urls": ["https://example.com/u.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        # Like first
        client.post(f"/posts/{post_id}/like/", headers=second_auth_headers)

        # Then unlike
        response = client.post(f"/posts/{post_id}/unlike/", headers=second_auth_headers)
        assert response.status_code == 200
        assert response.json()["likes_count"] == 0
        assert response.json()["is_liked"] is False

    @patch("app.posts.routers.r2_service")
    def test_get_post_likes(self, mock_r2, client, auth_headers, second_auth_headers):
        """GET /posts/{id}/likes/ should return paginated list of users who liked."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Popular", "media_urls": ["https://example.com/p.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        # Both users like the post
        client.post(f"/posts/{post_id}/like/", headers=auth_headers)
        client.post(f"/posts/{post_id}/like/", headers=second_auth_headers)

        response = client.get(f"/posts/{post_id}/likes/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 2
        assert data["has_more"] is False

    @patch("app.posts.routers.r2_service")
    def test_get_post_likes_pagination(self, mock_r2, client, auth_headers, second_auth_headers):
        """GET /posts/{id}/likes/ should respect limit param."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Popular", "media_urls": ["https://example.com/p.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        client.post(f"/posts/{post_id}/like/", headers=auth_headers)
        client.post(f"/posts/{post_id}/like/", headers=second_auth_headers)

        response = client.get(f"/posts/{post_id}/likes/?limit=1", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["total"] == 2
        assert data["has_more"] is True

    def test_like_post_not_found(self, client, auth_headers):
        """Liking a non-existent post should return 404."""
        response = client.post("/posts/nonexistent-id/like/", headers=auth_headers)
        assert response.status_code == 404


# ────────────────────────────────────────────
# Comments
# ────────────────────────────────────────────


class TestPostComments:
    @patch("app.posts.routers.r2_service")
    def test_create_comment(self, mock_r2, client, auth_headers):
        """Creating a comment on a post should succeed."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Comment me", "media_urls": ["https://example.com/c.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        response = client.post(
            f"/comments/{post_id}/",
            json={"comment": "Nice post!", "reply_to": None},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["comment"] == "Nice post!"
        assert data["post_id"] == post_id

    @patch("app.posts.routers.r2_service")
    def test_get_comments(self, mock_r2, client, auth_headers):
        """GET /comments/{post_id}/ should return paginated list of comments."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Has comments", "media_urls": ["https://example.com/h.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        # Add two comments
        client.post(
            f"/comments/{post_id}/",
            json={"comment": "First!", "reply_to": None},
            headers=auth_headers,
        )
        client.post(
            f"/comments/{post_id}/",
            json={"comment": "Second!", "reply_to": None},
            headers=auth_headers,
        )

        response = client.get(f"/comments/{post_id}/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 2
        assert data["has_more"] is False

    @patch("app.posts.routers.r2_service")
    def test_get_comments_pagination(self, mock_r2, client, auth_headers):
        """GET /comments/{post_id}/ should respect limit param."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Many comments", "media_urls": ["https://example.com/h.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        client.post(f"/comments/{post_id}/", json={"comment": "A", "reply_to": None}, headers=auth_headers)
        client.post(f"/comments/{post_id}/", json={"comment": "B", "reply_to": None}, headers=auth_headers)

        response = client.get(f"/comments/{post_id}/?limit=1", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["total"] == 2
        assert data["has_more"] is True

    @patch("app.posts.routers.r2_service")
    def test_delete_comment(self, mock_r2, client, auth_headers):
        """Comment author should be able to delete their comment."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Del comment", "media_urls": ["https://example.com/dc.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        comment_resp = client.post(
            f"/comments/{post_id}/",
            json={"comment": "To remove", "reply_to": None},
            headers=auth_headers,
        )
        comment_id = comment_resp.json()["id"]

        response = client.delete(f"/comments/{comment_id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["detail"] == "comment is deleted"

    @patch("app.posts.routers.r2_service")
    def test_delete_comment_other_user_not_found(
        self, mock_r2, client, auth_headers, second_auth_headers
    ):
        """A user cannot delete another user's comment (returns 404 per the query filter)."""
        mock_r2.extract_file_key_from_url.return_value = None

        create_resp = client.post(
            "/posts/",
            json={"caption": "Comment test", "media_urls": ["https://example.com/ct.jpg"]},
            headers=auth_headers,
        )
        post_id = create_resp.json()["id"]

        comment_resp = client.post(
            f"/comments/{post_id}/",
            json={"comment": "My comment", "reply_to": None},
            headers=auth_headers,
        )
        comment_id = comment_resp.json()["id"]

        response = client.delete(f"/comments/{comment_id}", headers=second_auth_headers)
        assert response.status_code == 404

    def test_comment_on_nonexistent_post(self, client, auth_headers):
        """Commenting on a nonexistent post should return 404."""
        response = client.post(
            "/comments/nonexistent-id/",
            json={"comment": "Orphan", "reply_to": None},
            headers=auth_headers,
        )
        assert response.status_code == 404


# ────────────────────────────────────────────
# Feed & User Posts
# ────────────────────────────────────────────


class TestFeedAndUserPosts:
    @patch("app.posts.routers.r2_service")
    def test_feed_returns_posts(self, mock_r2, client, auth_headers, second_auth_headers, second_user):
        """Feed should return posts from other users (when no follows, it shows random posts)."""
        mock_r2.extract_file_key_from_url.return_value = None

        # Second user creates a post
        client.post(
            "/posts/",
            json={"caption": "Feed post", "media_urls": ["https://example.com/f.jpg"]},
            headers=second_auth_headers,
        )

        response = client.get("/feed", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # The feed for a user with no follows shows others' posts (limit 10)
        assert len(data) >= 1
        assert data[0]["caption"] == "Feed post"

    def test_feed_empty(self, client, auth_headers):
        """Feed should return an empty list when no posts exist."""
        response = client.get("/feed", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    @patch("app.posts.routers.r2_service")
    def test_get_user_posts(self, mock_r2, client, auth_headers, test_user):
        """GET /users/{id}/posts/ should return posts by that user."""
        mock_r2.extract_file_key_from_url.return_value = None

        client.post(
            "/posts/",
            json={"caption": "User post 1", "media_urls": ["https://example.com/u1.jpg"]},
            headers=auth_headers,
        )
        client.post(
            "/posts/",
            json={"caption": "User post 2", "media_urls": ["https://example.com/u2.jpg"]},
            headers=auth_headers,
        )

        response = client.get(f"/users/{test_user.id}/posts/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_get_user_posts_empty(self, client, auth_headers, second_user):
        """Requesting posts for a user with none should return empty list."""
        response = client.get(f"/users/{second_user.id}/posts/", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []


# ────────────────────────────────────────────
# Auth Required
# ────────────────────────────────────────────


class TestPostsAuth:
    def test_create_post_no_auth(self, client):
        """Creating a post without auth should return 401."""
        response = client.post(
            "/posts/",
            json={"caption": "No auth", "media_urls": ["https://example.com/n.jpg"]},
        )
        assert response.status_code == 401

    def test_get_post_no_auth(self, client):
        """Getting a post without auth should return 401."""
        response = client.get("/posts/some-id/")
        assert response.status_code == 401

    def test_delete_post_no_auth(self, client):
        """Deleting a post without auth should return 401."""
        response = client.delete("/posts/some-id/")
        assert response.status_code == 401

    def test_like_post_no_auth(self, client):
        """Liking a post without auth should return 401."""
        response = client.post("/posts/some-id/like/")
        assert response.status_code == 401

    def test_feed_no_auth(self, client):
        """Accessing the feed without auth should return 401."""
        response = client.get("/feed")
        assert response.status_code == 401

    def test_comments_no_auth(self, client):
        """Accessing comments without auth should return 401."""
        response = client.get("/comments/some-id/")
        assert response.status_code == 401
