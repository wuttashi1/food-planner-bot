import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from app.services.ai_schema import Request, Plan
from app.services.domain import MenuData, MealData, RecipeData, DAYS, nutrition, canonical
from app.services.openrouter import AIError

# Aliases are matched against names, never guessed from imported external IDs.
GROUPS = [
    ["курица", "куриная грудка", "куряче філе", "chicken breast", "hähnchenbrust"],
    ["овсянка", "вівсянка", "oats", "haferflocken"],
    ["рис сухой", "рис сухий", "dry rice", "reis trocken"],
    ["паста сухая", "сухі макарони", "dry pasta", "nudeln trocken"],
    ["картофель", "картопля", "potatoes", "kartoffeln"],
    ["хлеб", "хліб", "bread", "brot"],
    ["молоко", "milk", "milch"],
    ["яйца", "яйця", "eggs", "eier"],
    ["сыр", "сир", "cheese", "käse"],
    ["говядина", "яловичина", "beef", "rindfleisch"],
    ["лосось", "salmon", "lachs"],
    ["йогурт", "yogurt", "joghurt"],
    ["орехи", "горіхи", "nuts", "nüsse"],
    ["бананы", "банани", "banana", "bananen"],
    ["оливковое масло", "оливкова олія", "olive oil", "olivenöl"],
    ["арахисовая паста", "арахісова паста", "peanut butter", "erdnussbutter"],
]
SCALABLE = set(range(6)) | {9}
VEGETARIAN_EXCLUDED = {0, 9, 10}
ALLERGEN_GROUPS = {
    "milk": {6, 8, 11},
    "молоко": {6, 8, 11},
    "milch": {6, 8, 11},
    "лактоза": {6, 8, 11},
    "lactose": {6, 8, 11},
    "laktose": {6, 8, 11},
    "nuts": {12, 15},
    "орехи": {12, 15},
    "горіхи": {12, 15},
    "nüsse": {12, 15},
    "peanut": {15},
    "арахис": {15},
    "арахіс": {15},
    "erdnuss": {15},
    "gluten": {1, 3, 5},
    "глютен": {1, 3, 5},
    "fish": {10},
    "рыба": {10},
    "риба": {10},
    "fisch": {10},
    "egg": {7},
    "яйцо": {7},
    "яйце": {7},
    "ei": {7},
}


def normalize(value: str) -> str:
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")).strip()


class Resolver:
    def __init__(self, data: MenuData, aliases=()):
        self.products = {p.ingredient_id: p for r in data.recipes for p in r.ingredients}
        self.names: dict[str, set[str]] = {}
        self.groups: dict[str, set[int]] = {}
        for key, product in self.products.items():
            self.add(product.ingredient_name, key)
            for index, group in enumerate(GROUPS):
                if normalize(product.ingredient_name) in {normalize(n) for n in group}:
                    self.groups.setdefault(key, set()).add(index)
                    for name in group:
                        self.add(name, key)
        for name, key in aliases:
            if key in self.products:
                self.add(name, key)

    def add(self, name, key):
        self.names.setdefault(normalize(name), set()).add(key)

    def resolve(self, name: str, fuzzy=True):
        name = normalize(name)
        matches = self.names.get(name, set())
        if not matches and fuzzy and len(name) >= 7:
            scores = sorted(
                ((SequenceMatcher(None, name, n).ratio(), ids) for n, ids in self.names.items()), key=lambda item: item[0], reverse=True
            )
            if scores and scores[0][0] >= 0.95:
                matches = set().union(*(ids for score, ids in scores if score >= scores[0][0] - 0.05))
        if len(matches) != 1:
            raise AIError("ingredients")
        return self.products[next(iter(matches))]

    def blocked(self, request: Request) -> set[str]:
        blocked = set()
        for term in request.allergies + request.excluded_foods + request.disliked_foods:
            norm = normalize(term)
            groups = ALLERGEN_GROUPS.get(norm, set())
            matches = self.names.get(norm, set())
            matched = matches | {key for key, values in self.groups.items() if groups & values}
            if not matched:
                # Unrecognized restrictions cannot safely be checked against the local catalog.
                raise AIError("restriction")
            blocked |= matched
        if request.diet == "vegetarian":
            blocked |= {key for key, values in self.groups.items() if values & VEGETARIAN_EXCLUDED}
            for key, p in self.products.items():
                if re.search("мяс|рыб|м’яс|риб|meat|fish|fleisch|fisch", p.category.casefold()):
                    blocked.add(key)
        return blocked


