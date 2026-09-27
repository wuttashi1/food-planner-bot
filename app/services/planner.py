import hashlib
import secrets
from datetime import timedelta
from sqlalchemy import select, update, delete, func
from app.database.models import (
    User,
    Household,
    Member,
    Invitation,
    Menu,
    MenuVersion,
    Recipe,
    Ingredient,
    RecipeIngredient,
    Meal,
    MealAssignment,
    MealCompletion,
    ShoppingStatus,
    ManualItem,
    Notification,
    Audit,
    utcnow,
)
from app.services.domain import MenuData, RecipeData, Product, MealData, DAYS, canonical, amounts, nutrition


class AccessDenied(ValueError):
    pass


def audit(session, user_id, household_id, action, detail=""):
    import structlog

    session.add(Audit(user_id=user_id, household_id=household_id, action=action, detail=detail[:500]))
    structlog.get_logger().info(action, user_id=user_id, household_id=household_id)


async def require(session, uid, hid, action="read"):
    member = await session.get(Member, (hid, uid))
    household = await session.get(Household, hid)
    if not member or not household:
        raise AccessDenied("Нет доступа к этому пространству.")
    allowed = action == "read" or member.role == "OWNER"
    allowed |= member.role == "EDITOR" and action in ("edit", "shop", "complete")
    allowed |= member.role == "EDITOR" and action == "import" and household.editor_import
    allowed |= member.role == "VIEWER" and action == "complete" and household.viewer_completion
    if not allowed:
        raise AccessDenied("Недостаточно прав. Обратитесь к владельцу пространства.")
    return household


async def create_household(session, uid, name, personal=False):
    h = Household(name=name[:128], personal=personal)
    session.add(h)
    await session.flush()
    session.add(Member(household_id=h.id, user_id=uid, role="OWNER"))
    user = await session.get(User, uid)
    user.active_household_id = h.id
    audit(session, uid, h.id, "household_create")
    await session.flush()
    return h


async def ensure_user(session, uid, name, timezone="Europe/Berlin"):
    user = await session.get(User, uid)
    if user:
        return user
    user = User(id=uid, name=name[:128], settings={"timezone": timezone, "notifications_enabled": True})
    session.add(user)
    await session.flush()
    await create_household(session, uid, "Личный рацион", personal=True)
    audit(session, uid, user.active_household_id, "user_start")
    return user


async def invite(session, uid, hid):
    await require(session, uid, hid, "owner")
    token = secrets.token_urlsafe(24)
    session.add(
        Invitation(household_id=hid, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=utcnow() + timedelta(days=2))
    )
    audit(session, uid, hid, "invitation_create")
    return token


async def invitation_info(session, token):
    inv = await session.scalar(
        select(Invitation).where(
            Invitation.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            Invitation.used.is_(False),
            Invitation.expires_at > utcnow(),
        )
    )
    if not inv:
        raise ValueError("Приглашение недействительно, отозвано или истекло.")
    return inv


async def join(session, uid, token):
    inv = await invitation_info(session, token)
    if await session.get(Member, (inv.household_id, uid)):
        raise ValueError("Вы уже участник этого пространства.")
    claimed = await session.execute(
        update(Invitation).where(Invitation.id == inv.id, Invitation.used.is_(False), Invitation.expires_at > utcnow()).values(used=True)
    )
    if claimed.rowcount != 1:
        raise ValueError("Приглашение уже использовано.")
    session.add(Member(household_id=inv.household_id, user_id=uid, role=inv.role))
    user = await session.get(User, uid)
    user.active_household_id = inv.household_id
    audit(session, uid, inv.household_id, "household_join")


async def change_role(session, uid, hid, target, role):
    await require(session, uid, hid, "owner")
    member = await session.get(Member, (hid, target))
    if not member or member.role == "OWNER" or role not in ("EDITOR", "VIEWER"):
        raise ValueError("Роль владельца нельзя изменить. Допустимы EDITOR и VIEWER.")
    member.role = role
    audit(session, uid, hid, "permission_change", f"{target}: {role}")


