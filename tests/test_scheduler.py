from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from sqlalchemy import select, func
from app.database.models import Notification, Delivery, User
from app.scheduler.worker import due_occurrences, collect, dispatch
from app.services import planner
from app.seed.demo import demo


def notice(**kwargs):
    return SimpleNamespace(time="13:00", kind="meal", weekday=5, offset_minutes=0, **kwargs)


def test_timezone_berlin_summer_winter():
    n = notice()
    assert due_occurrences(n, "Europe/Berlin", datetime(2026, 9, 18, 11, 0))
    assert not due_occurrences(n, "Europe/Berlin", datetime(2026, 9, 18, 13, 0))
    assert due_occurrences(n, "Europe/Berlin", datetime(2026, 1, 18, 12, 0))


def test_dst_nonexistent_and_ambiguous_once():
    n = notice()
    n.time = "02:30"
    assert not due_occurrences(n, "Europe/Berlin", datetime(2026, 3, 29, 1, 30))
    assert due_occurrences(n, "Europe/Berlin", datetime(2026, 10, 25, 0, 30))
    assert not due_occurrences(n, "Europe/Berlin", datetime(2026, 10, 25, 1, 30))


def test_cook_offset_crosses_midnight():
    n = notice()
    n.kind, n.time, n.offset_minutes = "cook", "00:15", 30
    hits = due_occurrences(n, "Europe/Berlin", datetime(2026, 9, 17, 21, 45))
    assert hits[0][0].isoformat() == "2026-09-18"


async def test_persistent_queue_idempotent_and_personal_disable(db):
    async with db() as s, s.begin():
        u = await planner.ensure_user(s, 1, "A")
        await planner.save_version(s, 1, u.active_household_id, demo(), action="edit")
        s.add(Notification(user_id=1, kind="meal", meal_type="lunch", enabled=True, time="13:00", offset_minutes=0))
    now = datetime(2026, 9, 18, 11)
    await collect(db, now)
    await collect(db, now)
    async with db() as s:
        assert await s.scalar(select(func.count()).select_from(Delivery)) == 1
    bot = SimpleNamespace(send_message=AsyncMock())
    await dispatch(db, bot, now)
    await dispatch(db, bot, now)
    assert bot.send_message.await_count == 1
    async with db() as s, s.begin():
        u = await s.get(User, 1)
        u.settings = u.settings | {"disabled_meals": ["lunch"]}
    await collect(db, datetime(2026, 9, 19, 11))
    async with db() as s:
        assert await s.scalar(select(func.count()).select_from(Delivery)) == 1
