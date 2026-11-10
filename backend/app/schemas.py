"""Request bodies shared by the routers and services."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Requirement(BaseModel):
    skill: str
    level: int = Field(2, ge=1, le=4)  # minimum level on the 0-4 scale (1 Learned, 2 Applied, 3 Experienced, 4 Mastered); 0 means no skill
    weight: float = Field(1.0, gt=0)  # importance
    min_people: int = Field(1, ge=1)  # how many people must reach `level`
    domain_tags: list[str] = Field(default_factory=list)


class RequirementsRequest(BaseModel):
    prompt: str


class ProjectInput(BaseModel):
    prompt: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    headcount: int | None = Field(None, ge=1, le=200)
    roles: list[str] = []
    # optional seniority wanted for a preferred role: {"Developer": "Senior"}; only Senior, Mid and Junior are accepted
    role_seniority: dict[str, str] = Field(default_factory=dict)
    top_n: int = Field(10, ge=1, le=100)
    # study plans suggested for people who could learn a skill that nobody has (used by staffing)
    learning_weeks: int = Field(4, ge=1, le=52)
    learning_hours_per_week: int = Field(5, ge=1, le=60)

    @field_validator("role_seniority")
    @classmethod
    def _seniority_values(cls, v: dict[str, str]) -> dict[str, str]:
        names = {"senior": "Senior", "mid": "Mid", "junior": "Junior"}
        return {str(k).strip(): names[str(x).strip().lower()] for k, x in v.items() if str(x).strip().lower() in names and str(k).strip()}


class LearningRequest(BaseModel):
    employee_id: int
    requirements: list[Requirement] = Field(default_factory=list)
    project: str = ""
    weeks: int = Field(4, ge=1, le=52)
    hours_per_week: int = Field(5, ge=1, le=60)


class LearningCoachMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class LearningCoachRequest(BaseModel):
    employee_id: int
    requirements: list[Requirement] = Field(min_length=1, max_length=20)
    weeks: int = Field(4, ge=1, le=52)
    hours_per_week: int = Field(5, ge=1, le=60)
    plan_markdown: str = Field(min_length=1, max_length=20000)
    messages: list[LearningCoachMessage] = Field(min_length=1, max_length=12)


class LearningPlanCheckRequest(BaseModel):
    """A learning plan after a person edited it: the app re-checks its numbers against the employee's ratings."""
    employee_id: int
    requirements: list[Requirement] = Field(min_length=1, max_length=20)
    weeks: int = Field(4, ge=1, le=52)
    hours_per_week: int = Field(5, ge=1, le=60)
    plan_markdown: str = Field(min_length=1, max_length=20000)


class ReparseRequest(BaseModel):
    sheet: str | None = None
    mapping: dict[str, str] | None = None
    layout: Literal["wide", "long"] | None = None


class CVSkill(BaseModel):
    raw_skill: str = Field(..., min_length=1, max_length=80)
    rating: int | None = Field(None, ge=0, le=4)  # 0 None, 1 Learned, 2 Applied, 3 Experienced, 4 Mastered
    evidence: str = Field("", max_length=400)


class CVSubmitRequest(BaseModel):
    """The CV record after the uploader reviewed and corrected it. The server re-checks every quote against the stored CV."""
    name: str = Field(..., min_length=1, max_length=120)
    employee_no: str | None = Field(None, max_length=40)
    roles: list[str] = Field(default_factory=list, max_length=5)
    background: str = Field("", max_length=1500)
    skills: list[CVSkill] = Field(..., min_length=1, max_length=60)


class CommitRequest(BaseModel):
    mode: Literal["merge", "replace"] = "merge"


class MappingUpdate(BaseModel):
    raw_skill: str
    canonical: str
    category: str | None = None


class TaxonomyItem(BaseModel):
    name: str
    category: str = "Other"
    aliases: list[str] = []


class RequestedDecision(BaseModel):
    """Decision on a skill that a project asked for but the taxonomy does not have."""
    name: str
    action: Literal["approve", "dismiss", "reopen"]
    canonical: str | None = None  # approve as an existing skill (alias) instead of a new one
    category: str | None = None
