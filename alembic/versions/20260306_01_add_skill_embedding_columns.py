"""add skill embedding columns for applicants and jobs

Revision ID: 20260306_01
Revises: 20260304_01
Create Date: 2026-03-06
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260306_01"
down_revision = "20260304_01"
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
    _add_column_if_missing("applicants", sa.Column("skill_embeddings", sa.JSON(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("required_skill_embeddings", sa.JSON(), nullable=True))


def downgrade() -> None:
    applicant_columns = _get_columns("applicants")
    job_columns = _get_columns("jobs")

    if "skill_embeddings" in applicant_columns:
        op.drop_column("applicants", "skill_embeddings")
    if "required_skill_embeddings" in job_columns:
        op.drop_column("jobs", "required_skill_embeddings")
