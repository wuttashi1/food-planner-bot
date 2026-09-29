import json
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pydantic import ValidationError
from sqlalchemy import select
from app.i18n import t, LOCALES
from app.database.models import User, AIUsage
from app.services.ai_schema import Request
from app.services import ai_menu
from app.services.openrouter import AIError
from app.services.domain import MenuData, DAYS, nutrition
from app.bot.ui import NavigationMessage

router = Router(name="ai_menu")


class AIWizard(StatesGroup):
    input = State()
    summary = State()
    preview = State()
    generating = State()


FIELDS = [
    "days",
    "goal",
    "target_kcal",
    "meals_per_day",
    "budget_eur",
    "allergies",
    "excluded_foods",
    "disliked_foods",
    "diet",
    "max_cooking_minutes",
    "wishes",
]
LABELS = {"target_kcal": "calories", "meals_per_day": "meals", "budget_eur": "budget", "max_cooking_minutes": "cooking"}
CHOICES = {
    "days": [1, 3, 5, 7],
    "goal": ["weight_gain", "maintenance", "weight_loss", "custom"],
    "meals_per_day": [3, 4, 5],
    "diet": ["regular", "vegetarian", "custom"],
    "max_cooking_minutes": [15, 30, 45, None],
}


def kb(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=str(label)[:60], callback_data="ai:" + value) for label, value in row] for row in rows]
    )


def summary(request, locale):
    lines = [t("summary", locale)]
    for key in FIELDS:
        value = request[key]
        if value is None:
            value = t("none", locale)
        elif isinstance(value, list):
            value = ", ".join(value) or "—"
        elif key in ("goal", "diet"):
            value = t(value, locale)
        lines.append(f"{t(LABELS.get(key, key), locale)}: {value}")
    return "\n".join(lines)


async def menu(message, locale):
    await message.answer(
        t("menu", locale),
        reply_markup=kb(
            [
                [(t("create", locale), "create")],
                [(t("settings", locale), "settings")],
                [(t("last", locale), "last"), (t("help", locale), "help")],
                [(t("back", locale), "exit")],
            ]
        ),
    )


@router.message(Command("ai"))
async def entry(message, state, user):
    await state.clear()
    await menu(message, user.settings["locale"])


async def show_summary(message, state, request, locale):
    await state.set_state(AIWizard.summary)
    await state.update_data(request=request)
    await message.answer(
        summary(request, locale),
        reply_markup=kb([[(t("generate", locale), "generate")], [(t("edit", locale), "edit"), (t("cancel", locale), "cancel")]]),
    )


async def prompt(message, state, locale):
    data = await state.get_data()
    step = data["step"]
    if step == len(FIELDS):
        request = Request.model_validate(data["request"])
        await show_summary(message, state, request.model_dump(), locale)
        return
    field = FIELDS[step]
    rows = []
    for value in CHOICES.get(field, []):
        label = t("none", locale) if value is None else t(str(value), locale)
        rows.append([(label, f"value:{step}:{json.dumps(value)}")])
    if field == "target_kcal":
        rows.append([(t("profile", locale, value=data["request"]["target_kcal"]), f"value:{step}:{data['request']['target_kcal']}")])
    if field == "budget_eur":
        rows.append([(t("none", locale), f"value:{step}:null")])
    rows.append([(t("cancel", locale), "cancel")])
    await message.answer(t(LABELS.get(field, field), locale), reply_markup=kb(rows))


async def accept(message, state, value, locale):
    data = await state.get_data()
    field = FIELDS[data["step"]]
    request = data["request"] | {field: value}
    try:
        Request.model_validate(request)
    except ValidationError:
        await message.answer(t("error.input", locale))
        return
    await state.update_data(request=request, step=data["step"] + 1)
    await prompt(message, state, locale)


@router.message(AIWizard.input, F.text, ~F.text.startswith("/"))
async def text_input(message, state, user):
    locale = user.settings["locale"]
    data = await state.get_data()
    field = FIELDS[data["step"]]
    raw = message.text.strip()
    try:
        if field in CHOICES:
            raise ValueError()
        if field in ("target_kcal", "budget_eur"):
            value = float(raw)
        elif field in ("allergies", "excluded_foods", "disliked_foods"):
            value = [] if raw == "-" else [part.strip() for part in raw.split(",")]
        else:
            value = "" if raw == "-" else raw
        await accept(message, state, value, locale)
    except (ValueError, KeyError):
        await message.answer(t("error.input", locale))


async def preview(message, state, locale, page=0):
    stored = (await state.get_data())["preview"]
    data = MenuData.model_validate(stored["data"])
    days = sorted({DAYS.index(m.day) for m in data.meals})
    page = max(0, min(page, len(days) - 1))
    day = days[page]
    recipes = {r.recipe_id: r for r in data.recipes}
    meals = sorted((m for m in data.meals if DAYS.index(m.day) == day), key=lambda m: m.meal_order)
    totals = {key: 0.0 for key in ("kcal", "protein", "fat", "carbs")}
    lines = [stored["name"], t(DAYS[day], locale), t("preview_note", locale)]
    for meal in meals:
        lines.append(f"{t(meal.meal_type, locale)} — {meal.meal_name}")
        for key, value in nutrition(recipes[meal.recipe_id], meal.portion).items():
            totals[key] += value
    lines += [f"🔥 {totals['kcal']:.0f} kcal"] + [f"{t(k, locale)}: {totals[k]:.1f} g" for k in ("protein", "fat", "carbs")]
    rows = [[(t(DAYS[d], locale), f"page:{i}") for i, d in enumerate(days[:4])]]
    if len(days) > 4:
        rows.append([(t(DAYS[d], locale), f"page:{i}") for i, d in enumerate(days) if i >= 4])
    rows += [
        [(t("save", locale), f"save:{stored['usage_id']}")],
        [(t("all", locale), "generate"), (t("day", locale), f"regen:{day}")],
        [(t("meal", locale), f"pick:{day}")],
        [(t("edit", locale), "edit"), (t("cancel", locale), "cancel")],
    ]
    await state.set_state(AIWizard.preview)
    await message.answer("\n\n".join(lines), reply_markup=kb(rows))


