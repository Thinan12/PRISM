import type { Dataset } from "../api/types";
import { fmtInt, fmtLAK, fmtNumber } from "../lib/format";

const SEV_CLASS = { error: "pill-flare", warning: "pill-ember", info: "" } as const;

export function QualityView({ ds }: { ds: Dataset }) {
  const p = ds.profile;
  const cols = new Map(ds.columns.map((c) => [c.key, c]));
  const deductions = Object.entries(p.quality_penalties).filter(([, v]) => v > 0);
  return (
    <div className="section" style={{ maxWidth: 1000 }}>
      <div className="stat-row">
        <div className="hull stat">
          <div className="label">Quality score</div>
          <div className="value" style={{ color: p.quality_score >= 85 ? "var(--aurora)" : "var(--ember)" }}>
            {p.quality_score}<small>/ 100</small>
          </div>
        </div>
        <div className="hull stat"><div className="label">Records</div><div className="value">{fmtInt(p.row_count)}</div></div>
        <div className="hull stat"><div className="label">Completeness</div><div className="value">{p.completeness_pct}<small>%</small></div></div>
        <div className="hull stat"><div className="label">Duplicate rows</div><div className="value" style={{ color: p.duplicate_rows ? "var(--ember)" : undefined }}>{fmtInt(p.duplicate_rows)}</div></div>
      </div>
      <p className="faint" style={{ fontSize: 12.5 }}>
        Score deductions: {deductions.length ? deductions.map(([k, v]) => `${k.replace(/_/g, " ")} −${v}`).join(" · ") : "none"}. The score is
        computed deterministically from the profile; see the workbook’s Data Quality sheet for the method.
      </p>

      {p.total_checks.length > 0 && (
        <>
          <h3 style={{ marginTop: 28 }}>Source totals reconciled</h3>
          <div className="hull" style={{ overflow: "hidden" }}>
            <table className="grid">
              <thead><tr><th>Column</th><th>Source row</th><th>Stated</th><th>Computed</th><th>Result</th></tr></thead>
              <tbody>
                {p.total_checks.map((t, i) => {
                  const lak = cols.get(t.column)?.unit === "LAK";
                  const f = lak ? fmtLAK : fmtNumber;
                  return (
                    <tr key={i}>
                      <td>{t.label}</td>
                      <td className="num">{t.source_row}</td>
                      <td className="num">{f(t.stated)}</td>
                      <td className="num">{f(t.computed)}</td>
                      <td>{t.matches ? <span className="pill pill-aurora"><span className="dot" />matches</span> : <span className="pill pill-flare"><span className="dot" />off by {f(t.difference)}</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h3 style={{ marginTop: 28 }}>Findings</h3>
      {p.issues.length === 0 && <div className="note">No issues found.</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {p.issues.map((i, k) => (
          <div key={k} className="hull" style={{ padding: "10px 14px", display: "flex", gap: 12, alignItems: "flex-start" }}>
            <span className={`pill ${SEV_CLASS[i.severity]}`} style={{ marginTop: 1 }}><span className="dot" />{i.severity}</span>
            <div style={{ minWidth: 0 }}>
              <div>{i.message}</div>
              {i.groups && i.groups.length > 0 && (
                <div className="faint mono" style={{ marginTop: 4 }}>
                  source rows: {i.groups.slice(0, 6).map((g) => g.join(" = ")).join(" · ")}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {ds.lineage.excluded_rows.length > 0 && (
        <>
          <h3 style={{ marginTop: 28 }}>Rows set aside from the data</h3>
          <div className="hull" style={{ overflow: "hidden" }}>
            <table className="grid">
              <thead><tr><th>Source row</th><th>Reason</th><th>Original values</th></tr></thead>
              <tbody>
                {ds.lineage.excluded_rows.map((e) => (
                  <tr key={`${e.source_row}-${e.reason}`}>
                    <td className="num">{e.source_row}</td>
                    <td>{e.reason}</td>
                    <td className="dim">{Object.entries(e.values).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
