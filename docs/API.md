# API

Interactive OpenAPI documentation: `/api/docs` (spec: `/api/openapi.json`). All endpoints are under `/api/v1` and use `Authorization: Bearer <token>`. Errors have the shape `{"error": {"code", "message", "details?"}}`.

| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/auth/login` | — | `{email, password}` → `{token, user}` (rate-limited) |
| GET | `/auth/me` | any | current user, workspaces and roles |
| POST | `/auth/logout-all` | any | revoke all of the user's tokens |
| POST | `/workspaces/{ws}/sources` | editor | multipart `file` upload → source; ingestion job runs and the workbook is generated |
| POST | `/workspaces/{ws}/sources/paste` | editor | `{text, name}` pasted table |
| GET | `/workspaces/{ws}/sources`, `/sources/{id}` | viewer | sources with status, datasets, artifacts |
| POST | `/workspaces/{ws}/sources/{id}/workbook` | editor | regenerate the workbook (job) |
| GET | `/workspaces/{ws}/jobs/{id}` | viewer | job status and result |
| GET | `/workspaces/{ws}/datasets` | viewer | datasets with current-version stats |
| GET | `/workspaces/{ws}/datasets/{id}?version=` | viewer | columns, profile, lineage |
| GET | `/workspaces/{ws}/datasets/{id}/versions` | viewer | version history |
| GET | `/workspaces/{ws}/datasets/{id}/rows?offset&limit&layer=clean\|raw` | viewer | paged rows (limit ≤ 1000) |
| GET | `/workspaces/{ws}/artifacts/{id}/download` | viewer | download a workbook (audited) |
| GET | `/workspaces/{ws}/audit` | admin | audit events |
| GET | `/workspaces/{ws}/ai-status` | viewer | `{provider, model, configured, reachable, detail}`; for Ollama also whether the server answers and has the model |
| POST | `/workspaces/{ws}/sources/{id}/blueprints` | editor | `{requirements?}` → job that generates a Blueprint (AI with rule-based fallback) |
| GET | `/workspaces/{ws}/sources/{id}/blueprint` | viewer | latest non-superseded Blueprint, or `null` |
| GET | `/workspaces/{ws}/sources/{id}/blueprints` | viewer | Blueprint history |
| GET | `/workspaces/{ws}/blueprints/{id}` | viewer | one Blueprint |
| PATCH | `/workspaces/{ws}/blueprints/{id}` | editor | `{edits: [{action, target, dataset_id?, id?, changes?}]}`; actions `approve`, `reject`, `remove`, `update`, `add` (KPI), `approve_all`; drafts only |
| POST | `/workspaces/{ws}/blueprints/{id}/apply` | editor | job: approved operations → new dataset versions + regenerated workbook (409 if not a draft or the data changed) |

Objects belonging to other workspaces always return 404, so their existence is not revealed.
