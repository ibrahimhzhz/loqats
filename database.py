from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

# If DATABASE_URL is not set, default to a local SQLite file
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ats.db")

# SQLite-only engine args
engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

# 1. Create the Engine
engine = create_engine(DATABASE_URL, **engine_kwargs)

# 2. Create the SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 3. Create the Base class
Base = declarative_base()

# 4. Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 5. Safe schema migrations (SQLite-compatible, idempotent)
def run_migrations():
    """
    ADD new columns to existing tables without dropping data.
    Each ALTER TABLE is wrapped in try/except — SQLite raises an error if
    the column already exists, which we silently ignore.
    """
    from sqlalchemy import text

    dialect = engine.dialect.name
    datetime_type = "TIMESTAMP" if dialect == "postgresql" else "DATETIME"
    binary_type = "BYTEA" if dialect == "postgresql" else "BLOB"

    new_columns = [
        # jobs table
        "ALTER TABLE jobs ADD COLUMN tracking_id TEXT",
        "ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'completed'",
        "ALTER TABLE jobs ADD COLUMN results JSON",
        "ALTER TABLE jobs ADD COLUMN total_resumes INTEGER",
        "ALTER TABLE jobs ADD COLUMN processed_resumes INTEGER DEFAULT 0",
        # SQLite rejects DEFAULT CURRENT_TIMESTAMP in ALTER TABLE;
        # add as nullable — SQLAlchemy server_default handles new inserts.
        f"ALTER TABLE jobs ADD COLUMN created_at {datetime_type}",
        # Public Careers Portal analytics
        "ALTER TABLE jobs ADD COLUMN views INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN application_count INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN form_config JSON",
        # Level 1: Grounded JD Requirements
        "ALTER TABLE jobs ADD COLUMN jd_requirements JSON",
        # Redesigned job posting fields
        "ALTER TABLE jobs ADD COLUMN department TEXT",
        "ALTER TABLE jobs ADD COLUMN job_type TEXT",
        "ALTER TABLE jobs ADD COLUMN work_location_type TEXT",
        "ALTER TABLE jobs ADD COLUMN office_location TEXT",
        "ALTER TABLE jobs ADD COLUMN openings INTEGER DEFAULT 1",
        "ALTER TABLE jobs ADD COLUMN salary_min INTEGER",
        "ALTER TABLE jobs ADD COLUMN salary_max INTEGER",
        "ALTER TABLE jobs ADD COLUMN currency TEXT DEFAULT 'USD'",
        "ALTER TABLE jobs ADD COLUMN pay_frequency TEXT DEFAULT 'Annual'",
        "ALTER TABLE jobs ADD COLUMN show_salary BOOLEAN DEFAULT 1",
        "ALTER TABLE jobs ADD COLUMN equity_bonus TEXT",
        "ALTER TABLE jobs ADD COLUMN nice_to_have_skills JSON",
        "ALTER TABLE jobs ADD COLUMN benefits JSON",
        "ALTER TABLE jobs ADD COLUMN require_cover_letter BOOLEAN DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN require_portfolio BOOLEAN DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN require_linkedin BOOLEAN DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN custom_questions JSON",
        "ALTER TABLE jobs ADD COLUMN hiring_manager TEXT",
        "ALTER TABLE jobs ADD COLUMN target_hire_date DATE",
        "ALTER TABLE jobs ADD COLUMN application_deadline DATE",
        "ALTER TABLE jobs ADD COLUMN visibility TEXT DEFAULT 'Public'",
        # applicants table
        "ALTER TABLE applicants ADD COLUMN breakdown JSON",
        f"ALTER TABLE applicants ADD COLUMN created_at {datetime_type}",
        # Public Careers Portal fields
        "ALTER TABLE applicants ADD COLUMN cover_letter TEXT",
        "ALTER TABLE applicants ADD COLUMN linkedin_url TEXT",
        "ALTER TABLE applicants ADD COLUMN portfolio_url TEXT",
        "ALTER TABLE applicants ADD COLUMN custom_answers JSON",
        # Resume PDF storage for downloads
        f"ALTER TABLE applicants ADD COLUMN resume_pdf {binary_type}",
        # Hiring Pipeline
        "ALTER TABLE applicants ADD COLUMN pipeline_stage TEXT DEFAULT 'Applied'",
        f"ALTER TABLE applicants ADD COLUMN stage_updated_at {datetime_type}",
        # Enriched extraction storage
        "ALTER TABLE applicants ADD COLUMN skills_detailed JSON",
        "ALTER TABLE applicants ADD COLUMN extracted_jobs JSON",
        "ALTER TABLE applicants ADD COLUMN extracted_education JSON",
        # Candidate signal fields
        "ALTER TABLE applicants ADD COLUMN has_measurable_impact BOOLEAN",
        "ALTER TABLE applicants ADD COLUMN has_contact_info BOOLEAN",
        "ALTER TABLE applicants ADD COLUMN has_clear_job_titles BOOLEAN",
        "ALTER TABLE applicants ADD COLUMN employment_gaps BOOLEAN",
        "ALTER TABLE applicants ADD COLUMN average_tenure_years REAL",
        "ALTER TABLE applicants ADD COLUMN extractable_text BOOLEAN DEFAULT 1",
        # Cover letter and custom answer analysis from extraction
        "ALTER TABLE applicants ADD COLUMN cover_letter_analysis JSON",
        "ALTER TABLE applicants ADD COLUMN custom_answer_analysis JSON",
        # Score breakdown storage (populated by scoring engine)
        "ALTER TABLE applicants ADD COLUMN score_breakdown JSON",
        "ALTER TABLE applicants ADD COLUMN knockout_flags JSON",
        "ALTER TABLE applicants ADD COLUMN candidate_signals JSON",
        # Off-database resume storage: object-store key instead of a BLOB
        "ALTER TABLE applicants ADD COLUMN resume_file_key TEXT",
    ]

    def _is_already_exists_error(error_message: str) -> bool:
        normalized = (error_message or "").lower()
        return (
            "duplicate column name" in normalized
            or "already exists" in normalized
            or "duplicate_table" in normalized
            or "duplicate_object" in normalized
        )

    def _column_exists(conn, table_name: str, column_name: str) -> bool:
        if dialect == "postgresql":
            query = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
                  AND column_name = :column_name
                LIMIT 1
                """
            )
            result = conn.execute(
                query,
                {"table_name": table_name, "column_name": column_name},
            ).first()
            return result is not None

        rows = conn.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
        return any(row.get("name") == column_name for row in rows)

    def _add_column_if_missing(conn, table_name: str, column_name: str, column_type_sql: str) -> None:
        if _column_exists(conn, table_name, column_name):
            return

        stmt = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type_sql}"
        try:
            conn.execute(text(stmt))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if _is_already_exists_error(str(e)):
                return
            print(f"⚠️ Migration statement failed: {stmt} | error={e}")

    with engine.connect() as conn:
        for stmt in new_columns:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if _is_already_exists_error(str(e)):
                    continue
                print(f"⚠️ Migration statement failed: {stmt} | error={e}")

        # Semantic skill embedding storage columns.
        _add_column_if_missing(conn, "applicants", "skill_embeddings", "JSON")
        _add_column_if_missing(conn, "jobs", "required_skill_embeddings", "JSON")

        dedup_statements = [
            """
            DELETE FROM applicants
            WHERE email IS NOT NULL
              AND TRIM(email) != ''
              AND id NOT IN (
                SELECT MAX(id)
                FROM applicants
                WHERE email IS NOT NULL AND TRIM(email) != ''
                GROUP BY job_id, email
              )
            """,
        ]

        for stmt in dedup_statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"⚠️ Dedup migration failed: {e}")

        # Backfill existing applicants to "Applied" pipeline stage
        backfill_statements = [
            "UPDATE applicants SET pipeline_stage = 'Applied' WHERE pipeline_stage IS NULL",
        ]
        for stmt in backfill_statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"⚠️ Backfill migration failed: {e}")

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_jobs_company_id_created_at ON jobs (company_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_applicants_company_job ON applicants (company_id, job_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_applicants_job_email ON applicants (job_id, email)",
            "CREATE INDEX IF NOT EXISTS ix_stage_log_applicant ON applicant_stage_log (applicant_id)",
        ]

        for stmt in index_statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"⚠️ Index migration failed: {stmt} | error={e}")