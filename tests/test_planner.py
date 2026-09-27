from datetime import date, timedelta
import pytest
from sqlalchemy import select, func
from app.database.models import User, MenuVersion, MealAssignment, MealCompletion, Household, utcnow
from app.services import planner
from app.services.excel import export_workbook, parse_workbook
from app.seed.demo import demo

MONDAY = date(2026, 9, 14)


async def setup(session, shared=False):
    a = await planner.ensure_user(session, 1, "A")
    menu = await planner.save_version(session, 1, a.active_household_id, demo(), action="edit")
    if shared:
        await planner.ensure_user(session, 2, "B")
        token = await planner.invite(session, 1, a.active_household_id)
        await session.flush()
        await planner.join(session, 2, token)
        await session.flush()
    return a.active_household_id, menu


async def test_two_users_changes_shopping_completion_and_rollback(db):
    async with db() as s, s.begin():
        hid, menu = await setup(s, True)
        original = menu.active_version_id
        rows = await planner.personal_meals(s, 1, hid, {0})
        lunch = next(r for r in rows if r["meal"].meal_type == "lunch")
        await planner.toggle_completion(s, 1, hid, lunch["meal"].id, MONDAY)
        await s.flush()
        assert await s.scalar(select(func.count()).select_from(MealCompletion).where(MealCompletion.user_id == 2)) == 0
        before = await planner.shopping(s, 2, hid, MONDAY, MONDAY + timedelta(days=6))
        assert next(x["amount"] for x in before if x["key"] == "m:chicken") == 2520
        await planner.edit_amount(s, 1, hid, lunch["meal"].id, "chicken", 220)
        # Independent reader session after commit below.
    async with db() as s, s.begin():
        b = await s.get(User, 2)
        assert b.active_household_id == hid
        after = await planner.shopping(s, 2, hid, MONDAY, MONDAY + timedelta(days=6))
        assert next(x["amount"] for x in after if x["key"] == "m:chicken") == 3080
        meals = await planner.personal_meals(s, 2, hid, {0})
        lunch = next(r for r in meals if r["meal"].meal_type == "lunch")
        assert next(a for p, a, u in lunch["amounts"] if p.ingredient_id == "chicken") == 220
        assert lunch["nutrition"]["kcal"] == 708.5
        await planner.rollback(s, 1, hid, original)
        restored = await planner.shopping(s, 2, hid, MONDAY, MONDAY)
        assert next(x["amount"] for x in restored if x["key"] == "m:chicken") == 360


async def test_personal_ingredient_portions_and_disabled_meals(db):
    async with db() as s, s.begin():
        hid, menu = await setup(s, True)
        s.add(MealAssignment(menu_id=menu.id, user_id=1, slot="0:lunch:3", portion=1, amounts={"chicken": 220, "rice": 120}))
        s.add(MealAssignment(menu_id=menu.id, user_id=2, slot="0:lunch:3", portion=1, amounts={"chicken": 150, "rice": 80}))
        await s.flush()
        items = await planner.shopping(s, 1, hid, MONDAY, MONDAY)
        assert next(x["amount"] for x in items if x["key"] == "m:chicken") == 370
        assert next(x["amount"] for x in items if x["key"] == "m:rice") == 200
        b = await s.get(User, 2)
        b.settings = b.settings | {"disabled_meals": ["lunch"]}
        items = await planner.shopping(s, 1, hid, MONDAY, MONDAY)
        assert next(x["amount"] for x in items if x["key"] == "m:chicken") == 220


