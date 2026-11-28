"""Forward-only, versioned, idempotent migrations under a Postgres DDL lock.

Existing legacy additive migrations run first. Never drop business tables.
"""
from sqlalchemy import text

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_message_source ON chat_messages (source_key) WHERE source_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_queue_fifo ON queue_entries (created_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_chats_status_activity ON chats (status, last_activity_at)",
    "CREATE INDEX IF NOT EXISTS ix_chats_ended ON chats (ended_at)",
    "CREATE INDEX IF NOT EXISTS ix_bans_status_expiry ON bans (status, expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_users_status_seen ON users (status, last_seen_at)",
    "CREATE INDEX IF NOT EXISTS ix_broadcast_status_schedule ON broadcasts (status, scheduled_for)",
    "CREATE INDEX IF NOT EXISTS ix_updates_created ON processed_updates (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_recent_ended ON recent_pairs (ended_at)",
]


async def migrate(engine):
    async with engine.begin() as conn:
        pg = engine.dialect.name == "postgresql"
        if pg:
            await conn.execute(text("SELECT pg_advisory_xact_lock(71389203)"))
        await conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(64) PRIMARY KEY)"))
        applied = set((await conn.execute(text("SELECT version FROM schema_migrations"))).scalars())
        if "20261128_hardening_v1" in applied:
            return
        for statement in INDEXES:
            await conn.execute(text(statement))
        if pg:
            # NOT VALID tolerates legacy rows; every NEW wallet write is checked.
            await conn.execute(text("""DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_user_nonnegative_balance') THEN
                    ALTER TABLE users ADD CONSTRAINT ck_user_nonnegative_balance CHECK (coin_balance >= 0) NOT VALID;
                END IF; END $$"""))
            # Separate a/b unique indexes don't prevent A in one chat and B in another.
            await conn.execute(text("""CREATE OR REPLACE FUNCTION guard_active_chat() RETURNS trigger AS $$
                BEGIN
                    PERFORM pg_advisory_xact_lock(71389, LEAST(NEW.user_a_id, NEW.user_b_id));
                    PERFORM pg_advisory_xact_lock(71389, GREATEST(NEW.user_a_id, NEW.user_b_id));
                    IF NEW.user_a_id = NEW.user_b_id OR EXISTS (
                        SELECT 1 FROM chats WHERE status='active' AND id <> COALESCE(NEW.id, -1)
                        AND (user_a_id IN (NEW.user_a_id, NEW.user_b_id) OR user_b_id IN (NEW.user_a_id, NEW.user_b_id))
                    ) THEN RAISE EXCEPTION 'user already has an active chat' USING ERRCODE='23505'; END IF;
                    RETURN NEW;
                END; $$ LANGUAGE plpgsql"""))
            await conn.execute(text("DROP TRIGGER IF EXISTS trg_active_chat ON chats"))
            await conn.execute(text("""CREATE TRIGGER trg_active_chat BEFORE INSERT OR UPDATE OF status,user_a_id,user_b_id ON chats
                FOR EACH ROW WHEN (NEW.status='active') EXECUTE FUNCTION guard_active_chat()"""))
        await conn.execute(text("INSERT INTO schema_migrations(version) VALUES ('20261128_hardening_v1')"))
