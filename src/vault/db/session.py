from collections.abc import AsyncGenerator
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from vault.config import settings

# Ensure data and media directories exist inside init_db() instead of module import time
def ensure_directories():
    settings.VAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    settings.videos_dir.mkdir(parents=True, exist_ok=True)
    settings.documents_dir.mkdir(parents=True, exist_ok=True)
    settings.slides_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

# NFS-safe SQLite pragmas: busy_timeout prevents lock contention; DELETE journal avoids NFS shared memory (.shm) bugs
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout = 10000")
    cursor.execute("PRAGMA journal_mode = DELETE")
    cursor.close()

async_session_maker = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    ensure_directories()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # Add collection_id column to jobs table if it was created in older schema
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN collection_id TEXT"))
        except Exception:
            pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
