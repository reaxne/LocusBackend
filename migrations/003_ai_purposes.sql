ALTER TABLE ai_results ADD COLUMN purpose TEXT NOT NULL DEFAULT 'roadmap';
ALTER TABLE ai_results DROP CONSTRAINT ai_results_pkey;
ALTER TABLE ai_results ADD PRIMARY KEY (user_id, purpose);
ALTER TABLE ai_results ADD CONSTRAINT ai_results_purpose_check
    CHECK (purpose IN ('roadmap', 'recommendations', 'profile'));
