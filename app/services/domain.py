from collections import defaultdict
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MEALS = {
    "breakfast": "🌅 Завтрак",
    "second_breakfast": "🥪 Второй завтрак",
    "lunch": "🍛 Обед",
    "snack": "🍎 Полдник",
    "dinner": "🌙 Ужин",
    "late_snack": "🥛 Перед сном",
}
UNITS = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "pcs": ("pcs", 1)}
Positive = Annotated[float, Field(gt=0, le=100000, allow_inf_nan=False)]
NonNegative = Annotated[float, Field(ge=0, le=100000, allow_inf_nan=False)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]


def canonical(amount, unit):
    if unit not in UNITS:
        raise ValueError(f"Неизвестная единица: {unit}. Используйте g, kg, ml, l, pcs.")
    base, factor = UNITS[unit]
    return float(Decimal(str(amount)) * factor), base


def quantity(amount, unit):
    if unit in ("g", "ml") and amount >= 1000:
        return f"{amount / 1000:g} {'кг' if unit == 'g' else 'л'}"
    return f"{amount:g} " + {"g": "г", "ml": "мл", "pcs": "шт"}.get(unit, unit)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Product(Strict):
    ingredient_id: Identifier
    ingredient_name: str = Field(min_length=1, max_length=128)
    amount: Positive
    unit: str
    category: str = Field(default="Другое", max_length=64)
    kcal_per_100: NonNegative
    protein_per_100: NonNegative
    fat_per_100: NonNegative
    carbs_per_100: NonNegative

    @field_validator("unit")
    @classmethod
    def unit_valid(cls, value):
        canonical(1, value)
        return value


class RecipeData(Strict):
    recipe_id: Identifier
    recipe_name: str = Field(min_length=1, max_length=128)
    servings: Positive = 1
    prep_time: int = Field(ge=0, le=1440, default=0)
    cook_time: int = Field(ge=0, le=1440, default=0)
    instructions: str = Field(default="", max_length=10000)
    ingredients: list[Product] = Field(min_length=1, max_length=100)


class MealData(Strict):
    day: str
    meal_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    meal_order: int = Field(ge=1, le=100)
    meal_name: str = Field(min_length=1, max_length=128)
    recipe_id: Identifier
    portion: Positive = 1
    enabled: bool = True

    @field_validator("day")
    @classmethod
    def day_valid(cls, value):
        if value not in DAYS:
            raise ValueError("day должен быть monday … sunday")
        return value

    @property
    def slot(self):
        return f"{DAYS.index(self.day)}:{self.meal_type}:{self.meal_order}"


class NoticeData(Strict):
    notification_type: str
    meal_type: str = ""
    enabled: bool = True
    time: str = "08:30"
    offset_minutes: int = Field(default=0, ge=0, le=1440)
    weekday: int = Field(default=5, ge=0, le=6)

    @model_validator(mode="after")
    def valid_meal(self):
        if self.notification_type in ("meal", "cook") and not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", self.meal_type):
            raise ValueError("Укажите корректный тип приёма пищи для еды/готовки")
        return self

    @field_validator("time")
    @classmethod
    def valid_time(cls, value):
        from datetime import datetime

        datetime.strptime(value, "%H:%M")
        if len(value) != 5:
            raise ValueError("Время в формате HH:MM")
        return value

    @field_validator("notification_type")
    @classmethod
    def valid_kind(cls, value):
        if value not in ("meal", "cook", "shopping_today", "shopping_tomorrow", "shopping_week", "weekly_plan"):
            raise ValueError("Неизвестный тип уведомления")
        return value


class MenuData(Strict):
    menu_id: int = Field(default=0, ge=0)
    menu_version: int = Field(default=0, ge=0)
    meals: list[MealData] = Field(min_length=1, max_length=700)
    recipes: list[RecipeData] = Field(min_length=1, max_length=700)
    settings: dict = Field(default_factory=dict)
    notifications: list[NoticeData] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def integrity(self):
        recipes = {r.recipe_id: r for r in self.recipes}
        if len(recipes) != len(self.recipes):
            raise ValueError("Дубли recipe_id")
        slots = set()
        products = {}
        for r in self.recipes:
            seen = set()
            for p in r.ingredients:
                if p.ingredient_id in seen:
                    raise ValueError(f"Дубль ingredient_id в рецепте {r.recipe_id}")
                seen.add(p.ingredient_id)
                definition = p.model_dump(exclude={"amount", "unit"}) | {"unit": canonical(1, p.unit)[1]}
                if p.ingredient_id in products and products[p.ingredient_id] != definition:
                    raise ValueError(f"Противоречивые данные продукта {p.ingredient_id}")
                products[p.ingredient_id] = definition
        for m in self.meals:
            if m.recipe_id not in recipes:
                raise ValueError(f"Нет рецепта {m.recipe_id}")
            if m.slot in slots:
                raise ValueError(f"Дубль приёма {m.slot}")
            slots.add(m.slot)
        known = {
            "timezone",
            "name",
            "weight",
            "height",
            "target_weight",
            "target_calories",
            "target_protein",
            "target_fat",
            "target_carbs",
            "gpt_prompt",
            "notifications_enabled",
            "disabled_meals",
            "units",
        }
        if set(self.settings) - known:
            raise ValueError("Неизвестные SETTINGS: " + ", ".join(set(self.settings) - known))
        for key, value in self.settings.items():
            if key == "timezone":
                try:
                    ZoneInfo(str(value))
                except (ZoneInfoNotFoundError, ValueError) as exc:
                    raise ValueError("Неизвестный часовой пояс.") from exc
            elif key in {"weight", "height", "target_weight", "target_calories", "target_protein", "target_fat", "target_carbs"}:
                number = float(value)
                if not 0 < number <= 100000:
                    raise ValueError(f"Некорректный {key}")
            elif key == "disabled_meals":
                if not isinstance(value, list) or any(
                    not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", item) for item in value
                ):
                    raise ValueError("disabled_meals должен быть массивом названий приёмов пищи")
            elif key in ("name", "gpt_prompt", "units"):
                limit = 3000 if key == "gpt_prompt" else 128
                if not isinstance(value, str) or not 1 <= len(value) <= limit:
                    raise ValueError(f"{key}: требуется текст длиной 1–{limit}")
            elif key == "notifications_enabled" and not isinstance(value, bool):
                raise ValueError("notifications_enabled должен быть TRUE/FALSE")
        notice_keys = [(n.notification_type, n.meal_type) for n in self.notifications]
        if len(notice_keys) != len(set(notice_keys)):
            raise ValueError("Повтор настройки уведомлений")
        return self


def amounts(recipe, portion=1, overrides=None):
    overrides = overrides or {}
    return [
        (p, overrides.get(p.ingredient_id, canonical(p.amount, p.unit)[0] * portion / recipe.servings), canonical(p.amount, p.unit)[1])
        for p in recipe.ingredients
    ]


def nutrition(recipe, portion=1, overrides=None):
    result = defaultdict(Decimal)
    for p, amount, _ in amounts(recipe, portion, overrides):
        for key in ("kcal", "protein", "fat", "carbs"):
            result[key] += Decimal(str(amount)) * Decimal(str(getattr(p, key + "_per_100"))) / 100
    return {key: round(float(value), 2) for key, value in result.items()}
