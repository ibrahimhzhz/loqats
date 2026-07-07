"""add enriched extraction and scoring fields to applicants

Revision ID: 20260304_01
Revises: 20260302_02
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260304_01"
down_revision = "20260302_02"
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
    # Enriched extraction storage
    _add_column_if_missing("applicants", sa.Column("skills_detailed", sa.JSON(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("extracted_jobs", sa.JSON(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("extracted_education", sa.JSON(), nullable=True))

    # Candidate signal fields
    _add_column_if_missing("applicants", sa.Column("has_measurable_impact", sa.Boolean(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("has_contact_info", sa.Boolean(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("has_clear_job_titles", sa.Boolean(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("employment_gaps", sa.Boolean(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("average_tenure_years", sa.Float(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("extractable_text", sa.Boolean(), nullable=True, server_default=sa.true()))

    # Cover letter and custom answer analysis from extraction
    _add_column_if_missing("applicants", sa.Column("cover_letter_analysis", sa.JSON(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("custom_answer_analysis", sa.JSON(), nullable=True))

    # Score breakdown storage (populated by scoring engine)
    _add_column_if_missing("applicants", sa.Column("score_breakdown", sa.JSON(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("knockout_flags", sa.JSON(), nullable=True))
    _add_column_if_missing("applicants", sa.Column("candidate_signals", sa.JSON(), nullable=True))


def downgrade() -> None:
    existing = _get_columns("applicants")

    for col_name in [
        "candidate_signals",
        "knockout_flags",
        "score_breakdown",
        "custom_answer_analysis",
        "cover_letter_analysis",
        "extractable_text",
        "average_tenure_years",
        "employment_gaps",
        "has_clear_job_titles",
        "has_contact_info",
        "has_measurable_impact",
        "extracted_education",
        "extracted_jobs",
        "skills_detailed",
    ]:
        if col_name in existing:
            op.drop_column("applicants", col_name)
