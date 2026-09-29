import asyncio
import io
from datetime import date, timedelta
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from sqlalchemy import select, delete, update
from app.database.models import (
    User,
    Household,
    Member,
    Menu,
    MealAssignment,
    ShoppingStatus,
    ManualItem,
    WeightLog,
    Notification,
    Invitation,
    Delivery,
    utcnow,
)
from app.services import planner, excel
from app.services.domain import MenuData, NoticeData, MEALS, canonical
from app.seed.demo import demo
from app.bot.ui import Action, REPLY, keyboard, button as b, NavigationMessage
from app.bot.handlers.marketplace import market_action, market_input, show_market
from app.bot.handlers.screens import show, today

router = Router()


class Input(StatesGroup):
    text = State()
    file = State()
    preview = State()
    setup = State()


async def ask(message, state, user, kind, prompt, ident=0):
    await state.set_state(Input.text)
    await state.set_data({"kind": kind, "ident": ident, "hid": user.active_household_id})
    await message.answer(prompt + "\n\n/cancel — отменить")


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=REPLY)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, session, user, settings):
    await state.clear()
    planner.audit(session, user.id, user.active_household_id, "user_start")
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("join_"):
        token = parts[1][5:]
        inv = await planner.invitation_info(session, token)
        h = await session.get(Household, inv.household_id)
        owner = await session.scalar(select(User).join(Member).where(Member.household_id == h.id, Member.role == "OWNER"))
        inviter = owner.name if owner else "Владелец"
        await state.update_data(join_token=token)
        await message.answer(
            f"👥 {inviter} приглашает вас в «{h.name}». Присоединиться?",
            reply_markup=keyboard([[b("✅ Присоединиться", "join"), b("❌ Отказаться", "cancel")]], user.active_household_id),
        )
    else:
        await message.answer(
            "👋 Добро пожаловать в Food Planner!\n\nПланируйте питание, считайте БЖУ, собирайте покупки и ведите общий рацион. Excel можно редактировать через ChatGPT.\n\nМастер необязателен — можно сразу импортировать меню.",
            reply_markup=REPLY,
        )
        await show(message, session, user, settings=settings)


COMMANDS = {
    "menu": "home",
    "market": "market",
    "menus": "menus",
    "profile": "profile",
    "today": "today",
    "tomorrow": "tomorrow",
    "week": "week",
    "shopping": "shopping",
    "shopping_today": "shoptoday",
    "shopping_tomorrow": "shoptomorrow",
    "shopping_week": "shopweek",
    "meals": "meals",
    "nutrition": "nutrition",
    "weight": "weight",
    "reminders": "reminders",
    "import": "import",
    "export": "export",
    "template": "template",
    "household": "household",
    "invite": "invite",
    "settings": "settings",
    "help": "help",
    "backup": "backup",
    "admin": "admin",
}


async def download(message, session, user, action):
    data = None
    if action == "export":
        menu = await planner.active_menu(session, user.id, user.active_household_id)
        if not menu:
            raise ValueError("Активное меню отсутствует. Сначала загрузите меню или демо.")
        data, _ = await planner.load_data(session, menu.active_version_id)
        data.settings = {k: v for k, v in user.settings.items() if k not in {"locale", "ai_preferences"}}
        data.notifications = [
            NoticeData(
                notification_type=n.kind,
                meal_type=n.meal_type,
                enabled=n.enabled,
                time=n.time,
                offset_minutes=n.offset_minutes,
                weekday=n.weekday,
            )
            for n in (await session.scalars(select(Notification).where(Notification.user_id == user.id))).all()
        ]
    raw = await asyncio.to_thread(excel.export_workbook, data)
    await message.answer_document(
        BufferedInputFile(raw, filename="food_planner_template.xlsx" if data is None else "food_planner_menu.xlsx")
    )
    planner.audit(session, user.id, user.active_household_id, "excel_export", action)
    await show(message, session, user, "excel")


async def begin_import(message, state, session, user):
    await planner.require(session, user.id, user.active_household_id, "import")
    await state.clear()
    await state.set_state(Input.file)
    await state.update_data(hid=user.active_household_id)
    await message.answer("📥 Отправьте .xlsx (до 5 МБ). После проверки появится preview. /cancel — отменить.")


async def invite_link(message, session, user):
    token = await planner.invite(session, user.id, user.active_household_id)
    me = await message.bot.get_me()
    await message.answer(
        f"🔗 Передайте эту ссылку второму участнику:\nhttps://t.me/{me.username}?start=join_{token}\n\nСсылка одноразовая, действует 48 часов. Начальная роль — VIEWER. Владелец может изменить её в разделе участников."
    )


async def backup(message, session, user, settings):
    if user.id not in settings.admins:
        raise planner.AccessDenied(
            "Полная БД содержит данные всех пользователей. Backup доступен только ADMIN_IDS. Ваше меню можно скачать через /export."
        )
    from app.services.backup import create_backup

    path = await asyncio.to_thread(create_backup, settings)
    from aiogram.types import FSInputFile

    await message.answer_document(FSInputFile(path), caption="🗄 Резервная копия SQLite. Храните её в защищённом месте.")


