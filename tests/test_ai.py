import asyncio
import json
from datetime import date
from unittest.mock import AsyncMock
import aiohttp
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func
from app.config import Settings
from app.i18n import CATALOGS, t, locale_for
from app.database.models import AIUsage, MenuVersion, Member
from app.services import planner, ai_menu
from app.services.ai_schema import Request, Plan
from app.services.ai_ingredients import Resolver, normalize, convert, scale
from app.services.openrouter import OpenRouter, AIError
from app.services.domain import nutrition
from app.seed.demo import demo
from app.bot.handlers.ai import AIWizard, callback, summary


def plan():
    return Plan.model_validate(
        {
            "plan_name": "Menu",
            "days": [
                {
                    "day": 1,
                    "meals": [
                        {
                            "meal_type": kind,
                            "title": kind,
                            "ingredients": [{"name": "oats", "amount": 180, "unit": "g"}],
                            "instructions": "Cook oats in water.",
                            "estimated_cooking_minutes": 10,
                        }
                        for kind in ("breakfast", "lunch", "dinner")
                    ],
                }
            ],
        }
    )


def config(**values):
    return Settings(_env_file=None, openrouter_api_key="test-only", ai_user_cooldown_seconds=0, **values)


@pytest.mark.parametrize("locale", ["ru", "uk", "en", "de"])
def test_catalogs(locale):
    assert CATALOGS[locale].keys() == CATALOGS["en"].keys()
    assert all(CATALOGS[locale].values())
    assert t("menu", locale) in CATALOGS[locale].values()
    assert summary(Request(output_language=locale).model_dump(), locale)
    assert locale_for(locale + "-XX") == locale
    assert locale_for("fr") == "en"


def test_validation_resolution_and_scaling():
    with pytest.raises(ValidationError):
        Request(target_kcal=float("nan"))
    raw = plan().model_dump()
    raw["days"][0]["meals"][0]["ingredients"][0]["amount"] = 0
    with pytest.raises(ValidationError):
        Plan.model_validate(raw)
    resolver = Resolver(demo())
    assert normalize(" HÄHNCHENBRUST ") == "hähnchenbrust"
    assert len({resolver.resolve(n).ingredient_id for n in ("куриная грудка", "куряче філе", "chicken breast", "Hähnchenbrust")}) == 1
    with pytest.raises(AIError):
        resolver.resolve("unknown ingredient")
    request = Request(meals_per_day=3)
    data = convert(plan(), request, demo(), resolver)
    scaled = scale(data, 2500, resolver)
    assert sum(nutrition(r)["kcal"] for r in scaled.recipes) == pytest.approx(2500, abs=0.1)
    with pytest.raises(AIError):
        convert(plan(), Request(meals_per_day=4), demo(), resolver)
    with pytest.raises(AIError):
        convert(plan(), Request(meals_per_day=3, allergies=["gluten"]), demo(), resolver)
    with pytest.raises(AIError):
        resolver.blocked(Request(allergies=["unknown allergy"]))


class Response:
    def __init__(self, status, raw):
        self.status = status
        self.content = self
        self.raw = raw

    async def readexactly(self, size):
        raise asyncio.IncompleteReadError(self.raw, size)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class HTTP:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def post(self, url, **kwargs):
        self.calls.append(kwargs)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def response(status=200, content=None):
    raw = json.dumps({"choices": [{"message": {"content": content if content is not None else plan().model_dump_json()}}]}).encode()
    return Response(status, raw)


async def test_client_valid_ignores_macros():
    raw = plan().model_dump()
    raw["days"][0]["meals"][0]["kcal"] = 999999
    http = HTTP([response(content=json.dumps(raw))])
    result = await OpenRouter(config(), http).generate({"output_language": "en"})
    assert not hasattr(result.days[0].meals[0], "kcal")
    sent = http.calls[0]["json"]
    assert sent["provider"]["require_parameters"] is True
    assert sent["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize(
    "status,code", [(401, "auth"), (402, "credits"), (429, "rate_limit"), (500, "provider"), (503, "provider"), (404, "model")]
)
async def test_client_http_errors(status, code, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    http = HTTP([response(status), response(status)])
    with pytest.raises(AIError, match=code):
        await OpenRouter(config(), http).generate({})
    assert len(http.calls) <= 2


@pytest.mark.parametrize("content", ["bad JSON", "{}", ""])
async def test_client_invalid(content):
    with pytest.raises(AIError):
        await OpenRouter(config(), HTTP([response(content=content)])).generate({})


@pytest.mark.parametrize("error,code", [(asyncio.TimeoutError(), "timeout"), (aiohttp.ClientConnectionError(), "network")])
async def test_client_transport(error, code):
    with pytest.raises(AIError, match=code):
        await OpenRouter(config(), HTTP([error])).generate({})


async def test_retry(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    http = HTTP([response(503), response()])
    await OpenRouter(config(), http).generate({})
    assert len(http.calls) == 2


async def seed(db):
    async with db() as session, session.begin():
        user = await planner.ensure_user(session, 1, "Test")
        menu = await planner.save_version(session, user.id, user.active_household_id, demo(), action="edit")
        return user.active_household_id, menu.active_version_id


async def test_pipeline_save_shopping_limits_and_stale(db):
    hid, original = await seed(db)
    client = AsyncMock()
    client.generate.return_value = plan()
    request = Request(meals_per_day=3)
    lock = asyncio.Lock()
    preview = await ai_menu.generate(db, lock, 1, hid, request, config(ai_user_daily_limit=1), client=client)
    async with db() as session:
        menu = await planner.active_menu(session, 1, hid)
        assert menu.active_version_id == original
    async with db() as session, session.begin():
        await ai_menu.save(session, 1, hid, preview)
    async with db() as session:
        items = await planner.shopping(session, 1, hid, date(2026, 9, 28), date(2026, 9, 28))
        assert len(items) == 1 and items[0]["amount"] == 540
        assert (await session.scalar(select(AIUsage))).status == "saved"
    with pytest.raises(AIError, match="daily_limit"):
        await ai_menu.generate(db, lock, 1, hid, request, config(ai_user_daily_limit=1), client=client)
    with pytest.raises(ValueError):
        async with db() as session, session.begin():
            await ai_menu.save(session, 1, hid, preview)
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(MenuVersion)) == 2
    assert client.generate.await_count == 1
    assert "user_id" not in client.generate.call_args.args[0]


async def test_repair_and_local_errors_do_not_consume_quota(db):
    hid, _ = await seed(db)
    client = AsyncMock()
    client.generate.side_effect = [AIError("ingredients"), plan()]
    lock = asyncio.Lock()
    with pytest.raises(AIError, match="restriction"):
        await ai_menu.generate(db, lock, 1, hid, Request(allergies=["mystery"]), config(), client=client)
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(AIUsage)) == 0
    await ai_menu.generate(db, lock, 1, hid, Request(meals_per_day=3), config(), client=client)
    assert client.generate.await_count == 2


