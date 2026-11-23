"""Blueprint: PRISM's validated, editable plan for understanding and transforming a source.

Every item carries ``origin`` (rules | ai | user), ``confidence`` (0–1) and, where it is
actionable, ``status`` (proposed | approved | rejected). Nothing is executed until the user
approves it; numbers are never part of the AI's output — KPI values are computed by DuckDB.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Origin = Literal["rules", "ai", "user"]
Status = Literal["proposed", "approved", "rejected"]


class SemanticRole(StrEnum):
    identifier = "identifier"
    timestamp = "timestamp"
    date = "date"
    time_of_day = "time_of_day"
    duration = "duration"
    currency_amount = "currency_amount"
    quantity = "quantity"
    percentage = "percentage"
    phone = "phone"
    person = "person"          # agent / staff / customer names
    organization_unit = "organization_unit"  # branch, department, team
    location = "location"
    category = "category"      # type, channel, product, …
    status = "status"
    boolean_flag = "boolean_flag"
    free_text = "free_text"
    other = "other"


class Aggregation(StrEnum):
    count_rows = "count_rows"
    count_distinct = "count_distinct"
    sum = "sum"
    average = "average"
    minimum = "minimum"
    maximum = "maximum"
    share_of_rows = "share_of_rows"  # rows matching filter / all rows


class KpiFormat(StrEnum):
    number = "number"
    lak = "lak"
    duration = "duration"
    percent = "percent"


class ChartKind(StrEnum):
    kpi_card = "kpi_card"
    bar = "bar"
    line = "line"
    donut = "donut"
    table = "table"


class Domain(Strict):
    label: str = Field(max_length=120)
    confidence: Confidence
    rationale: str = Field(max_length=600)
    origin: Origin


class ColumnPlan(Strict):
    key: str
    original_label: str
    display_name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    semantic_role: SemanticRole
    confidence: Confidence
    rationale: str = Field(default="", max_length=400)
    origin: Origin
    status: Status = "proposed"


class Filter(Strict):
    column: str
    values: list[str] = Field(min_length=1, max_length=50)


class Kpi(Strict):
    id: str
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    aggregation: Aggregation
    column: str | None = None
    filter: Filter | None = None
    format: KpiFormat = KpiFormat.number
    confidence: Confidence
    rationale: str = Field(default="", max_length=400)
    origin: Origin
    status: Status = "proposed"
    # computed deterministically (never by AI); refreshed whenever the blueprint changes
    sql: str | None = None
    value: float | None = None
    computed_on_version: int | None = None


class ValueMapping(Strict):
    to: str = Field(min_length=1, max_length=200)
    variants: list[str] = Field(min_length=1, max_length=50)
    rows_affected: int | None = None


class OpMapValues(Strict):
    op: Literal["map_values"] = "map_values"
    column: str
    mappings: list[ValueMapping] = Field(min_length=1, max_length=200)


class OpRemoveDuplicates(Strict):
    op: Literal["remove_duplicate_rows"] = "remove_duplicate_rows"
    rows_affected: int | None = None


class OpExcludeColumn(Strict):
    op: Literal["exclude_column"] = "exclude_column"
    column: str


class OpCastColumn(Strict):
    op: Literal["cast_column"] = "cast_column"
    column: str
    to_type: Literal["text", "integer", "number", "date"]


OperationSpec = Annotated[OpMapValues | OpRemoveDuplicates | OpExcludeColumn | OpCastColumn, Field(discriminator="op")]


class Operation(Strict):
    id: str
    spec: OperationSpec
    title: str = Field(max_length=200)
    rationale: str = Field(default="", max_length=600)
    confidence: Confidence
    origin: Origin
    status: Status = "proposed"


class DatasetPlan(Strict):
    dataset_id: int
    version: int
    title: str = Field(max_length=200)
    summary: str = Field(default="", max_length=1000)
    columns: list[ColumnPlan]
    operations: list[Operation] = []
    kpis: list[Kpi] = []


class Relationship(Strict):
    id: str
    from_dataset: int
    from_column: str
    to_dataset: int
    to_column: str
    match_ratio: float  # share of distinct from-values found in to-column (computed)
    verified: bool
    rationale: str = Field(default="", max_length=400)
    confidence: Confidence
    origin: Origin
    status: Status = "proposed"


class Panel(Strict):
    id: str
    title: str = Field(max_length=120)
    chart: ChartKind
    dataset_id: int
    kpi_ids: list[str] = []
    dimension: str | None = None
    time_column: str | None = None
    origin: Origin
    status: Status = "proposed"


class WorkbookPlan(Strict):
    include_kpi_sheet: bool = True
    include_blueprint_sheet: bool = True
    data_sheet_names: dict[str, str] = {}  # dataset_id -> sheet title


class Assumption(Strict):
    id: str
    text: str = Field(max_length=600)
    confidence: Confidence
    origin: Origin
    status: Status = "proposed"


class GeneratorInfo(Strict):
    engine: Literal["rules", "ai"]
    model: str | None = None
    attempts: int = 0
    fallback_reason: str | None = None
    warnings: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None


class Blueprint(Strict):
    schema_version: Literal[1] = 1
    source_id: int
    domain: Domain
    datasets: list[DatasetPlan]
    relationships: list[Relationship] = []
    dashboard: list[Panel] = []
    workbook: WorkbookPlan = WorkbookPlan()
    assumptions: list[Assumption] = []
    generator: GeneratorInfo

    @field_validator("datasets")
    @classmethod
    def _unique_display_names(cls, v: list[DatasetPlan]) -> list[DatasetPlan]:
        for ds in v:
            names = [c.display_name.casefold() for c in ds.columns if c.status != "rejected"]
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                raise ValueError(f"Duplicate column display names in dataset {ds.dataset_id}: {sorted(dupes)}")
        return v

    def dataset(self, dataset_id: int) -> DatasetPlan:
        for d in self.datasets:
            if d.dataset_id == dataset_id:
                return d
        raise KeyError(dataset_id)
