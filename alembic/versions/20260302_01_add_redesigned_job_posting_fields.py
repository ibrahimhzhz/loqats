"""add redesigned job posting fields to jobs

Revision ID: 20260302_01
Revises: 
Create Date: 2026-03-02
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260302_01"
down_revision = None
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
    _add_column_if_missing("jobs", sa.Column("department", sa.String(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("job_type", sa.String(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("work_location_type", sa.String(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("office_location", sa.String(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("openings", sa.Integer(), nullable=False, server_default=sa.text("1")))

    _add_column_if_missing("jobs", sa.Column("salary_min", sa.Integer(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("salary_max", sa.Integer(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("currency", sa.String(), nullable=False, server_default=sa.text("'USD'")))
    _add_column_if_missing("jobs", sa.Column("pay_frequency", sa.String(), nullable=False, server_default=sa.text("'Annual'")))
    _add_column_if_missing("jobs", sa.Column("show_salary", sa.Boolean(), nullable=False, server_default=sa.true()))

    _add_column_if_missing("jobs", sa.Column("equity_bonus", sa.String(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("nice_to_have_skills", sa.JSON(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("benefits", sa.JSON(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("require_cover_letter", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column_if_missing("jobs", sa.Column("require_portfolio", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column_if_missing("jobs", sa.Column("require_linkedin", sa.Boolean(), nullable=False, server_default=sa.false()))

    _add_column_if_missing("jobs", sa.Column("custom_questions", sa.JSON(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("hiring_manager", sa.String(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("target_hire_date", sa.Date(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("application_deadline", sa.Date(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("visibility", sa.String(), nullable=False, server_default=sa.text("'Public'")))

    existing = _get_columns("jobs")
    if "status" in existing:
        op.alter_column("jobs", "status", existing_type=sa.String(), server_default=sa.text("'Draft'"), existing_nullable=True)


def downgrade() -> None:
    existing = _get_columns("jobs")

    if "visibility" in existing:
        op.drop_column("jobs", "visibility")
    if "application_deadline" in existing:
        op.drop_column("jobs", "application_deadline")
    if "target_hire_date" in existing:
        op.drop_column("jobs", "target_hire_date")
    if "hiring_manager" in existing:
        op.drop_column("jobs", "hiring_manager")
    if "custom_questions" in existing:
        op.drop_column("jobs", "custom_questions")

    if "require_linkedin" in existing:
        op.drop_column("jobs", "require_linkedin")
    if "require_portfolio" in existing:
        op.drop_column("jobs", "require_portfolio")
    if "require_cover_letter" in existing:
        op.drop_column("jobs", "require_cover_letter")
    if "benefits" in existing:
        op.drop_column("jobs", "benefits")
    if "nice_to_have_skills" in existing:
        op.drop_column("jobs", "nice_to_have_skills")
    if "equity_bonus" in existing:
        op.drop_column("jobs", "equity_bonus")

    if "show_salary" in existing:
        op.drop_column("jobs", "show_salary")
    if "pay_frequency" in existing:
        op.drop_column("jobs", "pay_frequency")
    if "currency" in existing:
        op.drop_column("jobs", "currency")
    if "salary_max" in existing:
        op.drop_column("jobs", "salary_max")
    if "salary_min" in existing:
        op.drop_column("jobs", "salary_min")

    if "openings" in existing:
        op.drop_column("jobs", "openings")
    if "office_location" in existing:
        op.drop_column("jobs", "office_location")
    if "work_location_type" in existing:
        op.drop_column("jobs", "work_location_type")
    if "job_type" in existing:
        op.drop_column("jobs", "job_type")
    if "department" in existing:
        op.drop_column("jobs", "department")
