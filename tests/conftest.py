import pytest_asyncio
from app.database.session import database
from app.database.models import Base


@pytest_asyncio.fixture
async def db(tmp_path):
    engine, sessions = database("sqlite+aiosqlite:///" + str(tmp_path / "test.db"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield sessions
    await engine.dispose()