def convert(plan: Plan, request: Request, catalog: MenuData, resolver: Resolver, day_numbers=None, meal_types=None) -> MenuData:
    expected_days = day_numbers or list(range(1, request.days + 1))
    if [day.day for day in plan.days] != expected_days:
        raise AIError("invalid_response")
    expected = meal_types or (["breakfast", "lunch", "dinner"] + ["snack"] * (request.meals_per_day - 3))
    blocked = resolver.blocked(request)
    existing = {r.recipe_id: r for r in catalog.recipes}
    recipes, meals = {}, []
    for day in plan.days:
        if Counter(m.meal_type for m in day.meals) != Counter(expected):
            raise AIError("invalid_response")
        for order, dish in enumerate(day.meals, 1):
            if request.max_cooking_minutes and dish.estimated_cooking_minutes > request.max_cooking_minutes:
                raise AIError("invalid_response")
            if dish.recipe_id:
                if dish.recipe_id not in existing:
                    raise AIError("invalid_response")
                recipe = existing[dish.recipe_id].model_copy(deep=True)
                if request.max_cooking_minutes and recipe.prep_time + recipe.cook_time > request.max_cooking_minutes:
                    raise AIError("invalid_response")
            else:
                products = []
                seen = set()
                for food in dish.ingredients:
                    product = resolver.resolve(food.name)
                    if canonical(1, product.unit)[1] != food.unit or product.ingredient_id in seen:
                        raise AIError("ingredients")
                    seen.add(product.ingredient_id)
                    products.append(product.model_copy(update={"amount": food.amount, "unit": food.unit}))
                rid = f"ai_{day.day}_{order}"
                while rid in existing:
                    rid += "_n"
                recipe = RecipeData(
                    recipe_id=rid,
                    recipe_name=dish.title,
                    instructions=dish.instructions,
                    cook_time=dish.estimated_cooking_minutes,
                    ingredients=products,
                )
            if any(p.ingredient_id in blocked for p in recipe.ingredients):
                raise AIError("restriction")
            recipes[recipe.recipe_id] = recipe
            meals.append(
                MealData(
                    day=DAYS[day.day - 1], meal_type=dish.meal_type, meal_order=order, meal_name=dish.title, recipe_id=recipe.recipe_id
                )
            )
    return MenuData(recipes=list(recipes.values()), meals=meals)


def scale(data: MenuData, target: float, resolver: Resolver) -> MenuData:
    result = data.model_copy(deep=True)
    recipes = {r.recipe_id: r for r in result.recipes}
    for day in {m.day for m in result.meals}:
        meals = [m for m in result.meals if m.day == day]
        total = sum(nutrition(recipes[m.recipe_id], m.portion)["kcal"] for m in meals)
        if abs(total - target) <= target * 0.1:
            continue
        adjustable = sum(
            nutrition(
                recipes[m.recipe_id],
                m.portion,
                {
                    p.ingredient_id: 0
                    for p in recipes[m.recipe_id].ingredients
                    if not resolver.groups.get(p.ingredient_id, set()) & SCALABLE
                },
            )["kcal"]
            for m in meals
        )
        if not adjustable:
            raise AIError("target")
        factor = (target - (total - adjustable)) / adjustable
        if not 0.5 <= factor <= 2:
            raise AIError("target")
        for m in meals:
            recipe = recipes[m.recipe_id].model_copy(deep=True)
            recipe.recipe_id = f"{recipe.recipe_id[:40]}_scaled_{DAYS.index(day)}_{m.meal_order}"
            for p in recipe.ingredients:
                if resolver.groups.get(p.ingredient_id, set()) & SCALABLE:
                    p.amount = round(p.amount * factor, 3)
            recipes[recipe.recipe_id] = recipe
            m.recipe_id = recipe.recipe_id
    used = {m.recipe_id for m in result.meals}
    result.recipes = [r for key, r in recipes.items() if key in used]
    return MenuData.model_validate(result.model_dump())
