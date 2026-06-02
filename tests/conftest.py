"""
Shared test fixtures for Meeloop backend tests.

Uses an in-memory SQLite database so tests are fast and isolated.
Each test function gets a fresh database via the `session` fixture.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy import event
from geoalchemy2 import Geography
from sqlalchemy.ext.compiler import compiles


# Make SQLite treat Geography columns as plain TEXT (PostGIS not available in tests)
@compiles(Geography, "sqlite")
def _compile_geography_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.database import get_session
from app.security import get_password_hash, create_access_token
from app.users.models import User, UserSession
from app.loops.models import LoopProfile, LoopProfilePhoto
from main import app
from datetime import datetime, timedelta, timezone
import secrets


class FakePipeline:
    """Queues Redis commands and executes them in-order when execute() is awaited."""

    def __init__(self, redis: "FakeRedis"):
        self._redis = redis
        self._queue: list = []

    def delete(self, key: str) -> "FakePipeline":
        self._queue.append(("delete", key))
        return self

    def zadd(self, key: str, mapping: dict) -> "FakePipeline":
        self._queue.append(("zadd", key, mapping))
        return self

    def zrevrange(self, key: str, start: int, stop: int) -> "FakePipeline":
        self._queue.append(("zrevrange", key, start, stop))
        return self

    def expire(self, key: str, ttl: int) -> "FakePipeline":
        # No-op in tests
        return self

    def sadd(self, key: str, *members: str) -> "FakePipeline":
        self._queue.append(("sadd", key, members))
        return self

    def hset(self, key: str, mapping: dict | None = None, **kwargs) -> "FakePipeline":
        m = mapping or kwargs
        self._queue.append(("hset", key, m))
        return self

    def hgetall(self, key: str) -> "FakePipeline":
        self._queue.append(("hgetall", key))
        return self

    async def execute(self) -> list:
        results = []
        for op in self._queue:
            cmd = op[0]
            if cmd == "delete":
                self._redis._zsets.pop(op[1], None)
                self._redis._hashes.pop(op[1], None)
                self._redis._sets.pop(op[1], None)
                self._redis._store.pop(op[1], None)
                results.append(1)
            elif cmd == "zadd":
                zset = self._redis._zsets.setdefault(op[1], {})
                zset.update(op[2])
                results.append(len(op[2]))
            elif cmd == "zrevrange":
                zset = self._redis._zsets.get(op[1], {})
                ordered = sorted(zset, key=lambda k: zset[k], reverse=True)
                s, e = op[2], op[3]
                results.append(ordered[s:] if e == -1 else ordered[s:e + 1])
            elif cmd == "sadd":
                s = self._redis._sets.setdefault(op[1], set())
                s.update(op[2])
                results.append(len(op[2]))
            elif cmd == "hset":
                h = self._redis._hashes.setdefault(op[1], {})
                h.update(op[2])
                results.append(len(op[2]))
            elif cmd == "hgetall":
                results.append(dict(self._redis._hashes.get(op[1], {})))
        self._queue.clear()
        return results


class FakeRedis:
    """In-memory async Redis stand-in for tests.
    Supports strings, sorted sets, sets, hashes, and pipeline."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._sets: dict[str, set] = {}
        self._hashes: dict[str, dict] = {}

    def _clear(self):
        self._store.clear()
        self._zsets.clear()
        self._sets.clear()
        self._hashes.clear()

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if (key in self._store or key in self._zsets or key in self._hashes) else 0

    async def delete(self, key: str) -> int:
        removed = 0
        for store in (self._store, self._zsets, self._sets, self._hashes):
            if key in store:
                del store[key]  # type: ignore[arg-type]
                removed += 1
        return removed

    async def expire(self, key: str, ttl: int) -> int:
        return 1

    # Sorted sets
    async def zadd(self, key: str, mapping: dict) -> int:
        self._zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        zset = self._zsets.get(key, {})
        ordered = sorted(zset, key=lambda k: zset[k], reverse=True)
        return ordered[start:] if stop == -1 else ordered[start:stop + 1]

    # Sets
    async def sadd(self, key: str, *members: str) -> int:
        self._sets.setdefault(key, set()).update(members)
        return len(members)

    async def smembers(self, key: str) -> set:
        return set(self._sets.get(key, set()))

    # Hashes
    async def hset(self, key: str, mapping: dict | None = None, **kwargs) -> int:
        m = mapping or kwargs
        self._hashes.setdefault(key, {}).update(m)
        return len(m)

    async def hgetall(self, key: str) -> dict:
        return dict(self._hashes.get(key, {}))

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


