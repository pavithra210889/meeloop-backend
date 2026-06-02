"""
Migration script: copy meme templates from meme_templates.sqlite → live PostgreSQL.

Usage:
    export DATABASE_URL="postgresql://user:pass@host:5432/meeloop"
    python migrate_templates_postgres.py

Optional: specify a default user UUID (must exist in the live DB).
If omitted, the script queries for the first user automatically.
"""
import os
import sqlite3
import uuid
import psycopg2
import psycopg2.extras

SOURCE_DB = "meme_templates.sqlite"
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("ERROR: Set DATABASE_URL environment variable first.\n"
                     "  export DATABASE_URL='postgresql://user:pass@host:5432/meeloop'")


def get_default_user_id(pg_conn):
    """Return the UUID of the first user in the live DB."""
    with pg_conn.cursor() as cur:
        cur.execute('SELECT id FROM "user" LIMIT 1')
        row = cur.fetchone()
        if not row:
            raise SystemExit("ERROR: No users found in the live database. "
                             "Create at least one user first.")
        return row[0]


def migrate():
    src = sqlite3.connect(SOURCE_DB)
    src.row_factory = sqlite3.Row
    src_cur = src.cursor()

    pg = psycopg2.connect(DATABASE_URL)
    psycopg2.extras.register_uuid()

    try:
        default_user_id = get_default_user_id(pg)
        print(f"Using default user ID: {default_user_id}")

        src_cur.execute("SELECT COUNT(*) FROM meme_templates_templates WHERE active = 1")
        total = src_cur.fetchone()[0]
        print(f"Source templates to migrate: {total}")

        with pg.cursor() as pg_cur:
            pg_cur.execute("SELECT COUNT(*) FROM memetemplates")
            existing = pg_cur.fetchone()[0]
        if existing > 0:
            answer = input(f"Target already has {existing} rows. Continue? (y/n): ")
            if answer.lower() != "y":
                print("Aborted.")
                return

        src_cur.execute("""
            SELECT id, content, hash_tags, urls, created_at, updated_at
            FROM meme_templates_templates
            WHERE active = 1
            ORDER BY id
        """)

        batch = []
        batch_size = 500
        migrated = skipped = errors = 0

        print("Migrating…")
        for row in src_cur:
            content = row["content"] or ""
            created_at = row["created_at"]
            updated_at = row["updated_at"]
            # urls is stored as JSON string in SQLite; pass through as-is (text column)
            urls = row["urls"] or "[]"
            hash_tags = row["hash_tags"] or "[]"

            new_id = str(uuid.uuid4())
            batch.append((
                new_id, "IMAGE", content, urls, hash_tags, "{}",
                created_at, updated_at, default_user_id, default_user_id,
            ))

            if len(batch) >= batch_size:
                migrated += _flush(pg, batch)
                batch = []
                print(f"  {migrated}/{total}")

        if batch:
            migrated += _flush(pg, batch)

        pg.commit()
        print(f"\nDone. Migrated: {migrated} | Errors: {errors}")

        with pg.cursor() as pg_cur:
            pg_cur.execute("SELECT COUNT(*) FROM memetemplates")
            print(f"Total rows in memetemplates: {pg_cur.fetchone()[0]}")

    except Exception:
        pg.rollback()
        raise
    finally:
        src.close()
        pg.close()


def _flush(pg, batch):
    sql = """
        INSERT INTO memetemplates
            (id, template_type, content, urls, hash_tags, metadata_info,
             created_at, updated_at, created_by_id, updated_by_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """
    with pg.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, batch, page_size=200)
    pg.commit()
    return len(batch)


if __name__ == "__main__":
    print("Meme Templates → PostgreSQL Migration")
    print("=" * 45)
    migrate()
