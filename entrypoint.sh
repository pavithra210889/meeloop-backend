#!/bin/sh
set -e

echo "Running database migrations..."

python - <<'EOF'
import os
import sqlalchemy
from alembic.config import Config
from alembic.script import ScriptDirectory

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    raise SystemExit("DATABASE_URL not set")

cfg = Config("alembic.ini")
script = ScriptDirectory.from_config(cfg)
known = {rev.revision for rev in script.walk_revisions()}

engine = sqlalchemy.create_engine(db_url)
with engine.connect() as conn:
    try:
        rows = conn.execute(sqlalchemy.text("SELECT version_num FROM alembic_version")).fetchall()
        current = {row[0] for row in rows}

        # Remove revisions that don't exist in our migration files
        stale = current - known
        if stale:
            print(f"Removing stale revisions not in codebase: {stale}")
            for rev in stale:
                conn.execute(sqlalchemy.text("DELETE FROM alembic_version WHERE version_num = :r"), {"r": rev})
            conn.commit()
            current -= stale

        # If alembic_version is now empty but tables already exist, the DB has
        # been migrated before but lost tracking. Stamp to current heads so
        # alembic upgrade head only runs truly new migrations going forward.
        if not current:
            has_tables = conn.execute(sqlalchemy.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user')"
            )).scalar()
            if has_tables:
                heads = [rev.revision for rev in script.get_revisions("heads")]
                print(f"DB has tables but alembic_version is empty. Stamping heads: {heads}")
                for head in heads:
                    conn.execute(sqlalchemy.text(
                        "INSERT INTO alembic_version (version_num) VALUES (:r)"
                    ), {"r": head})
                conn.commit()

    except Exception as e:
        print(f"alembic_version check skipped: {e}")
EOF

echo "Migration files present:"
ls migrations/versions/ | grep -v __pycache__ | sort

echo "Alembic heads in codebase:"
alembic heads 2>&1 || true

echo "Current DB versions:"
alembic current 2>&1 || true

echo "Running alembic upgrade head..."
alembic upgrade head
echo "Migrations complete."

exec gunicorn main:app \
    -k uvicorn.workers.UvicornWorker \
    -w ${GUNICORN_WORKERS:-4} \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --keep-alive 65 \
    --access-logfile - \
    --error-logfile -
