from app.services.domain import MenuData, RecipeData, MealData, Product, DAYS


def demo():
    # Approximate example values per 100 g/ml. Not a medical recommendation.
    foods = {
        "oats": ("Овсянка", "Крупы", 370, 13, 7, 62),
        "milk": ("Молоко", "Молочное", 52, 3, 2.5, 4.7),
        "banana": ("Бананы", "Фрукты", 89, 1.1, 0.3, 23),
        "peanut": ("Арахисовая паста", "Орехи", 588, 25, 50, 20),
        "egg": ("Яйца", "Яйца", 143, 13, 10, 0.7),
        "bread": ("Хлеб", "Хлеб", 250, 9, 3, 47),
        "cheese": ("Сыр", "Молочное", 350, 25, 27, 1),
        "rice": ("Рис сухой", "Крупы", 360, 7, 1, 79),
        "chicken": ("Курица", "Мясо / рыба", 110, 23, 1.5, 0),
        "beef": ("Говядина", "Мясо / рыба", 180, 20, 11, 0),
        "pasta": ("Паста сухая", "Макароны", 350, 12, 1.5, 72),
        "potato": ("Картофель", "Овощи", 77, 2, 0.1, 17),
        "salmon": ("Лосось", "Мясо / рыба", 208, 20, 13, 0),
        "veg": ("Овощи", "Овощи", 35, 2, 0.3, 6),
        "yogurt": ("Йогурт", "Молочное", 65, 4, 3, 5),
        "quark": ("Quark", "Молочное", 67, 12, 0.2, 4),
        "nuts": ("Орехи", "Орехи", 650, 15, 60, 15),
        "oil": ("Оливковое масло", "Соусы / специи", 900, 0, 100, 0),
    }
    defs = [
        (
            "oat",
            "Овсянка с бананом",
            [("oats", 90), ("milk", 250), ("banana", 120), ("peanut", 20)],
            "1. Сварите овсянку на молоке.\n2. Добавьте банан и пасту.",
            5,
            10,
        ),
        (
            "toast",
            "Тост с яйцом и сыром",
            [("egg", 60), ("bread", 60), ("cheese", 25)],
            "1. Приготовьте яйцо.\n2. Поджарьте хлеб и добавьте сыр.",
            3,
            10,
        ),
        (
            "chicken",
            "Рис с курицей",
            [("rice", 90), ("chicken", 180), ("veg", 150), ("oil", 10)],
            "1. Сварите рис.\n2. Обжарьте курицу до полной готовности.\n3. Добавьте овощи и подайте с рисом.",
            5,
            25,
        ),
        (
            "snack",
            "Йогурт, Quark и орехи",
            [("yogurt", 150), ("quark", 100), ("nuts", 20)],
            "1. Смешайте йогурт и Quark.\n2. Добавьте орехи.",
            2,
            0,
        ),
        (
            "beef",
            "Паста с говядиной",
            [("pasta", 75), ("beef", 100), ("veg", 150), ("oil", 5)],
            "1. Сварите пасту.\n2. Потушите говядину с овощами до готовности.\n3. Смешайте.",
            5,
            30,
        ),
        (
            "fish",
            "Лосось с картофелем",
            [("salmon", 140), ("potato", 260), ("veg", 150), ("oil", 5)],
            "1. Нарежьте картофель.\n2. Запеките с рыбой до полной готовности.\n3. Подайте с овощами.",
            10,
            30,
        ),
    ]
    recipes = []
    for key, name, ingredients, instructions, prep, cook in defs:
        products = []
        for pid, amount in ingredients:
            n, category, kcal, protein, fat, carbs = foods[pid]
            products.append(
                Product(
                    ingredient_id=pid,
                    ingredient_name=n,
                    amount=amount,
                    unit="ml" if pid == "milk" else "g",
                    category=category,
                    kcal_per_100=kcal,
                    protein_per_100=protein,
                    fat_per_100=fat,
                    carbs_per_100=carbs,
                )
            )
        recipes.append(
            RecipeData(recipe_id=key, recipe_name=name, ingredients=products, instructions=instructions, prep_time=prep, cook_time=cook)
        )
    meals = []
    for i, day in enumerate(DAYS):
        for order, (kind, rid) in enumerate(
            [
                ("breakfast", "oat"),
                ("second_breakfast", "toast"),
                ("lunch", "chicken"),
                ("snack", "snack"),
                ("dinner", "beef" if i % 2 == 0 else "fish"),
            ],
            1,
        ):
            recipe = next(r for r in recipes if r.recipe_id == rid)
            meals.append(MealData(day=day, meal_type=kind, meal_order=order, meal_name=recipe.recipe_name, recipe_id=rid))
    return MenuData(meals=meals, recipes=recipes)
