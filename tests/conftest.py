import os
from pathlib import Path

# Set environment variable BEFORE importing any vault modules
TEST_DB_PATH = Path("./data/test_vault.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH}"

import asyncio
import pytest
from sqlmodel import SQLModel

from vault.db.session import engine, init_db


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    async def init():
        await init_db()

    asyncio.run(init())

    yield

    async def cleanup():
        await engine.dispose()
        if TEST_DB_PATH.is_file():
            try:
                TEST_DB_PATH.unlink()
            except Exception:
                pass

    asyncio.run(cleanup())
