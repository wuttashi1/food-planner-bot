"""AI usage and ingredient aliases."""

from alembic import op
import sqlalchemy as sa

revision = "4c10_ai_menu"
down_revision = "2719b51919c2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(40)),
        sa.Column("duration_ms", sa.Integer()),
    )
    op.create_index("ix_ai_usage_user_id", "ai_usage", ["user_id"])
    op.create_table(
        "ingredient_aliases",
        sa.Column("ingredient_id", sa.Integer(), sa.ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("locale", sa.String(2), primary_key=True),
        sa.Column("alias", sa.String(128), primary_key=True),
    )


def downgrade():
    op.drop_table("ingredient_aliases")
    op.drop_index("ix_ai_usage_user_id", "ai_usage")
    op.drop_table("ai_usage")
