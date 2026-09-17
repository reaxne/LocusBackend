"""Validated frontend state, stored alongside the existing survey contract."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

QUESTIONS = ['grade', 'entryYear', 'interest', 'academicPerformance', 'studyLanguage', 'city',
             'mustStay', 'budget', 'funding', 'category', 'academicStrengths', 'SAT', 'IELTS',
             'NUET', 'UNT', 'AET', 'extracurricularInterests', 'constraints']
LIMITS = {'SAT': (400, 1600, 10), 'IELTS': (0, 9, .5), 'NUET': (0, 240, 1), 'UNT': (0, 140, 1)}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Exam(StrictModel):
    status: Literal['unknown', 'planned', 'completed', 'not-planned']
    score: float | None


class Goal(StrictModel):
    targetScore: float | None
    targetDate: str | None


def check_score(exam, score):
    if score is not None:
        low, high, step = LIMITS[exam]
        if not low <= score <= high or score % step:
            raise ValueError(f'Invalid {exam} score')


class ApplicantProfile(StrictModel):
    grade: Literal[9, 10, 11, 12]
    entryYear: int = Field(ge=2026, le=2100)
    interest: Literal['Software engineering', 'AI & data', 'Cybersecurity']
    city: Literal['Any city', 'Astana', 'Almaty', 'Karaganda', 'Shymkent', 'Other city']
    mustStay: bool
    budget: float | None = Field(ge=0, le=100_000_000)
    funding: Literal['self', 'grant', 'either']
    category: Literal['domestic', 'international', 'unknown']
    country: Literal['Kazakhstan']
    level: Literal['bachelor']
    studyLanguage: Literal['any', 'ru', 'kk', 'en']
    academicPerformance: Literal['unknown', 'excellent', 'good', 'needs-support']
    constraints: str = Field(max_length=1000)
    academicStrengths: list[Literal['Mathematics', 'Programming', 'English', 'Writing', 'Research', 'Teamwork']] = Field(max_length=6)
    extracurricularInterests: list[Literal['Hackathons', 'Volunteering', 'Research', 'Personal Projects', 'Competitions', 'Leadership']] = Field(max_length=6)
    exams: dict[str, Exam]
    examGoals: dict[str, Goal]
    ieltsSectionScores: dict[str, float | None]

    @model_validator(mode='after')
    def valid_exams(self):
        if set(self.exams) != {*LIMITS, 'AET'} or set(self.examGoals) != set(LIMITS):
            raise ValueError('Invalid exam keys')
        if set(self.ieltsSectionScores) != {'Listening', 'Reading', 'Writing', 'Speaking'}:
            raise ValueError('Invalid IELTS sections')
        for exam, result in self.exams.items():
            if result.score is not None:
                if exam == 'AET' or result.status != 'completed':
                    raise ValueError('Only completed numeric exams accept scores')
                check_score(exam, result.score)
        for exam, goal in self.examGoals.items():
            check_score(exam, goal.targetScore)
            if goal.targetDate is not None and date.fromisoformat(goal.targetDate).isoformat() != goal.targetDate:
                raise ValueError('Use YYYY-MM-DD')
        for score in self.ieltsSectionScores.values():
            check_score('IELTS', score)
        return self


def to_survey(profile: ApplicantProfile) -> dict:
    values = profile.model_dump()
    keys = ['grade', 'entryYear', 'interest', 'city', 'mustStay', 'budget', 'funding',
            'category', 'academicStrengths', 'extracurricularInterests']
    survey = {key: values[key] for key in keys}
    survey['city'] = [] if profile.city == 'Any city' else profile.city
    survey['funding'] = {'self': ['self_funded'], 'grant': ['state_grant', 'university_scholarship'],
                         'either': ['self_funded', 'state_grant', 'university_scholarship']}[profile.funding]
    survey.update({exam: result.score for exam, result in profile.exams.items()})
    return survey


class FrontendState(StrictModel):
    profile: ApplicantProfile | None
    draft: ApplicantProfile
    draftStep: int = Field(ge=0, lt=len(QUESTIONS))
    answeredQuestions: list[str] = Field(max_length=len(QUESTIONS))
    revision: int = Field(ge=0)

    @model_validator(mode='after')
    def valid_progress(self):
        answers = set(self.answeredQuestions)
        if len(answers) != len(self.answeredQuestions) or not answers <= set(QUESTIONS):
            raise ValueError('Invalid answered question IDs')
        if self.profile is not None and answers != set(QUESTIONS):
            raise ValueError('Answer or skip all questions before submitting')
        return self
