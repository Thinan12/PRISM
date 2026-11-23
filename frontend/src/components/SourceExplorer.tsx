import type { Source } from "../api/types";
import { fmtInt, fmtTimestamp } from "../lib/format";

export function StatusPill({ status }: { status: Source["status"] }) {
  if (status === "ready") return <span className="pill pill-aurora"><span className="dot" />ready</span>;
  if (status === "failed") return <span className="pill pill-flare"><span className="dot" />failed</span>;
  return <span className="pill pill-nebula pulse"><span className="dot" />{status === "pending" ? "queued" : "reading"}</span>;
}

interface Props {
  sources: Source[];
  activeSource?: number;
  activeDataset?: number;
  onSelect: (sourceId: number, datasetId?: number) => void;
  onAdd: () => void;
  canEdit: boolean;
}

export function SourceExplorer({ sources, activeSource, activeDataset, onSelect, onAdd, canEdit }: Props) {
  return (
    <aside className="explorer" aria-label="Data sources">
      <div className="panel-head">
        <span>Sources</span>
        {canEdit && <button className="btn btn-sm btn-ghost" onClick={onAdd} aria-label="Add data">+ Add</button>}
      </div>
      <div className="explorer-list">
        {sources.length === 0 && <div className="faint" style={{ padding: "8px 10px", fontSize: 13 }}>No data yet.</div>}
        {sources.map((s) => (
          <div key={s.id}>
            <button className={`src-item ${activeSource === s.id ? "active" : ""}`} onClick={() => onSelect(s.id)}>
              <div className="src-name"><span className="kind-badge">{s.kind}</span><span title={s.filename}>{s.filename}</span></div>
              <div className="src-meta"><StatusPill status={s.status} /><span>{fmtTimestamp(s.created_at)}</span></div>
            </button>
            {activeSource === s.id && s.datasets.map((d) => (
              <button key={d.id} className={`ds-sub ${activeDataset === d.id ? "active" : ""}`} onClick={() => onSelect(s.id, d.id)}>
                <span title={d.name}>{d.name}</span>
                <span className="mono faint">{fmtInt(d.row_count)}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}
