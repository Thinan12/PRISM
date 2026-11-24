"""Declarative analytical query (ADR-6).

Nobody — user, UI or AI — submits SQL. A query is this structure; ``engine.py`` validates it
against the dataset schema and compiles it to parameterized DuckDB SQL, which is what runs and
what is shown for inspection.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.blueprint.schema import Aggregation


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeGrain(StrEnum):
    day = "day"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"


class Op(StrEnum):
    eq = "eq"
    neq = "neq"
    in_ = "in"
    not_in = "not_in"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    between = "between"
    contains = "contains"
    is_null = "is_null"
    not_null = "not_null"


Scalar = str | float | int | bool


class Condition(_S):
    column: str = Field(max_length=200)
    op: Op
    values: list[Scalar] = Field(default_factory=list, max_length=100)


class Measure(_S):
    aggregation: Aggregation
    column: str | None = Field(default=None, max_length=200)
    # filtered aggregate, e.g. SUM(cost) FILTER (WHERE type = 'complaint'); required for share_of_rows
    filter: Condition | None = None
    label: str | None = Field(default=None, max_length=120)


class Dimension(_S):
    column: str = Field(max_length=200)
    grain: TimeGrain | None = None  # date / datetime columns only


class Sort(_S):
    by: Literal["measure", "dimension"] = "measure"
    index: int = Field(default=0, ge=0, le=3)
    direction: Literal["asc", "desc"] = "desc"


class Analysis(StrEnum):
    none = "none"
    change = "change"    # period-over-period change on the time dimension (LAG window)
    share = "share"      # share of total of the first measure
    anomaly = "anomaly"  # robust z-score (median / MAD) of the first measure per period


class QuerySpec(_S):
    dataset_id: int
    measures: list[Measure] = Field(min_length=1, max_length=4)
    dimensions: list[Dimension] = Field(default_factory=list, max_length=2)
    filters: list[Condition] = Field(default_factory=list, max_length=12)
    sort: Sort | None = None
    limit: int = Field(default=200, ge=1, le=1000)
    analysis: Analysis = Analysis.none
