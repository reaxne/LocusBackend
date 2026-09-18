from datetime import date as Date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from recommendation.normalization import normalize


def camel_case(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(word.title() for word in rest)


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, alias_generator=camel_case, allow_inf_nan=False
    )


Exam = Literal["SAT", "IELTS", "NUET", "UNT", "AET"]
Funding = Literal["self_funded", "state_grant", "university_scholarship", "other"]
Score = Annotated[float, Field(ge=0, le=1)]
Nonnegative = Annotated[float, Field(ge=0)]


class StudentProfile(Model):
    """Validated recommendation view of the existing, untyped survey answers."""

    grade: int = Field(ge=1, le=12)
    entry_year: int = Field(ge=2000, le=2200)
    interest: list[str] = Field(default_factory=list)
    city: list[str] = Field(default_factory=list)
    must_stay: bool = False
    budget: int | None = Field(default=None, ge=0)
    funding: list[Funding] = Field(default_factory=list)
    category: str | None = None
    academic_strengths: list[str] = Field(default_factory=list)
    SAT: Nonnegative | None = None
    IELTS: Nonnegative | None = None
    NUET: Nonnegative | None = None
    UNT: Nonnegative | None = None
    AET: Nonnegative | None = None
    extracurricular_interests: list[str] = Field(default_factory=list)

    @field_validator("interest", "city", "academic_strengths", "extracurricular_interests",
                     "funding", mode="before")
    @classmethod
    def normalize_lists(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("Expected text or a list of text values")
        result = {}
        for item in value:
            clean = " ".join(item.split())
            if clean:
                result.setdefault(normalize(clean), clean)
        return list(result.values())

    @field_validator("SAT", "IELTS", "NUET", "UNT", "AET", "budget", "category", mode="before")
    @classmethod
    def empty_to_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("grade", "entry_year", "budget", "SAT", "IELTS", "NUET", "UNT", "AET",
                     mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value):
        if isinstance(value, bool):
            raise ValueError("A boolean is not a numeric answer")
        return value

    @model_validator(mode="after")
    def require_hard_constraint_city(self):
        if self.must_stay and not self.city:
            raise ValueError("city is required when mustStay is true")
        return self


class Source(Model):
    url: HttpUrl
    verified_at: Date
    academic_year: int = Field(ge=2000, le=2200)

    def applies(self, year: int, as_of: Date) -> bool:
        return self.academic_year == year and self.verified_at <= as_of


class ExamRequirement(Model):
    minimum: Nonnegative | None = None
    source: Source | None = None


class Deadline(Model):
    task_type: str
    date: Date | None = None
    route_id: str | None = None
    source: Source | None = None

    def verified(self, year: int, as_of: Date) -> bool:
        return self.date is not None and self.source is not None and self.source.applies(year, as_of)


class AdmissionRoute(Model):
    id: str = Field(min_length=1)
    requirements: dict[Exam, ExamRequirement] = Field(default_factory=dict)
    # Curators must confirm that no unmodeled requirements are omitted.
    requirements_complete: bool = False
    source: Source | None = None
    allows_retakes: bool = False


class Scholarship(Model):
    name: str
    source: Source | None = None


class Program(Model):
    id: str = Field(min_length=1)
    university_id: str = Field(min_length=1)
    university_name: str = Field(min_length=1)
    name: str = Field(min_length=1)
    city: str | None = None
    degree: str | None = None
    program_code: str | None = None
    program_group: str | None = None
    description: str | None = None
    interests: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    career_paths: list[str] = Field(default_factory=list)
    important_courses: list[str] = Field(default_factory=list)
    recommended_academic_strengths: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    tuition_per_year: int | None = Field(default=None, ge=0)
    state_grant_available: bool | None = None
    scholarships: list[Scholarship] | None = None
    dormitory_available: bool | None = None
    extracurricular: list[str] | None = None
    admission_year: int | None = Field(default=None, ge=2000, le=2200)
    admission_routes: list[AdmissionRoute] = Field(default_factory=list)
    deadlines: list[Deadline] = Field(default_factory=list)
    sources: dict[str, Source] = Field(default_factory=dict)
    active: bool = True
    is_demo: bool = False

    @model_validator(mode="after")
    def unique_routes(self):
        ids = [route.id for route in self.admission_routes]
        if len(ids) != len(set(ids)):
            raise ValueError("Admission route ids must be unique within a program")
        if any(item.route_id is not None and item.route_id not in ids for item in self.deadlines):
            raise ValueError("Deadline refers to an unknown route")
        return self


EligibilityStatus = Literal["eligible_now", "potentially_eligible", "ineligible", "unknown"]


class RequirementResult(Model):
    exam: Exam
    student_value: float | None
    required_value: float | None
    status: Literal["passed", "missing", "failed", "unknown"]
    source: Source | None = None


class RouteResult(Model):
    route: str
    eligible: bool
    status: EligibilityStatus
    requirements: list[RequirementResult]
    exam_score: Score | None = None
    warnings: list[str] = Field(default_factory=list)


class EligibilityResult(Model):
    eligible: bool
    status: EligibilityStatus
    available_routes: list[RouteResult]
    best_admission_route: str | None
    missing_requirements: list[RequirementResult] = Field(default_factory=list)
    failed_requirements: list[RequirementResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FinancialResult(Model):
    score: Score | None
    within_budget: bool | None
    difference: int | None
    tuition_per_year: int | None
    funding_options: list[str]
    funding_dependent: bool
    warnings: list[str]


class RoadmapTask(Model):
    id: str
    title: str
    description: str
    status: Literal["todo", "completed"] = "todo"
    priority: Literal["high", "medium", "low"] = "medium"
    deadline: Date | None = None
    deadline_status: Literal["verified", "unknown"] = "unknown"
    reason: str
    source: Source | None = None
    target: float | None = None
    blocking: bool = False
    depends_on: list[str] = Field(default_factory=list)
    how: list[str] = Field(default_factory=list)
    timing: str = "В ближайшую неделю · рекомендация"
    completion_criteria: str = "Результат шага записан и проверен."


class RequirementsSummary(Model):
    completed: list[RequirementResult] = Field(default_factory=list)
    missing: list[RequirementResult] = Field(default_factory=list)
    failed: list[RequirementResult] = Field(default_factory=list)
    unknown: list[RequirementResult] = Field(default_factory=list)


class Recommendation(Model):
    university_id: str
    university: str
    program_id: str
    program: str
    is_demo: bool
    match_score: Score
    eligibility: EligibilityResult
    scores: dict[str, Score | None]
    weights: dict[str, Score]
    financial: FinancialResult
    why_recommended: list[str]
    warnings: list[str]
    requirements: RequirementsSummary
    missing_requirements: list[RequirementResult]
    roadmap: list[RoadmapTask]
    next_action: RoadmapTask | None
    sources: dict[str, Source]
    city: str | None = None
    interests: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    program_group: str | None = None


class StudentSummary(Model):
    target_entry_year: int
    main_interests: list[str]
    academic_strengths: list[str]
    constraints: list[str]
    category: str | None


class ExcludedProgram(Model):
    program_id: str
    reasons: list[str]
    eligibility: EligibilityResult | None = None


class RecommendationResponse(Model):
    student_summary: StudentSummary
    recommendations: list[Recommendation]
    warnings: list[str]
    evaluated_at: Date
    matching_method: str
    excluded_programs: list[ExcludedProgram] = Field(default_factory=list)
