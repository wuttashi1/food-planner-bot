"""Single centralized persistent outbox scanner; UTC instants, IANA wall-clock rules."""

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
from sqlalchemy import select, delete
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from app.database.models import User, Notification, Delivery, utcnow
from app.services.planner import personal_meals
from app.services.backup import create_backup
from app.bot.ui import keyboard, button as b
import structlog

log = structlog.get_logger()


def due_occurrences(notice, zone, now, grace_minutes=5):
    """One occurrence on DST folds; nonexistent local times are skipped."""
    tz = ZoneInfo(zone)
    aware = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    local = aware.astimezone(tz)
    hour, minute = map(int, notice.time.split(":"))
    result = []
    for shift in (-1, 0, 1):
        day = local.date() + timedelta(days=shift)
        if notice.kind in ("shopping_week", "weekly_plan") and day.weekday() != notice.weekday:
            continue
        scheduled = datetime.combine(day, time(hour, minute), tzinfo=tz)
        utc = scheduled.astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) != scheduled.replace(tzinfo=None):
            continue
        due = utc - timedelta(minutes=notice.offset_minutes if notice.kind == "cook" else 0)
        if timedelta(0) <= aware - due <= timedelta(minutes=grace_minutes):
            result.append((day, due.replace(tzinfo=None)))
    return result


async def collect(sessions, now=None, lock=None):
    now = now or utcnow()
    async with lock if lock is not None else nullcontext(), sessions() as session, session.begin():
        notices = (await session.scalars(select(Notification).where(Notification.enabled.is_(True)))).all()
        for n in notices:
            u = await session.get(User, n.user_id)
            if not u or not u.active_household_id or not u.settings.get("notifications_enabled", True):
                continue
            if n.meal_type in u.settings.get("disabled_meals", []):
                continue
            for target, due in due_occurrences(n, u.settings.get("timezone", "Europe/Berlin"), now):
                key = f"notice:{n.id}:{u.active_household_id}:{target}"
                if await session.scalar(select(Delivery.id).where(Delivery.key == key)):
                    continue
                payload = {"hid": u.active_household_id, "notice_id": n.id}
                if n.kind in ("meal", "cook"):
                    meals = [
                        m
                        for m in await personal_meals(session, u.id, u.active_household_id, {target.weekday()})
                        if m["meal"].meal_type == n.meal_type
                    ]
                    if not meals:
                        continue
                    payload["meal_id"] = meals[0]["meal"].id
                    payload["date"] = target.isoformat()
                    payload["text"] = (
                        ("👨‍🍳 Скоро готовить" if n.kind == "cook" else "🍽 Время еды") + "\n" + "\n".join(m["meal"].name for m in meals)
                    )
                    if n.kind == "cook":
                        payload["text"] += (
                            f"\nЧерез {n.offset_minutes} мин приём пищи.\nГотовка ~{meals[0]['recipe'].prep_time + meals[0]['recipe'].cook_time} мин"
                        )
                else:
                    payload["text"] = {
                        "shopping_today": "🛒 Покупки на сегодня",
                        "shopping_tomorrow": "🛒 Покупки на завтра",
                        "shopping_week": "🛒 Недельная закупка",
                        "weekly_plan": "📅 Время спланировать неделю",
                    }[n.kind]
                    payload["action"] = {
                        "shopping_today": "shoptoday",
                        "shopping_tomorrow": "shoptomorrow",
                        "shopping_week": "shopweek",
                        "weekly_plan": "week",
                    }[n.kind]
                session.add(Delivery(user_id=u.id, key=key, due_at=due, payload=payload))
        await session.execute(delete(Delivery).where(Delivery.sent_at < now - timedelta(days=30)))


async def dispatch(sessions, bot, now=None, lock=None):
    now = now or utcnow()
    # Short DB reads, then network I/O outside the transaction.
    async with lock if lock is not None else nullcontext(), sessions() as session:
        ids = list(
            (
                await session.scalars(
                    select(Delivery.id)
                    .where(Delivery.sent_at.is_(None), Delivery.due_at <= now, Delivery.attempts < 5)
                    .order_by(Delivery.id)
                    .limit(20)
                )
            ).all()
        )
    for ident in ids:
        async with lock if lock is not None else nullcontext(), sessions() as session:
            d = await session.get(Delivery, ident)
            if d.retry_at and d.retry_at > now:
                continue
            user = await session.get(User, d.user_id)
            p, uid = dict(d.payload), d.user_id
            skip = False
            if not p.get("broadcast"):
                n = await session.get(Notification, p.get("notice_id")) if p.get("notice_id") else None
                skip = (
                    not user.settings.get("notifications_enabled", True)
                    or user.active_household_id != p.get("hid")
                    or (n is not None and (not n.enabled or n.meal_type in user.settings.get("disabled_meals", [])))
                )
                skip |= now - d.due_at > timedelta(hours=2)
                if p.get("meal_id") and not skip:
                    meals = await personal_meals(session, uid, p["hid"], {datetime.fromisoformat(p["date"]).weekday()})
                    skip = not any(m["meal"].id == p["meal_id"] for m in meals)
            buttons = None
            if p.get("meal_id"):
                buttons = keyboard(
                    [
                        [b("👨‍🍳 Рецепт", "recipe", p["meal_id"], day=p["date"]), b("🛒 Ингредиенты", "meal", p["meal_id"], day=p["date"])],
                        [b("✅ Готово", "cooked", ident), b("⏰ Через 15 мин", "snooze", ident)],
                    ],
                    p["hid"],
                )
            elif p.get("action"):
                buttons = keyboard([[b("Открыть", p["action"])]], p["hid"])
        success, retry, permanent = skip, None, False
        if not skip:
            try:
                await bot.send_message(uid, p["text"], reply_markup=buttons)
                success = True
            except TelegramRetryAfter as exc:
                retry = now + timedelta(seconds=exc.retry_after)
            except (TelegramForbiddenError, TelegramBadRequest):
                permanent = True
            except Exception as exc:
                log.error("notification_failure", delivery_id=ident, error_type=type(exc).__name__)
                retry = now + timedelta(minutes=1)
        async with lock if lock is not None else nullcontext(), sessions() as session, session.begin():
            d = await session.get(Delivery, ident)
            d.attempts += 1
            d.retry_at = retry
            if success or permanent:
                d.sent_at = now
            if permanent:
                log.warning("notification_unreachable", user_id=uid)
        await asyncio.sleep(0.06)  # Below Telegram bulk rate; respects RetryAfter on retries.


async def worker(sessions, bot, settings, stop, lock=None):
    while not stop.is_set():
        try:
            await collect(sessions, lock=lock)
            await dispatch(sessions, bot, lock=lock)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            marker = settings.data_dir / "backups" / "last_daily.txt"
            if settings.backup_enabled and (not marker.exists() or marker.read_text() != today):
                await asyncio.to_thread(create_backup, settings)
                marker.write_text(today)
            # Heartbeat is touched only after a successful scheduler/database pass.
            (settings.data_dir / "heartbeat").touch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("scheduler_failure", error_type=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=20)
        except TimeoutError:
            continue
