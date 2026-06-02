"""
Migrate memetemplates table from local database.sqlite → Azure PostgreSQL (production).

Only touches the memetemplates table — nothing else is modified.

Usage:
    python3 push_templates_to_prod.py
"""
import sqlite3
import json
import psycopg2
import psycopg2.extras

LOCAL_DB     = "/Users/nanne/Downloads/personal/meeloop/backend/database.sqlite"
DATABASE_URL = "postgresql://meeloopdb:Meel00pPr0d2026!@meeloop-prod-db.postgres.database.azure.com/meeloop?sslmode=require"
BATCH_SIZE   = 500

INSERT_SQL = """
    INSERT INTO memetemplates
        (id, template_type, content, urls, hash_tags, metadata_info,
         created_at, updated_at, created_by_id, updated_by_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING
"""

def main():
    # ── Source ───────────────────────────────────────────────────
    src = sqlite3.connect(LOCAL_DB)
    src.row_factory = sqlite3.Row
    cur = src.cursor()

    cur.execute("SELECT COUNT(*) FROM memetemplates")
    total = cur.fetchone()[0]
    print(f"Local memetemplates rows: {total}")

    # ── Destination ──────────────────────────────────────────────
    pg = psycopg2.connect(DATABASE_URL)
    psycopg2.extras.register_uuid()

    with pg.cursor() as pgcur:
        pgcur.execute("SELECT COUNT(*) FROM memetemplates")
        existing = pgcur.fetchone()[0]
    print(f"Production memetemplates rows (before): {existing}")

    # Get a valid production user ID to satisfy the FK constraint
    with pg.cursor() as pgcur:
        pgcur.execute('SELECT id FROM "user" LIMIT 1')
        prod_user_id = pgcur.fetchone()[0]
    print(f"Using production user ID for FK: {prod_user_id}")

    if existing > 0:
        ans = input(f"Production already has {existing} rows. Continue anyway? (y/n): ")
        if ans.lower() != "y":
            print("Aborted.")
            return

    # ── Migrate ──────────────────────────────────────────────────
    cur.execute("""
        SELECT id, template_type, content, urls, hash_tags, metadata_info,
               created_at, updated_at, created_by_id, updated_by_id
        FROM memetemplates
    """)

    migrated = 0
    try:
        while True:
            rows = cur.fetchmany(BATCH_SIZE)
            if not rows:
                break

            batch = []
            for row in rows:
                urls       = row["urls"]       if isinstance(row["urls"], str)       else json.dumps(row["urls"])
                hash_tags  = row["hash_tags"]  if isinstance(row["hash_tags"], str)  else json.dumps(row["hash_tags"])
                meta       = row["metadata_info"] if isinstance(row["metadata_info"], str) else json.dumps(row["metadata_info"])

                batch.append((
                    row["id"],
                    row["template_type"],
                    row["content"],
                    urls,
                    hash_tags,
                    meta,
                    row["created_at"],
                    row["updated_at"],
                    prod_user_id,   # use valid production user
                    prod_user_id,
                ))

            with pg.cursor() as pgcur:
                psycopg2.extras.execute_batch(pgcur, INSERT_SQL, batch, page_size=BATCH_SIZE)
            pg.commit()

            migrated += len(batch)
            print(f"  {migrated}/{total} rows migrated...")

        print(f"\nMigration complete — {migrated} rows inserted (duplicates skipped).")

    except Exception as e:
        pg.rollback()
        raise e
    finally:
        with pg.cursor() as pgcur:
            pgcur.execute("SELECT COUNT(*) FROM memetemplates")
            print(f"Production memetemplates rows (after): {pgcur.fetchone()[0]}")
        src.close()
        pg.close()

if __name__ == "__main__":
    main()
