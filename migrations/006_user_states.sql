CREATE TABLE user_states (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state_json JSONB NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
