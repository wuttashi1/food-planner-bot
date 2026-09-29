import pytest
import aiohttp
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


@pytest.fixture(autouse=True)
def no_real_openrouter(monkeypatch):
    original = aiohttp.ClientSession._request

    async def guarded(self, method, url, *args, **kwargs):
        if "openrouter.ai" in str(url).lower():
            raise AssertionError("Real OpenRouter requests are prohibited in tests")
        return await original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "_request", guarded)