async def leave(session, uid, hid, target=None):
    target = uid if target is None else target
    await require(session, uid, hid, "read" if uid == target else "owner")
    member = await session.get(Member, (hid, target))
    if not member or member.role == "OWNER":
        raise ValueError("Владелец не может покинуть пространство. Используйте удаление пространства.")
    await session.delete(member)
    await session.execute(
        delete(MealAssignment).where(
            MealAssignment.user_id == target, MealAssignment.menu_id.in_(select(Menu.id).where(Menu.household_id == hid))
        )
    )
    user = await session.get(User, target)
    if user.active_household_id == hid:
        other = await session.scalar(select(Member.household_id).where(Member.user_id == target, Member.household_id != hid))
        if other:
            user.active_household_id = other
        else:
            await create_household(session, target, "Личный рацион", personal=True)
    audit(session, uid, hid, "household_leave", str(target))


async def active_menu(session, uid, hid):
    h = await require(session, uid, hid)
    m = await session.get(Menu, h.active_menu_id) if h.active_menu_id else None
    if m and m.household_id != hid:
        raise AccessDenied("Некорректное активное меню.")
    return m


async def load_data(session, version_id):
    version = await session.get(MenuVersion, version_id)
    if not version:
        raise ValueError("Версия не найдена.")
    recipes = list((await session.scalars(select(Recipe).where(Recipe.version_id == version_id).order_by(Recipe.id))).all())
    products = {p.id: p for p in (await session.scalars(select(Ingredient).where(Ingredient.version_id == version_id))).all()}
    links = (await session.scalars(select(RecipeIngredient).where(RecipeIngredient.recipe_id.in_([r.id for r in recipes])))).all()
    data_recipes = []
    for r in recipes:
        data_recipes.append(
            RecipeData(
                recipe_id=r.external_id,
                recipe_name=r.name,
                servings=r.servings,
                prep_time=r.prep_time,
                cook_time=r.cook_time,
                instructions=r.instructions,
                ingredients=[
                    Product(
                        ingredient_id=products[link.ingredient_id].external_id,
                        ingredient_name=products[link.ingredient_id].name,
                        amount=link.amount,
                        unit=products[link.ingredient_id].unit,
                        category=products[link.ingredient_id].category,
                        **{k + "_per_100": getattr(products[link.ingredient_id], k) for k in ("kcal", "protein", "fat", "carbs")},
                    )
                    for link in links
                    if link.recipe_id == r.id
                ],
            )
        )
    rec_ids = {r.id: r.external_id for r in recipes}
    meals = (await session.scalars(select(Meal).where(Meal.version_id == version_id).order_by(Meal.day, Meal.meal_order))).all()
    return MenuData(
        menu_id=version.menu_id,
        menu_version=version.number,
        recipes=data_recipes,
        meals=[
            MealData(
                day=DAYS[m.day],
                meal_type=m.meal_type,
                meal_order=m.meal_order,
                meal_name=m.name,
                recipe_id=rec_ids[m.recipe_id],
                portion=m.portion,
                enabled=m.enabled,
            )
            for m in meals
        ],
    ), meals


