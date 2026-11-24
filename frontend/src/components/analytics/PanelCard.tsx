import { useState } from "react";
import type { ChartKind, DashPanel, PanelData, QueryResult } from "../../api/types";
import { saveBlob } from "../../api/client";
import { fmtInt } from "../../lib/format";
import { Chart, ResultTable, canDraw, toCsv, type Pick_ } from "./Charts";

const CHART_NAMES: Record<ChartKind, string> = { kpi_card: "Number", bar: "Bars", line: "Line", donut: "Parts of whole", table: "Table" };

export function SqlBlock({ result }: { result: QueryResult }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="sql-block">
      <div className="sql-meta">
        <span>{result.dataset_name} · v{result.dataset_version}</span>
        <span>{fmtInt(result.matched_rows)} of {fmtInt(result.total_rows)} records</span>
        <span>{result.elapsed_ms} ms</span>
        <button
          className="linkish"
          onClick={() => {
            void navigator.clipboard?.writeText(result.sql).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? "Copied" : "Copy SQL"}
        </button>
      </div>
      <pre className="sql" aria-label="SQL that produced this result">{result.sql}</pre>
      <div className="faint sql-desc">{result.description}</div>
    </div>
  );
}

export function PanelCard({ panel, data, editing, colorOf, selected, onPick, onChange, onRemove, onMove, first, last }: {
  panel: DashPanel;
  data: PanelData | undefined;
  editing: boolean;
  colorOf: (k: string) => string;
  selected?: unknown;
  onPick?: (p: Pick_) => void;
  onChange: (p: DashPanel) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  first: boolean;
  last: boolean;
}) {
  const [view, setView] = useState<"chart" | "table" | "sql">("chart");
  const r = data?.result ?? null;
  const allowed: ChartKind[] = r ? (["kpi_card", "bar", "line", "donut", "table"] as ChartKind[]).filter((k) => k === "table" || canDraw(k, r)) : [panel.chart];
  const kpi = panel.chart === "kpi_card" && view === "chart";
  return (
    <section className={`dash-panel hull ${kpi ? "is-kpi" : ""} ${editing ? "editing" : ""}`} style={{ gridColumn: `span ${panel.width}` }} aria-label={panel.title}>
      <header className="dash-panel-head">
        {editing ? (
          <input className="input input-sm" value={panel.title} maxLength={120} aria-label="Panel title"
                 onChange={(e) => onChange({ ...panel, title: e.target.value || panel.title })} />
        ) : (
          !kpi && <h3 className="dash-panel-title">{panel.title}</h3>
        )}
        <div className="dash-panel-tools">
          {editing ? (
            <>
              <select className="input input-sm" value={panel.chart} aria-label="Chart type" onChange={(e) => onChange({ ...panel, chart: e.target.value as ChartKind })}>
                {allowed.map((k) => <option key={k} value={k}>{CHART_NAMES[k]}</option>)}
              </select>
              <select className="input input-sm" value={panel.width} aria-label="Width" onChange={(e) => onChange({ ...panel, width: Number(e.target.value) })}>
                {[3, 4, 6, 8, 12].map((w) => <option key={w} value={w}>{w === 12 ? "Full" : w === 6 ? "Half" : w === 3 ? "Quarter" : w === 4 ? "Third" : "⅔"}</option>)}
              </select>
              <button className="icon-btn" disabled={first} onClick={() => onMove(-1)} aria-label="Move earlier">←</button>
              <button className="icon-btn" disabled={last} onClick={() => onMove(1)} aria-label="Move later">→</button>
              <button className="icon-btn danger" onClick={onRemove} aria-label={`Remove ${panel.title}`}>✕</button>
            </>
          ) : (
            r && (
              <div className="seg" role="group" aria-label="View">
                {(["chart", "table", "sql"] as const).map((v) => (
                  <button key={v} className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => setView(v)}>{v === "sql" ? "SQL" : v[0].toUpperCase() + v.slice(1)}</button>
                ))}
                <button onClick={() => saveBlob(new Blob([toCsv(r)], { type: "text/csv;charset=utf-8" }), `${panel.title}.csv`)} aria-label={`Download ${panel.title} as CSV`}>CSV</button>
              </div>
            )
          )}
        </div>
      </header>
      <div className="dash-panel-body">
        {!data && <div className="skeleton" aria-busy="true" />}
        {data?.error && <div className="error-box">{data.error}</div>}
        {r && view === "chart" && <Chart chart={panel.chart} result={r} title={panel.title} colorOf={colorOf} selected={selected} onPick={editing ? undefined : onPick} />}
        {r && view === "table" && <ResultTable result={r} />}
        {r && view === "sql" && <SqlBlock result={r} />}
      </div>
      {data?.summary && !kpi && view === "chart" && <p className="dash-panel-summary">{data.summary}</p>}
      {panel.note && view === "chart" && !kpi && <p className="faint dash-panel-note">{panel.note}</p>}
    </section>
  );
}
