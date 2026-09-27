from datetime import datetime, timezone, date
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(128))
    active_household_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Household(Base):
    __tablename__ = "households"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    personal: Mapped[bool] = mapped_column(Boolean, default=False)
    active_menu_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    editor_import: Mapped[bool] = mapped_column(Boolean, default=False)
    viewer_completion: Mapped[bool] = mapped_column(Boolean, default=True)


class Member(Base):
    __tablename__ = "members"
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), default="VIEWER")
    __table_args__ = (CheckConstraint("role IN ('OWNER','EDITOR','VIEWER')"),)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(10), default="VIEWER")


class Menu(Base):
    __tablename__ = "menus"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    active_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class MenuVersion(Base):
    __tablename__ = "menu_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    menu_id: Mapped[int] = mapped_column(ForeignKey("menus.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("menu_id", "number"),)


class Recipe(Base):
    __tablename__ = "recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("menu_versions.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256))
    servings: Mapped[float]
    prep_time: Mapped[int]
    cook_time: Mapped[int]
    instructions: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("version_id", "external_id"),)


class Ingredient(Base):
    __tablename__ = "ingredients"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("menu_versions.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256))
    unit: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(64))
    kcal: Mapped[float]
    protein: Mapped[float]
    fat: Mapped[float]
    carbs: Mapped[float]
    __table_args__ = (UniqueConstraint("version_id", "external_id"),)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True)
    amount: Mapped[float]
    __table_args__ = (CheckConstraint("amount > 0"),)


class Meal(Base):
    __tablename__ = "meals"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("menu_versions.id", ondelete="CASCADE"), index=True)
    day: Mapped[int]
    meal_type: Mapped[str] = mapped_column(String(32))
    meal_order: Mapped[int]
    name: Mapped[str] = mapped_column(String(256))
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    portion: Mapped[float]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("version_id", "day", "meal_type", "meal_order"),)


class MealAssignment(Base):
    """Stable weekly slot survives menu version changes. Overrides are canonical amounts."""

    __tablename__ = "meal_assignments"
    menu_id: Mapped[int] = mapped_column(ForeignKey("menus.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    slot: Mapped[str] = mapped_column(String(80), primary_key=True)
    portion: Mapped[float] = mapped_column(default=1)
    amounts: Mapped[dict] = mapped_column(JSON, default=dict)


class MealCompletion(Base):
    __tablename__ = "meal_completions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    meal_id: Mapped[int] = mapped_column(ForeignKey("meals.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShoppingStatus(Base):
    __tablename__ = "shopping_status"
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    period: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))


class ManualItem(Base):
    __tablename__ = "manual_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[float]
    unit: Mapped[str] = mapped_column(String(20))


class WeightLog(Base):
    __tablename__ = "weight_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    weight: Mapped[float]


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    meal_type: Mapped[str] = mapped_column(String(32), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    time: Mapped[str] = mapped_column(String(5), default="08:30")
    offset_minutes: Mapped[int] = mapped_column(Integer, default=0)
    weekday: Mapped[int] = mapped_column(Integer, default=5)
    __table_args__ = (UniqueConstraint("user_id", "kind", "meal_type"),)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(160), unique=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    household_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MenuPublication(Base):
    """Public immutable snapshot. Personal settings never belong in payload."""

    __tablename__ = "menu_publications"
    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_menu_id: Mapped[int | None] = mapped_column(ForeignKey("menus.id", ondelete="SET NULL"), nullable=True)
    source_version_id: Mapped[int | None] = mapped_column(ForeignKey("menu_versions.id", ondelete="SET NULL"), nullable=True, unique=True)
    title: Mapped[str] = mapped_column(String(128))
    author_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(600))
    search_text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class MenuCopy(Base):
    __tablename__ = "menu_copies"
    publication_id: Mapped[int] = mapped_column(ForeignKey("menu_publications.id"), primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    menu_id: Mapped[int | None] = mapped_column(ForeignKey("menus.id", ondelete="SET NULL"), nullable=True)
