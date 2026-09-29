import time
import asyncio
from datetime import timedelta
from sqlalchemy import select, func
import structlog
from app.database.models import AIUsage, IngredientAlias, Ingredient, utcnow
from app.services import planner
from app.services.ai_schema import Request
from app.services.ai_ingredients import Resolver, convert, scale
from app.services.domain import MenuData, DAYS
from app.services.openrouter import OpenRouter, AIError


async def reserve(session, uid, request, settings):
    now = utcnow()
    if uid not in settings.admins:
        count = await session.scalar(
            select(func.count())
            .select_from(AIUsage)
            .where(AIUsage.user_id == uid, AIUsage.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0))
        )
        last = await session.scalar(select(func.max(AIUsage.created_at)).where(AIUsage.user_id == uid))
        if count >= settings.ai_user_daily_limit:
            raise AIError("daily_limit")
        if last and now - last < timedelta(seconds=settings.ai_user_cooldown_seconds):
            raise AIError("cooldown")
    usage = AIUsage(user_id=uid, model=settings.openrouter_model, status="started", days=request.days)
    session.add(usage)
    await session.flush()
    return usage.id


async def generate(
    sessions,
    lock,
    uid: int,
    hid: int,
    request: Request,
    settings,
    previous: dict | None = None,
    day: int | None = None,
    meal: int | None = None,
    client=None,
) -> dict:
    if not settings.openrouter_api_key.get_secret_value():
        raise AIError("not_configured")
    async with lock, sessions() as session, session.begin():
        await planner.require(session, uid, hid, "edit")
        menu = await planner.active_menu(session, uid, hid)
        if not menu:
            raise AIError("catalog")
        catalog, _ = await planner.load_data(session, menu.active_version_id)
        aliases = (
            await session.execute(
                select(IngredientAlias.alias, Ingredient.external_id)
                .join(Ingredient, Ingredient.id == IngredientAlias.ingredient_id)
                .where(Ingredient.version_id == menu.active_version_id)
            )
        ).all()
        resolver = Resolver(catalog, aliases)
        blocked = resolver.blocked(request)
        revision, menu_id = menu.revision, menu.id
        products = [p for key, p in resolver.products.items() if key not in blocked]
        if not products or len(products) > 300:
            raise AIError("catalog")
        payload = request.model_dump()
        payload["ingredients"] = [{"name": p.ingredient_name, "unit": p.unit} for p in products]
        payload["recipes"] = [
            {"recipe_id": r.recipe_id, "title": r.recipe_name, "ingredients": [p.ingredient_name for p in r.ingredients]}
            for r in catalog.recipes[:40]
            if not any(p.ingredient_id in blocked for p in r.ingredients)
        ]
        day_numbers, meal_types = None, None
        old = None
        if day is not None:
            if not previous or previous["hid"] != hid or previous["menu_id"] != menu_id or previous["revision"] != revision:
                raise AIError("stale")
            old = MenuData.model_validate(previous["data"])
            same_day = [m for m in old.meals if DAYS.index(m.day) == day]
            if not same_day:
                raise AIError("stale")
            day_numbers = [day + 1]
            payload["days"] = 1
            payload["day_numbers"] = day_numbers
            if meal is not None:
                selected = next((m for m in same_day if m.meal_order == meal), None)
                if not selected:
                    raise AIError("stale")
                meal_types = [selected.meal_type]
                payload["meals_per_day"] = 1
                payload["meal_types"] = meal_types
                payload["other_meals"] = [m.model_dump(include={"meal_type", "meal_name"}) for m in same_day if m != selected]
        usage_id = await reserve(session, uid, request, settings)
    logger = structlog.get_logger()
    logger.info("ai_generation_started", user_id=uid, days=request.days, model=settings.openrouter_model)
    started = time.monotonic()
    error = None
    try:
        api = client or OpenRouter(settings)
        for attempt in range(2):
            try:
                plan = await api.generate(payload)
                data = convert(plan, request, catalog, resolver, day_numbers, meal_types)
                break
            except AIError as exc:
                if attempt or exc.code not in ("ingredients", "invalid_response", "restriction"):
                    raise
                payload["repair"] = {
                    "error": exc.code,
                    "instruction": "Return a corrected plan using only the provided catalog and restrictions.",
                }
        if old:
            keep = [m for m in old.meals if not (DAYS.index(m.day) == day and (meal is None or m.meal_order == meal))]
            used = {m.recipe_id for m in keep}
            old_recipes = [r for r in old.recipes if r.recipe_id in used]
            existing_ids = {r.recipe_id for r in old_recipes}
            for r in data.recipes:
                if r.recipe_id in existing_ids:
                    new_id = r.recipe_id[:45] + "_replacement"
                    for m in data.meals:
                        if m.recipe_id == r.recipe_id:
                            m.recipe_id = new_id
                    r.recipe_id = new_id
            if meal is not None:
                data.meals[0].meal_order = meal
            data = MenuData(meals=keep + data.meals, recipes=old_recipes + data.recipes)
        data = scale(data, request.target_kcal, resolver)
        preview = {
            "hid": hid,
            "menu_id": menu_id,
            "revision": revision,
            "name": plan.plan_name,
            "data": data.model_dump(),
            "request": request.model_dump(),
            "usage_id": usage_id,
        }
        return preview
    except asyncio.CancelledError:
        error = "cancelled"
        raise
    except AIError as exc:
        error = exc.code
        raise
    except Exception:
        error = "provider"
        raise
    finally:
        duration = int((time.monotonic() - started) * 1000)
        async with lock, sessions() as session, session.begin():
            usage = await session.get(AIUsage, usage_id)
            usage.status = "failed" if error else "preview"
            usage.error_code = error
            usage.duration_ms = duration
        logger.info("ai_generation_finished", user_id=uid, duration_ms=duration, error_code=error)


async def save(session, uid: int, hid: int, preview: dict):
    if preview["hid"] != hid:
        raise AIError("stale")
    menu = await planner.active_menu(session, uid, hid)
    if not menu or menu.id != preview["menu_id"]:
        raise AIError("stale")
    data = MenuData.model_validate(preview["data"])
    data.menu_id = menu.id
    old_version_id = menu.active_version_id
    result = await planner.save_version(session, uid, hid, data, expected_revision=preview["revision"], action="edit")
    aliases = (
        await session.execute(
            select(IngredientAlias.locale, IngredientAlias.alias, Ingredient.external_id)
            .join(Ingredient, Ingredient.id == IngredientAlias.ingredient_id)
            .where(Ingredient.version_id == old_version_id)
        )
    ).all()
    new_products = {
        p.external_id: p.id
        for p in (await session.scalars(select(Ingredient).where(Ingredient.version_id == result.active_version_id))).all()
    }
    for locale, alias, external_id in aliases:
        if external_id in new_products:
            session.add(IngredientAlias(ingredient_id=new_products[external_id], locale=locale, alias=alias))
    usage = await session.get(AIUsage, preview["usage_id"])
    if not usage or usage.user_id != uid or usage.status != "preview":
        raise AIError("stale")
    usage.status = "saved"
    structlog.get_logger().info("ai_menu_saved", user_id=uid, menu_id=result.id)
    return result