_fake_redis = FakeRedis()


@pytest.fixture(autouse=True)
def _mock_redis():
    """Replace the global redis_client with an in-memory fake for all tests."""
    _fake_redis._clear()
    with patch("app.redis_client.redis_client", _fake_redis), \
         patch("app.sockets.active.redis_client", _fake_redis), \
         patch("app.geo.service.redis_client", _fake_redis), \
         patch("app.messages.routers.redis_client", _fake_redis), \
         patch("app.ar_filters.routers.redis_client", _fake_redis), \
         patch("app.recommendations.routers.redis_client", _fake_redis), \
         patch("app.recommendations.engine.redis_client", _fake_redis), \
         patch("app.recommendations.scheduler.redis_client", _fake_redis):
        yield


@pytest.fixture(name="engine")
def engine_fixture():
    """Create an in-memory SQLite engine for testing."""
    # Disable GeoAlchemy2 spatial features not supported on SQLite
    for table in SQLModel.metadata.tables.values():
        for col in table.columns:
            if hasattr(col.type, 'spatial_index'):
                col.type.spatial_index = False
                col.type.from_text = None

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Register GeoAlchemy2 functions as passthrough in SQLite
    @event.listens_for(engine, "connect")
    def _register_geo_functions(dbapi_conn, connection_record):
        dbapi_conn.create_function("ST_GeogFromText", 1, lambda x: x)
        dbapi_conn.create_function("AsBinary", 1, lambda x: x)
        dbapi_conn.create_function("CreateSpatialIndex", 2, lambda *a: None)

    SQLModel.metadata.create_all(engine)
    yield engine


@pytest.fixture(name="session")
def session_fixture(engine):
    """Provide a transactional DB session that rolls back after each test."""
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session):
    """TestClient with the DB session overridden to use the test DB."""

    def get_session_override():
        yield session

    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="test_user")
def test_user_fixture(session) -> User:
    """Create a verified, active test user."""
    user = User(
        name="Test User",
        username="testuser",
        email="test@meeloop.com",
        password=get_password_hash("testpassword123"),
        is_active=True,
        is_verified=True,
        is_loop_enabled=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(name="second_user")
def second_user_fixture(session) -> User:
    """Create a second test user for multi-user scenarios."""
    user = User(
        name="Second User",
        username="seconduser",
        email="second@meeloop.com",
        password=get_password_hash("testpassword123"),
        is_active=True,
        is_verified=True,
        is_loop_enabled=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(name="auth_headers")
def auth_headers_fixture(test_user, session) -> dict:
    """Return Authorization headers with a valid session token."""
    token = _create_session_token(test_user, session)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(name="second_auth_headers")
def second_auth_headers_fixture(second_user, session) -> dict:
    """Auth headers for the second user."""
    token = _create_session_token(second_user, session)
    return {"Authorization": f"Bearer {token}"}


def _create_session_token(user: User, session: Session) -> str:
    """Create a server-side session and return its token."""
    token = secrets.token_urlsafe(32)
    user_session = UserSession(
        user_id=user.id,
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    session.add(user_session)
    session.commit()
    return token


@pytest.fixture(name="loop_profile")
def loop_profile_fixture(test_user, session) -> LoopProfile:
    """Create a loop profile for the test user."""
    profile = LoopProfile(
        user_id=test_user.id,
        displayname="Test Display",
        bio="Test bio",
        gender="male",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@pytest.fixture(name="second_loop_profile")
def second_loop_profile_fixture(second_user, session) -> LoopProfile:
    """Create a loop profile for the second user."""
    profile = LoopProfile(
        user_id=second_user.id,
        displayname="Second Display",
        bio="Second bio",
        gender="female",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@pytest.fixture(name="loop_photos")
def loop_photos_fixture(loop_profile, session) -> list[LoopProfilePhoto]:
    """Create sample photos for the test user's loop profile."""
    photos = []
    for i in range(3):
        photo = LoopProfilePhoto(
            loop_profile_id=loop_profile.id,
            photo_url=f"https://example.com/photo_{i}.jpg",
            order=i,
            is_primary=(i == 0),
        )
        session.add(photo)
        photos.append(photo)
    session.commit()
    for p in photos:
        session.refresh(p)
    return photos
