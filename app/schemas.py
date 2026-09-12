from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class HistoricalInputs(Contract):
    sleep_hours: Annotated[float, Field(ge=0, le=24)]
    academic_load: Annotated[int, Field(strict=True, ge=0, le=10)]
    screen_hours: Annotated[float, Field(ge=0, le=24)]
    extracurricular_load: Annotated[int, Field(strict=True, ge=0, le=10)]
    deadline_pressure: Annotated[int, Field(strict=True, ge=0, le=10)] | None = None
    recovery: Annotated[int, Field(strict=True, ge=0, le=10)] | None = None
    reported_strain: Annotated[int, Field(strict=True, ge=0, le=10)] | None

    @field_validator('sleep_hours', 'screen_hours', mode='before')
    @classmethod
    def quarter_hours(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('Hours must be a JSON number')
        numeric = Decimal(str(value))
        if not numeric.is_finite() or numeric % Decimal('0.25') != 0:
            raise ValueError('Hours must be finite quarter-hour increments')
        return value


class RoutineInputs(HistoricalInputs):
    sleep_hours: Annotated[float, Field(ge=0, le=12)]
    screen_hours: Annotated[float, Field(ge=0, le=16)]
    deadline_pressure: Annotated[int, Field(strict=True, ge=0, le=10)]
    recovery: Annotated[int, Field(strict=True, ge=0, le=10)]


class CheckInCreate(RoutineInputs):
    observation_date: date
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator('observation_date', mode='before')
    @classmethod
    def calendar_date_only(cls, value):
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError('Use a calendar date in YYYY-MM-DD format')
        return value

    @field_validator('timezone')
    @classmethod
    def iana_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Use a valid IANA timezone')
        return value


class CheckInEdit(RoutineInputs):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]


class Antecedent(Contract):
    variable: str
    term: str
    degree: float = Field(ge=0, le=1)


class RuleActivation(Contract):
    id: str
    weight: Literal[1.0]
    antecedents: list[Antecedent]
    consequent: str
    firing_strength: float = Field(ge=0, le=1)


class Aggregate(Contract):
    universe: list[float]
    membership: list[float]


class Component(Contract):
    id: str
    label: str
    raw_centroid: float = Field(ge=0, le=100)
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=100)
    explanation: str
    rules: list[RuleActivation]
    aggregate: Aggregate


class Assessment(Contract):
    raw_score: float | None = Field(default=None, ge=0, le=100)
    components: list[Component] = Field(default_factory=list)
    status: Literal['ok', 'unsupported', 'error']
    raw_centroid: float | None
    score: float | None = Field(ge=0, le=100)
    category: str | None
    model_version: str
    spec_hash: str
    memberships: dict[str, dict[str, float]]
    rules: list[RuleActivation]
    aggregate: Aggregate
    reason: str | None
    limitations: list[str]


class CheckInResponse(Contract):
    id: UUID
    observation_date: date
    timezone: str
    revision: int
    recorded_at: datetime
    retrospective: bool
    questionnaire_version: str
    inputs: HistoricalInputs
    assessment: Assessment
    history_version: int


class CheckInPage(Contract):
    items: list[CheckInResponse]
    next_cursor: str | None
    history_version: int
