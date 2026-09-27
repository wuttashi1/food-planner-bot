from datetime import timedelta, datetime
from app.database.models import utcnow, Member
from app.services import marketplace as market
from app.services.domain import MenuData, DAYS, DAY_RU, nutrition
from app.bot.ui import keyboard, button as b, pages, meal_label


async def show_market(message, session, user, screen="market", ident=0, page=0, query="", admin=False):
    hid = user.active_household_id
    rows = []
    if screen in ("market", "mypublic"):
        items = await market.catalog(session, page, query if screen == "market" else "", user.id if screen == "mypublic" else None)
        text = "🏪 Магазин меню\nБесплатные меню от пользователей этого бота." if screen == "market" else "📣 Мои публикации"
        if query and screen == "market":
            text += f"\nПоиск: {query}"
        if not items:
            text += "\n\nПока ничего нет. Поделитесь своим меню!" if not query else "\n\nНичего не найдено. Попробуйте другое слово."
        for item in items[:8]:
            status = " · скрыто" if item.blocked else " · снято" if not item.visible else ""
            rows.append([b(item.title + status, "marketitem", item.id)])
        if screen == "market":
            rows += [[b("🔎 Найти меню", "marketsearch"), b("📣 Мои публикации", "mypublic")]]
            if query:
                rows.append([b("Сбросить поиск", "marketclear")])
        member = await session.get(Member, (hid, user.id))
        if member.role == "OWNER":
            rows.append([b("➕ Поделиться своим меню", "marketpublish")])
        pages(rows, screen, page, page * 8 + len(items))
        if screen == "mypublic":
            rows.insert(-1, [b("⬅️ Магазин меню", "market")])
    else:
        item = await market.listing(session, ident, user.id, admin)
        data = MenuData.model_validate(item.payload)
        if screen == "marketday":
            if not 0 <= page < 7:
                raise ValueError("Неизвестный день.")
            recipes = {r.recipe_id: r for r in data.recipes}
            text = f"🏪 {item.title} → {DAY_RU[page]}\n"
            for meal in [m for m in data.meals if m.day == DAYS[page]]:
                recipe = recipes[meal.recipe_id]
                text += f"\n{meal_label(meal.meal_type)}: {meal.meal_name}\n{nutrition(recipe, meal.portion)['kcal']:.0f} kcal · {recipe.prep_time + recipe.cook_time} мин\n"
            rows = [[b("⬅️ О меню", "marketitem", ident)]]
        else:
            text = f"🏪 {item.title}\nАвтор: {item.author_name}\n\n{item.description}\n\n{market.summary(data)}\nДобавлено в пространства: {await market.copy_count(session, ident)}"
            if item.blocked or not item.visible:
                text += "\n\nПубликация скрыта администратором." if item.blocked else "\n\nПубликация снята с каталога."
            else:
                member = await session.get(Member, (hid, user.id))
                from app.services.planner import require, AccessDenied

                try:
                    await require(session, user.id, hid, "import")
                    rows.append([b("➕ Добавить в мои меню", "marketcopy", ident)])
                except AccessDenied:
                    text += "\n\nДля добавления меню нужны права импорта в текущем пространстве."
            days = sorted({DAYS.index(m.day) for m in data.meals})
            rows += [[b(DAY_RU[d], "marketday", ident, d) for d in days[i : i + 2]] for i in range(0, len(days), 2)]
            if item.author_id == user.id and item.visible and not item.blocked:
                rows.append([b("Снять мою публикацию", "marketwithdraw", ident)])
            if admin and not item.blocked:
                rows.append([b("🛡 Скрыть как администратор", "marketblock", ident)])
            rows += [[b("⬅️ Магазин меню", "market"), b("🏠 Главная", "home")]]
    for pos in range(0, len(text), 3800):
        await message.answer(text[pos : pos + 3800], reply_markup=keyboard(rows, hid) if pos + 3800 >= len(text) else None)


