"""
Migration script to copy meme templates from meme_templates.sqlite to database.sqlite
"""
import sqlite3
import uuid
from pathlib import Path

# Database paths
SOURCE_DB = "meme_templates.sqlite"
TARGET_DB = "database.sqlite"
DEFAULT_USER_ID = 1  # Use first user as default for NULL created_by_id

def migrate_templates():
    """Migrate templates from source to target database"""
    
    # Connect to both databases
    source_conn = sqlite3.connect(SOURCE_DB)
    target_conn = sqlite3.connect(TARGET_DB)
    
    source_cursor = source_conn.cursor()
    target_cursor = target_conn.cursor()
    
    try:
        # Get count of records to migrate
        source_cursor.execute("SELECT COUNT(*) FROM meme_templates_templates WHERE active = 1")
        total_count = source_cursor.fetchone()[0]
        print(f"Found {total_count} active templates to migrate")
        
        # Check if target table is empty
        target_cursor.execute("SELECT COUNT(*) FROM memetemplates")
        existing_count = target_cursor.fetchone()[0]
        if existing_count > 0:
            response = input(f"Target table already has {existing_count} records. Continue? (y/n): ")
            if response.lower() != 'y':
                print("Migration cancelled")
                return
        
        # Fetch all active templates from source
        source_cursor.execute("""
            SELECT id, content, hash_tags, urls, created_at, updated_at, created_by_id
            FROM meme_templates_templates
            WHERE active = 1
            ORDER BY id
        """)
        
        migrated = 0
        skipped = 0
        errors = 0
        
        print("\nStarting migration...")
        
        for row in source_cursor.fetchall():
            template_id, content, hash_tags, urls, created_at, updated_at, created_by_id = row
            
            # Handle NULL content (use empty string)
            if content is None:
                content = ""
            
            # Handle NULL created_by_id (use default user)
            if created_by_id is None:
                created_by_id = DEFAULT_USER_ID
                print(f"  Template {template_id}: Using default user ID {DEFAULT_USER_ID} for NULL created_by_id")
            
            # Set updated_by_id to same as created_by_id
            updated_by_id = created_by_id
            
            try:
                # Generate a UUID string for the id column (backend uses UUID7/string PKs)
                new_id = str(uuid.uuid4())

                target_cursor.execute("""
                    INSERT INTO memetemplates
                    (id, template_type, content, urls, hash_tags, metadata_info, created_at, updated_at, created_by_id, updated_by_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (new_id, "IMAGE", content, urls, hash_tags, "{}", created_at, updated_at, created_by_id, updated_by_id))
                
                migrated += 1
                if migrated % 1000 == 0:
                    print(f"  Migrated {migrated}/{total_count} templates...")
                    target_conn.commit()
                    
            except sqlite3.IntegrityError as e:
                if "UNIQUE constraint" in str(e):
                    print(f"  Skipping template {template_id}: Already exists")
                    skipped += 1
                else:
                    print(f"  Error migrating template {template_id}: {e}")
                    errors += 1
            except Exception as e:
                print(f"  Error migrating template {template_id}: {e}")
                errors += 1
        
        # Final commit
        target_conn.commit()
        
        print(f"\nMigration completed!")
        print(f"  Migrated: {migrated}")
        print(f"  Skipped: {skipped}")
        print(f"  Errors: {errors}")
        
        # Verify migration
        target_cursor.execute("SELECT COUNT(*) FROM memetemplates")
        final_count = target_cursor.fetchone()[0]
        print(f"\nTotal templates in target database: {final_count}")
        
    except Exception as e:
        print(f"Migration failed: {e}")
        target_conn.rollback()
        raise
    finally:
        source_conn.close()
        target_conn.close()

if __name__ == "__main__":
    print("Meme Templates Migration Script")
    print("=" * 50)
    migrate_templates()

