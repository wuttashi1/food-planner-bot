from aiogram.types import BotCommand

COMMAND_DESCRIPTIONS = {
    "ai": "✨ AI Menu",
    "start": "👋 Начало работы",
    "menu": "🏠 Главная",
    "menus": "📋 Мои меню",
    "market": "🏪 Общий магазин меню",
    "profile": "👤 Мой профиль",
    "today": "🍽 Рацион на сегодня",
    "tomorrow": "🌙 Рацион на завтра",
    "week": "📅 Меню на неделю",
    "shopping": "🛒 Список покупок",
    "shopping_today": "☀️ Покупки сегодня",
    "shopping_tomorrow": "🌙 Покупки завтра",
    "shopping_week": "📅 Покупки недели",
    "meals": "🥘 Мои блюда и рецепты",
    "nutrition": "📊 Калории, БЖУ и цели",
    "weight": "⚖️ Учёт веса",
    "reminders": "⏰ Настройки уведомлений",
    "import": "📥 Импорт меню Excel",
    "export": "📤 Скачать меню",
    "template": "📄 Excel-шаблон",
    "household": "👥 Совместный рацион",
    "invite": "🔗 Пригласить участника",
    "settings": "⚙️ Настройки",
    "help": "❓ Помощь",
    "backup": "🗄 Backup для администратора",
    "admin": "🛠 Админ-панель",
}


async def register(bot):
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMAND_DESCRIPTIONS.items()])
