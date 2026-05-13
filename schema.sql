-- =============================================================================
-- Nistula Unified Messaging Platform — PostgreSQL Schema
-- Part 2 of the technical assessment
-- =============================================================================

-- -----------------------------------------------------------------------------
-- EXTENSIONS
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";   -- UUID generation
CREATE EXTENSION IF NOT EXISTS "citext";       -- Case-insensitive text (email matching)
CREATE TYPE channel_source AS ENUM (
    'whatsapp', 'booking_com', 'airbnb', 'instagram', 'direct'
);

CREATE TYPE query_type AS ENUM (
    'pre_sales_availability',
    'pre_sales_pricing',
    'post_sales_checkin',
    'special_request',
    'complaint',
    'general_enquiry'
);

CREATE TYPE message_direction AS ENUM ('inbound', 'outbound');

-- Tracks the lifecycle of an outbound message's draft
CREATE TYPE draft_status AS ENUM (
    'ai_drafted',      -- Claude produced the draft, not yet reviewed
    'agent_edited',    -- A human edited the AI draft before sending
    'agent_written',   -- Human wrote from scratch (no AI involvement)
    'auto_sent'        -- AI draft was sent without human review
);

CREATE TYPE action_type AS ENUM ('auto_send', 'agent_review', 'escalate');

CREATE TYPE reservation_status AS ENUM (
    'enquiry', 'tentative', 'confirmed', 'checked_in', 'checked_out', 'cancelled'
);


