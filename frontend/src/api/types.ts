export type Role = "admin" | "editor" | "viewer";

export interface WorkspaceRef {
  id: number;
  name: string;
  slug: string;
  role: Role;
}

export interface User {
  id: number;
  email: string;
  name: string;
  is_superadmin: boolean;
  workspaces: WorkspaceRef[];
}

export interface Job {
  id: number;
  type: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  finished_at: string | null;
}

export interface DatasetBrief {
  id: number;
  name: string;
  sheet: string | null;
  current_version: number;
  row_count: number | null;
  column_count: number | null;
  quality_score: number | null;
}

export interface ArtifactInfo {
  id: number;
  kind: string;
  filename: string;
  size_bytes: number;
  created_at: string;
}

export interface SheetSummary {
  name: string;
  rows: number;
  cols: number;
  merged_ranges: number;
  tables: number;
  hidden: boolean;
}

export interface Source {
  id: number;
  kind: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  status: "pending" | "processing" | "ready" | "failed";
  error: string | null;
  summary: { sheets?: SheetSummary[]; notes?: string[]; tables?: number } | null;
  created_at: string;
  datasets: DatasetBrief[];
  artifacts: ArtifactInfo[];
  job: Job | null;
}

export type LogicalType = "text" | "integer" | "number" | "boolean" | "date" | "datetime" | "duration" | "phone" | "empty";

export interface ColumnMeta {
  key: string;
  label: string;
  original_label: string | null;
  source_column: string;
  logical_type: LogicalType;
  unit: string | null;
  date_order: string | null;
  semantic_hint: string | null;
  has_lao: boolean;
  parse_failures: number;
  notes: string[];
}

export interface ColumnProfile {
  key: string;
  label: string;
  logical_type: LogicalType;
  unit: string | null;
  semantic_hint: string | null;
  non_null: number;
  missing: number;
  missing_pct: number;
  parse_failures: number;
  failure_examples: { row: number; value: string }[];
  distinct: number;
  unique_ratio: number;
  min?: number | string | null;
  max?: number | string | null;
  sum?: number | null;
  mean?: number | null;
  median?: number | null;
  std?: number | null;
  outliers?: number;
  span_days?: number;
  future?: number;
  by_month?: Record<string, number>;
  top_values?: { value: string; count: number }[];
  spelling_variants?: string[][];
  samples: unknown[];
  has_lao: boolean;
  display?: Record<string, string | null>;
}

export interface Issue {
  severity: "error" | "warning" | "info";
  code: string;
  column: string | null;
  count: number;
  message: string;
  examples?: { row: number; value: string }[];
  groups?: number[][];
}

export interface TotalCheck {
  source_row: number;
  column: string;
  label: string;
  stated: number;
  computed: number;
  difference: number;
  matches: boolean;
}

export interface Profile {
  generated_at: string;
  row_count: number;
  column_count: number;
  duplicate_rows: number;
  completeness_pct: number;
  quality_score: number;
  quality_penalties: Record<string, number>;
  total_checks: TotalCheck[];
  columns: ColumnProfile[];
  issues: Issue[];
}

export interface Lineage {
  sheet: string;
  origin: string;
  title: string | null;
  header_rows: number[];
  source_columns: string[];
  first_row: number | null;
  last_row: number | null;
  excluded_rows: { source_row: number; reason: string; values: Record<string, string | null> }[];
  notes: string[];
  merged_cells_filled: number;
}

export interface Dataset {
  id: number;
  name: string;
  sheet: string | null;
  source_id: number | null;
  source_filename: string | null;
  current_version: number;
  created_at: string;
  version: { id: number; version: number; row_count: number; content_hash: string; created_at: string; source_id: number | null };
  columns: ColumnMeta[];
  profile: Profile;
  lineage: Lineage;
}

export interface Rows {
  columns: string[];
  rows: unknown[][];
  total: number;
  offset: number;
  limit: number;
  layer: "clean" | "raw";
}

// ---------------- Blueprint (Phase 2) ----------------
export type Origin = "rules" | "ai" | "user";
export type ItemStatus = "proposed" | "approved" | "rejected";

export interface ColumnPlan {
  key: string;
  original_label: string;
  display_name: string;
  description: string;
  semantic_role: string;
  confidence: number;
  rationale: string;
  origin: Origin;
  status: ItemStatus;
}

export interface Kpi {
  id: string;
  name: string;
  description: string;
  aggregation: string;
  column: string | null;
  filter: { column: string; values: string[] } | null;
  format: "number" | "lak" | "duration" | "percent";
  confidence: number;
  rationale: string;
  origin: Origin;
  status: ItemStatus;
  sql: string | null;
  value: number | null;
  computed_on_version: number | null;
}

export interface OperationItem {
  id: string;
  spec:
    | { op: "map_values"; column: string; mappings: { to: string; variants: string[]; rows_affected: number | null }[] }
    | { op: "remove_duplicate_rows"; rows_affected: number | null }
    | { op: "exclude_column"; column: string }
    | { op: "cast_column"; column: string; to_type: string };
  title: string;
  rationale: string;
  confidence: number;
  origin: Origin;
  status: ItemStatus;
}

