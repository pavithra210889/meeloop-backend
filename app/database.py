from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy import event
from app.config import settings

engine = create_engine(settings.DATABASE_URL, echo=settings.DATABASE_ECHO)


# SQLite needs an explicit pragma to enforce foreign keys; PostgreSQL does it by default.
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_session():
    with Session(engine) as session:
        yield session
