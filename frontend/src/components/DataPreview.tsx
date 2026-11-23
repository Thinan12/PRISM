import { useState } from "react";
import { useRows } from "../api/hooks";
import type { Dataset } from "../api/types";
import { fmtInt, fmtValue, isNumericType } from "../lib/format";

const PAGE = 100;

export function DataPreview({ ws, ds }: { ws: number; ds: Dataset }) {
  const [page, setPage] = useState(0);
  const [layer, setLayer] = useState<"clean" | "raw">("clean");
  const { data, isLoading, error } = useRows(ws, ds.id, page * PAGE, PAGE, layer);
  const cols = new Map(ds.columns.map((c) => [c.key, c]));
  const pages = Math.max(1, Math.ceil(ds.profile.row_count / PAGE));

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 16px", borderBottom: "1px solid var(--hull-line-soft)" }}>
        <div role="radiogroup" aria-label="Data layer" style={{ display: "flex", gap: 4 }}>
          {(["clean", "raw"] as const).map((l) => (
            <button key={l} role="radio" aria-checked={layer === l} className={`btn btn-sm ${layer === l ? "" : "btn-ghost"}`} onClick={() => { setLayer(l); setPage(0); }}>
              {l === "clean" ? "Typed" : "Original text"}
            </button>
          ))}
        </div>
        <span className="faint" style={{ fontSize: 12 }}>
          {layer === "clean" ? "Converted values; unreadable cells are blank and flagged." : "Exactly as read from the source (normalized whitespace)."}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          <span className="faint mono">
            {fmtInt(page * PAGE + 1)}–{fmtInt(Math.min((page + 1) * PAGE, ds.profile.row_count))} of {fmtInt(ds.profile.row_count)}
          </span>
          <button className="btn btn-sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)} aria-label="Previous page">‹</button>
          <button className="btn btn-sm" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)} aria-label="Next page">›</button>
        </div>
      </div>
      {error && <div className="error-box" style={{ margin: 16 }}>{(error as Error).message}</div>}
      {isLoading && !data && <div className="faint" style={{ padding: 24 }}>Loading rows…</div>}
      {data && (
        <div className="grid-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="lineage">row</th>
                {data.columns.filter((c) => !c.startsWith("_")).map((c) => (
                  <th key={c} title={c}>{cols.get(c)?.label ?? c}</th>
                ))}
                <th className="lineage">source</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r, i) => {
                const rec = Object.fromEntries(data.columns.map((c, j) => [c, r[j]]));
                return (
                  <tr key={i}>
                    <td className="lineage">{String(rec._prism_row)}</td>
                    {data.columns.filter((c) => !c.startsWith("_")).map((c) => {
                      const meta = cols.get(c);
                      const v = rec[c];
                      const empty = v === null || v === undefined || v === "";
                      return (
                        <td key={c} className={`${empty ? "null" : ""} ${meta && layer === "clean" && isNumericType(meta.logical_type) ? "num" : ""}`}>
                          {empty ? "·" : layer === "clean" ? fmtValue(meta, v) : String(v)}
                        </td>
                      );
                    })}
                    <td className="lineage">{String(rec._source_sheet)}!{String(rec._source_row)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
