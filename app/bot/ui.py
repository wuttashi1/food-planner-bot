from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from app.services.domain import MEALS


class Action(CallbackData, prefix="f"):
    a: str
    i: int = 0
    p: int = 0
    d: str = ""
    h: int = 0


def keyboard(rows, hid=0):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label[:60], callback_data=Action(a=action, i=ident, p=page, d=day, h=hid).pack())
                for label, action, ident, page, day in row
            ]
            for row in rows
        ]
    )


def button(label, action, ident=0, page=0, day=""):
    return label, action, ident, page, day


def nav(hid):
    return keyboard([[button("🏠 Главная", "home")]], hid)


REPLY = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=t) for t in row]
        for row in [
            ("🍽 Сегодня", "🛒 Покупки"),
            ("📋 Мои меню", "🏪 Магазин меню"),
            ("👤 Мой профиль", "🏠 Главная"),
        ]
    ],
    resize_keyboard=True,
)


def totals(rows):
    values = {key: sum(row["nutrition"][key] for row in rows) for key in ("kcal", "protein", "fat", "carbs")}
    return f"🔥 {values['kcal']:.0f} kcal\n🥩 Белки: {values['protein']:.1f} г\n🥑 Жиры: {values['fat']:.1f} г\n🍚 Углеводы: {values['carbs']:.1f} г"


def meal_label(kind):
    return MEALS.get(kind, kind)


def pages(rows, action, page, total, day=""):
    controls = []
    if page > 0:
        controls.append(button("⬅️", action, page=page - 1, day=day))
    if (page + 1) * 8 < total:
        controls.append(button("➡️", action, page=page + 1, day=day))
    if controls:
        rows.append(controls)
    parents = {
        "day": ("Неделя", "week"),
        "meals": ("Мои меню", "menus"),
        "versions": ("Управление меню", "menu_manage"),
        "imports": ("Excel", "excel"),
        "members": ("Совместный рацион", "household"),
        "weight": ("Мой профиль", "profile"),
        "reminders": ("Мой профиль", "profile"),
        "shoptoday": ("Покупки", "shopping"),
        "shoptomorrow": ("Покупки", "shopping"),
        "shopweek": ("Покупки", "shopping"),
        "personalshop": ("Покупки", "shopping"),
    }
    parent = parents.get(action)
    if parent:
        rows.append([button("⬅️ " + parent[0], parent[1]), button("🏠 Главная", "home")])
    else:
        rows.append([button("🏠 Главная", "home")])
    return rows


class NavigationMessage:
    """Reuse the clicked screen; follow-up messages/documents remain ordinary messages."""

    def __init__(self, message):
        self.message = message
        self.used = False

    def __getattr__(self, key):
        return getattr(self.message, key)

    async def answer(self, text, **kwargs):
        from aiogram.exceptions import TelegramBadRequest

        edit = getattr(self.message, "edit_text", None)
        if not self.used and edit is not None and not isinstance(kwargs.get("reply_markup"), ReplyKeyboardMarkup):
            self.used = True
            try:
                return await edit(text, **kwargs)
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    return self.message
                if "message can't be edited" not in str(exc) and "message to edit not found" not in str(exc):
                    raise
        self.used = True
        return await self.message.answer(text, **kwargs)
