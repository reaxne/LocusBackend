CREATE TABLE ai_requests (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'preparing',
    cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    metrics JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX ai_requests_owner_time ON ai_requests(user_id, started_at);
-- No public endpoint exposes these rows. Opt-in, development-only payload capture.
CREATE TABLE ai_debug_payloads (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES ai_requests(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
