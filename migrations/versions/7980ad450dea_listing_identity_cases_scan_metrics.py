"""listing identity, cases, scan metrics

Revision ID: 7980ad450dea
Revises: 372243be407d
Create Date: 2026-08-22 22:39:34.040613

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7980ad450dea'
down_revision: Union[str, Sequence[str], None] = '372243be407d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('cases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('property_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'ACKNOWLEDGED', 'FILED', 'RESOLVED', 'DISMISSED', 'DISPUTED', name='casestatus'), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('match_signal', sa.String(length=100), nullable=True),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_alert_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('alert_count', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('change_log', sa.Text(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_case_status', 'cases', ['status'], unique=False)
    op.create_index('uq_case_listing_property', 'cases', ['listing_id', 'property_id'], unique=True)

    op.add_column('alerts', sa.Column('case_id', sa.UUID(), nullable=True))

    # server_default is required: these are NOT NULL columns being added to
    # tables that already contain rows.
    op.add_column('scan_logs', sa.Column('trigger', sa.String(length=20), nullable=False, server_default='manual'))
    op.add_column('scan_logs', sa.Column('listings_new', sa.Integer(), nullable=True))
    op.add_column('scan_logs', sa.Column('listings_updated', sa.Integer(), nullable=True))
    op.add_column('scan_logs', sa.Column('cases_opened', sa.Integer(), nullable=True))
    op.add_column('scan_logs', sa.Column('enrichment_rate', sa.Float(), nullable=True))

    op.add_column('scraped_listings', sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('scraped_listings', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('scraped_listings', sa.Column('times_seen', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('scraped_listings', sa.Column('delisted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('scraped_listings', sa.Column('content_fingerprint', sa.String(length=64), nullable=True))

    # Existing rows predate the concept of a sighting window; treat the scrape
    # time as both first and last sighting.
    op.execute(
        "UPDATE scraped_listings "
        "SET first_seen_at = COALESCE(scraped_at, created_at), "
        "    last_seen_at  = COALESCE(scraped_at, created_at) "
        "WHERE first_seen_at IS NULL"
    )

    # ── Collapse duplicate sightings before the unique index goes on ──────
    # Keep the earliest row per (source, external_id) so the primary key that
    # alerts already point at survives; then merge onto it, because the row we
    # keep is the *stalest* one and deleting the later rows would otherwise
    # throw away the most recent content and, worse, the most recent fraud
    # verdict. On the development database this is a no-op (measured: 191 rows,
    # 0 duplicates) but it must exist for any environment that accumulated
    # them, and the index below cannot be created without it.
    #
    # Ordering is by COALESCE(scraped_at, created_at) — the same expression the
    # backfill above used — with an id tiebreak, and every statement below uses
    # an identical window so "keep_id" means the same thing throughout.
    op.execute(
        """
        CREATE TEMP VIEW _dupe_ranked AS
        SELECT id, source, external_id,
               COALESCE(scraped_at, created_at) AS seen_at,
               FIRST_VALUE(id) OVER w_asc  AS keep_id,
               FIRST_VALUE(id) OVER w_desc AS newest_id,
               FIRST_VALUE(id) OVER w_verdict AS verdict_id,
               COUNT(*) OVER (PARTITION BY source, external_id) AS n
        FROM scraped_listings
        WHERE external_id IS NOT NULL
        WINDOW
            w_asc AS (PARTITION BY source, external_id
                      ORDER BY COALESCE(scraped_at, created_at) ASC, id ASC),
            w_desc AS (PARTITION BY source, external_id
                       ORDER BY COALESCE(scraped_at, created_at) DESC, id DESC),
            -- most recent row that actually carries a verdict; a later
            -- re-scrape that came back "unknown" must not erase a "fraud".
            w_verdict AS (PARTITION BY source, external_id
                          ORDER BY (fraud_reason IS NOT NULL) DESC,
                                   COALESCE(scraped_at, created_at) DESC, id DESC)
        """
    )
    op.execute(
        """
        WITH agg AS (
            SELECT keep_id,
                   MIN(seen_at)    AS oldest,
                   MAX(seen_at)    AS newest,
                   COUNT(*)        AS seen,
                   -- both are constant within a partition; array_agg because
                   -- PostgreSQL has no MIN() for uuid.
                   (array_agg(newest_id))[1]  AS newest_id,
                   (array_agg(verdict_id))[1] AS verdict_id
            FROM _dupe_ranked WHERE n > 1
            GROUP BY keep_id
        )
        UPDATE scraped_listings l
        SET first_seen_at        = agg.oldest,
            last_seen_at         = agg.newest,
            times_seen           = agg.seen,
            title                = nw.title,
            price                = nw.price,
            description          = nw.description,
            location             = nw.location,
            image_urls           = nw.image_urls,
            url                  = nw.url,
            street_address       = COALESCE(nw.street_address, l.street_address),
            latitude             = COALESCE(nw.latitude, l.latitude),
            longitude            = COALESCE(nw.longitude, l.longitude),
            enriched             = (COALESCE(l.enriched, false) OR COALESCE(nw.enriched, false)),
            fraud_status         = vd.fraud_status,
            fraud_confidence     = vd.fraud_confidence,
            fraud_reason         = vd.fraud_reason,
            matched_property_id  = COALESCE(vd.matched_property_id, l.matched_property_id),
            -- GREATEST ignores NULLs in PostgreSQL, so this is "whichever
            -- sighting we actually alerted on".
            alerted_at           = GREATEST(l.alerted_at, nw.alerted_at)
        FROM agg
        JOIN scraped_listings nw ON nw.id = agg.newest_id
        JOIN scraped_listings vd ON vd.id = agg.verdict_id
        WHERE l.id = agg.keep_id
        """
    )
    op.execute(
        """
        UPDATE alerts a
        SET listing_id = r.keep_id
        FROM _dupe_ranked r
        WHERE a.listing_id = r.id AND r.id <> r.keep_id
        """
    )
    op.execute(
        "DELETE FROM scraped_listings WHERE id IN "
        "(SELECT id FROM _dupe_ranked WHERE id <> keep_id)"
    )
    op.execute("DROP VIEW _dupe_ranked")

    op.create_index('ix_listing_last_seen', 'scraped_listings', ['last_seen_at'], unique=False)
    op.create_index('uq_listing_source_external', 'scraped_listings', ['source', 'external_id'], unique=True, postgresql_where=sa.text('external_id IS NOT NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('uq_listing_source_external', table_name='scraped_listings', postgresql_where=sa.text('external_id IS NOT NULL'))
    op.drop_index('ix_listing_last_seen', table_name='scraped_listings')
    op.drop_column('scraped_listings', 'content_fingerprint')
    op.drop_column('scraped_listings', 'delisted_at')
    op.drop_column('scraped_listings', 'times_seen')
    op.drop_column('scraped_listings', 'last_seen_at')
    op.drop_column('scraped_listings', 'first_seen_at')
    op.drop_column('scan_logs', 'enrichment_rate')
    op.drop_column('scan_logs', 'cases_opened')
    op.drop_column('scan_logs', 'listings_updated')
    op.drop_column('scan_logs', 'listings_new')
    op.drop_column('scan_logs', 'trigger')
    op.drop_column('alerts', 'case_id')
    op.drop_index('uq_case_listing_property', table_name='cases')
    op.drop_index('ix_case_status', table_name='cases')
    op.drop_table('cases')
    sa.Enum(name='casestatus').drop(op.get_bind(), checkfirst=True)
    # ### end Alembic commands ###
