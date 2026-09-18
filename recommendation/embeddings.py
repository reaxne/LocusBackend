"""Deterministic, collision-free matching of student interests to programs."""

from dataclasses import dataclass

from recommendation.models import Program, StudentProfile
from recommendation.normalization import concepts


FIELD_WEIGHTS = {
    "name": 1.0,
    "interests": 0.9,
    "tags": 0.8,
    "career_paths": 0.7,
    "important_courses": 0.6,
    "description": 0.4,
}


@dataclass(frozen=True)
class InterestMatch:
    score: float | None
    matched_interests: tuple[str, ...] = ()


def _field_concepts(values: list[str] | tuple[str, ...] | str | None) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {concept for value in values for concept in concepts(value)}


def program_keywords(program: Program) -> dict[str, float]:
    """Map each catalog keyword to its strongest explicit source field."""
    fields = {
        "name": program.name,
        "interests": program.interests,
        "tags": program.tags,
        "career_paths": program.career_paths,
        "important_courses": program.important_courses,
        "description": program.description,
    }
    weights: dict[str, float] = {}
    for field, value in fields.items():
        for keyword in _field_concepts(value):
            weights[keyword] = max(weights.get(keyword, 0), FIELD_WEIGHTS[field])
    return weights


class InterestMatcher:
    """Exact normalized keyword matching; no vectors, hashes, or fuzzy guesses."""

    name = "deterministic_keyword_match_v2"

    def match(self, student: StudentProfile, program: Program) -> InterestMatch:
        if not student.interest:
            return InterestMatch(None)
        keywords = program_keywords(program)
        if not keywords:
            return InterestMatch(None)
        matched: list[str] = []
        scores: list[float] = []
        for interest in student.interest:
            interest_keywords = concepts(interest)
            score = max((keywords.get(keyword, 0) for keyword in interest_keywords), default=0)
            scores.append(score)
            if score:
                matched.append(interest)
        return InterestMatch(sum(scores) / len(scores), tuple(matched))

    def matches(self, student: StudentProfile, programs: list[Program]) -> dict[str, InterestMatch]:
        return {program.id: self.match(student, program) for program in programs}

    def scores(self, student: StudentProfile, programs: list[Program]) -> dict[str, float | None]:
        """Compatibility helper for callers that require only numeric scores."""
        return {program_id: match.score for program_id, match in self.matches(student, programs).items()}
