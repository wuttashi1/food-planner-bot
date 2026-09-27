from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from app.bot.handlers.screens import show, today
from app.bot.handlers.actions import callback, text_input, wizard
from app.bot.ui import Action
from app.config import Settings
from app.database.models import Notification, MealAssignment
from app.seed.demo import demo
from app.services import planner


@pytest.fixture
def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=123, chat_id=1, user_id=1))


@pytest.fixture
def message():
    return SimpleNamespace(
        answer=AsyncMock(),
        answer_document=AsyncMock(),
        bot=SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="TestBot"))),
        text="",
    )


async def user_and_menu(s):
    user = await planner.ensure_user(s, 1, "A")
    menu = await planner.save_version(s, 1, user.active_household_id, demo(), action="edit")
    return user, menu


async def click(action, message, state, s, user, **kwargs):
    q = SimpleNamespace(answer=AsyncMock(), message=message)
    await callback(q, Action(a=action, h=user.active_household_id, **kwargs), state, s, user, Settings(admin_ids="1", _env_file=None))


@pytest.mark.parametrize(
    "screen",
    [
        "home",
        "today",
        "tomorrow",
        "week",
        "wholeweek",
        "nutrition",
        "nutrition_week",
        "shopping",
        "shoptoday",
        "shoptomorrow",
        "shopweek",
        "personalshop",
        "meals",
        "weight",
        "excel",
        "prompt",
        "menus",
        "versions",
        "imports",
        "household",
        "members",
        "permissions",
        "settings",
        "units",
        "mealtypes",
        "reminders",
        "help",
        "helpstart",
        "helptrouble",
        "admin",
        "adminusers",
        "adminhouses",
        "adminimports",
        "adminerrors",
    ],
)
async def test_every_screen_and_callback_limit(db, message, screen):
    async with db() as s, s.begin():
        user, menu = await user_and_menu(s)
        await show(message, s, user, screen, settings=Settings(admin_ids="1", _env_file=None))
        assert message.answer.await_count
        for call in message.answer.await_args_list:
            assert len(call.args[0]) <= 4096
            markup = call.kwargs.get("reply_markup")
            if markup:
                for row in markup.inline_keyboard:
                    for button in row:
                        assert len(button.callback_data.encode()) <= 64


async def test_edit_amount_via_buttons_and_personal_portion(db, message, state):
    async with db() as s, s.begin():
        user, menu = await user_and_menu(s)
        row = next(m for m in await planner.personal_meals(s, 1, user.active_household_id, {0}) if m["meal"].meal_type == "lunch")
        await click("editmeal", message, state, s, user, i=row["meal"].id)
        await click("pickamount", message, state, s, user, i=row["meal"].id, p=1)
        message.text = "220"
        await text_input(message, state, s, user, Settings(_env_file=None))
        row = next(m for m in await planner.personal_meals(s, 1, user.active_household_id, {0}) if m["meal"].meal_type == "lunch")
        assert row["nutrition"]["kcal"] == 708.5
        await click("pickpersonal", message, state, s, user, i=row["meal"].id, p=1)
        message.text = "150"
        await text_input(message, state, s, user, Settings(_env_file=None))
        assignment = await s.get(MealAssignment, (menu.id, 1, "0:lunch:3"))
        assert assignment.amounts["chicken"] == 150


async def test_notice_wizard_persists_cook_offset(db, message, state):
    async with db() as s, s.begin():
        user, _ = await user_and_menu(s)
        await click("addnotice", message, state, s, user)
        await click("noticekind", message, state, s, user, d="cook")
        await click("noticemeal", message, state, s, user, d="lunch")
        await click("noticeoffset", message, state, s, user, i=30)
        message.text = "13:00"
        await text_input(message, state, s, user, Settings(_env_file=None))
        n = await s.scalar(select(Notification).where(Notification.user_id == 1))
        assert (n.kind, n.meal_type, n.time, n.offset_minutes) == ("cook", "lunch", "13:00", 30)