async def test_permissions_and_cross_household_ids(db):
    async with db() as s, s.begin():
        hid, menu = await setup(s, True)
        rows = await planner.personal_meals(s, 1, hid, {0})
        mid = rows[0]["meal"].id
        with pytest.raises(planner.AccessDenied):
            await planner.edit_amount(s, 2, hid, mid, "oats", 100)
        with pytest.raises(planner.AccessDenied):
            await planner.require(s, 99, hid)
        await planner.change_role(s, 1, hid, 2, "EDITOR")
        await planner.require(s, 2, hid, "edit")
        with pytest.raises(planner.AccessDenied):
            await planner.require(s, 2, hid, "import")
        h = await s.get(Household, hid)
        h.editor_import = True
        await planner.require(s, 2, hid, "import")
        c = await planner.ensure_user(s, 3, "C")
        with pytest.raises(ValueError):
            await planner.scoped_meal(s, 3, c.active_household_id, mid, "edit")


async def test_invitation_expiry_reuse_and_revoke(db):
    async with db() as s, s.begin():
        hid, _ = await setup(s)
        await planner.ensure_user(s, 2, "B")
        token = await planner.invite(s, 1, hid)
        await s.flush()
        assert token not in (await planner.invitation_info(s, token)).token_hash
        await planner.join(s, 2, token)
        with pytest.raises(ValueError):
            await planner.join(s, 2, token)
        expired = await planner.invite(s, 1, hid)
        await s.flush()
        inv = await planner.invitation_info(s, expired)
        inv.expires_at = utcnow() - timedelta(seconds=1)
        with pytest.raises(ValueError):
            await planner.invitation_info(s, expired)
        inv.expires_at = utcnow() + timedelta(hours=1)
        inv.used = True
        with pytest.raises(ValueError):
            await planner.invitation_info(s, expired)


async def test_export_import_and_transaction_rollback(db):
    async with db() as s, s.begin():
        hid, menu = await setup(s)
        version = menu.active_version_id
        data, _ = await planner.load_data(s, version)
        raw = export_workbook(data)
    parsed, warnings = parse_workbook(raw)
    assert not warnings
    with pytest.raises(RuntimeError):
        async with db() as s, s.begin():
            await planner.save_version(s, 1, hid, parsed)
            raise RuntimeError("Simulated failure before commit")
    async with db() as s, s.begin():
        menu = await planner.active_menu(s, 1, hid)
        assert menu.active_version_id == version
        assert await s.scalar(select(func.count()).select_from(MenuVersion)) == 1
        await planner.save_version(s, 1, hid, parsed)
        assert menu.active_version_id != version


async def test_stale_preview_and_foreign_menu(db):
    async with db() as s, s.begin():
        hid, menu = await setup(s)
        revision = menu.revision
        data, _ = await planner.load_data(s, menu.active_version_id)
        await planner.save_version(s, 1, hid, data, action="edit")
        with pytest.raises(ValueError, match="изменилось"):
            await planner.save_version(s, 1, hid, data, expected_revision=revision)
        data.menu_id = 9999
        with pytest.raises(ValueError, match="другому меню"):
            await planner.save_version(s, 1, hid, data)


async def test_checklist_shared_and_invalidated_by_amount_change(db):
    async with db() as s, s.begin():
        hid, menu = await setup(s, True)
        items = await planner.shopping(s, 1, hid, MONDAY, MONDAY)
        index = next(i for i, x in enumerate(items) if x["key"] == "m:chicken")
        await planner.toggle_shopping(s, 1, hid, MONDAY, MONDAY, index)
        await s.flush()
        items = await planner.shopping(s, 2, hid, MONDAY, MONDAY)
        assert items[index]["bought"]
        lunch = next(x for x in await planner.personal_meals(s, 1, hid, {0}) if x["meal"].meal_type == "lunch")
        await planner.edit_amount(s, 1, hid, lunch["meal"].id, "chicken", 220)
        assert not (await planner.shopping(s, 2, hid, MONDAY, MONDAY))[index]["bought"]


async def test_delete_menu_cascades_without_orphans(db):
    async with db() as s, s.begin():
        hid, menu = await setup(s)
        h = await s.get(Household, hid)
        h.active_menu_id = None
        await s.delete(menu)
    async with db() as s:
        assert await s.scalar(select(func.count()).select_from(MenuVersion)) == 0
