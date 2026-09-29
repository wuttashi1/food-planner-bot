from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=128)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    days: Literal[1, 3, 5, 7] = 1
    goal: Literal["weight_gain", "maintenance", "weight_loss", "custom"] = "maintenance"
    target_kcal: float = Field(default=2000, ge=800, le=6000, allow_inf_nan=False)
    meals_per_day: Literal[3, 4, 5] = 4
    budget_eur: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    allergies: list[Text] = Field(default_factory=list, max_length=20)
    excluded_foods: list[Text] = Field(default_factory=list, max_length=20)
    disliked_foods: list[Text] = Field(default_factory=list, max_length=20)
    diet: Literal["regular", "vegetarian", "custom"] = "regular"
    max_cooking_minutes: Literal[15, 30, 45] | None = 30
    wishes: str = Field(default="", max_length=500)
    output_language: Literal["ru", "uk", "en", "de"] = "en"


class Output(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Food(Output):
    name: Text
    amount: float = Field(gt=0, le=5000, allow_inf_nan=False)
    unit: Literal["g", "ml", "pcs"]


class Dish(Output):
    meal_type: Literal["breakfast", "lunch", "snack", "dinner"]
    title: Text
    recipe_id: str | None = Field(default=None, max_length=64)
    ingredients: list[Food] = Field(min_length=1, max_length=30)
    instructions: str = Field(min_length=1, max_length=3000)
    estimated_cooking_minutes: int = Field(ge=1, le=240)


class Day(Output):
    day: int = Field(ge=1, le=7)
    meals: list[Dish] = Field(min_length=1, max_length=5)


class Plan(Output):
    plan_name: Text
    days: list[Day] = Field(min_length=1, max_length=7)


def response_schema() -> dict:
    schema = Plan.model_json_schema()

    def strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for value in node:
                strict(value)

    strict(schema)
    return schema