async def test_permissions_and_rollback(db):
    hid, original = await seed(db)
    async with db() as session, session.begin():
        await planner.ensure_user(session, 2, "Viewer")
        session.add(Member(household_id=hid, user_id=2, role="VIEWER"))
    with pytest.raises(planner.AccessDenied):
        await ai_menu.generate(db, asyncio.Lock(), 2, hid, Request(), config(), client=AsyncMock())
    client = AsyncMock(generate=AsyncMock(return_value=plan()))
    preview = await ai_menu.generate(db, asyncio.Lock(), 1, hid, Request(meals_per_day=3), config(), client=client)
    with pytest.raises(RuntimeError):
        async with db() as session, session.begin():
            await ai_menu.save(session, 1, hid, preview)
            raise RuntimeError("rollback")
    async with db() as session:
        menu = await planner.active_menu(session, 1, hid)
        assert menu.active_version_id == original


async def test_cancel_callback():
    from types import SimpleNamespace

    state = AsyncMock()
    state.get_state.return_value = AIWizard.input.state
    state.get_data.return_value = {}
    query = SimpleNamespace(data="ai:cancel", answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
    user = SimpleNamespace(settings={"locale": "de"})
    await callback(query, state, user, config(), None, None)
    query.answer.assert_awaited_once()
    state.clear.assert_awaited_once()
    assert query.message.edit_text.call_args.args[0] == t("menu", "de")


async def test_single_meal_regeneration_minimal_context(db):
    hid, _ = await seed(db)
    client = AsyncMock(generate=AsyncMock(return_value=plan()))
    lock = asyncio.Lock()
    request = Request(meals_per_day=3)
    first = await ai_menu.generate(db, lock, 1, hid, request, config(), client=client)
    replacement = plan()
    replacement.days[0].meals = replacement.days[0].meals[:1]
    replacement.days[0].meals[0].title = "Replacement"
    client.generate.return_value = replacement
    second = await ai_menu.generate(db, lock, 1, hid, request, config(), previous=first, day=0, meal=1, client=client)
    payload = client.generate.call_args.args[0]
    assert payload["days"] == 1 and payload["meals_per_day"] == 1
    assert len(payload["other_meals"]) == 2
    assert len(second["data"]["meals"]) == 3
    assert next(m for m in second["data"]["meals"] if m["meal_order"] == 1)["meal_name"] == "Replacement"


async def test_persisted_locale_and_cooldown(db):
    from app.database.models import User

    hid, _ = await seed(db)
    settings = Settings(_env_file=None, openrouter_api_key="test-only", ai_user_cooldown_seconds=60)
    async with db() as session, session.begin():
        user = await session.get(User, 1)
        user.settings = user.settings | {"locale": "uk"}
        await ai_menu.reserve(session, 1, Request(), settings)
    async with db() as session, session.begin():
        assert (await session.get(User, 1)).settings["locale"] == "uk"
        with pytest.raises(AIError, match="cooldown"):
            await ai_menu.reserve(session, 1, Request(), settings)


async def test_network_guard():
    async with aiohttp.ClientSession() as session:
        with pytest.raises(AssertionError, match="prohibited"):
            await session.post("https://openrouter.ai/api/v1/chat/completions")


async def test_missing_key(db):
    with pytest.raises(AIError, match="not_configured"):
        await ai_menu.generate(db, asyncio.Lock(), 1, 1, Request(), Settings(_env_file=None, openrouter_api_key=""))


async def test_wizard_choices_and_summary(db):
    from types import SimpleNamespace
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    from app.bot.handlers.ai import accept

    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
    await state.set_state(AIWizard.input)
    await state.set_data({"request": Request().model_dump(), "step": 0})
    message = SimpleNamespace(answer=AsyncMock())
    await accept(message, state, 3, "en")
    assert (await state.get_data())["request"]["days"] == 3
    await state.update_data(step=10)
    await accept(message, state, "Simple meals", "en")
    assert await state.get_state() == AIWizard.summary.state