export interface DatasetPlan {
  dataset_id: number;
  version: number;
  title: string;
  summary: string;
  columns: ColumnPlan[];
  operations: OperationItem[];
  kpis: Kpi[];
}

export interface BlueprintContent {
  source_id: number;
  domain: { label: string; confidence: number; rationale: string; origin: Origin };
  datasets: DatasetPlan[];
  relationships: { id: string; from_dataset: number; from_column: string; to_dataset: number; to_column: string; match_ratio: number; verified: boolean; rationale: string; confidence: number; origin: Origin; status: ItemStatus }[];
  dashboard: { id: string; title: string; chart: string; dataset_id: number; kpi_ids: string[]; dimension: string | null; time_column: string | null; origin: Origin; status: ItemStatus }[];
  assumptions: { id: string; text: string; confidence: number; origin: Origin; status: ItemStatus }[];
  generator: { engine: "rules" | "ai"; model: string | null; attempts: number; fallback_reason: string | null; warnings: string[] };
}

export interface BlueprintRecord {
  id: number;
  source_id: number;
  number: number;
  status: "draft" | "applied" | "superseded";
  engine: "rules" | "ai";
  requirements: string | null;
  created_at: string;
  applied_at: string | null;
  result: { datasets: { dataset_id: number; version: number; row_count: number }[]; artifact_id: number; unreviewed_items_skipped: number } | null;
  content: BlueprintContent;
}

export type EditTarget = "column" | "operation" | "kpi" | "relationship" | "assumption" | "panel" | "blueprint";
export interface BlueprintEdit {
  action: "approve" | "reject" | "remove" | "update" | "add" | "approve_all";
  target: EditTarget;
  dataset_id?: number;
  id?: string;
  changes?: Record<string, unknown>;
}

// ---------------- Phase 3: analytics ----------------

export type Aggregation = "count_rows" | "count_distinct" | "sum" | "average" | "minimum" | "maximum" | "share_of_rows";
export type CondOp = "eq" | "neq" | "in" | "not_in" | "gt" | "gte" | "lt" | "lte" | "between" | "contains" | "is_null" | "not_null";
export type Scalar = string | number | boolean;
export interface Condition {
  column: string;
  op: CondOp;
  values: Scalar[];
}
export interface QuerySpec {
  dataset_id: number;
  measures: { aggregation: Aggregation; column?: string | null; filter?: Condition | null; label?: string | null }[];
  dimensions: { column: string; grain?: "day" | "week" | "month" | "quarter" | "year" | null }[];
  filters: Condition[];
  sort?: { by: "measure" | "dimension"; index: number; direction: "asc" | "desc" } | null;
  limit: number;
  analysis: "none" | "change" | "share" | "anomaly";
}
export type ValueFormat = "number" | "lak" | "duration" | "percent" | "date" | "text" | "boolean";
export interface ResultColumn {
  key: string;
  label: string;
  kind: "dimension" | "measure" | "change" | "change_pct" | "share" | "typical" | "z" | "anomaly";
  type: string;
  format: ValueFormat;
  grain: string | null;
  column: string | null;
}
export interface QueryResult {
  spec: QuerySpec;
  description: string;
  columns: ResultColumn[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  matched_rows: number;
  total_rows: number;
  sql: string;
  dataset_id: number;
  dataset_name: string;
  dataset_version: number;
  computed_at: string;
  elapsed_ms: number;
}
export type ChartKind = "kpi_card" | "bar" | "line" | "donut" | "table";
export interface DashPanel {
  id: string;
  title: string;
  chart: ChartKind;
  query: QuerySpec;
  width: number;
  note?: string | null;
}
export interface FilterDef {
  dataset_id: number;
  column: string;
  label: string;
}
export interface DashboardContent {
  panels: DashPanel[];
  filters: FilterDef[];
}
export interface Dashboard {
  id: number;
  source_id: number;
  name: string;
  origin: "blueprint" | "rules" | "user";
  revision: number;
  content: DashboardContent;
  created_at: string;
  updated_at: string | null;
  notes: string[];
}
export interface ActiveFilter {
  dataset_id: number;
  condition: Condition;
  source_panel?: string | null;
}
export interface PanelData {
  panel_id: string;
  result: QueryResult | null;
  summary: string | null;
  error: string | null;
}
export interface FilterOption {
  dataset_id: number;
  column: string;
  label: string;
  type: LogicalType;
  min?: string | number | null;
  max?: string | number | null;
  values?: { value: string; count: number }[];
}
export interface Question {
  id: number;
  dataset_id: number;
  dataset_version: number;
  question: string;
  status: "queued" | "answered" | "unanswerable" | "failed";
  engine: "ai" | "rules" | null;
  model: string | null;
  chart: ChartKind | null;
  answer: string | null;
  notes: string[] | null;
  fallback_reason: string | null;
  error: string | null;
  spec: QuerySpec | null;
  result: QueryResult | null;
  created_at: string;
  job?: Job | null;
}
export interface Insight {
  kind: "trend" | "ranking" | "share" | "anomaly";
  title: string;
  text: string;
  sql: string;
  spec: QuerySpec;
  dataset_version: number;
}