async def test_onboarding_wizard(db, message, state):
    async with db() as s, s.begin():
        user, _ = await user_and_menu(s)
        await click("setup", message, state, s, user)
        for text in ("Anna", "52", "175", "65", "2600", "1 3 4 5", "Europe/Berlin", "да"):
            message.text = text
            await wizard(message, state, s, user)
        assert user.name == "Anna"
        assert user.settings["disabled_meals"] == ["late_snack", "second_breakfast"]
        assert await state.get_state() is None


async def test_stale_household_callback_rejected(db, message, state):
    async with db() as s, s.begin():
        user, _ = await user_and_menu(s)
        q = SimpleNamespace(answer=AsyncMock(), message=message)
        with pytest.raises(ValueError, match="сменили"):
            await callback(q, Action(a="home", h=999), state, s, user, Settings(_env_file=None))


async def test_meal_recipe_and_eat_buttons(db, message, state):
    async with db() as s, s.begin():
        user, _ = await user_and_menu(s)
        row = (await planner.personal_meals(s, 1, user.active_household_id, {today(user).weekday()}))[0]
        await click("meal", message, state, s, user, i=row["meal"].id)
        await click("recipe", message, state, s, user, i=row["meal"].id)
        await click("eat", message, state, s, user, i=row["meal"].id, d=today(user).isoformat())
        assert "съедено" in message.answer.await_args.args[0]


async def test_import_preview_no_changes_until_confirm(db, message, state):
    from app.database.models import MenuVersion
    from sqlalchemy import func
    from app.bot.handlers.actions import begin_import, import_file
    from app.services.excel import export_workbook

    settings = Settings(_env_file=None)
    async with db() as s, s.begin():
        user, menu = await user_and_menu(s)
        data, _ = await planner.load_data(s, menu.active_version_id)
        raw = export_workbook(data)
        original = menu.active_version_id
        await begin_import(message, state, s, user)
        message.document = SimpleNamespace(
            file_name="menu.xlsx", file_size=len(raw), mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        async def transfer(doc, destination):
            destination.write(raw)

        message.bot.download = transfer
        await import_file(message, state, s, user, settings)
        assert menu.active_version_id == original
        assert await s.scalar(select(func.count()).select_from(MenuVersion)) == 1
        await click("warnings", message, state, s, user)
        await click("compare", message, state, s, user)
        await click("confirmimport", message, state, s, user, i=0)
        assert menu.active_version_id != original
        assert await s.scalar(select(func.count()).select_from(MenuVersion)) == 2
        assert await state.get_state() is None


async def test_reply_navigation_wins_over_text_fsm(db, state):
    from aiogram.types import Message, Chat, User as TelegramUser
    from app.bot.handlers.actions import router, Input
    from datetime import datetime, timezone

    event = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=TelegramUser(id=1, is_bot=False, first_name="A"),
        text="🍽 Сегодня",
    )
    await state.set_state(Input.text)
    # Exercise aiogram filters in registration order, including FSM state.
    matched = None
    for handler in router.message.handlers:
        result, _ = await handler.check(event, raw_state=Input.text.state, bot=SimpleNamespace())
        if result:
            matched = handler.callback.__name__
            break
    assert matched == "reply"


async def test_invite_start_confirmation(db, message, state):
    from app.bot.handlers.actions import start

    async with db() as s, s.begin():
        owner, _ = await user_and_menu(s)
        token = await planner.invite(s, owner.id, owner.active_household_id)
        guest = await planner.ensure_user(s, 2, "Guest")
        await s.flush()
        message.text = "/start join_" + token
        await start(message, state, s, guest, Settings(_env_file=None))
        assert "A приглашает" in message.answer.await_args.args[0]
        assert guest.active_household_id != owner.active_household_id
        await click("join", message, state, s, guest)
        assert guest.active_household_id == owner.active_household_id
