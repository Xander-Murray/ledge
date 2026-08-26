"""enforce sealed journals

Revision ID: b68eea2e0615
Revises: f8ef7beaa235
Create Date: 2026-08-26 13:55:03.514120

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b68eea2e0615"
down_revision: str | Sequence[str] | None = "f8ef7beaa235"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION validate_journal_sealing()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            posting_count bigint;
            posting_total numeric;
        BEGIN
            SELECT count(*), COALESCE(sum(amount_cents), 0)
            INTO posting_count, posting_total
            FROM postings
            WHERE journal_entry_id = NEW.id;

            IF posting_count < 2 THEN
                RAISE EXCEPTION
                    'journal entry % must have at least two postings before sealing',
                    NEW.id
                    USING ERRCODE = '23514';
            END IF;

            IF posting_total <> 0 THEN
                RAISE EXCEPTION
                    'journal entry % must balance to zero before sealing; total is %',
                    NEW.id,
                    posting_total
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_journal_entry()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' AND NEW.sealed_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'journal entry % cannot be inserted already sealed',
                    NEW.id
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.sealed_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'sealed journal entry % cannot be changed',
                    OLD.id
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_sealed_journal_postings()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            journal_ids uuid[];
            journal_record record;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                journal_ids := ARRAY[NEW.journal_entry_id];
            ELSIF TG_OP = 'DELETE' THEN
                journal_ids := ARRAY[OLD.journal_entry_id];
            ELSE
                journal_ids := ARRAY[
                    OLD.journal_entry_id,
                    NEW.journal_entry_id
                ];
            END IF;

            FOR journal_record IN
                SELECT id, sealed_at
                FROM journal_entries
                WHERE id = ANY(journal_ids)
                ORDER BY id
                FOR UPDATE
            LOOP
                IF journal_record.sealed_at IS NOT NULL THEN
                    RAISE EXCEPTION
                        'postings for sealed journal entry % cannot be changed',
                        journal_record.id
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER a_protect_journal_entry
        BEFORE INSERT OR UPDATE OR DELETE ON journal_entries
        FOR EACH ROW EXECUTE FUNCTION protect_journal_entry()
        """
    )
    op.execute(
        """
        CREATE TRIGGER b_validate_journal_sealing
        BEFORE UPDATE OF sealed_at ON journal_entries
        FOR EACH ROW
        WHEN (OLD.sealed_at IS NULL AND NEW.sealed_at IS NOT NULL)
        EXECUTE FUNCTION validate_journal_sealing()
        """
    )
    op.execute(
        """
        CREATE TRIGGER protect_sealed_journal_postings
        BEFORE INSERT OR UPDATE OR DELETE ON postings
        FOR EACH ROW EXECUTE FUNCTION protect_sealed_journal_postings()
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER protect_sealed_journal_postings ON postings")
    op.execute("DROP TRIGGER b_validate_journal_sealing ON journal_entries")
    op.execute("DROP TRIGGER a_protect_journal_entry ON journal_entries")
    op.execute("DROP FUNCTION protect_sealed_journal_postings()")
    op.execute("DROP FUNCTION protect_journal_entry()")
    op.execute("DROP FUNCTION validate_journal_sealing()")