async def market_action(message, session, user, state, settings, c, input_state):
    hid = user.active_household_id
    admin = user.id in settings.admins
    if c.a in ("market", "mypublic", "marketitem", "marketday", "marketclear"):
        context = await state.get_data()
        query = context.get("market_query", "")
        await state.clear()  # Navigation always exits pending input/confirmation.
        if c.a == "marketclear":
            query = ""
        await state.update_data(market_query=query)
        await show_market(message, session, user, "market" if c.a == "marketclear" else c.a, c.i, c.p, query, admin)
    elif c.a == "marketsearch":
        await state.set_state(input_state)
        await state.set_data({"kind": "market_search", "ident": 0, "hid": hid})
        await message.answer(
            "🔎 Введите название, автора или слово из описания.\n/cancel — отменить",
            reply_markup=keyboard([[b("⬅️ Магазин меню", "market")]], hid),
        )
    elif c.a == "marketpublish":
        menu, data = await market.prepare(session, user.id, hid)
        await state.set_state(input_state)
        await state.set_data(
            {
                "kind": "market_description",
                "ident": menu.id,
                "hid": hid,
                "publish_version": menu.active_version_id,
                "publish_title": menu.name,
            }
        )
        await message.answer(
            f"📣 Публикация «{menu.name}»\n\n{market.summary(data)}\n\nВведите короткое описание (до 600 символов). Затем покажу preview.\n/cancel — отменить",
            reply_markup=keyboard([[b("⬅️ Мои меню", "menus")]], hid),
        )
    elif c.a == "marketconfirm":
        ctx = await state.get_data()
        if ctx.get("hid") != hid or not ctx.get("publish_description") or datetime.fromisoformat(ctx["publish_expires"]) < utcnow():
            raise ValueError("Preview истёк. Подготовьте публикацию заново.")
        item = await market.publish(
            session, user.id, hid, ctx["ident"], ctx["publish_version"], ctx["publish_description"], ctx["publish_title"]
        )
        await state.clear()
        await message.answer("✅ Меню опубликовано в общем каталоге.")
        await show_market(message, session, user, "marketitem", item.id, admin=admin)
    elif c.a == "marketcopy":
        item = await market.listing(session, c.i)
        from app.services.planner import require

        h = await require(session, user.id, hid, "import")
        await message.answer(
            f"Добавить «{item.title}» в «{h.name}»?\n\nЭто независимая копия. Текущий рацион останется активным.",
            reply_markup=keyboard([[b("✅ Добавить", "marketcopyyes", c.i)], [b("⬅️ О меню", "marketitem", c.i)]], hid),
        )
    elif c.a == "marketcopyyes":
        menu, created = await market.copy_menu(session, user.id, hid, c.i)
        await message.answer(
            "✅ Меню добавлено." if created else "Это меню уже есть в вашем пространстве.",
            reply_markup=keyboard(
                [[b("🍽 Сделать активным", "activate", menu.id)], [b("📋 Мои меню", "menus"), b("🏪 Магазин меню", "market")]], hid
            ),
        )
    elif c.a in ("marketwithdraw", "marketblock"):
        item = await market.listing(session, c.i, user.id, admin)
        if c.a == "marketblock" and not admin:
            raise ValueError("Доступно только администратору.")
        if c.a == "marketwithdraw" and item.author_id != user.id:
            raise ValueError("Это не ваша публикация.")
        await message.answer(
            f"Скрыть «{item.title}» из каталога? Уже добавленные копии сохранятся.",
            reply_markup=keyboard(
                [[b("✅ Скрыть", "markethideyes", c.i, int(c.a == "marketblock"))], [b("⬅️ О меню", "marketitem", c.i)]], hid
            ),
        )
    elif c.a == "markethideyes":
        if c.p and not admin:
            raise ValueError("Доступно только администратору.")
        await market.withdraw(session, user.id, hid, c.i, admin=bool(c.p))
        await message.answer("Публикация скрыта. Существующие копии сохранены.")
        await show_market(message, session, user, "market" if c.p else "mypublic", admin=admin)


async def market_input(message, session, user, state, ctx):
    text = message.text.strip()
    if ctx["kind"] == "market_search":
        if not 1 <= len(text) <= 100:
            raise ValueError("Поиск: от 1 до 100 символов.")
        await state.clear()
        await state.update_data(market_query=text)
        await show_market(message, session, user, query=text)
    else:
        if not 1 <= len(text) <= 600:
            raise ValueError("Описание: от 1 до 600 символов.")
        menu, data = await market.prepare(session, user.id, user.active_household_id)
        if menu.id != ctx["ident"] or menu.active_version_id != ctx["publish_version"] or menu.name != ctx["publish_title"]:
            raise ValueError("Меню изменилось. Начните публикацию заново.")
        await state.set_state(None)
        await state.update_data(publish_description=text, publish_expires=(utcnow() + timedelta(minutes=15)).isoformat())
        await message.answer(
            f"📣 Предпросмотр публикации\n\n{menu.name}\nАвтор: {user.name}\n{text}\n\n{market.summary(data)}\n\nВсе пользователи этого бота увидят название, ваше имя, описание, включённые блюда, рецепты и ингредиенты. Личные настройки, вес, порции и отметки не публикуются. Изменения исходного меню не меняют публикацию.",
            reply_markup=keyboard([[b("✅ Опубликовать для всех", "marketconfirm")], [b("❌ Отмена", "market")]], user.active_household_id),
        )