@router.message(Command(*COMMANDS))
async def command(message: Message, state: FSMContext, session, user, settings):
    action = COMMANDS[message.text.split()[0][1:].split("@")[0]]
    await state.clear()
    if action == "market":
        await show_market(message, session, user, admin=user.id in settings.admins)
    elif action in ("template", "export"):
        await download(message, session, user, action)
    elif action == "import":
        await begin_import(message, state, session, user)
    elif action == "invite":
        await invite_link(message, session, user)
    elif action == "backup":
        await backup(message, session, user, settings)
    else:
        if action == "home":
            await message.answer("🏠 Главная", reply_markup=REPLY)
        await show(message, session, user, action, settings=settings)


@router.message(Input.file, F.document)
async def import_file(message: Message, state: FSMContext, session, user, settings):
    await planner.require(session, user.id, user.active_household_id, "import")
    context = await state.get_data()
    if context["hid"] != user.active_household_id:
        raise ValueError("Пространство изменилось. Начните /import заново.")
    doc = message.document
    if (
        not doc.file_name
        or not doc.file_name.lower().endswith(".xlsx")
        or doc.mime_type not in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream")
    ):
        raise ValueError("Нужен файл .xlsx, без макросов.")
    limit = settings.max_import_mb * 1024 * 1024
    if not doc.file_size or doc.file_size > limit:
        raise ValueError("Размер файла превышает лимит.")

    class LimitedBuffer(io.BytesIO):
        def write(self, raw):
            if self.tell() + len(raw) > limit:
                raise ValueError("Размер файла превышает лимит.")
            return super().write(raw)

    buffer = LimitedBuffer()
    await message.bot.download(doc, destination=buffer)
    try:
        data, warnings = await asyncio.to_thread(excel.parse_workbook, buffer.getvalue(), limit)
    except Exception as exc:
        import structlog

        structlog.get_logger().warning("excel_validation_error", user_id=user.id, error_type=type(exc).__name__)
        raise ValueError(str(exc)[:1600]) from exc
    menu = await planner.active_menu(session, user.id, user.active_household_id)
    previous = "Активного меню нет."
    if menu:
        old, _ = await planner.load_data(session, menu.active_version_id)
        previous = excel.preview(old)
    await state.set_state(Input.preview)
    await state.update_data(
        payload=data.model_dump(),
        warnings=warnings,
        revision=menu.revision if menu else 0,
        menu_id=menu.id if menu else 0,
        previous=previous,
        expires=(utcnow() + timedelta(minutes=30)).isoformat(),
    )
    await message.answer(
        "📥 Проверка завершена\n\n"
        + excel.preview(data)
        + f"\n⚠️ Предупреждений: {len(warnings)}\n\nСуществующие данные ещё не изменены. Замена также сохраняет предыдущую версию для отката.",
        reply_markup=keyboard(
            [
                [b("✅ Новая версия", "confirmimport", 0)],
                [b("📋 Отдельное меню", "confirmimport", 1), b("♻️ Заменить", "confirmimport", 2)],
                [b("🔍 Предупреждения", "warnings"), b("📊 Сравнить", "compare")],
                [b("❌ Отмена", "cancel")],
            ],
            user.active_household_id,
        ),
    )


PROMPTS = {
    "newhouse": "Введите название общего пространства.",
    "addweight": "Введите вес в кг, например 52.4.",
    "targetweight": "Введите целевой вес в кг.",
    "timezone": "Введите часовой пояс IANA, например Europe/Berlin.",
    "goal": "Введите цели через пробел: kcal белки жиры углеводы. Например: 2600 110 85 340.",
    "myprompt": "Отправьте ваш промт для ChatGPT (до 3000 символов).",
    "manual": "Введите название; количество; единицу. Например: Coca-Cola Zero; 2; bottles. Товар добавится на сегодня.",
    "menuname": "Введите новое название активного меню.",
    "editmeal": "Введите ingredient_id и новое количество в g/ml/pcs, например chicken 220. Изменение затронет этот рецепт во всём общем меню.",
    "replace": "Введите recipe_id существующего рецепта для замены блюда.",
    "portion": "Введите число ваших порций, например 0.75. Это изменит только вашу порцию.",
    "mygrams": "Введите ingredient_id и ваши граммы/ml/pcs, например chicken 150. Личное количество действует для этого недельного приёма. Введите reset, чтобы убрать личные количества.",
    "broadcast": "Введите текст рассылки (до 3000 символов). Затем будет предварительный просмотр и отдельное подтверждение.",
}


