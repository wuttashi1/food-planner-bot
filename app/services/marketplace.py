"""Public sharing is opt-in; snapshots and imported menus have independent lifetimes."""

from sqlalchemy import select, func
from app.database.models import Menu, MenuPublication, MenuCopy, User
from app.services import planner
from app.services.domain import MenuData, nutrition


def public_data(data):
    enabled = [m.model_copy(deep=True) for m in data.meals if m.enabled]
    used = {m.recipe_id for m in enabled}
    if not enabled:
        raise ValueError("В меню нет включённых приёмов пищи для публикации.")
    return MenuData(meals=enabled, recipes=[r.model_copy(deep=True) for r in data.recipes if r.recipe_id in used])


def summary(data):
    recipes = {r.recipe_id: r for r in data.recipes}
    days = len({m.day for m in data.meals if m.enabled})
    kcal = sum(nutrition(recipes[m.recipe_id], m.portion)["kcal"] for m in data.meals if m.enabled)
    return f"📅 {days} дней · 🍽 {len(data.meals)} приёмов\n🔥 {kcal / max(days, 1):.0f} kcal/день · 🥘 {len(data.recipes)} рецептов"


async def prepare(session, uid, hid):
    await planner.require(session, uid, hid, "owner")
    menu = await planner.active_menu(session, uid, hid)
    if not menu or not menu.active_version_id:
        raise ValueError("Сначала добавьте и выберите меню в «Мои меню».")
    data, _ = await planner.load_data(session, menu.active_version_id)
    return menu, public_data(data)


async def publish(session, uid, hid, menu_id, version_id, description, expected_title):
    menu, data = await prepare(session, uid, hid)
    if menu.id != menu_id or menu.active_version_id != version_id or menu.name != expected_title:
        raise ValueError("Меню изменилось после preview. Подготовьте публикацию заново.")
    description = description.strip()
    if not 1 <= len(description) <= 600:
        raise ValueError("Описание должно содержать от 1 до 600 символов.")
    existing = await session.scalar(select(MenuPublication).where(MenuPublication.source_version_id == version_id))
    if existing:
        if existing.blocked:
            raise ValueError("Эта публикация скрыта администратором.")
        if existing.author_id != uid:
            raise planner.AccessDenied("Эта версия уже опубликована другим автором.")
        if not existing.visible:
            raise ValueError("Эта версия снята с публикации. Измените меню и опубликуйте новую версию.")
        return existing
    author = await session.get(User, uid)
    item = MenuPublication(
        author_id=uid,
        source_menu_id=menu.id,
        source_version_id=version_id,
        title=menu.name,
        author_name=author.name,
        description=description,
        search_text=f"{menu.name} {description} {author.name}".casefold(),
        payload=data.model_dump(mode="json"),
    )
    session.add(item)
    await session.flush()
    planner.audit(session, uid, hid, "market_publish", str(item.id))
    return item


async def listing(session, ident, uid=None, admin=False):
    item = await session.get(MenuPublication, ident)
    if not item or ((not item.visible or item.blocked) and not (item.author_id == uid or admin)):
        raise ValueError("Публикация больше недоступна.")
    return item


async def catalog(session, page=0, query="", author_id=None):
    if page < 0:
        raise ValueError("Некорректная страница.")
    statement = select(MenuPublication)
    if author_id is not None:
        statement = statement.where(MenuPublication.author_id == author_id)
    else:
        statement = statement.where(MenuPublication.visible.is_(True), MenuPublication.blocked.is_(False))
    if query:
        statement = statement.where(MenuPublication.search_text.contains(query.casefold(), autoescape=True))
    return (
        await session.scalars(statement.order_by(MenuPublication.created_at.desc(), MenuPublication.id.desc()).offset(page * 8).limit(9))
    ).all()


async def copy_menu(session, uid, hid, ident):
    h = await planner.require(session, uid, hid, "import")
    item = await listing(session, ident)
    copied = await session.get(MenuCopy, (ident, hid))
    if copied and copied.menu_id:
        return await session.get(Menu, copied.menu_id), False
    previous = h.active_menu_id
    data = public_data(MenuData.model_validate(item.payload))
    menu = await planner.save_version(session, uid, hid, data, mode="separate", name=item.title, action="edit")
    # Adding to the library never silently switches the household's current plan.
    h.active_menu_id = previous
    if copied:
        copied.menu_id = menu.id
    else:
        session.add(MenuCopy(publication_id=ident, household_id=hid, menu_id=menu.id))
    planner.audit(session, uid, hid, "market_copy", str(ident))
    await session.flush()
    return menu, True


async def withdraw(session, uid, hid, ident, admin=False):
    await planner.require(session, uid, hid)
    item = await listing(session, ident, uid=uid, admin=admin)
    if admin:
        item.blocked = True
    elif item.author_id == uid:
        item.visible = False
    else:
        raise planner.AccessDenied("Снять публикацию может только её автор.")
    planner.audit(session, uid, hid, "market_block" if admin else "market_withdraw", str(ident))


async def copy_count(session, ident):
    return await session.scalar(select(func.count()).select_from(MenuCopy).where(MenuCopy.publication_id == ident))
