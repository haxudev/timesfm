from datetime import date
from typing import Annotated, Literal

from pydantic import (
  AwareDatetime,
  BaseModel,
  ConfigDict,
  Field,
  FiniteFloat,
  field_validator,
  model_validator,
)

Horizon = Literal[1, 5, 20]
JobStatus = Literal[
  "pending", "running", "succeeded", "partial", "failed", "cancelled",
]


class Contract(BaseModel):
  model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PredictionRequest(Contract):
  ticker: str = Field(min_length=1, max_length=15)
  horizon: Horizon = 5

  @field_validator("horizon", mode="before")
  @classmethod
  def integer_horizon(cls, value):
    if type(value) is not int:
      raise ValueError("horizon must be an integer")
    return value


class Symbol(Contract):
  ticker: str
  name: str | None = None
  instrument_type: Literal["stock", "index"]


class SymbolsResponse(Contract):
  items: list[Symbol]


class ComponentState(Contract):
  status: Literal["ready", "not_ready", "unavailable", "not_supported"]
  reason: str | None = None
  artifact_id: str | None = None


class Components(Contract):
  timesfm: ComponentState
  lightgbm: ComponentState
  garch: ComponentState


class HorizonPrediction(Contract):
  horizon: Horizon
  target_date: date
  timesfm_return: FiniteFloat | None = None
  lightgbm_return: FiniteFloat | None = None
  up_probability: Annotated[FiniteFloat, Field(ge=0, le=1)] | None = None
  volatility: Annotated[FiniteFloat, Field(ge=0)] | None = None


class PathPoint(Contract):
  date: date
  cumulative_return: FiniteFloat


class HistoryPoint(Contract):
  date: date
  price: Annotated[FiniteFloat, Field(gt=0)]


class PredictionBundle(Symbol):
  schema_version: Literal[2] = 2
  bundle_id: str
  origin: date
  issued_at: AwareDatetime
  snapshot_id: str
  status: Literal["succeeded", "partial"]
  horizons: list[HorizonPrediction]
  path: list[PathPoint]
  history: list[HistoryPoint]
  components: Components
  warnings: list[str]

  @field_validator("horizons")
  @classmethod
  def all_horizons(cls, value):
    if [item.horizon for item in value] != [1, 5, 20]:
      raise ValueError("all horizons must be ordered 1,5,20")
    return value


class SubmittedJob(Contract):
  job_id: str
  status: JobStatus


class JobError(Contract):
  code: str
  message: str


class JobResponse(Contract):
  id: str
  status: JobStatus
  cancellation_requested: bool = False
  result: PredictionBundle | None
  error: JobError | None


TimesFMContext = Annotated[
  list[Annotated[FiniteFloat, Field(ge=-3.4e38, le=3.4e38, strict=True)]],
  Field(min_length=32, max_length=512),
]


class InternalTimesFMRequest(Contract):
  context: TimesFMContext
  horizon: int = Field(20, ge=1, le=20, strict=True)


class InternalTimesFMBatchRequest(Contract):
  contexts: list[TimesFMContext] = Field(min_length=1, max_length=8)
  horizon: int = Field(20, ge=1, le=20, strict=True)


class InternalTimesFMOutput(Contract):
  point: list[FiniteFloat] = Field(min_length=1, max_length=20)
  quantiles: list[Annotated[list[FiniteFloat], Field(min_length=9, max_length=9)]] = Field(
    min_length=1, max_length=20,
  )

  @model_validator(mode="after")
  def matching_horizons(self):
    if len(self.point) != len(self.quantiles):
      raise ValueError("point and quantiles horizons must match")
    return self


class InternalTimesFMResponse(InternalTimesFMOutput):
  artifact_id: str | None = None


class InternalTimesFMBatchResponse(Contract):
  outputs: list[InternalTimesFMOutput] = Field(min_length=1, max_length=8)
  artifact_id: str | None = None