@router.callback_query(F.data.startswith("ai:"))
async def callback(query, state, user, settings, sessions, db_lock):
    await query.answer()
    message = NavigationMessage(query.message)
    locale = user.settings["locale"]
    parts = query.data.split(":")[1:]
    action = parts[0]
    current = await state.get_state()
    stored = await state.get_data()
    try:
        if action in ("menu", "cancel", "exit"):
            await state.clear()
            if action == "exit":
                from app.bot.handlers.screens import show

                async with db_lock, sessions() as session, session.begin():
                    await show(message, session, user, settings=settings)
            else:
                await menu(message, locale)
        elif action == "language":
            await message.answer(
                t("language", locale),
                reply_markup=kb(
                    [
                        [(label, "locale:" + code)]
                        for code, label in zip(LOCALES, ["🇷🇺 Русский", "🇺🇦 Українська", "🇬🇧 English", "🇩🇪 Deutsch"])
                    ]
                ),
            )
        elif action == "locale":
            if parts[1] not in LOCALES:
                raise AIError("input")
            async with db_lock, sessions() as session, session.begin():
                account = await session.get(User, user.id)
                account.settings = account.settings | {"locale": parts[1]}
            await state.clear()
            await menu(message, parts[1])
        elif action == "help":
            await message.answer(t("help_text", locale), reply_markup=kb([[(t("back", locale), "menu")]]))
        elif action == "last":
            if stored.get("preview"):
                await preview(message, state, locale)
            else:
                async with sessions() as session:
                    usage = await session.scalar(select(AIUsage).where(AIUsage.user_id == user.id).order_by(AIUsage.id.desc()).limit(1))
                text = (
                    t("empty", locale)
                    if not usage
                    else f"{usage.created_at:%Y-%m-%d %H:%M} UTC · {usage.model}\n"
                    + t("saved" if usage.status == "saved" else "error." + (usage.error_code or "stale"), locale)
                )
                await message.answer(text, reply_markup=kb([[(t("back", locale), "menu")]]))
        elif action in ("create", "settings", "edit"):
            request = Request.model_validate(
                {"target_kcal": user.settings.get("target_calories", 2000)}
                | user.settings.get("ai_preferences", {})
                | {"output_language": locale}
            ).model_dump()
            if action == "edit":
                request = stored.get("request", request)
            await state.set_data({"request": request, "hid": user.active_household_id, "step": 0})
            if action == "create" and user.settings.get("ai_preferences"):
                await show_summary(message, state, request, locale)
            else:
                await state.set_state(AIWizard.input)
                await prompt(message, state, locale)
        elif action == "value":
            if current != AIWizard.input.state or int(parts[1]) != stored.get("step"):
                raise AIError("stale")
            await accept(message, state, json.loads(":".join(parts[2:])), locale)
        elif action in ("generate", "regen"):
            if current not in (AIWizard.summary.state, AIWizard.preview.state) or stored.get("hid") != user.active_household_id:
                raise AIError("stale")
            request = Request.model_validate(stored["request"] | {"output_language": locale})
            async with db_lock, sessions() as session, session.begin():
                account = await session.get(User, user.id)
                account.settings = account.settings | {"ai_preferences": request.model_dump()}
            await state.set_state(AIWizard.generating)
            progress = await message.answer(t("generating", locale))
            try:
                result = await ai_menu.generate(
                    sessions,
                    db_lock,
                    user.id,
                    user.active_household_id,
                    request,
                    settings,
                    previous=stored.get("preview"),
                    day=int(parts[1]) if action == "regen" else None,
                    meal=int(parts[2]) if len(parts) > 2 else None,
                )
            except Exception:
                message = NavigationMessage(progress)
                raise
            await state.update_data(preview=result)
            await preview(NavigationMessage(progress), state, locale)
        elif action == "page":
            if current != AIWizard.preview.state:
                raise AIError("stale")
            await preview(message, state, locale, int(parts[1]))
        elif action == "pick":
            if current != AIWizard.preview.state:
                raise AIError("stale")
            day = int(parts[1])
            data = MenuData.model_validate(stored["preview"]["data"])
            await message.answer(
                t("meal", locale),
                reply_markup=kb([[(m.meal_name, f"regen:{day}:{m.meal_order}")] for m in data.meals if DAYS.index(m.day) == day]),
            )
        elif action == "save":
            if current != AIWizard.preview.state or len(parts) != 2 or int(parts[1]) != stored.get("preview", {}).get("usage_id"):
                raise AIError("stale")
            async with db_lock, sessions() as session, session.begin():
                await ai_menu.save(session, user.id, user.active_household_id, stored["preview"])
            await state.clear()
            await message.answer(t("saved", locale), reply_markup=kb([[(t("back", locale), "exit")]]))
    except (AIError, ValueError, KeyError) as exc:
        await state.clear()
        code = exc.code if isinstance(exc, AIError) else "stale"
        await message.answer(t("error." + code, locale), reply_markup=kb([[(t("back", locale), "menu")]]))
    except Exception:
        await state.clear()
        await message.answer(t("error.provider", locale), reply_markup=kb([[(t("back", locale), "menu")]]))
