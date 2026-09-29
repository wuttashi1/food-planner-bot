import asyncio
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from sqlalchemy import text
from app.config import Settings
from app.database.session import database
from app.bot.middleware import SessionMiddleware
from app.bot.handlers.actions import router
from app.bot.handlers.ai import router as ai_router
from app.bot.commands import register
from app.scheduler.worker import worker
from app.logging_config import configure
import structlog


async def main(check=False):
    settings = Settings()
    token = settings.validate_token() if not check else None
    settings.directories()
    configure(settings.log_level)
    engine, sessions = database(settings.database_url)
    bot, task = None, None
    stop = asyncio.Event()
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT version_num FROM alembic_version"))
            await connection.execute(text("SELECT id FROM users LIMIT 1"))
        if check:
            print("Startup check OK: configuration, imports, migrated database.")
            return
        bot = Bot(token)
        dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
        middleware = SessionMiddleware(sessions, settings)
        dispatcher.message.outer_middleware(middleware)
        dispatcher.callback_query.outer_middleware(middleware)
        dispatcher.include_router(ai_router)
        dispatcher.include_router(router)
        await register(bot)
        task = asyncio.create_task(worker(sessions, bot, settings, stop, middleware.lock))
        structlog.get_logger().info("startup")
        await dispatcher.start_polling(bot, close_bot_session=False, tasks_concurrency_limit=32)
    finally:
        stop.set()
        if task:
            try:
                await asyncio.wait_for(task, 10)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if bot:
            await bot.session.close()
        await engine.dispose()
        structlog.get_logger().info("shutdown")


if __name__ == "__main__":
    try:
        asyncio.run(main(check="--check" in sys.argv))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("BOT_TOKEN is missing"):
            print(str(exc), file=sys.stderr)
        else:
            # Secret-bearing exceptions are intentionally not printed.
            print(f"Startup failed ({type(exc).__name__}). Check configuration and migrations.", file=sys.stderr)
        sys.exit(1)