async def save_version(session, uid, hid, data, mode="version", expected_revision=None, name=None, action="import"):
    """Caller owns transaction; CAS prevents stale previews overwriting another edit."""
    h = await require(session, uid, hid, action)
    data = MenuData.model_validate(data.model_dump())
    menu = await active_menu(session, uid, hid)
    if mode not in ("version", "separate", "replace"):
        raise ValueError("Неизвестный режим импорта.")
    if mode == "separate" or not menu:
        menu = Menu(household_id=hid, name=(name or "Новое меню")[:128], revision=0)
        session.add(menu)
        await session.flush()
    elif data.menu_id not in (0, menu.id):
        raise ValueError("Файл относится к другому меню. Выберите «Отдельное меню».")
    revision = menu.revision
    if expected_revision is not None and mode != "separate" and revision != expected_revision:
        raise ValueError("Меню изменилось после preview. Загрузите файл заново.")
    result = await session.execute(update(Menu).where(Menu.id == menu.id, Menu.revision == revision).values(revision=revision + 1))
    if result.rowcount != 1:
        raise ValueError("Одновременное изменение меню. Повторите операцию.")
    number = (await session.scalar(select(func.max(MenuVersion.number)).where(MenuVersion.menu_id == menu.id)) or 0) + 1
    version = MenuVersion(menu_id=menu.id, number=number, created_by=uid)
    session.add(version)
    await session.flush()
    products, recipes = {}, {}
    for r in data.recipes:
        rec = Recipe(
            version_id=version.id,
            external_id=r.recipe_id,
            name=r.recipe_name,
            servings=r.servings,
            prep_time=r.prep_time,
            cook_time=r.cook_time,
            instructions=r.instructions,
        )
        session.add(rec)
        await session.flush()
        recipes[r.recipe_id] = rec.id
        for p in r.ingredients:
            amount, unit = canonical(p.amount, p.unit)
            if p.ingredient_id not in products:
                product = Ingredient(
                    version_id=version.id,
                    external_id=p.ingredient_id,
                    name=p.ingredient_name,
                    unit=unit,
                    category=p.category,
                    **{k: getattr(p, k + "_per_100") for k in ("kcal", "protein", "fat", "carbs")},
                )
                session.add(product)
                await session.flush()
                products[p.ingredient_id] = product.id
            session.add(RecipeIngredient(recipe_id=rec.id, ingredient_id=products[p.ingredient_id], amount=amount))
    for m in data.meals:
        session.add(
            Meal(
                version_id=version.id,
                day=DAYS.index(m.day),
                meal_type=m.meal_type,
                meal_order=m.meal_order,
                name=m.meal_name,
                recipe_id=recipes[m.recipe_id],
                portion=m.portion,
                enabled=m.enabled,
            )
        )
    menu.active_version_id = version.id
    h.active_menu_id = menu.id
    if action == "import":
        user = await session.get(User, uid)
        user.settings = user.settings | data.settings
        if "name" in data.settings:
            user.name = data.settings["name"]
        if "weight" in data.settings:
            from app.database.models import WeightLog
            from zoneinfo import ZoneInfo
            from datetime import datetime

            session.add(
                WeightLog(
                    user_id=uid,
                    date=datetime.now(ZoneInfo(user.settings.get("timezone", "Europe/Berlin"))).date(),
                    weight=float(data.settings["weight"]),
                )
            )
        for n in data.notifications:
            notice = await session.scalar(
                select(Notification).where(
                    Notification.user_id == uid, Notification.kind == n.notification_type, Notification.meal_type == n.meal_type
                )
            )
            if not notice:
                notice = Notification(user_id=uid, kind=n.notification_type, meal_type=n.meal_type)
                session.add(notice)
            for k in ("enabled", "time", "offset_minutes", "weekday"):
                setattr(notice, k, getattr(n, k))
    audit(session, uid, hid, "excel_import" if action == "import" else "menu_change", f"menu={menu.id}, version={number}, mode={mode}")
    await session.flush()
    return menu


async def rollback(session, uid, hid, version_id):
    await require(session, uid, hid, "edit")
    version = await session.get(MenuVersion, version_id)
    menu = await active_menu(session, uid, hid)
    if not version or not menu or version.menu_id != menu.id:
        raise ValueError("Версия не принадлежит активному меню.")
    menu.active_version_id = version.id
    menu.revision += 1
    audit(session, uid, hid, "menu_rollback", str(version.id))


async def scoped_meal(session, uid, hid, meal_id, action="read"):
    menu = await active_menu(session, uid, hid)
    await require(session, uid, hid, action)
    meal = await session.get(Meal, meal_id)
    if not menu or not meal or meal.version_id != menu.active_version_id:
        raise ValueError("Карточка устарела. Откройте актуальное меню.")
    return menu, meal


async def edit_amount(session, uid, hid, meal_id, ingredient_id, amount):
    menu, meal = await scoped_meal(session, uid, hid, meal_id, "edit")
    data, rows = await load_data(session, menu.active_version_id)
    md = data.meals[[m.id for m in rows].index(meal_id)]
    recipe = next(r for r in data.recipes if r.recipe_id == md.recipe_id)
    product = next((p for p in recipe.ingredients if p.ingredient_id == ingredient_id), None)
    if not product:
        raise ValueError("Продукт не найден в блюде.")
    product.amount = amount
    product.unit = canonical(1, product.unit)[1]
    await save_version(session, uid, hid, data, expected_revision=menu.revision, action="edit")


