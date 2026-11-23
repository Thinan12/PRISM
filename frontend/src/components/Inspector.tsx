import type { ColumnMeta, ColumnProfile, Dataset, Source } from "../api/types";
import { TYPE_META, fmtBytes, fmtDate, fmtDuration, fmtInt, fmtLAK, fmtNumber, fmtTimestamp } from "../lib/format";
import type { Selection } from "./SchemaMap";

function statFmt(col: ColumnMeta, v: unknown): string {
  if (v == null) return "—";
  if (col.logical_type === "duration") return fmtDuration(Number(v));
  if (col.logical_type === "date" || col.logical_type === "datetime") return fmtDate(String(v), col.logical_type === "datetime");
  if (col.unit === "LAK") return fmtLAK(Number(v));
  return fmtNumber(Number(v));
}

function ColumnInspector({ ds, col, p }: { ds: Dataset; col: ColumnMeta; p: ColumnProfile }) {
  const t = TYPE_META[col.logical_type];
  const total = ds.profile.row_count || 1;
  const issues = ds.profile.issues.filter((i) => i.column === col.key);
  const numeric = ["integer", "number", "duration"].includes(col.logical_type);
  const dated = col.logical_type === "date" || col.logical_type === "datetime";
  const maxTop = Math.max(1, ...(p.top_values ?? []).map((x) => x.count));
  return (
    <>
      <div className="insp-head">
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <span className="pill" style={{ color: t.color }}><span className="mono">{t.glyph}</span>{t.label}</span>
          {col.unit && <span className="pill">{col.unit}</span>}
          {col.semantic_hint && <span className="pill">{col.semantic_hint.replace("_", " ")}</span>}
        </div>
        <div className="insp-title">{col.label}</div>
        <div className="faint" style={{ fontSize: 12 }}>
          Source column {col.source_column}
          {col.original_label && col.original_label !== col.label ? ` · “${col.original_label}”` : ""} · key <span className="mono">{col.key}</span>
        </div>
      </div>
      <div className="insp-sec">
        <h4>Profile</h4>
        <dl className="kv">
          <dt>Filled</dt><dd>{fmtInt(p.non_null)} / {fmtInt(ds.profile.row_count)}</dd>
          <dt>Missing</dt><dd style={{ color: p.missing ? "var(--ember)" : undefined }}>{fmtInt(p.missing)} ({p.missing_pct}%)</dd>
          <dt>Unreadable</dt><dd style={{ color: p.parse_failures ? "var(--ember)" : undefined }}>{fmtInt(p.parse_failures)}</dd>
          <dt>Distinct</dt><dd>{fmtInt(p.distinct)}</dd>
          {numeric && (
            <>
              <dt>Total</dt><dd>{statFmt(col, p.sum)}</dd>
              <dt>Average</dt><dd>{statFmt(col, p.mean)}</dd>
              <dt>Median</dt><dd>{statFmt(col, p.median)}</dd>
              <dt>Min – Max</dt><dd>{statFmt(col, p.min)} – {statFmt(col, p.max)}</dd>
              {!!p.outliers && <><dt>Outliers</dt><dd>{fmtInt(p.outliers)}</dd></>}
            </>
          )}
          {dated && (
            <>
              <dt>Earliest</dt><dd>{statFmt(col, p.min)}</dd>
              <dt>Latest</dt><dd>{statFmt(col, p.max)}</dd>
              <dt>Span</dt><dd>{fmtInt(p.span_days)} days</dd>
            </>
          )}
        </dl>
        <div className="meter" style={{ marginTop: 12 }} aria-label={`${Math.round((p.non_null / total) * 100)}% filled`}>
          <span style={{ width: `${(p.non_null / total) * 100}%`, background: "var(--aurora)" }} />
        </div>
      </div>
      {dated && p.by_month && Object.keys(p.by_month).length > 0 && (
        <div className="insp-sec">
          <h4>Records by month</h4>
          <MonthBars data={p.by_month} />
        </div>
      )}
      {p.top_values && p.top_values.length > 0 && (
        <div className="insp-sec">
          <h4>Most frequent</h4>
          <div className="bar-list">
            {p.top_values.slice(0, 10).map((tv) => (
              <div className="bar-row" key={tv.value}>
                <span className="fill" style={{ width: `${(tv.count / maxTop) * 100}%` }} />
                <span>{col.logical_type === "duration" ? fmtDuration(Number(tv.value)) : tv.value}</span>
                <span className="mono">{fmtInt(tv.count)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {(issues.length > 0 || col.notes.length > 0) && (
        <div className="insp-sec">
          <h4>Findings</h4>
          {issues.map((i, k) => (
            <div key={k} className={`note ${i.severity !== "info" ? "ember" : ""}`}>
              {i.message}
              {i.examples && i.examples.length > 0 && (
                <div className="mono" style={{ marginTop: 6 }}>
                  {i.examples.slice(0, 5).map((e) => (
                    <div key={e.row}>row {e.row}: “{e.value}”</div>
                  ))}
                </div>
              )}
            </div>
          ))}
          {col.notes.map((n, k) => <div key={`n${k}`} className="note">{n}</div>)}
        </div>
      )}
    </>
  );
}

function MonthBars({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 80 }} role="img" aria-label="Records per month">
      {entries.map(([k, v]) => (
        <div key={k} title={`${k}: ${v}`} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
          <div style={{ width: "100%", height: `${(v / max) * 60}px`, background: "var(--aurora)", opacity: 0.75, borderRadius: 2 }} />
          {entries.length <= 12 && <span className="faint" style={{ fontSize: 10 }}>{k.slice(5)}</span>}
        </div>
      ))}
    </div>
  );
}

function DatasetInspector({ ds }: { ds: Dataset }) {
  const p = ds.profile;
  return (
    <>
      <div className="insp-head">
        <span className="pill">Table</span>
        <div className="insp-title">{ds.name}</div>
        <div className="faint" style={{ fontSize: 12 }}>Version {ds.version.version} · {fmtTimestamp(ds.version.created_at)}</div>
      </div>
      <div className="insp-sec">
        <h4>Shape</h4>
        <dl className="kv">
          <dt>Records</dt><dd>{fmtInt(p.row_count)}</dd>
          <dt>Columns</dt><dd>{p.column_count}</dd>
          <dt>Completeness</dt><dd>{p.completeness_pct}%</dd>
          <dt>Duplicate rows</dt><dd style={{ color: p.duplicate_rows ? "var(--ember)" : undefined }}>{fmtInt(p.duplicate_rows)}</dd>
          <dt>Quality score</dt><dd>{p.quality_score}</dd>
        </dl>
      </div>
      <div className="insp-sec">
        <h4>Lineage</h4>
        <dl className="kv">
          <dt>Sheet</dt><dd>{ds.lineage.sheet}</dd>
          <dt>Header row(s)</dt><dd>{ds.lineage.header_rows.join(", ") || "none"}</dd>
          <dt>Data rows</dt><dd>{ds.lineage.first_row}–{ds.lineage.last_row}</dd>
          <dt>Source columns</dt><dd className="mono">{ds.lineage.source_columns.join(" ")}</dd>
          <dt>Content hash</dt><dd className="mono" title={ds.version.content_hash}>{ds.version.content_hash.slice(0, 12)}…</dd>
        </dl>
      </div>
      {ds.lineage.notes.length > 0 && (
        <div className="insp-sec">
          <h4>Structure notes</h4>
          {ds.lineage.notes.map((n, i) => <div key={i} className="note">{n}</div>)}
        </div>
      )}
    </>
  );
}

function SourceInspector({ source }: { source: Source }) {
  return (
    <>
      <div className="insp-head">
        <span className="kind-badge">{source.kind}</span>
        <div className="insp-title">{source.filename}</div>
        <div className="faint" style={{ fontSize: 12 }}>Uploaded {fmtTimestamp(source.created_at)}</div>
      </div>
      <div className="insp-sec">
        <h4>File</h4>
        <dl className="kv">
          <dt>Size</dt><dd>{fmtBytes(source.size_bytes)}</dd>
          <dt>SHA-256</dt><dd className="mono" title={source.sha256}>{source.sha256.slice(0, 16)}…</dd>
          <dt>Tables</dt><dd>{source.datasets.length}</dd>
          <dt>Status</dt><dd>{source.status}</dd>
        </dl>
      </div>
      {(source.summary?.notes?.length ?? 0) > 0 && (
        <div className="insp-sec">
          <h4>Notes</h4>
          {source.summary!.notes!.map((n, i) => <div key={i} className="note">{n}</div>)}
        </div>
      )}
    </>
  );
}

export function Inspector({ selection, source, datasets }: { selection: Selection; source?: Source; datasets: Dataset[] }) {
  if (!selection || !source) {
    return (
      <div className="insp-sec faint" style={{ border: 0, paddingTop: 22 }}>
        Select the file, a table or a column on the Schema Map to inspect it.
      </div>
    );
  }
  if (selection.kind === "source") return <SourceInspector source={source} />;
  const ds = datasets.find((d) => d.id === selection.datasetId);
  if (!ds) return null;
  if (selection.kind === "dataset") return <DatasetInspector ds={ds} />;
  const col = ds.columns.find((c) => c.key === selection.key);
  const p = ds.profile.columns.find((c) => c.key === selection.key);
  if (!col || !p) return null;
  return <ColumnInspector ds={ds} col={col} p={p} />;
}
