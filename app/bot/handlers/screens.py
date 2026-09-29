from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from app.database.models import User, Household, Member, Menu, MenuVersion, MealCompletion, WeightLog, Notification, Audit, Recipe
from app.services import planner
from app.services.domain import DAY_RU, MEALS, quantity
from app.services.excel import GPT_PROMPT
from app.i18n import t
from aiogram.types import InlineKeyboardButton
from app.bot.ui import keyboard, button as b, pages, totals, meal_label


def today(user):
    return datetime.now(ZoneInfo(user.settings.get("timezone", "Europe/Berlin"))).date()


def period(user, kind, anchor=""):
    start = date.fromisoformat(anchor) if anchor else today(user)
    if kind == "tomorrow" and not anchor:
        start += timedelta(days=1)
    if kind == "week":
        start -= timedelta(days=start.weekday())
        return start, start + timedelta(days=6)
    return start, start


async def show(message, session, user, screen="home", ident=0, page=0, day="", settings=None):
    hid = user.active_household_id
    h = await planner.require(session, user.id, hid)
    member = await session.get(Member, (hid, user.id))
    menu = await planner.active_menu(session, user.id, hid)
    rows, text = [], ""
    can_edit = member.role in ("OWNER", "EDITOR")
    can_import = member.role == "OWNER" or (member.role == "EDITOR" and h.editor_import)
    if page < 0:
        raise ValueError("Некорректная страница.")
    if screen == "home":
        meals = await planner.personal_meals(session, user.id, hid, {today(user).weekday()})
        text = "🍽 Food Planner\n" + h.name + "\n\nСегодня:\n" + totals(meals)
        notices = (
            await session.scalars(
                select(Notification)
                .where(Notification.user_id == user.id, Notification.kind == "meal", Notification.enabled.is_(True))
                .order_by(Notification.time)
            )
        ).all()
        clock = datetime.now(ZoneInfo(user.settings.get("timezone", "Europe/Berlin"))).strftime("%H:%M")
        next_notice = next((n for n in notices if n.time > clock and any(m["meal"].meal_type == n.meal_type for m in meals)), None)
        if next_notice:
            text += f"\n\nСледующий приём: {meal_label(next_notice.meal_type)} — {next_notice.time}"
        if not menu:
            text += "\n\nМеню пока нет. Загрузите Excel или попробуйте демо."
        text += f"\n\nАктивное меню: {menu.name if menu else 'не выбрано'}"
        rows = [
            [b("🍽 Сегодня", "today"), b("📅 Неделя", "week")],
            [b("📋 Мои меню", "menus"), b("🏪 Магазин меню", "market")],
            [b("👥 Совместный рацион", "household"), b("❓ Помощь", "help")],
        ]
        if not menu:
            rows.insert(0, [b("🏪 Выбрать готовое меню", "market")])
    elif screen == "profile":
        text = f"👤 Мой профиль\n{user.name}\n\nПитание, вес и уведомления — ваши личные данные."
        rows = [
            [b("📊 Питание и цели", "nutrition"), b("⚖️ Мой вес", "weight")],
            [b("⏰ Напоминания", "reminders"), b("⚙️ Настройки", "settings")],
            [b("🏠 Главная", "home")],
        ]
    elif screen == "menu_manage":
        text = f"📋 Мои меню → Управление\nАктивное меню: {menu.name if menu else 'не выбрано'}"
        rows = []
        if menu and can_edit:
            rows += [[b("✏️ Переименовать", "menuname"), b("📜 Версии и откат", "versions")]]
        if menu and member.role == "OWNER":
            rows += [[b("📣 Опубликовать в магазине", "marketpublish")], [b("🗑 Удалить меню", "askdelmenu")]]
        rows += [[b("⬅️ Мои меню", "menus"), b("🏠 Главная", "home")]]
    elif screen == "mealtools":
        _, meal = await planner.scoped_meal(session, user.id, hid, ident)
        text = f"🥘 {meal.name} → Изменить"
        rows = [[b("🍽 Моя порция", "portion", ident), b("🥕 Мои количества", "mygrams", ident)]]
        if can_edit:
            rows += [[b("✏️ Общие ингредиенты", "editmeal", ident), b("🔄 Заменить блюдо", "replace", ident)]]
        rows += [[b("⬅️ Блюдо", "meal", ident, day=day)]]
    elif screen in ("today", "tomorrow", "day"):
        target = date.fromisoformat(day) if day else today(user) + timedelta(days=1 if screen == "tomorrow" else 0)
        meals = await planner.personal_meals(session, user.id, hid, {target.weekday()})
        done = set(
            (
                await session.scalars(
                    select(MealCompletion.meal_id).where(MealCompletion.user_id == user.id, MealCompletion.date == target)
                )
            ).all()
        )
        text = f"📅 {DAY_RU[target.weekday()]}, {target:%d.%m.%Y}\n\n" + totals(meals)
        for row in meals[page * 8 : page * 8 + 8]:
            m = row["meal"]
            text += f"\n\n{meal_label(m.meal_type)}\n{m.name}\n{row['nutrition']['kcal']:.0f} kcal" + (
                " ✅ съедено" if m.id in done else ""
            )
            rows.append([b(meal_label(m.meal_type) + ": " + m.name, "meal", m.id, day=target.isoformat())])
        pages(rows, "day", page, len(meals), target.isoformat())
    elif screen == "meal":
        _, m = await planner.scoped_meal(session, user.id, hid, ident)
        meals = await planner.personal_meals(session, user.id, hid, {m.day})
        row = next((r for r in meals if r["meal"].id == ident), None)
        if not row:
            raise ValueError("Приём пищи отключён в ваших настройках.")
        r = row["recipe"]
        text = f"🥘 {m.name}\n{meal_label(m.meal_type)}\nПорция: {row['portion']:g}\n\n" + totals([row])
        text += "\n\nИнгредиенты:\n" + "\n".join(f"{p.ingredient_name} — {quantity(a, unit)}" for p, a, unit in row["amounts"])
        text += f"\n\n⏱ Подготовка: {r.prep_time} мин\n🍳 Готовка: {r.cook_time} мин"
        if not day:
            target = today(user)
            day = (target - timedelta(days=target.weekday()) + timedelta(days=m.day)).isoformat()
        rows = [[b("👨‍🍳 Рецепт", "recipe", m.id, day=day)]]
        if can_edit or h.viewer_completion:
            rows[0].append(b("✅ Съел / отменить", "eat", m.id, day=day))
        rows += [[b("✏️ Изменить / моя порция", "mealtools", m.id, day=day)], [b("⬅️ Рацион дня", "day", day=day), b("🏠 Главная", "home")]]
    elif screen == "recipe":
        _, m = await planner.scoped_meal(session, user.id, hid, ident)
        r = await session.get(Recipe, m.recipe_id)
        text = f"👨‍🍳 {r.name}\n\n{r.instructions}"
        rows = [[b("⬅️ Блюдо", "meal", ident, day=day), b("🏠 Главная", "home")]]
    elif screen == "week":
        start, _ = period(user, "week")
        text = "📅 Эта неделя\n"
        for i, name in enumerate(DAY_RU):
            meals = await planner.personal_meals(session, user.id, hid, {i})
            text += f"\n{name}: {sum(r['nutrition']['kcal'] for r in meals):.0f} kcal · {len(meals)} приёмов"
        rows = [
            [b(DAY_RU[i][:2], "day", day=(start + timedelta(days=i)).isoformat()) for i in group] for group in ([0, 1, 2], [3, 4, 5], [6])
        ]
        rows += [
            [b("📋 Вся неделя", "wholeweek"), b("🔥 БЖУ недели", "nutrition_week")],
            [b("🛒 Покупки недели", "shopweek"), b("📤 Excel", "export")],
            [b("🏠 Главная", "home")],
        ]
    elif screen == "wholeweek":
        for i, name in enumerate(DAY_RU):
            meals = await planner.personal_meals(session, user.id, hid, {i})
            text += name + "\n" + "\n".join(f"{meal_label(r['meal'].meal_type)}: {r['meal'].name}" for r in meals) + "\n\n"
        rows = [[b("⬅️ Неделя", "week")]]
    elif screen in ("nutrition", "nutrition_week"):
        meals = await planner.personal_meals(session, user.id, hid, set(range(7)) if screen.endswith("week") else {today(user).weekday()})
        text = ("📊 Неделя\n" if screen.endswith("week") else "📊 Сегодня\n") + totals(meals)
        if screen.endswith("week"):
            text += f"\nСреднее за 7 дней: {sum(r['nutrition']['kcal'] for r in meals) / 7:.0f} kcal"
        else:
            done = set(
                (
                    await session.scalars(
                        select(MealCompletion.meal_id).where(MealCompletion.user_id == user.id, MealCompletion.date == today(user))
                    )
                ).all()
            )
            text += "\n\nСъедено:\n" + totals([r for r in meals if r["meal"].id in done])
        text += f"\n\nЦель: {user.settings.get('target_calories', 'не задана')} kcal"
        text += f"\nЦель Б/Ж/У: {user.settings.get('target_protein', '—')} / {user.settings.get('target_fat', '—')} / {user.settings.get('target_carbs', '—')} г"
        rows = [
            [b("☀️ День", "nutrition"), b("📅 Неделя", "nutrition_week")],
            [b("🎯 Цель", "goal"), b("⚖️ Вес", "weight")],
            [b("🏠 Главная", "home")],
        ]
    elif screen == "shopping":
        text = "🛒 Покупки\nОбщий список суммирует порции всех участников."
        rows = [
            [b("☀️ Сегодня", "shoptoday"), b("🌙 Завтра", "shoptomorrow")],
            [b("📅 Неделя", "shopweek"), b("🥘 Для блюда", "today")],
            [b("👤 Мои покупки", "personalshop")],
            [b("🏠 Главная", "home")],
        ]
        if can_edit:
            rows.insert(-1, [b("➕ Добавить товар", "manual")])
    elif screen.startswith("shop") or screen == "personalshop":
        kind = {"shoptoday": "today", "shoptomorrow": "tomorrow", "shopweek": "week", "personalshop": "week"}[screen]
        start, end = period(user, kind, day)
        items = await planner.shopping(session, user.id, hid, start, end, personal=screen == "personalshop")
        text = f"🛒 {start:%d.%m} — {end:%d.%m}\n" + ("Личные порции" if screen == "personalshop" else "Общие покупки")
        category = None
        for index, item in enumerate(items[page * 8 : page * 8 + 8], page * 8):
            if category != item["category"]:
                category = item["category"]
                text += "\n\n" + category
            label = ("✅ " if item["bought"] else "☐ ") + item["name"] + " — " + quantity(item["amount"], item["unit"])
            text += "\n" + label
            if screen != "personalshop" and can_edit:
                # A fingerprint of item identity + amount detects stale/reordered lists.
                token = f"{start.isoformat()},{(end - start).days},{item['fingerprint'][:8]}"
                rows.append([b(label, "buy", index, page, token)])
        if not items:
            text += "\nПока пусто — добавьте меню или ручной товар."
        if screen != "personalshop" and can_edit:
            rows.append([b("🔄 Сбросить отметки", "resetshop", day=f"{start},{(end - start).days}")])
        pages(rows, screen, page, len(items), start.isoformat())
    elif screen == "meals":
        if not menu:
            text = "🥘 Добавьте меню через /import или демо в /menu."
        else:
            recipes = (await session.scalars(select(Recipe).where(Recipe.version_id == menu.active_version_id).order_by(Recipe.id))).all()
            text = "🥘 Рецепты"
            rows = [[b(r.name, "recipebook", r.id)] for r in recipes[page * 8 : page * 8 + 8]]
            pages(rows, "meals", page, len(recipes))
    elif screen == "recipebook":
        r = await session.get(Recipe, ident)
        if not menu or not r or r.version_id != menu.active_version_id:
            raise ValueError("Рецепт устарел. Откройте /meals заново.")
        text = f"👨‍🍳 {r.name}\nПорций: {r.servings:g}\nПодготовка: {r.prep_time} мин · Готовка: {r.cook_time} мин\n\n{r.instructions}"
        rows = [[b("⬅️ Рецепты", "meals"), b("🏠 Главная", "home")]]
    elif screen == "weight":
        logs = (
            await session.scalars(
                select(WeightLog)
                .where(WeightLog.user_id == user.id)
                .order_by(WeightLog.date.desc(), WeightLog.id.desc())
                .offset(page * 8)
                .limit(9)
            )
        ).all()
        text = (
            "⚖️ Вес\n🎯 Цель: "
            + str(user.settings.get("target_weight", "не задана"))
            + " кг\n\n"
            + "\n".join(f"{x.date:%d.%m.%Y} — {x.weight:g} кг" for x in logs[:8])
        )
        rows = [[b("➕ Измерение", "addweight"), b("🎯 Целевой вес", "targetweight")]]
        pages(rows, "weight", page, page * 8 + len(logs))
    elif screen == "excel":
        text = "📄 Excel ↔ ChatGPT ↔ Telegram\n\n1. Скачайте меню.\n2. Отправьте .xlsx в ChatGPT с промтом.\n3. Скачайте результат.\n4. Загрузите через /import.\n5. Проверьте preview и подтвердите."
        rows = [
            [b("📄 Шаблон", "template"), b("📤 Моё меню", "export")],
            [b("📥 Импорт", "import"), b("📋 История", "imports")],
            [b("📋 Промт", "prompt"), b("✏️ Мой GPT Prompt", "myprompt")],
            [b("⬅️ Мои меню", "menus"), b("🏠 Главная", "home")],
        ]
        if not can_import:
            rows[1] = [b("📋 История импортов", "imports")]
    elif screen == "prompt":
        text = user.settings.get("gpt_prompt") or GPT_PROMPT
        rows = [[b("⬅️ Excel", "excel")]]
    elif screen in ("menus", "versions", "imports"):
        if screen == "menus":
            items = (await session.scalars(select(Menu).where(Menu.household_id == hid).order_by(Menu.id).offset(page * 8).limit(9))).all()
            text = (
                f"📋 Мои меню\nПространство: {h.name}\nАктивное: {menu.name if menu else 'не выбрано'}\n\nВыберите меню, чтобы сделать его активным."
                if can_edit
                else f"📋 Мои меню\nАктивное: {menu.name if menu else 'не выбрано'}\nМенять активное меню может владелец или редактор."
            )
            rows = [[b(("✅ " if menu and m.id == menu.id else "") + m.name, "activate", m.id)] for m in items[:8]] if can_edit else []
            if menu:
                rows += [[b("📅 Неделя", "week"), b("🥘 Рецепты", "meals")]]
            rows += [[b("🏪 Найти готовое меню", "market"), b("📄 Excel / ChatGPT", "excel")]]
            if menu and can_edit:
                rows += [[b("⚙️ Управление меню", "menu_manage")]]
            if not items and can_edit:
                rows += [[b("🌱 Попробовать демо", "seed")]]
        elif screen == "versions":
            items = (
                await session.scalars(
                    select(MenuVersion)
                    .where(MenuVersion.menu_id == (menu.id if menu else -1))
                    .order_by(MenuVersion.number.desc())
                    .offset(page * 8)
                    .limit(9)
                )
            ).all()
            text = "📜 Версии. Нажмите, чтобы восстановить:"
            rows = [[b(f"v{v.number} · {v.created_at:%d.%m %H:%M}", "rollback", v.id)] for v in items[:8]]
        else:
            items = (
                await session.scalars(
                    select(Audit)
                    .where(Audit.household_id == hid, Audit.action == "excel_import")
                    .order_by(Audit.id.desc())
                    .offset(page * 8)
                    .limit(9)
                )
            ).all()
            text = "📋 История импортов\n" + "\n".join(f"{x.created_at:%d.%m %H:%M} · {x.detail}" for x in items[:8])
        pages(rows, screen, page, page * 8 + len(items))
    elif screen == "household":
        text = f"👥 {h.name}\nВаша роль: {member.role}"
        spaces = (
            (
                await session.execute(
                    select(Household).join(Member).where(Member.user_id == user.id).order_by(Household.id).offset(page * 8).limit(9)
                )
            )
            .scalars()
            .all()
        )
        rows = [[b(("✅ " if x.id == hid else "") + x.name, "switch", x.id)] for x in spaces[:8]]
        rows += [[b("➕ Создать пространство", "newhouse"), b("👥 Участники", "members")]]
        if member.role == "OWNER":
            rows += [[b("🔗 Пригласить", "invite"), b("⚙️ Управление", "house_manage")]]
        else:
            rows += [[b("🚪 Покинуть пространство", "askleave")]]
        pages(rows, "household", page, page * 8 + len(spaces))
    elif screen == "house_manage":
        await planner.require(session, user.id, hid, "owner")
        text = f"👥 {h.name} → Управление"
        rows = [[b("⚙️ Права участников", "permissions")], [b("❌ Удалить пространство", "askdelhouse")], [b("⬅️ Пространства", "household")]]
    elif screen == "members":
        items = (
            await session.execute(
                select(Member, User).join(User).where(Member.household_id == hid).order_by(User.id).offset(page * 8).limit(9)
            )
        ).all()
        text = "👥 Участники\n" + "\n".join(f"{u.name} — {m.role}" for m, u in items[:8])
        if member.role == "OWNER":
            for m, u in items[:8]:
                if m.role != "OWNER":
                    rows.append([b(u.name + ": роль", "role", u.id), b("Исключить", "askkick", u.id)])
        pages(rows, "members", page, page * 8 + len(items))
    elif screen == "permissions":
        await planner.require(session, user.id, hid, "owner")
        text = "⚙️ Права пространства"
        rows = [
            [b(f"Импорт EDITOR: {'✅' if h.editor_import else '❌'}", "permimport")],
            [b(f"VIEWER «съел»: {'✅' if h.viewer_completion else '❌'}", "permcomplete")],
            [b("🔗 Отозвать приглашения", "revoke")],
            [b("⬅️ Пространство", "household")],
        ]
    elif screen == "settings":
        text = f"⚙️ Настройки\n👤 {user.name}\nTelegram ID: {user.id}\n🌍 {user.settings.get('timezone', 'Europe/Berlin')}"
        rows = [
            [b("👤 Изменить профиль", "setup"), b("🌍 Часовой пояс", "timezone")],
            [b("🍽 Приёмы пищи", "mealtypes"), b("📊 Единицы", "units")],
            [b("⬅️ Мой профиль", "profile"), b("🏠 Главная", "home")],
        ]
    elif screen == "units":
        text = "📊 Количества хранятся в g, ml, pcs. В покупках 1000 г отображаются как 1 кг, 1000 мл — как 1 л. БЖУ — на 100 g/ml/pcs."
        rows = [[b("⬅️ Настройки", "settings")]]
    elif screen == "mealtypes":
        kinds = dict(MEALS)
        if menu:
            data, _ = await planner.load_data(session, menu.active_version_id)
            kinds.update({m.meal_type: meal_label(m.meal_type) for m in data.meals})
        text = "🍽 Ваши приёмы пищи\nОтключённые не входят в ваш рацион, покупки и напоминания."
        disabled = user.settings.get("disabled_meals", [])
        rows = [[b(label + (" ❌" if kind in disabled else " ✅"), "togglemeal", day=kind)] for kind, label in kinds.items()]
        rows.append([b("⬅️ Настройки", "settings")])
    elif screen == "reminders":
        notices = (
            await session.scalars(
                select(Notification).where(Notification.user_id == user.id).order_by(Notification.id).offset(page * 8).limit(9)
            )
        ).all()
        text = "⏰ Личные напоминания\nЧасовой пояс: " + user.settings.get("timezone", "Europe/Berlin")
        labels = {
            "meal": "🍽 Еда",
            "cook": "👨‍🍳 Готовка",
            "shopping_today": "🛒 Сегодня",
            "shopping_tomorrow": "🛒 Завтра",
            "shopping_week": "🛒 Недельная закупка",
            "weekly_plan": "📅 Недельный план",
        }
        rows = [
            [b(f"{'✅' if n.enabled else '❌'} {labels[n.kind]} {meal_label(n.meal_type) if n.meal_type else ''} {n.time}", "notice", n.id)]
            for n in notices[:8]
        ]
        rows += [
            [b("➕ Настроить уведомление", "addnotice")],
            [b("🔔 Все: " + ("✅" if user.settings.get("notifications_enabled", True) else "❌"), "allnotices")],
        ]
        pages(rows, "reminders", page, page * 8 + len(notices))
    elif screen == "notice":
        n = await session.get(Notification, ident)
        if not n or n.user_id != user.id:
            raise ValueError("Уведомление не найдено.")
        text = f"⏰ {n.kind} {n.meal_type}\nВремя: {n.time}\nЗаранее: {n.offset_minutes} мин\nДень недели: {DAY_RU[n.weekday]}"
        rows = [[b("🔔 Вкл / выкл", "togglenotice", ident), b("🕐 Изменить", "editnotice", ident)], [b("⬅️ Напоминания", "reminders")]]
    elif screen == "help":
        text = "❓ Помощь\nВыберите раздел:"
        rows = [
            [b("🚀 Начало", "helpstart"), b("🍽 Меню", "week")],
            [b("🛒 Покупки", "shopping"), b("⏰ Напоминания", "reminders")],
            [b("📄 Excel", "excel"), b("🤖 ChatGPT", "prompt")],
            [b("👥 Совместный рацион", "household"), b("⚙️ Настройки", "settings")],
            [b("🆘 Проблемы", "helptrouble")],
        ]
    elif screen == "helpstart":
        text = "Начните с /menu → Демо или /template. Мастер /settings необязателен. Меню повторяется каждую неделю. В карточке блюда можно менять ингредиенты, личную порцию и отмечать еду. /cancel отменяет ввод."
    elif screen == "helptrouble":
        text = "Если кнопка устарела, откройте /menu. При ошибке Excel проверьте строку из сообщения. Изменения другого участника видны при следующем открытии раздела. После перезапуска незавершённый ввод нужно повторить. Администратору: docker compose logs --tail=100."
    elif screen == "admin":
        if user.id not in settings.admins:
            raise planner.AccessDenied("Команда доступна только ADMIN_IDS.")
        text = "🛠 Администрирование\n"
        for label, model in [("Пользователей", User), ("Пространств", Household), ("Импортов", Audit)]:
            q = select(func.count()).select_from(model)
            if model == Audit:
                q = q.where(Audit.action == "excel_import")
            text += f"{label}: {await session.scalar(q)}\n"
        rows = [
            [b("👥 Пользователи", "adminusers"), b("🏠 Пространства", "adminhouses")],
            [b("📥 Импорты", "adminimports"), b("⚠️ Ошибки", "adminerrors")],
            [b("🗄 Backup", "backup"), b("📢 Рассылка", "broadcast")],
        ]
    elif screen in ("adminusers", "adminhouses", "adminimports", "adminerrors"):
        if user.id not in settings.admins:
            raise planner.AccessDenied("Только ADMIN_IDS.")
        if screen == "adminusers":
            items = (await session.scalars(select(User).order_by(User.id).offset(page * 8).limit(9))).all()
            text = "\n".join(f"{x.id}: {x.name}" for x in items[:8])
        elif screen == "adminhouses":
            items = (await session.scalars(select(Household).order_by(Household.id).offset(page * 8).limit(9))).all()
            text = "\n".join(f"{x.id}: {x.name}" for x in items[:8])
        elif screen == "adminimports":
            items = (
                await session.scalars(
                    select(Audit).where(Audit.action == "excel_import").order_by(Audit.id.desc()).offset(page * 8).limit(9)
                )
            ).all()
            text = "\n".join(f"{x.created_at}: {x.detail}" for x in items[:8])
        else:
            from pathlib import Path
            import json

            path = Path("logs/app.jsonl")
            entries = []
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 32768))
                    for line in stream.read().decode(errors="replace").splitlines():
                        try:
                            item = json.loads(line)
                        except ValueError:
                            continue
                        if item.get("level") in ("error", "critical"):
                            entries.append(item)
            items = list(reversed(entries))[page * 8 : page * 8 + 9]
            text = "\n".join(f"{x.get('timestamp', '')} {x.get('event', '')} {x.get('error_type', '')}" for x in items[:8])
        text = text or "Записей пока нет."
        pages(rows, screen, page, page * 8 + len(items))
    else:
        raise ValueError("Откройте /menu — неизвестный раздел.")
    if screen not in ("home", "profile", "menus", "household", "menu_manage", "house_manage"):
        text = f"{h.name} · {menu.name if menu else 'меню не выбрано'}\n\n" + text
    if not rows:
        rows = [[b("🏠 Главная", "home")]]
    # Telegram limits: split readable text while keeping navigation on final part.
    chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)] or ["Пока пусто."]
    for chunk in chunks[:-1]:
        await message.answer(chunk)
    markup = keyboard(rows, hid)
    locale = user.settings.get("locale", "en")
    if screen == "home":
        markup.inline_keyboard.insert(0, [InlineKeyboardButton(text=t("menu", locale), callback_data="ai:menu")])
    if screen == "settings":
        markup.inline_keyboard.insert(0, [InlineKeyboardButton(text=t("language", locale), callback_data="ai:language")])
    await message.answer(chunks[-1], reply_markup=markup)
