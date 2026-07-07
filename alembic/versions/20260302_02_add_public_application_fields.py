"""add public application response fields to applicants

Revision ID: 20260302_02
Revises: 20260302_01
Create Date: 2026-03-02
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260302_02"
down_revision = "20260302_01"
branch_labels = None
depends_on = None


def _get_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    existing = _get_columns(table_name)
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing("applicants", sa.Column("cover_letter", sa.Text(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("portfolio_url", sa.String(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("custom_answers", sa.JSON(), nullable=True))


def downgrade() -> None:
    existing = _get_columns("applicants")

    if "custom_answers" in existing:
        op.drop_column("applicants", "custom_answers")
    if "portfolio_url" in existing:
        op.drop_column("applicants", "portfolio_url")
    if "cover_letter" in existing:
        op.drop_column("applicants", "cover_letter")
