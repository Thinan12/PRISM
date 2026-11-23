# Database schema

Managed by Alembic (`backend/alembic`). The database holds metadata only; dataset contents live in Parquet files under `PRISM_DATA_DIR`.

| Table | Key columns | Notes |
|---|---|---|
| `users` | email (unique), password_hash (scrypt), is_superadmin, token_version | bump `token_version` to revoke sessions |
| `workspaces` | slug (unique), timezone | tenant boundary |
| `memberships` | user_id + workspace_id (unique), role | admin / editor / viewer |
| `sources` | workspace_id, kind, filename, sha256, storage_path, status, error, summary (JSON) | immutable original file |
| `datasets` | workspace_id, source_id, name, sheet, table_index, current_version | one per detected table |
| `dataset_versions` | dataset_id + version (unique), row_count, columns / profile / lineage (JSON), clean_path, raw_path, content_hash | immutable |
| `artifacts` | workspace_id, source_id, kind, filename, storage_path, sha256, meta | generated workbooks |
| `blueprints` | workspace_id, source_id + number (unique), status (draft / applied / superseded), engine (rules / ai), content (JSON), requirements, applied_at / applied_by, result | editable only while draft |
| `jobs` | type, status, payload / result (JSON), attempts, progress, timestamps | queue (`SKIP LOCKED` on PostgreSQL) |
| `audit_events` | at, workspace_id, actor_id, action, target, details, ip | append-only |

JSON columns are `JSONB` on PostgreSQL and `JSON` on SQLite.

Storage layout: `ws_<id>/sources/<sha256>.<kind>`, `ws_<id>/datasets/<dataset>/v<n>/{clean,raw}.parquet`, `ws_<id>/artifacts/<hash>_<name>.xlsx`.
