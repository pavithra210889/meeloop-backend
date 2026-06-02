"""
Tests for the recommendations system:
- GET /recommendations/users  — user suggestions
- GET /recommendations/posts  — post recommendations
- POST /recommendations/event — impression / click / dismiss tracking
"""

import pytest
from sqlmodel import Session
from app.users.models import Follow, User
from app.posts.models import Post, Like
from app.security import get_password_hash


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_user(session: Session, username: str) -> User:
    u = User(
        name=username.capitalize(),
        username=username,
        email=f"{username}@test.com",
        password=get_password_hash("pass"),
        is_active=True,
        is_verified=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _follow(session: Session, follower: User, following: User) -> None:
    session.add(Follow(follower_id=follower.id, following_id=following.id))
    session.commit()


def _make_post(session: Session, author: User, caption: str = "test post") -> Post:
    p = Post(caption=caption, posted_by=author.id)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _like_post(session: Session, user: User, post: Post) -> None:
    session.add(Like(user_id=user.id, post_id=post.id, liked=True))
    session.commit()


# ── User Suggestions ──────────────────────────────────────────────────────────

def test_user_suggestions_returns_200(client, auth_headers):
    res = client.get("/recommendations/users", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "has_more" in data
    assert isinstance(data["items"], list)


def test_user_suggestions_empty_for_isolated_user(client, auth_headers):
    """A user with no follows and no followers gets an empty or cold-start list."""
    res = client.get("/recommendations/users", headers=auth_headers)
    assert res.status_code == 200
    # Result is a list (may be empty or cold-start popular accounts)
    assert isinstance(res.json()["items"], list)


def test_user_suggestions_friend_of_friend(client, session, auth_headers, test_user):
    """Users should be suggested when a mutual follow relationship exists."""
    # test_user → alice → bob
    # bob should be suggested to test_user
    alice = _make_user(session, "alice_rec")
    bob = _make_user(session, "bob_rec")
    _follow(session, test_user, alice)
    _follow(session, alice, bob)

    res = client.get("/recommendations/users", headers=auth_headers)
    assert res.status_code == 200
    ids = [item["id"] for item in res.json()["items"]]
    assert bob.id in ids


def test_user_suggestions_excludes_already_followed(client, session, auth_headers, test_user):
    """Users I already follow must not appear in suggestions."""
    alice = _make_user(session, "alice_already")
    _follow(session, test_user, alice)

    res = client.get("/recommendations/users", headers=auth_headers)
    assert res.status_code == 200
    ids = [item["id"] for item in res.json()["items"]]
    assert alice.id not in ids


def test_user_suggestions_excludes_self(client, auth_headers, test_user):
    """The current user must never appear in their own suggestions."""
    res = client.get("/recommendations/users", headers=auth_headers)
    assert res.status_code == 200
    ids = [item["id"] for item in res.json()["items"]]
    assert test_user.id not in ids


def test_user_suggestions_pagination(client, session, auth_headers, test_user):
    """limit and offset parameters are respected."""
    # Create 5 suggestion candidates via mutual follows
    pivot = _make_user(session, "pivot_user")
    _follow(session, test_user, pivot)
    for i in range(5):
        u = _make_user(session, f"cand_{i}")
        _follow(session, pivot, u)

    res1 = client.get("/recommendations/users?limit=2&offset=0", headers=auth_headers)
    res2 = client.get("/recommendations/users?limit=2&offset=2", headers=auth_headers)
    assert res1.status_code == 200
    assert res2.status_code == 200
    ids1 = [i["id"] for i in res1.json()["items"]]
    ids2 = [i["id"] for i in res2.json()["items"]]
    assert len(ids1) <= 2
    assert len(ids2) <= 2
    assert set(ids1).isdisjoint(set(ids2))


def test_user_suggestions_requires_auth(client):
    res = client.get("/recommendations/users")
    assert res.status_code in (401, 403)


# ── Post Recommendations ──────────────────────────────────────────────────────

def test_post_recommendations_returns_200(client, auth_headers):
    res = client.get("/recommendations/posts", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert isinstance(data["items"], list)


def test_post_recommendations_excludes_own_posts(client, session, auth_headers, test_user):
    """The current user's own posts must never appear in recommendations."""
    _make_post(session, test_user, "my own post")
    res = client.get("/recommendations/posts", headers=auth_headers)
    assert res.status_code == 200
    for item in res.json()["items"]:
        assert item["posted_by"] != test_user.id


def test_post_recommendations_excludes_followed_users(client, session, auth_headers, test_user):
    """Posts from users I already follow belong in the Following feed, not here."""
    alice = _make_user(session, "alice_post_excl")
    _follow(session, test_user, alice)
    post = _make_post(session, alice, "alice post")
    _like_post(session, _make_user(session, "liker1"), post)

    res = client.get("/recommendations/posts", headers=auth_headers)
    assert res.status_code == 200
    ids = [item["id"] for item in res.json()["items"]]
    assert post.id not in ids


def test_post_recommendations_ranks_by_engagement(client, session, auth_headers, test_user):
    """Posts with more engagement should rank higher."""
    author = _make_user(session, "popular_author")
    hot_post = _make_post(session, author, "hot post")
    cold_post = _make_post(session, author, "cold post")

    # Give hot_post 5 likes, cold_post 1 like
    for i in range(5):
        liker = _make_user(session, f"liker_hot_{i}")
        _like_post(session, liker, hot_post)
    liker_cold = _make_user(session, "liker_cold")
    _like_post(session, liker_cold, cold_post)

    res = client.get("/recommendations/posts", headers=auth_headers)
    assert res.status_code == 200
    ids = [item["id"] for item in res.json()["items"]]

    if hot_post.id in ids and cold_post.id in ids:
        assert ids.index(hot_post.id) < ids.index(cold_post.id)


def test_post_recommendations_requires_auth(client):
    res = client.get("/recommendations/posts")
    assert res.status_code in (401, 403)


# ── Event Tracking ────────────────────────────────────────────────────────────

def test_record_dismiss_event(client, auth_headers):
    res = client.post("/recommendations/event", json={
        "item_type": "user",
        "item_id": "some-uuid-123",
        "event_type": "dismiss",
    }, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_record_follow_event(client, auth_headers):
    res = client.post("/recommendations/event", json={
        "item_type": "user",
        "item_id": "some-uuid-456",
        "event_type": "follow",
    }, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_record_like_event_on_post(client, auth_headers):
    res = client.post("/recommendations/event", json={
        "item_type": "post",
        "item_id": "post-uuid-789",
        "event_type": "like",
    }, headers=auth_headers)
    assert res.status_code == 200


def test_record_event_invalid_item_type(client, auth_headers):
    res = client.post("/recommendations/event", json={
        "item_type": "story",
        "item_id": "some-id",
        "event_type": "click",
    }, headers=auth_headers)
    assert res.status_code == 422


def test_record_event_invalid_event_type(client, auth_headers):
    res = client.post("/recommendations/event", json={
        "item_type": "user",
        "item_id": "some-id",
        "event_type": "purchase",
    }, headers=auth_headers)
    assert res.status_code == 422


def test_record_event_requires_auth(client):
    res = client.post("/recommendations/event", json={
        "item_type": "user", "item_id": "x", "event_type": "click"
    })
    assert res.status_code in (401, 403)