async def toggle_completion(session, uid, hid, meal_id, on_date):
    _, meal = await scoped_meal(session, uid, hid, meal_id, "complete")
    if meal.day != on_date.weekday():
        raise ValueError("Дата не соответствует блюду.")
    key = (uid, meal_id, on_date)
    item = await session.get(MealCompletion, key)
    if item:
        await session.delete(item)
    else:
        session.add(MealCompletion(user_id=uid, meal_id=meal_id, date=on_date))


async def personal_meals(session, uid, hid, weekdays):
    menu = await active_menu(session, uid, hid)
    if not menu or not menu.active_version_id:
        return []
    data, rows = await load_data(session, menu.active_version_id)
    user = await session.get(User, uid)
    assignments = {
        a.slot: a
        for a in (
            await session.scalars(select(MealAssignment).where(MealAssignment.menu_id == menu.id, MealAssignment.user_id == uid))
        ).all()
    }
    recipes = {r.recipe_id: r for r in data.recipes}
    result = []
    for m, row in zip(data.meals, rows):
        if DAYS.index(m.day) not in weekdays or not m.enabled or m.meal_type in user.settings.get("disabled_meals", []):
            continue
        a = assignments.get(m.slot)
        portion = a.portion if a else m.portion
        overrides = a.amounts if a else {}
        r = recipes[m.recipe_id]
        result.append(
            {
                "meal": row,
                "recipe": r,
                "portion": portion,
                "overrides": overrides,
                "nutrition": nutrition(r, portion, overrides),
                "amounts": amounts(r, portion, overrides),
            }
        )
    return result


async def shopping(session, uid, hid, start, end, personal=False):
    await require(session, uid, hid)
    if not 0 <= (end - start).days <= 6:
        raise ValueError("Диапазон покупок: от 1 до 7 дней.")
    members = [uid] if personal else list((await session.scalars(select(Member.user_id).where(Member.household_id == hid))).all())
    weekdays = {(start + timedelta(days=i)).weekday() for i in range((end - start).days + 1)}
    items = {}
    for member in members:
        for row in await personal_meals(session, member, hid, weekdays):
            for p, amount, unit in row["amounts"]:
                key = "m:" + p.ingredient_id
                if key not in items:
                    items[key] = {
                        "key": key,
                        "name": p.ingredient_name,
                        "amount": 0,
                        "unit": unit,
                        "category": p.category,
                        "source": "menu",
                    }
                items[key]["amount"] += amount
    for p in (await session.scalars(select(ManualItem).where(ManualItem.household_id == hid, ManualItem.date.between(start, end)))).all():
        key = f"x:{p.id}"
        items[key] = {"key": key, "name": p.name, "amount": p.amount, "unit": p.unit, "category": "Другое", "source": "manual"}
    period = f"{start}:{end}"
    statuses = {
        s.item_key: s.fingerprint
        for s in (
            await session.scalars(select(ShoppingStatus).where(ShoppingStatus.household_id == hid, ShoppingStatus.period == period))
        ).all()
    }
    for item in items.values():
        item["amount"] = round(item["amount"], 3)
        item["fingerprint"] = hashlib.sha256(f"{item['key']}:{item['amount']}:{item['unit']}".encode()).hexdigest()
        item["bought"] = statuses.get(item["key"]) == item["fingerprint"]
    return sorted(items.values(), key=lambda x: (x["category"], x["name"]))


async def toggle_shopping(session, uid, hid, start, end, index):
    await require(session, uid, hid, "shop")
    items = await shopping(session, uid, hid, start, end)
    if index < 0 or index >= len(items):
        raise ValueError("Список изменился. Откройте покупки снова.")
    item = items[index]
    key = (hid, f"{start}:{end}", item["key"])
    status = await session.get(ShoppingStatus, key)
    if status:
        await session.delete(status)
        await session.flush()
    if not item["bought"]:
        session.add(ShoppingStatus(household_id=hid, period=key[1], item_key=item["key"], fingerprint=item["fingerprint"]))
    audit(session, uid, hid, "shopping_change", item["key"])
