from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from app.database.models import MenuPublication, MenuCopy, Menu, Member, Household
from app.services import planner, marketplace as market
from app.services.domain import MenuData
from app.seed.demo import demo
from app.bot.handlers.marketplace import market_action, market_input, show_market
from app.bot.handlers.actions import Input, callback
from app.bot.ui import Action, REPLY, NavigationMessage
from app.config import Settings
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from app.bot.handlers.screens import show


async def setup(s):
    a = await planner.ensure_user(s, 1, "Автор")
    menu = await planner.save_version(s, 1, a.active_household_id, demo(), name="Быстрые завтраки", action="edit")
    a.settings = {"weight": 52, "gpt_prompt": "PRIVATE", "timezone": "Europe/Berlin"}
    b = await planner.ensure_user(s, 2, "Читатель")
    return a, b, menu


async def publish(s, a, menu):
    return await market.publish(s, a.id, a.active_household_id, menu.id, menu.active_version_id, "Простые блюда на неделю", menu.name)


async def test_publish_sanitized_snapshot_and_search(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        assert await market.catalog(s) == []
        item = await publish(s, a, menu)
        assert item.payload["settings"] == {}
        assert item.payload["notifications"] == []
        assert item.payload["menu_id"] == 0
        assert "PRIVATE" not in str(item.payload)
        assert (await market.catalog(s, query="БЫСТРЫЕ"))[0].id == item.id
        assert await market.catalog(s, query="%") == []
        assert (await publish(s, a, menu)).id == item.id
        assert await s.scalar(select(func.count()).select_from(MenuPublication)) == 1


async def test_copy_is_independent_keeps_active_and_private_settings(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        old = await planner.save_version(s, b.id, b.active_household_id, demo(), action="edit")
        b.settings = {"weight": 90, "timezone": "UTC"}
        item = await publish(s, a, menu)
        copied, created = await market.copy_menu(s, b.id, b.active_household_id, item.id)
        assert created and copied.id != menu.id
        assert (await planner.active_menu(s, b.id, b.active_household_id)).id == old.id
        assert b.settings == {"weight": 90, "timezone": "UTC"}
        again, created = await market.copy_menu(s, b.id, b.active_household_id, item.id)
        assert not created and again.id == copied.id
        rows = await planner.personal_meals(s, a.id, a.active_household_id, {0})
        lunch = next(x["meal"] for x in rows if x["meal"].meal_type == "lunch")
        await planner.edit_amount(s, a.id, a.active_household_id, lunch.id, "chicken", 220)
        copy_data, _ = await planner.load_data(s, copied.active_version_id)
        original = MenuData.model_validate(item.payload)
        assert copy_data.recipes == original.recipes
        assert await market.copy_count(s, item.id) == 1


async def test_withdraw_hides_listing_but_preserves_copies(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        item = await publish(s, a, menu)
        copied, _ = await market.copy_menu(s, b.id, b.active_household_id, item.id)
        with pytest.raises(planner.AccessDenied):
            await market.withdraw(s, b.id, b.active_household_id, item.id)
        await market.withdraw(s, a.id, a.active_household_id, item.id)
        assert await market.catalog(s) == []
        assert (await market.catalog(s, author_id=a.id))[0].id == item.id
        with pytest.raises(ValueError):
            await market.listing(s, item.id, b.id)
        with pytest.raises(ValueError):
            await market.copy_menu(s, b.id, b.active_household_id, item.id)
        assert await s.get(Menu, copied.id)


async def test_permissions_for_publish_and_copy(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        item = await publish(s, a, menu)
        s.add(Member(household_id=a.active_household_id, user_id=b.id, role="VIEWER"))
        await s.flush()
        with pytest.raises(planner.AccessDenied):
            await market.prepare(s, b.id, a.active_household_id)
        with pytest.raises(planner.AccessDenied):
            await market.copy_menu(s, b.id, a.active_household_id, item.id)
        member = await s.get(Member, (a.active_household_id, b.id))
        member.role = "EDITOR"
        with pytest.raises(planner.AccessDenied):
            await market.copy_menu(s, b.id, a.active_household_id, item.id)
        h = await s.get(Household, a.active_household_id)
        h.editor_import = True
        assert (await market.copy_menu(s, b.id, a.active_household_id, item.id))[1]
        with pytest.raises(planner.AccessDenied):
            await market.prepare(s, b.id, a.active_household_id)


async def test_stale_publish_and_admin_block(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        with pytest.raises(ValueError, match="изменилось"):
            await market.publish(s, a.id, a.active_household_id, menu.id, menu.active_version_id + 1, "Описание", menu.name)
        item = await publish(s, a, menu)
        await market.withdraw(s, b.id, b.active_household_id, item.id, admin=True)
        assert await market.catalog(s) == []
        with pytest.raises(ValueError, match="администратором"):
            await publish(s, a, menu)


async def test_source_deletion_does_not_break_public_menu(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        item = await publish(s, a, menu)
        ident, hid = item.id, b.active_household_id
        h = await s.get(Household, a.active_household_id)
        h.active_menu_id = None
        await s.delete(menu)
    async with db() as s, s.begin():
        item = await market.listing(s, ident)
        assert item.source_menu_id is None and item.source_version_id is None
        assert (await market.copy_menu(s, 2, hid, ident))[1]


async def test_copy_can_be_added_again_after_deletion(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        item = await publish(s, a, menu)
        copied, _ = await market.copy_menu(s, b.id, b.active_household_id, item.id)
        ident, hid = item.id, b.active_household_id
        await s.delete(copied)
    async with db() as s, s.begin():
        assert (await market.copy_menu(s, 2, hid, ident))[1]
        assert await s.scalar(select(func.count()).select_from(MenuCopy)) == 1


async def test_catalog_pagination(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        for i in range(10):
            await publish(s, a, menu)
            menu = await planner.save_version(s, a.id, a.active_household_id, demo(), action="edit")
        assert len(await market.catalog(s)) == 9  # extra row indicates next page
        assert len(await market.catalog(s, page=1)) == 2


async def test_publication_workflow_requires_confirmation(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
        msg = SimpleNamespace(answer=AsyncMock(), text="Меню для занятых")
        settings = Settings(_env_file=None)
        await market_action(msg, s, a, state, settings, Action(a="marketpublish"), Input.text)
        await market_input(msg, s, a, state, await state.get_data())
        assert await s.scalar(select(func.count()).select_from(MenuPublication)) == 0
        assert "Все пользователи" in msg.answer.await_args.args[0]
        await market_action(msg, s, a, state, settings, Action(a="marketconfirm"), Input.text)
        assert await s.scalar(select(func.count()).select_from(MenuPublication)) == 1
        assert await state.get_state() is None


async def test_market_buttons_screens_and_cancel_input(db):
    async with db() as s, s.begin():
        a, b, menu = await setup(s)
        item = await publish(s, a, menu)
        msg = SimpleNamespace(answer=AsyncMock())
        for screen in ("market", "mypublic", "marketitem", "marketday"):
            await show_market(msg, s, a, screen, item.id)
        for screen in ("profile", "menu_manage", "house_manage"):
            await show(msg, s, a, screen)
        for call in msg.answer.await_args_list:
            assert len(call.args[0]) < 4096
            for row in call.kwargs["reply_markup"].inline_keyboard:
                assert all(len(button.callback_data.encode()) <= 64 for button in row)
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
        await state.set_state(Input.text)
        await state.set_data({"kind": "market_description", "ident": menu.id, "hid": a.active_household_id})
        q = SimpleNamespace(answer=AsyncMock(), message=msg)
        await callback(q, Action(a="menus", h=a.active_household_id), state, s, a, Settings(_env_file=None))
        assert await state.get_state() is None


async def test_navigation_reuses_message():
    msg = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
    nav = NavigationMessage(msg)
    await nav.answer("Первый экран")
    await nav.answer("Продолжение")
    msg.edit_text.assert_awaited_once_with("Первый экран")
    msg.answer.assert_awaited_once_with("Продолжение")
    assert len(REPLY.keyboard) == 3
    assert len({b.text for row in REPLY.keyboard for b in row}) == 6