@router.callback_query(Action.filter())
async def callback(query: CallbackQuery, callback_data: Action, state: FSMContext, session, user, settings):
    c = callback_data
    await query.answer()
    message = NavigationMessage(query.message)
    hid = user.active_household_id
    if c.h != hid:
        raise ValueError("Вы сменили пространство. Откройте /menu и используйте новые кнопки.")
    await planner.require(session, user.id, hid)
    if c.a.startswith("market") or c.a == "mypublic":
        await market_action(message, session, user, state, settings, c, Input.text)
        return
    screens = {
        "home",
        "today",
        "tomorrow",
        "day",
        "week",
        "wholeweek",
        "profile",
        "menus",
        "menu_manage",
        "meal",
        "mealtools",
        "recipe",
        "recipebook",
        "meals",
        "shopping",
        "shoptoday",
        "shoptomorrow",
        "shopweek",
        "personalshop",
        "household",
        "house_manage",
        "members",
        "settings",
        "nutrition",
        "nutrition_week",
        "weight",
        "reminders",
        "notice",
        "excel",
        "prompt",
        "imports",
        "versions",
        "help",
        "permissions",
        "mealtypes",
        "units",
        "admin",
    }
    if c.a in screens:
        await state.clear()
    if c.a == "cancel":
        await state.clear()
        await message.answer("Отменено.")
    elif c.a == "join":
        token = (await state.get_data()).get("join_token")
        if not token:
            raise ValueError("Откройте ссылку приглашения заново.")
        await planner.join(session, user.id, token)
        await state.clear()
        await show(message, session, user, "household")
    elif c.a in ("template", "export"):
        await download(message, session, user, c.a)
    elif c.a == "import":
        await begin_import(message, state, session, user)
    elif c.a in ("warnings", "compare", "confirmimport"):
        ctx = await state.get_data()
        from datetime import datetime

        if not ctx.get("payload") or ctx.get("hid") != hid or datetime.fromisoformat(ctx["expires"]) < utcnow():
            raise ValueError("Preview истёк. Загрузите файл заново через /import.")
        if c.a == "warnings":
            await message.answer(("\n".join(ctx["warnings"]) or "Предупреждений нет.")[:3800])
        elif c.a == "compare":
            await message.answer(
                "Текущее меню:\n" + ctx["previous"] + "\n\nНовое:\n" + excel.preview(MenuData.model_validate(ctx["payload"]))
            )
        else:
            mode = ["version", "separate", "replace"][c.i]
            current = await planner.active_menu(session, user.id, hid)
            if mode != "separate" and (current.id if current else 0) != ctx["menu_id"]:
                raise ValueError("Активное меню изменилось. Повторите импорт.")
            await planner.save_version(
                session, user.id, hid, MenuData.model_validate(ctx["payload"]), mode=mode, expected_revision=ctx["revision"]
            )
            await state.clear()
            await message.answer("✅ Меню импортировано. Покупки и БЖУ пересчитаны.")
            await show(message, session, user)
    elif c.a == "seed":
        await planner.save_version(session, user.id, hid, demo(), mode="separate", name="Демо · 7 дней", action="edit")
        await show(message, session, user, "week")
    elif c.a == "eat":
        await planner.toggle_completion(session, user.id, hid, c.i, date.fromisoformat(c.d))
        await show(message, session, user, "day", day=c.d)
    elif c.a == "buy":
        anchor, span, fingerprint = c.d.split(",")
        start = date.fromisoformat(anchor)
        end = start + timedelta(days=int(span))
        items = await planner.shopping(session, user.id, hid, start, end)
        if c.i >= len(items) or items[c.i]["fingerprint"][:8] != fingerprint:
            raise ValueError("Состав или количество покупок изменились. Откройте список заново.")
        await planner.toggle_shopping(session, user.id, hid, start, end, c.i)
        await session.flush()
        await show(message, session, user, "shopweek" if int(span) else "shoptoday", page=c.p, day=anchor)
    elif c.a == "resetshop":
        await planner.require(session, user.id, hid, "shop")
        anchor, span = c.d.split(",")
        start = date.fromisoformat(anchor)
        end = start + timedelta(days=int(span))
        await session.execute(delete(ShoppingStatus).where(ShoppingStatus.household_id == hid, ShoppingStatus.period == f"{start}:{end}"))
        await message.answer("Отметки сброшены.")
    elif c.a == "activate":
        h = await planner.require(session, user.id, hid, "edit")
        m = await session.get(Menu, c.i)
        if not m or m.household_id != hid:
            raise ValueError("Меню не найдено.")
        h.active_menu_id = m.id
        planner.audit(session, user.id, hid, "menu_activate", str(m.id))
        await show(message, session, user)
    elif c.a == "rollback":
        await planner.rollback(session, user.id, hid, c.i)
        await show(message, session, user, "versions")
    elif c.a == "switch":
        await planner.require(session, user.id, c.i)
        user.active_household_id = c.i
        await state.clear()
        await show(message, session, user, "household")
    elif c.a == "invite":
        await invite_link(message, session, user)
    elif c.a == "role":
        await planner.require(session, user.id, hid, "owner")
        await message.answer(
            "Выберите роль:", reply_markup=keyboard([[b("EDITOR", "setrole", c.i, 1), b("VIEWER", "setrole", c.i, 0)]], hid)
        )
    elif c.a == "setrole":
        await planner.change_role(session, user.id, hid, c.i, "EDITOR" if c.p else "VIEWER")
        await show(message, session, user, "members")
    elif c.a in ("permimport", "permcomplete", "revoke"):
        h = await planner.require(session, user.id, hid, "owner")
        if c.a == "permimport":
            h.editor_import = not h.editor_import
        elif c.a == "permcomplete":
            h.viewer_completion = not h.viewer_completion
        else:
            await session.execute(update(Invitation).where(Invitation.household_id == hid).values(used=True))
        planner.audit(session, user.id, hid, "permission_change", c.a)
        await show(message, session, user, "permissions")
    elif c.a in ("askleave", "askkick", "askdelhouse", "askdelmenu"):
        if c.a != "askleave":
            await planner.require(session, user.id, hid, "owner")
        action = {"askleave": "leave", "askkick": "kick", "askdelhouse": "delhouse", "askdelmenu": "delmenu"}[c.a]
        if action == "delmenu":
            active = await planner.active_menu(session, user.id, hid)
            if not active:
                raise ValueError("Меню отсутствует.")
            c.i = active.id
        await message.answer(
            "Подтвердите действие. Удаление пространства или меню удаляет его историю.",
            reply_markup=keyboard([[b("✅ Подтвердить", action, c.i), b("❌ Отмена", "cancel")]], hid),
        )
    elif c.a in ("leave", "kick"):
        await planner.leave(session, user.id, hid, c.i if c.a == "kick" else None)
        await show(message, session, user, "household")
    elif c.a == "delmenu":
        h = await planner.require(session, user.id, hid, "owner")
        m = await planner.active_menu(session, user.id, hid)
        if m and m.id != c.i:
            raise ValueError("Активное меню изменилось. Повторите удаление.")
        if m:
            h.active_menu_id = None
            await session.delete(m)
            planner.audit(session, user.id, hid, "menu_delete")
        await show(message, session, user)
    elif c.a == "delhouse":
        h = await planner.require(session, user.id, hid, "owner")
        affected = (await session.scalars(select(User).where(User.active_household_id == hid))).all()
        for u in affected:
            await planner.create_household(session, u.id, "Личный рацион", personal=True)
        await session.delete(h)
        planner.audit(session, user.id, hid, "household_delete")
        await show(message, session, user, "household")
    elif c.a == "togglemeal":
        disabled = set(user.settings.get("disabled_meals", []))
        disabled.symmetric_difference_update({c.d})
        user.settings = user.settings | {"disabled_meals": sorted(disabled)}
        await show(message, session, user, "mealtypes")
    elif c.a == "allnotices":
        user.settings = user.settings | {"notifications_enabled": not user.settings.get("notifications_enabled", True)}
        await show(message, session, user, "reminders")
    elif c.a == "togglenotice":
        n = await session.get(Notification, c.i)
        if not n or n.user_id != user.id:
            raise ValueError("Уведомление не найдено.")
        n.enabled = not n.enabled
        await show(message, session, user, "notice", ident=c.i)
    elif c.a in ("addnotice", "editnotice"):
        if c.a == "editnotice":
            n = await session.get(Notification, c.i)
            if not n or n.user_id != user.id:
                raise ValueError("Уведомление не найдено.")
            await ask(message, state, user, "notice_time", "Введите новое время HH:MM, например 13:00.", c.i)
            await state.update_data(notice_kind=n.kind, meal_type=n.meal_type, offset=n.offset_minutes, weekday=n.weekday)
        else:
            await state.clear()
            labels = [
                ("🍽 Еда", "meal"),
                ("👨‍🍳 Готовка", "cook"),
                ("🛒 Покупки сегодня", "shopping_today"),
                ("🛒 Покупки завтра", "shopping_tomorrow"),
                ("🛒 Недельная закупка", "shopping_week"),
                ("📅 Недельный план", "weekly_plan"),
            ]
            await message.answer(
                "Какое напоминание настроить?", reply_markup=keyboard([[b(label, "noticekind", day=kind)] for label, kind in labels], hid)
            )
    elif c.a == "noticekind":
        if c.d not in ("meal", "cook", "shopping_today", "shopping_tomorrow", "shopping_week", "weekly_plan"):
            raise ValueError("Неизвестное напоминание.")
        await state.set_data({"hid": hid, "notice_kind": c.d, "meal_type": "", "offset": 0, "weekday": 5, "ident": 0})
        if c.d in ("meal", "cook"):
            kinds = dict(MEALS)
            menu = await planner.active_menu(session, user.id, hid)
            if menu:
                data, _ = await planner.load_data(session, menu.active_version_id)
                kinds.update({m.meal_type: MEALS.get(m.meal_type, m.meal_type) for m in data.meals})
            await message.answer(
                "Выберите приём пищи:", reply_markup=keyboard([[b(label, "noticemeal", day=kind)] for kind, label in kinds.items()], hid)
            )
        elif c.d in ("shopping_week", "weekly_plan"):
            from app.services.domain import DAY_RU

            await message.answer(
                "В какой день?", reply_markup=keyboard([[b(label, "noticeday", i)] for i, label in enumerate(DAY_RU)], hid)
            )
        else:
            await state.set_state(Input.text)
            await state.update_data(kind="notice_time")
            await message.answer("Введите время HH:MM, например 19:00.")
    elif c.a in ("noticemeal", "noticeday", "noticeoffset"):
        ctx = await state.get_data()
        if ctx.get("hid") != hid or "notice_kind" not in ctx:
            raise ValueError("Начните настройку заново в /reminders.")
        if c.a == "noticemeal":
            await state.update_data(meal_type=c.d)
            if ctx["notice_kind"] == "cook":
                await message.answer(
                    "За сколько минут до еды напомнить?",
                    reply_markup=keyboard(
                        [
                            [b(str(n) + " мин", "noticeoffset", n) for n in (15, 30, 45)],
                            [b("1 час", "noticeoffset", 60), b("2 часа", "noticeoffset", 120)],
                            [b("Своё время", "noticeoffset", -1)],
                        ],
                        hid,
                    ),
                )
                return
        elif c.a == "noticeday":
            await state.update_data(weekday=c.i)
        elif c.i == -1:
            await state.set_state(Input.text)
            await state.update_data(kind="notice_offset")
            await message.answer("Введите число минут заранее, от 0 до 1440.")
            return
        else:
            await state.update_data(offset=c.i)
        await state.set_state(Input.text)
        await state.update_data(kind="notice_time")
        await message.answer("Введите время приёма пищи HH:MM." if ctx["notice_kind"] == "cook" else "Введите время HH:MM.")
    elif c.a in ("editmeal", "mygrams", "replace"):
        menu, meal = await planner.scoped_meal(session, user.id, hid, c.i, "read" if c.a == "mygrams" else "edit")
        data, meals = await planner.load_data(session, menu.active_version_id)
        md = data.meals[[m.id for m in meals].index(c.i)]
        recipe = next(r for r in data.recipes if r.recipe_id == md.recipe_id)
        if c.a == "replace":
            choices = data.recipes
            rows = [[b(r.recipe_name, "pickrecipe", c.i, index)] for index, r in enumerate(choices[c.p * 8 : c.p * 8 + 8], c.p * 8)]
            text = "Выберите замену блюда:"
        else:
            choices = recipe.ingredients
            rows = [
                [b(product.ingredient_name, "pickpersonal" if c.a == "mygrams" else "pickamount", c.i, index)]
                for index, product in enumerate(choices[c.p * 8 : c.p * 8 + 8], c.p * 8)
            ]
            text = "Выберите продукт. Количество для всего рецепта." if c.a == "editmeal" else "Выберите продукт для вашей личной порции."
            if c.a == "mygrams":
                rows.append([b("Сбросить мои количества", "resetgrams", c.i)])
            else:
                rows.append([b("➕ Продукт", "addproduct", c.i), b("✏️ Инструкция", "instructions", c.i)])
        controls = []
        if c.p:
            controls.append(b("⬅️", c.a, c.i, c.p - 1))
        if (c.p + 1) * 8 < len(choices):
            controls.append(b("➡️", c.a, c.i, c.p + 1))
        if controls:
            rows.append(controls)
        rows.append([b("🏠 Главная", "home")])
        await message.answer(text, reply_markup=keyboard(rows, hid))
    elif c.a in ("pickamount", "pickpersonal", "pickrecipe", "resetgrams", "instructions", "addproduct"):
        menu, meal = await planner.scoped_meal(session, user.id, hid, c.i, "read" if c.a in ("pickpersonal", "resetgrams") else "edit")
        data, meals = await planner.load_data(session, menu.active_version_id)
        md = data.meals[[m.id for m in meals].index(c.i)]
        recipe = next(r for r in data.recipes if r.recipe_id == md.recipe_id)
        if c.a == "pickrecipe":
            if not 0 <= c.p < len(data.recipes):
                raise ValueError("Список изменился.")
            selected = data.recipes[c.p]
            md.recipe_id, md.meal_name = selected.recipe_id, selected.recipe_name
            await planner.save_version(session, user.id, hid, data, action="edit", expected_revision=menu.revision)
            await show(message, session, user, "today")
        elif c.a == "resetgrams":
            assignment = await session.get(MealAssignment, (menu.id, user.id, md.slot))
            if assignment:
                assignment.amounts = {}
            await message.answer("Личные количества сброшены.")
        elif c.a in ("instructions", "addproduct"):
            prompt = (
                "Отправьте новую пошаговую инструкцию (до 3000 символов)."
                if c.a == "instructions"
                else "Введите через ; название; количество; единицу g/ml/pcs; категорию; kcal; белки; жиры; углеводы. Пищевая ценность — на 100 единиц. Например: Огурец; 100; g; Овощи; 15; 0.7; 0.1; 3.6"
            )
            await ask(message, state, user, c.a, prompt, c.i)
        else:
            if not 0 <= c.p < len(recipe.ingredients):
                raise ValueError("Список изменился.")
            product = recipe.ingredients[c.p]
            unit = canonical(product.amount, product.unit)[1]
            await ask(
                message,
                state,
                user,
                "amountinput" if c.a == "pickamount" else "personalinput",
                f"{product.ingredient_name}: введите новое количество в {unit}.",
                c.i,
            )
            await state.update_data(product_id=product.ingredient_id)
    elif c.a == "setup":
        await state.set_state(Input.setup)
        await state.set_data({"step": 0, "values": {}, "hid": hid})
        await message.answer("🚀 Шаг 1/8: ваше имя? /cancel — пропустить мастер.")
    elif c.a in PROMPTS:
        if c.a in ("editmeal", "replace", "menuname"):
            await planner.require(session, user.id, hid, "edit")
        if c.a == "manual":
            await planner.require(session, user.id, hid, "shop")
        if c.a == "broadcast" and user.id not in settings.admins:
            raise planner.AccessDenied("Только ADMIN_IDS.")
        prompt = PROMPTS[c.a]
        if c.a in ("editmeal", "replace", "mygrams"):
            menu, _ = await planner.scoped_meal(session, user.id, hid, c.i)
            data, _ = await planner.load_data(session, menu.active_version_id)
            if c.a == "replace":
                prompt += "\n\n" + "\n".join(f"{r.recipe_id} — {r.recipe_name}" for r in data.recipes)[:2500]
            else:
                _, m = await planner.scoped_meal(session, user.id, hid, c.i)
                row = next(r for r in await planner.personal_meals(session, user.id, hid, {m.day}) if r["meal"].id == c.i)
                prompt += (
                    "\n\n" + "\n".join(f"{p.ingredient_id}: {p.ingredient_name} — {a:g} {unit}" for p, a, unit in row["amounts"])[:2500]
                )
        await ask(message, state, user, c.a, prompt, c.i)
    elif c.a == "sendbroadcast":
        if user.id not in settings.admins:
            raise planner.AccessDenied("Только ADMIN_IDS.")
        ctx = await state.get_data()
        text = ctx.get("broadcast")
        if not text:
            raise ValueError("Предпросмотр рассылки устарел.")
        import secrets

        batch = secrets.token_hex(8)
        users = (await session.scalars(select(User.id))).all()
        for uid in users:
            session.add(Delivery(user_id=uid, key=f"broadcast:{batch}:{uid}", due_at=utcnow(), payload={"text": text, "broadcast": True}))
        await state.clear()
        planner.audit(session, user.id, hid, "broadcast_queued", str(len(users)))
        await message.answer(f"Рассылка поставлена в очередь: {len(users)} получателей.")
    elif c.a == "snooze":
        delivery = await session.get(Delivery, c.i)
        if not delivery or delivery.user_id != user.id:
            raise ValueError("Напоминание не найдено.")
        if await session.scalar(select(Delivery.id).where(Delivery.key == f"snooze:{c.i}")):
            raise ValueError("Уже отложено.")
        session.add(Delivery(user_id=user.id, key=f"snooze:{c.i}", due_at=utcnow() + timedelta(minutes=15), payload=delivery.payload))
        await message.answer("⏰ Напомню через 15 минут.")
    elif c.a == "cooked":
        delivery = await session.get(Delivery, c.i)
        if not delivery or delivery.user_id != user.id:
            raise ValueError("Напоминание не найдено.")
        await session.execute(delete(Delivery).where(Delivery.key == f"snooze:{c.i}", Delivery.sent_at.is_(None)))
        await message.answer("✅ Готово! Отложенное напоминание отменено.")
    elif c.a == "backup":
        await backup(message, session, user, settings)
    else:
        await show(message, session, user, c.a, c.i, c.p, c.d, settings)