CREATE TABLE properties (
    property_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_code       TEXT NOT NULL UNIQUE,           -- e.g. 'villa-b1'
    name                TEXT NOT NULL,
    location            TEXT,
    bedrooms            SMALLINT,
    max_guests          SMALLINT,
    base_rate_inr       NUMERIC(10, 2),
    extra_guest_rate    NUMERIC(10, 2),
    check_in_time       TIME,
    check_out_time      TIME,
    wifi_password       TEXT,
    caretaker_hours     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =============================================================================
-- TABLE: guests
-- One record per guest, across all channels.
-- Design decision: a single canonical guest record is identified by email
-- (using citext for case-insensitive uniqueness). Channel-specific
-- identifiers (whatsapp_id, airbnb_guest_id, etc.) are nullable columns
-- rather than a separate identity table — simpler at this scale, easy to
-- normalise later. A partial unique index per channel prevents duplicates
-- within each channel.
-- =============================================================================

CREATE TABLE guests (
    guest_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    full_name           TEXT NOT NULL,
    email               CITEXT,                         -- canonical identifier
    phone               TEXT,

    -- Channel-specific external identifiers (nullable)
    whatsapp_id         TEXT,
    booking_com_id      TEXT,
    airbnb_guest_id     TEXT,
    instagram_user_id   TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Soft-uniqueness: one record per channel user id
    CONSTRAINT uq_guests_email          UNIQUE (email),
    CONSTRAINT uq_guests_whatsapp       UNIQUE (whatsapp_id),
    CONSTRAINT uq_guests_booking_com    UNIQUE (booking_com_id),
    CONSTRAINT uq_guests_airbnb         UNIQUE (airbnb_guest_id),
    CONSTRAINT uq_guests_instagram      UNIQUE (instagram_user_id)
);

-- Index for name-based lookup (fuzzy search at application layer)
CREATE INDEX idx_guests_full_name ON guests (full_name);


-- =============================================================================
-- TABLE: reservations
-- Links a guest to a property stay. A guest can have many reservations;
-- a reservation belongs to one guest and one property.
-- =============================================================================

CREATE TABLE reservations (
    reservation_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    booking_ref         TEXT NOT NULL UNIQUE,           -- e.g. NIS-2024-0891
    guest_id            UUID NOT NULL REFERENCES guests (guest_id) ON DELETE RESTRICT,
    property_id         UUID NOT NULL REFERENCES properties (property_id) ON DELETE RESTRICT,
    check_in_date       DATE,
    check_out_date      DATE,
    num_adults          SMALLINT DEFAULT 2,
    num_children        SMALLINT DEFAULT 0,
    status              reservation_status NOT NULL DEFAULT 'enquiry',
    total_rate_inr      NUMERIC(12, 2),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_reservation_dates CHECK (check_out_date > check_in_date)
);

CREATE INDEX idx_reservations_guest_id    ON reservations (guest_id);
CREATE INDEX idx_reservations_property_id ON reservations (property_id);
CREATE INDEX idx_reservations_dates       ON reservations (check_in_date, check_out_date);


-- =============================================================================
-- TABLE: conversations
-- Groups messages into logical threads. One conversation per booking_ref
-- per channel (or per guest if no booking exists yet).
-- A conversation is linked to a guest and optionally to a reservation.
-- =============================================================================

CREATE TABLE conversations (
    conversation_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    guest_id            UUID NOT NULL REFERENCES guests (guest_id) ON DELETE RESTRICT,
    reservation_id      UUID REFERENCES reservations (reservation_id) ON DELETE SET NULL,
    source              channel_source NOT NULL,
    subject             TEXT,                           -- optional thread label
    is_open             BOOLEAN NOT NULL DEFAULT TRUE,
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conversations_guest_id       ON conversations (guest_id);
CREATE INDEX idx_conversations_reservation_id ON conversations (reservation_id);
CREATE INDEX idx_conversations_source_open    ON conversations (source, is_open);


-- =============================================================================
-- TABLE: messages
-- Every message, inbound and outbound, across all channels in one table.
-- Inbound rows capture the raw guest text and the AI classification.
-- Outbound rows capture the drafted reply, the draft lifecycle, and the
-- confidence score that drove the action decision.
--
-- Design decision: single-table for all messages vs separate inbound/outbound
-- tables. Single table simplifies conversation threading and timeline queries.
-- Nullable columns (ai_*, draft_status, confidence_score) are only populated
-- for the relevant direction, which is a minor normalisation trade-off
-- acceptable at this scale. See README for fuller discussion.
-- =============================================================================

CREATE TABLE messages (
    message_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id     UUID NOT NULL REFERENCES conversations (conversation_id) ON DELETE CASCADE,
    guest_id            UUID NOT NULL REFERENCES guests (guest_id) ON DELETE RESTRICT,
    reservation_id      UUID REFERENCES reservations (reservation_id) ON DELETE SET NULL,

    -- Channel metadata
    source              channel_source NOT NULL,
    direction           message_direction NOT NULL,
    external_message_id TEXT,                           -- ID from WhatsApp/Booking.com/etc.

    -- Core content
    message_text        TEXT NOT NULL,
    sent_at             TIMESTAMPTZ NOT NULL,           -- when guest sent or we sent
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- AI classification (inbound only; NULL for outbound)
    query_type          query_type,
    ai_confidence_score NUMERIC(4, 3)                   -- 0.000 – 1.000
        CONSTRAINT chk_confidence CHECK (ai_confidence_score BETWEEN 0 AND 1),

    -- AI draft lifecycle (outbound only; NULL for inbound)
    draft_status        draft_status,
    ai_action_taken     action_type,
    agent_id            UUID,                           -- FK to agents table (future)
    agent_edited_at     TIMESTAMPTZ,
    sent_by_agent_at    TIMESTAMPTZ,

    -- Audit
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Core query patterns
CREATE INDEX idx_messages_conversation_id ON messages (conversation_id, sent_at DESC);
CREATE INDEX idx_messages_guest_id        ON messages (guest_id, sent_at DESC);
CREATE INDEX idx_messages_reservation_id  ON messages (reservation_id);
CREATE INDEX idx_messages_query_type      ON messages (query_type) WHERE direction = 'inbound';
CREATE INDEX idx_messages_ai_action       ON messages (ai_action_taken) WHERE direction = 'outbound';


-- =============================================================================
-- TABLE: ai_draft_log
-- Immutable audit trail of every Claude API call.
-- Kept separate from messages so the messages table stays lean and
-- we can store the full prompt/response payload without bloating
-- the hot query path.
-- =============================================================================

CREATE TABLE ai_draft_log (
    log_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id          UUID NOT NULL REFERENCES messages (message_id) ON DELETE CASCADE,
    model               TEXT NOT NULL DEFAULT 'claude-sonnet-4-20250514',
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    raw_prompt          TEXT,                           -- full system + user prompt sent
    raw_response        TEXT,                           -- full JSON response from Claude
    claude_confidence   NUMERIC(4, 3),                  -- Claude's self-reported score
    final_confidence    NUMERIC(4, 3),                  -- after blending with local heuristic
    latency_ms          INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ai_draft_log_message_id ON ai_draft_log (message_id);


-- =============================================================================
-- TRIGGERS: updated_at auto-maintenance
-- =============================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_guests_updated_at
    BEFORE UPDATE ON guests
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reservations_updated_at
    BEFORE UPDATE ON reservations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_messages_updated_at
    BEFORE UPDATE ON messages
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
-- SEED: example property (Villa B1)
-- =============================================================================

INSERT INTO properties (
    property_code, name, location, bedrooms, max_guests,
    base_rate_inr, extra_guest_rate, check_in_time, check_out_time,
    wifi_password, caretaker_hours
) VALUES (
    'villa-b1', 'Villa B1', 'Assagao, North Goa', 3, 6,
    18000.00, 2000.00, '14:00', '11:00',
    'Nistula@2024', '08:00–22:00'
);
