from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoringConfig:
    weights: dict[str, float] = field(default_factory=lambda: {
        "interest": 0.35, "academic": 0.20, "financial": 0.20,
        "city": 0.10, "exam": 0.10, "extracurricular": 0.05,
    })
    constrained_financial_weight: float = 0.30
    outside_city_score: float = 0.35
    funding_dependent_score: float = 0.70
    exam_baseline: float = 0.70
    exam_full_score_margin: float = 0.25
    minimum_preparation_days: int = 90
    embedding_cache_size: int = 4096

    def __post_init__(self):
        if set(self.weights) != {"interest", "academic", "financial", "city", "exam", "extracurricular"}:
            raise ValueError("Configure all six scoring components")
        values = [*self.weights.values(), self.constrained_financial_weight,
                  self.outside_city_score, self.funding_dependent_score, self.exam_baseline]
        if any(not 0 <= value <= 1 for value in values) or sum(self.weights.values()) <= 0:
            raise ValueError("Weights and scores must be between zero and one")
        if self.exam_full_score_margin <= 0 or self.minimum_preparation_days < 0:
            raise ValueError("Invalid exam planning configuration")
        if self.embedding_cache_size < 1:
            raise ValueError("Embedding cache must have positive capacity")


DEFAULT_CONFIG = ScoringConfig()
