# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
# Run dev server (auto-reload)
uvicorn main:app --reload

# Run all tests
pytest

# Run a single test file
pytest tests/test_auth.py

# Run a single test function
pytest tests/test_auth.py::test_function_name -v

# Database migrations
alembic upgrade head                    # Apply all migrations
alembic revision --autogenerate -m "description"  # Create new migration

# Install dependencies (venv at ./env)
source env/bin/activate
pip install -r requirements.txt
```

## Architecture

**Framework**: FastAPI app with Socket.IO for real-time features, SQLModel/SQLAlchemy ORM on SQLite.

### Module Structure

Each feature domain lives in `app/<domain>/` with a consistent layout:
- `models.py` — SQLModel table definitions
- `routers.py` — FastAPI route handlers
- `schemas.py` — Pydantic request/response schemas (some modules)

Domains: `users`, `posts`, `messages`, `contacts`, `stories`, `calls`, `notifications`, `loops`, `meme_templates`, `reports`, `storage`

### Entry Point & Wiring

`main.py` creates the FastAPI app, mounts Socket.IO at `/ws` via `socketio.ASGIApp`, mounts static media at `/media`, and includes all routers. Docs are disabled when `ENVIRONMENT=production`.

### Database & Sessions

- `app/database.py` — SQLite engine with `PRAGMA foreign_keys=ON`. Provides `get_session()` generator.
- `app/dependencies.py` — `SessionDep = Annotated[Session, Depends(get_session)]` used in all route handlers.
- Socket.IO events use a separate `scoped_session` with `SessionProxy` (in `app/sockets/socketio_events.py`) since they run outside FastAPI's dependency injection.

### Authentication

Dual auth system: **session tokens** (preferred) and **JWT** (backward compatibility).
- `app/security.py` — password hashing (Argon2), JWT encode/decode, session token generation
- `app/users/routers.py` — auth routes (`/signup/`, `/login/`, `/me/`)
- OAuth: Google, Facebook, Truecaller integrations in `app/services/`
- OTP via SMS (MSG91) or mock provider, email-based password reset

### Real-time (Socket.IO)

- `app/sockets/socketio_server.py` — AsyncServer with Redis manager for multi-worker support
- `app/sockets/socketio_events.py` — Message, reaction, call signaling, typing, key transfer events
- `app/loops/socketio_events.py` — Loop-specific socket events
- `app/sockets/active.py` — In-memory `active_users: Dict[int, str]` mapping user_id to socket sid

### IDs

All models use UUID v7 string IDs (`app/uuid_utils.py`). Time-sortable, globally unique.

### Config

`app/config.py` — Plain class reading from `.env` via `python-dotenv`. See `env.example` for all available variables. Key settings: `DATABASE_URL`, `SECRET_KEY`, `REDIS_URL`, `R2_*` (Cloudflare R2 storage), Firebase credentials, OAuth keys.

### Storage

Media files can be stored locally (`MEDIA_ROOT`) or on Cloudflare R2. The `app/storage/` module handles uploads and serving.

### Migrations

Alembic with `render_as_batch=True` (required for SQLite ALTER TABLE support). All models must be imported in `migrations/env.py` for autogenerate to detect them. The DB URL is hardcoded in `env.py` as `sqlite:///./database.sqlite`.

### Tests

Tests use in-memory SQLite via `conftest.py` fixtures: `engine`, `session`, `client` (TestClient with overridden DB session), `test_user`/`second_user`, `auth_headers`/`second_auth_headers`. Auth in tests uses session tokens, not JWT.

## Error Handling

All HTTP errors use `HTTPException`. Standard response body: `{ "detail": "human-readable message" }`.

```python
raise HTTPException(status_code=404, detail="Message not found")
raise HTTPException(status_code=403, detail="Not authorised")
```

Do not create custom exception classes unless adding new middleware. Let FastAPI's default 422 handle Pydantic validation errors — do not catch them manually.

## Pagination Conventions

| Pattern | Used for | Query params |
|---------|----------|-------------|
| Cursor-based (`before_id`) | Messages, feeds, call history | `limit`, `before_id` (UUID) |
| Offset-based | Notifications, bookmarks, lists | `limit`, `offset` |

Always return a plain list (not a wrapped object) unless the endpoint also returns metadata (e.g., `MemeTemplatePaginatedResponse`).

## Timestamps

All `datetime` values must be stored and returned in UTC. Use `datetime.now(timezone.utc)` — never `datetime.utcnow()` (deprecated). SQLModel columns should use `default_factory=lambda: datetime.now(timezone.utc)`.

## Query Patterns

Avoid N+1 queries. Use `selectinload` or `joinedload` when loading related objects:

```python
from sqlmodel import select
from sqlalchemy.orm import selectinload

statement = select(Message).options(selectinload(Message.reactions))
```

Do not call `session.get()` in a loop — batch with `select(...).where(Model.id.in_([...]))`.

## Logging

Set `DATABASE_ECHO=false` in production to suppress SQL logs. Use Python's `logging` module — do not use `print()` in production code. Log level `INFO` for normal operations, `WARNING` for recoverable issues, `ERROR` for failures that need investigation.

## Common Gotchas

- **SQLite vs Postgres**: Alembic uses `render_as_batch=True` for SQLite ALTER TABLE support. This is fine for dev but production uses Postgres. If writing raw SQL, check for Postgres-only syntax (e.g., `ON CONFLICT DO NOTHING`).
- **PostGIS required**: The `nearby` and `loops/nearby` endpoints use PostGIS spatial queries. These will fail on plain SQLite in dev — test them against a Postgres instance.
- **`ENVIRONMENT=production`**: Disables `/docs`, `/redoc`, `/openapi.json`. If you need to inspect the schema in staging, temporarily set `ENVIRONMENT=staging`.
- **Socket.IO sessions**: Events in `socketio_events.py` use a `scoped_session` (not `SessionDep`) because they run outside FastAPI's DI. Always call `session.remove()` in a `finally` block to return the connection to the pool.