def number(value, minimum=0.01, maximum=100000):
    n = float(value.replace(",", "."))
    if not minimum <= n <= maximum:
        raise ValueError(f"Число должно быть от {minimum} до {maximum}.")
    return n


REPLIES = {
    "🏠 Главная": "home",
    "📋 Мои меню": "menus",
    "🏪 Магазин меню": "market",
    "👤 Мой профиль": "profile",
    "🍽 Сегодня": "today",
    "📅 Неделя": "week",
    "🛒 Покупки": "shopping",
    "🥘 Блюда": "meals",
    "📊 Питание": "nutrition",
    "⏰ Напоминания": "reminders",
    "👥 Совместный рацион": "household",
    "⚙️ Настройки": "settings",
}


@router.message(F.text.in_(REPLIES))
async def reply(message: Message, state: FSMContext, session, user, settings):
    await state.clear()
    if REPLIES[message.text] == "market":
        await show_market(message, session, user, admin=user.id in settings.admins)
    else:
        await show(message, session, user, REPLIES[message.text], settings=settings)


@router.message(Input.text, F.text)
async def text_input(message: Message, state: FSMContext, session, user, settings):
    ctx = await state.get_data()
    if ctx["hid"] != user.active_household_id:
        raise ValueError("Пространство изменилось. Повторите действие.")
    if ctx["kind"] in ("market_search", "market_description"):
        await market_input(message, session, user, state, ctx)
        return
    kind, ident = ctx["kind"], ctx["ident"]
    text, hid = message.text.strip(), user.active_household_id
    if kind in ("amountinput", "personalinput"):
        text = ctx["product_id"] + " " + text
        kind = "editmeal" if kind == "amountinput" else "mygrams"
    if kind == "notice_offset":
        offset = number(text, 0, 1440)
        if offset != int(offset):
            raise ValueError("Введите целое число минут.")
        await state.update_data(offset=int(offset), kind="notice_time")
        await message.answer("Введите время еды HH:MM, например 19:30.")
        return
    if kind == "notice_time":
        text = f"{ctx['notice_kind']}; {ctx['meal_type'] or '-'}; {text}; {ctx['offset']}; {ctx['weekday']}"
        kind = "noticeinput"
    if kind == "newhouse":
        if not 1 <= len(text) <= 128:
            raise ValueError("Название: 1–128 символов.")
        await planner.create_household(session, user.id, text)
    elif kind in ("addweight", "targetweight"):
        weight = number(text, 1, 500)
        if kind == "addweight":
            session.add(WeightLog(user_id=user.id, date=today(user), weight=weight))
            user.settings = user.settings | {"weight": weight}
        else:
            user.settings = user.settings | {"target_weight": weight}
    elif kind == "timezone":
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(text)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Неизвестный часовой пояс. Пример: Europe/Berlin.") from exc
        user.settings = user.settings | {"timezone": text}
    elif kind == "goal":
        values = text.split()
        if len(values) != 4:
            raise ValueError("Введите 4 числа: kcal белки жиры углеводы.")
        user.settings = user.settings | dict(
            zip(["target_calories", "target_protein", "target_fat", "target_carbs"], [number(v) for v in values])
        )
    elif kind == "myprompt":
        if len(text) > 3000:
            raise ValueError("Максимум 3000 символов.")
        user.settings = user.settings | {"gpt_prompt": text}
    elif kind == "manual":
        await planner.require(session, user.id, hid, "shop")
        name, amount, unit = [x.strip() for x in text.split(";")]
        amount = number(amount)
        if not name or len(name) > 128 or not unit or len(unit) > 20:
            raise ValueError("Проверьте название и единицу.")
        if unit in ("g", "kg", "ml", "l", "pcs"):
            amount, unit = canonical(amount, unit)
        session.add(ManualItem(household_id=hid, date=today(user), name=name, amount=amount, unit=unit))
        planner.audit(session, user.id, hid, "shopping_manual")
    elif kind == "menuname":
        await planner.require(session, user.id, hid, "edit")
        menu = await planner.active_menu(session, user.id, hid)
        if not menu:
            raise ValueError("Меню отсутствует.")
        menu.name = text[:128]
    elif kind == "editmeal":
        pid, amount = text.split()
        await planner.edit_amount(session, user.id, hid, ident, pid, number(amount))
    elif kind in ("instructions", "addproduct"):
        menu, meal = await planner.scoped_meal(session, user.id, hid, ident, "edit")
        data, meals = await planner.load_data(session, menu.active_version_id)
        md = data.meals[[m.id for m in meals].index(ident)]
        recipe = next(r for r in data.recipes if r.recipe_id == md.recipe_id)
        if kind == "instructions":
            if len(text) > 3000:
                raise ValueError("Максимум 3000 символов.")
            recipe.instructions = text
        else:
            from app.services.domain import Product
            import secrets

            parts = [x.strip() for x in text.split(";")]
            if len(parts) != 8:
                raise ValueError("Введите 8 значений через точку с запятой.")
            name, amount, unit, category, kcal, protein, fat, carbs = parts
            recipe.ingredients.append(
                Product(
                    ingredient_id="p_" + secrets.token_hex(6),
                    ingredient_name=name,
                    amount=number(amount),
                    unit=unit,
                    category=category,
                    kcal_per_100=number(kcal, 0),
                    protein_per_100=number(protein, 0),
                    fat_per_100=number(fat, 0),
                    carbs_per_100=number(carbs, 0),
                )
            )
        await planner.save_version(session, user.id, hid, data, action="edit", expected_revision=menu.revision)
    elif kind == "replace":
        menu, meal = await planner.scoped_meal(session, user.id, hid, ident, "edit")
        data, rows = await planner.load_data(session, menu.active_version_id)
        recipe = next((r for r in data.recipes if r.recipe_id == text), None)
        if not recipe:
            raise ValueError("Рецепт не найден.")
        item = data.meals[[m.id for m in rows].index(ident)]
        item.recipe_id, item.meal_name = recipe.recipe_id, recipe.recipe_name
        await planner.save_version(session, user.id, hid, data, action="edit", expected_revision=menu.revision)
    elif kind in ("portion", "mygrams"):
        menu, meal = await planner.scoped_meal(session, user.id, hid, ident)
        slot = f"{meal.day}:{meal.meal_type}:{meal.meal_order}"
        assignment = await session.get(MealAssignment, (menu.id, user.id, slot))
        if not assignment:
            assignment = MealAssignment(menu_id=menu.id, user_id=user.id, slot=slot, portion=meal.portion, amounts={})
            session.add(assignment)
        if kind == "portion":
            assignment.portion = number(text, 0.01, 100)
            assignment.amounts = {}
        elif text == "reset":
            assignment.amounts = {}
        else:
            pid, amount = text.split()
            data, rows = await planner.load_data(session, menu.active_version_id)
            md = data.meals[[m.id for m in rows].index(ident)]
            recipe = next(r for r in data.recipes if r.recipe_id == md.recipe_id)
            if pid not in {p.ingredient_id for p in recipe.ingredients}:
                raise ValueError("Продукта нет в этом рецепте.")
            assignment.amounts = assignment.amounts | {pid: number(amount)}
    elif kind == "noticeinput":
        values = [v.strip() for v in text.split(";")]
        if len(values) != 5:
            raise ValueError("Нужно 5 значений, разделённых точкой с запятой.")
        n = NoticeData(
            notification_type=values[0],
            meal_type="" if values[1] == "-" else values[1],
            time=values[2],
            offset_minutes=int(values[3]),
            weekday=int(values[4]),
        )
        if n.notification_type in ("meal", "cook") and not n.meal_type:
            raise ValueError("Укажите тип приёма пищи.")
        old = (
            await session.get(Notification, ident)
            if ident
            else await session.scalar(
                select(Notification).where(
                    Notification.user_id == user.id, Notification.kind == n.notification_type, Notification.meal_type == n.meal_type
                )
            )
        )
        if old and old.user_id != user.id:
            raise planner.AccessDenied("Нет доступа.")
        if old is None:
            old = Notification(user_id=user.id)
            session.add(old)
        old.kind, old.meal_type = n.notification_type, n.meal_type
        for key in ("enabled", "time", "offset_minutes", "weekday"):
            setattr(old, key, getattr(n, key))
    elif kind == "broadcast":
        if user.id not in settings.admins or len(text) > 3000:
            raise ValueError("Рассылка доступна администратору, до 3000 символов.")
        await state.update_data(broadcast=text)
        await message.answer(
            "📢 Предпросмотр:\n\n" + text, reply_markup=keyboard([[b("✅ Отправить всем", "sendbroadcast"), b("❌ Отмена", "cancel")]], hid)
        )
        return
    await session.flush()
    await state.clear()
    await message.answer("✅ Сохранено.", reply_markup=REPLY)
    destination = {
        "newhouse": "household",
        "addweight": "weight",
        "targetweight": "weight",
        "goal": "nutrition",
        "timezone": "settings",
        "myprompt": "excel",
        "manual": "shoptoday",
        "menuname": "menus",
        "noticeinput": "reminders",
    }.get(kind, "today")
    await show(message, session, user, destination)


