import asyncio
import time
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError
from app.services.planner import ensure_user
import structlog


class SessionMiddleware(BaseMiddleware):
    def __init__(self, sessions, settings):
        self.sessions = sessions
        self.settings = settings
        self.lock = asyncio.Lock()  # Shared with scheduler DB transactions; one SQLite writer at a time.
        self.last = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return
        message = event.message if isinstance(event, CallbackQuery) else event
        if not isinstance(message, Message) or message.chat.type != "private":
            return
        now = time.monotonic()
        if now - self.last.get(user.id, 0) < 0.25:
            if isinstance(event, CallbackQuery):
                await event.answer("Подождите секунду.")
            return
        self.last[user.id] = now
        if len(self.last) > 10000:
            self.last = {k: v for k, v in self.last.items() if now - v < 60}
        try:
            async with self.lock, self.sessions() as session:
                async with session.begin():
                    db_user = await ensure_user(session, user.id, user.first_name, self.settings.timezone)
                    data.update(session=session, user=db_user, settings=self.settings)
                    return await handler(event, data)
        except (ValueError, ValidationError) as exc:
            # Truncate external validation details; tokens never included.
            text = str(exc)[:1800]
            await message.answer("⚠️ " + text)
        except SQLAlchemyError:
            structlog.get_logger().error("database_failure", user_id=user.id)
            await message.answer("Не удалось сохранить изменения. Повторите операцию.")
        except Exception as exc:
            structlog.get_logger().error("handler_failure", error_type=type(exc).__name__, user_id=user.id)
            await message.answer("Не удалось выполнить действие. Откройте /menu и повторите.")
