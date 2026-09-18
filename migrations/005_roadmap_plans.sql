CREATE TABLE roadmap_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    timezone TEXT NOT NULL DEFAULT 'Asia/Almaty',
    available_hours_per_week DOUBLE PRECISION,
    achievements JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (available_hours_per_week IS NULL OR available_hours_per_week BETWEEN 0.5 AND 40)
);

CREATE TABLE roadmap_plans (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    request_id UUID NOT NULL REFERENCES ai_requests(id) ON DELETE RESTRICT,
    input_hash TEXT NOT NULL,
    plan_json JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE roadmap_step_progress (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('todo', 'in_progress', 'completed')),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, step_id),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);

CREATE INDEX roadmap_plans_request ON roadmap_plans(request_id);