SETUP_KEYS = ["name", "weight", "height", "target_weight", "target_calories", "meals", "timezone", "notifications_enabled"]
SETUP_PROMPTS = [
    "Ваше имя?",
    "Вес в кг?",
    "Рост в см?",
    "Целевой вес в кг?",
    "Цель kcal в день?",
    "Введите номера приёмов через пробел: 1 Завтрак, 2 Второй завтрак, 3 Обед, 4 Полдник, 5 Ужин, 6 Перед сном",
    "Часовой пояс, например Europe/Berlin?",
    "Включить уведомления? да / нет",
]


@router.message(Input.setup, F.text)
async def wizard(message: Message, state: FSMContext, session, user):
    from zoneinfo import ZoneInfo

    ctx = await state.get_data()
    if ctx["hid"] != user.active_household_id:
        raise ValueError("Начните мастер заново.")
    step, values, text = ctx["step"], ctx["values"], message.text.strip()
    key = SETUP_KEYS[step]
    if key in ("weight", "height", "target_weight", "target_calories"):
        values[key] = number(text, 1, 10000 if key == "target_calories" else 500)
    elif key == "meals":
        choices = dict(enumerate(MEALS, 1))
        try:
            enabled = {choices[int(value)] for value in text.split()}
        except (ValueError, KeyError) as exc:
            raise ValueError("Введите номера от 1 до 6.") from exc
        if not enabled or enabled - set(MEALS):
            raise ValueError("Используйте названия из списка.")
        values["disabled_meals"] = sorted(set(MEALS) - enabled)
    elif key == "timezone":
        ZoneInfo(text)
        values[key] = text
    elif key == "notifications_enabled":
        if text.lower() not in ("да", "нет"):
            raise ValueError("Введите да или нет.")
        values[key] = text.lower() == "да"
    else:
        if not text or len(text) > 128:
            raise ValueError("Имя: 1–128 символов.")
        values[key] = text
    if step < 7:
        await state.update_data(step=step + 1, values=values)
        await message.answer(f"Шаг {step + 2}/8: " + SETUP_PROMPTS[step + 1])
    else:
        user.name = values["name"]
        user.settings = user.settings | values
        session.add(WeightLog(user_id=user.id, date=today(user), weight=values["weight"]))
        for kind, time in [("breakfast", "08:30"), ("lunch", "13:00"), ("dinner", "19:30")]:
            old = await session.scalar(
                select(Notification).where(Notification.user_id == user.id, Notification.kind == "meal", Notification.meal_type == kind)
            )
            if old is None:
                session.add(Notification(user_id=user.id, kind="meal", meal_type=kind, time=time, enabled=values["notifications_enabled"]))
        await state.clear()
        await message.answer("✅ Профиль настроен. Время напоминаний можно изменить в /reminders.")
        await show(message, session, user)


@router.message()
async def fallback(message: Message):
    await message.answer("Откройте /menu. Для загрузки Excel сначала используйте /import. /cancel отменяет текущий ввод.")
