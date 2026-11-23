import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { downloadFile } from "../api/client";
import { useAiStatus, useApplyBlueprint, useBlueprint, useEditBlueprint, useGenerateBlueprint } from "../api/hooks";
import type { BlueprintEdit, BlueprintRecord, ColumnPlan, DatasetPlan, ItemStatus, Kpi, OperationItem, Origin, Source } from "../api/types";
import { fmtInt, fmtKpi } from "../lib/format";
import { PrismMark } from "./PrismMark";

const ROLES = [
  "identifier", "timestamp", "date", "time_of_day", "duration", "currency_amount", "quantity", "percentage", "phone",
  "person", "organization_unit", "location", "category", "status", "boolean_flag", "free_text", "other",
];

function OriginChip({ origin }: { origin: Origin }) {
  const cls = origin === "ai" ? "pill-nebula" : origin === "user" ? "pill-aurora" : "";
  const label = origin === "ai" ? "AI-inferred" : origin === "user" ? "edited" : "rules";
  return <span className={`pill ${cls}`} title={`Origin: ${label}`}><span className="dot" />{label}</span>;
}

function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className="conf" title={`Confidence ${pct}%`} aria-label={`Confidence ${pct}%`}>
      <span style={{ width: `${pct}%`, background: value >= 0.75 ? "var(--aurora)" : value >= 0.5 ? "var(--comet)" : "var(--ember)" }} />
    </span>
  );
}

function Decide({ status, onDecide, onRemove, disabled, label }: {
  status: ItemStatus;
  onDecide: (s: "approve" | "reject") => void;
  onRemove?: () => void;
  disabled: boolean;
  label: string;
}) {
  return (
    <span className="decide" role="group" aria-label={`Decision for ${label}`}>
      <button className={`dbtn ${status === "approved" ? "on-approve" : ""}`} aria-pressed={status === "approved"} disabled={disabled} onClick={() => onDecide("approve")} title="Approve">✓</button>
      <button className={`dbtn ${status === "rejected" ? "on-reject" : ""}`} aria-pressed={status === "rejected"} disabled={disabled} onClick={() => onDecide("reject")} title="Reject">✕</button>
      {onRemove && <button className="dbtn" disabled={disabled} onClick={onRemove} title="Remove from blueprint" aria-label={`Remove ${label}`}>⌫</button>}
    </span>
  );
}

function InlineText({ value, onSave, disabled, label, className }: { value: string; onSave: (v: string) => void; disabled: boolean; label: string; className?: string }) {
  const [v, setV] = useState(value);
  const [prev, setPrev] = useState(value);
  if (value !== prev) {
    setPrev(value);
    setV(value);
  }
  return (
    <input
      className={`inline-input ${className ?? ""}`}
      value={v}
      disabled={disabled}
      aria-label={label}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => { if (v.trim() && v !== value) onSave(v.trim()); else setV(value); }}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { setV(value); (e.target as HTMLInputElement).blur(); } }}
    />
  );
}

function ColumnRow({ c, edit, locked }: { c: ColumnPlan; edit: (e: Omit<BlueprintEdit, "dataset_id">) => void; locked: boolean }) {
  return (
    <div className={`bp-col ${c.status}`} title={c.rationale}>
      <span className="faint bp-orig" title="Original header">{c.original_label}</span>
      <span className="arrow faint">→</span>
      <InlineText value={c.display_name} disabled={locked} label={`Name for ${c.original_label}`}
        onSave={(v) => edit({ action: "update", target: "column", id: c.key, changes: { display_name: v } })} />
      <select className="inline-select" value={c.semantic_role} disabled={locked} aria-label={`Role of ${c.display_name}`}
        onChange={(e) => edit({ action: "update", target: "column", id: c.key, changes: { semantic_role: e.target.value } })}>
        {ROLES.map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}
      </select>
      <Confidence value={c.confidence} />
      <OriginChip origin={c.origin} />
      <Decide status={c.status} disabled={locked} label={c.display_name} onDecide={(a) => edit({ action: a, target: "column", id: c.key })} />
    </div>
  );
}

function OperationCard({ o, plan, edit, locked }: { o: OperationItem; plan: DatasetPlan; edit: (e: Omit<BlueprintEdit, "dataset_id">) => void; locked: boolean }) {
  const label = (k: string) => plan.columns.find((c) => c.key === k)?.display_name ?? k;
  return (
    <div className={`bp-card ${o.status}`}>
      <div className="bp-card-head">
        <span className="op-kind mono">{o.spec.op.replace(/_/g, " ")}</span>
        <OriginChip origin={o.origin} />
        <Confidence value={o.confidence} />
        <span style={{ marginLeft: "auto" }} />
        <Decide status={o.status} disabled={locked} label={o.title} onDecide={(a) => edit({ action: a, target: "operation", id: o.id })}
          onRemove={() => edit({ action: "remove", target: "operation", id: o.id })} />
      </div>
      <div className="bp-card-title">{o.title}</div>
      {o.spec.op === "map_values" && (
        <ul className="mapping-list">
          {o.spec.mappings.map((m, i) => (
            <li key={i}>
              <span className="dim">{m.variants.join(" · ")}</span> <span className="faint">→</span>{" "}
              <InlineText value={m.to} disabled={locked} label={`Canonical value for ${m.variants.join(", ")}`}
                onSave={(v) => edit({ action: "update", target: "operation", id: o.id,
                  changes: { mappings: (o.spec as Extract<OperationItem["spec"], { op: "map_values" }>).mappings.map((x, j) => ({ to: j === i ? v : x.to, variants: x.variants })) } })} />
              {m.rows_affected != null && <span className="faint mono"> {fmtInt(m.rows_affected)} rows · {label(o.spec.op === "map_values" ? o.spec.column : "")}</span>}
            </li>
          ))}
        </ul>
      )}
      {o.rationale && <div className="bp-rationale">{o.rationale}</div>}
    </div>
  );
}

function KpiCard({ k, edit, locked }: { k: Kpi; edit: (e: Omit<BlueprintEdit, "dataset_id">) => void; locked: boolean }) {
  const [sql, setSql] = useState(false);
  return (
    <div className={`bp-kpi ${k.status}`}>
      <div className="bp-card-head">
        <OriginChip origin={k.origin} />
        <Confidence value={k.confidence} />
        <span style={{ marginLeft: "auto" }} />
        <Decide status={k.status} disabled={locked} label={k.name} onDecide={(a) => edit({ action: a, target: "kpi", id: k.id })}
          onRemove={() => edit({ action: "remove", target: "kpi", id: k.id })} />
      </div>
      <div className="kpi-value" aria-label={`${k.name} value`}>{fmtKpi(k)}</div>
      <InlineText value={k.name} disabled={locked} label="KPI name" className="kpi-name"
        onSave={(v) => edit({ action: "update", target: "kpi", id: k.id, changes: { name: v } })} />
      {k.sql && (
        <>
          <button className="linkish" onClick={() => setSql((s) => !s)} aria-expanded={sql}>{sql ? "Hide SQL" : "Show SQL"}</button>
          {sql && <pre className="sql">{k.sql}</pre>}
        </>
      )}
    </div>
  );
}

function DatasetSection({ plan, edit, locked }: { plan: DatasetPlan; edit: (e: BlueprintEdit) => void; locked: boolean }) {
  const e = (x: Omit<BlueprintEdit, "dataset_id">) => edit({ ...x, dataset_id: plan.dataset_id });
  const kpis = plan.kpis.filter((k) => k.status !== "rejected" || !locked);
  return (
    <section className="bp-section" aria-label={`Blueprint for ${plan.title}`}>
      <h3 className="display">{plan.title}</h3>
      {plan.summary && <p className="dim" style={{ marginTop: -6 }}>{plan.summary}</p>}
      {kpis.length > 0 && (
        <>
          <h4 className="bp-h4">KPIs <span className="faint">· values computed by DuckDB, never by AI</span></h4>
          <div className="kpi-grid">{kpis.map((k) => <KpiCard key={k.id} k={k} edit={e} locked={locked} />)}</div>
        </>
      )}
      {plan.operations.length > 0 && (
        <>
          <h4 className="bp-h4">{locked && plan.operations.some((o) => o.status === "approved") ? "Operations" : "Proposed operations"} <span className="faint">· only approved operations run</span></h4>
          <div className="bp-ops">{plan.operations.map((o) => <OperationCard key={o.id} o={o} plan={plan} edit={e} locked={locked} />)}</div>
        </>
      )}
      <h4 className="bp-h4">Columns</h4>
      <div className="bp-cols">{plan.columns.map((c) => <ColumnRow key={c.key} c={c} edit={e} locked={locked} />)}</div>
    </section>
  );
}

export function BlueprintView({ ws, source, canEdit }: { ws: number; source: Source; canEdit: boolean }) {
  const { data: rec, isLoading, error } = useBlueprint(ws, source.id);
  const ai = useAiStatus(ws);
  const generate = useGenerateBlueprint(ws, source.id);
  const editM = useEditBlueprint(ws, source.id);
  const apply = useApplyBlueprint(ws, source.id);
  const [req, setReq] = useState("");
  const reduce = useReducedMotion();

  if (isLoading) return <div className="faint" style={{ padding: 24 }}>Loading blueprint…</div>;
  if (error) return <div className="error-box" style={{ margin: 16 }}>{(error as Error).message}</div>;

  const aiPill = ai.data?.configured
    ? <span className="pill pill-nebula"><span className="dot" />AI: {ai.data.model}</span>
    : <span className="pill"><span className="dot" />AI not configured — deterministic rules</span>;

  if (!rec || generate.isPending) {
    return (
      <div className="section" style={{ maxWidth: 760, margin: "4vh auto 0" }}>
        {generate.isPending ? (
          <div className="reading" style={{ minHeight: "40vh" }}>
            <div>
              <PrismMark size={64} animate={!reduce} />
              <h2 className="display">Interpreting {source.filename}</h2>
              <p className="dim">Profiles only are analysed{ai.data?.configured ? " by Claude" : ""}; every proposal is checked against the data.</p>
            </div>
          </div>
        ) : (
          <>
            <h2 className="display" style={{ fontSize: 28, margin: "0 0 6px" }}>Create a Blueprint</h2>
            <p className="dim">PRISM proposes what each column means, which clean-ups to run, which KPIs matter and how the tables relate. Nothing changes until you approve it.</p>
            <div style={{ margin: "10px 0 14px" }}>{aiPill}</div>
            <label className="dim" style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
              What should PRISM focus on? (optional)
              <textarea className="textarea" style={{ minHeight: 90, fontFamily: "var(--font-ui)" }} maxLength={2000} value={req} onChange={(e) => setReq(e.target.value)}
                placeholder="e.g. compare branches on call cost and complaint rate" disabled={!canEdit} />
            </label>
            {generate.error && <div className="error-box" role="alert" style={{ marginTop: 10 }}>{(generate.error as Error).message}</div>}
            <button className="btn btn-primary" style={{ marginTop: 12 }} disabled={!canEdit} onClick={() => generate.mutate(req)}>Generate Blueprint</button>
            {!canEdit && <p className="faint">Viewers can see blueprints but not create them.</p>}
          </>
        )}
      </div>
    );
  }

  const bp = rec.content;
  const locked = rec.status !== "draft" || !canEdit || editM.isPending;
  const edit = (e: BlueprintEdit) => editM.mutate({ id: rec.id, edits: [e] });
  const proposed = bp.datasets.reduce((n, p) => n + [...p.columns, ...p.operations, ...p.kpis].filter((x) => x.status === "proposed").length, 0);

  return (
    <motion.div className="bp" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }}>
      <header className="bp-head">
        <div className="bp-domain hull">
          <div className="faint" style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" }}>Domain</div>
          <div className="display" style={{ fontSize: 22, fontWeight: 600 }}>{bp.domain.label}</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
            <OriginChip origin={bp.domain.origin} /><Confidence value={bp.domain.confidence} />
          </div>
          <div className="dim" style={{ fontSize: 12.5, marginTop: 6 }}>{bp.domain.rationale}</div>
        </div>
        <div className="bp-status">
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <span className="pill">Blueprint #{rec.number}</span>
            <span className={`pill ${rec.status === "applied" ? "pill-aurora" : rec.status === "draft" ? "pill-nebula" : ""}`}><span className="dot" />{rec.status}</span>
            {bp.generator.engine === "ai" ? <span className="pill pill-nebula">Claude · {bp.generator.model}</span> : <span className="pill">deterministic rules</span>}
          </div>
          {bp.generator.fallback_reason && <div className="note ember" style={{ marginTop: 8 }}>{bp.generator.fallback_reason}</div>}
          {bp.generator.warnings.length > 0 && (
            <details className="bp-warn"><summary className="dim">{bp.generator.warnings.length} validation note(s)</summary>
              <ul>{bp.generator.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </details>
          )}
          {rec.status === "draft" && canEdit && (
            <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              <button className="btn btn-sm" disabled={locked || proposed === 0} onClick={() => edit({ action: "approve_all", target: "blueprint" })}>Approve all ({proposed})</button>
              <button className="btn btn-sm btn-aurora" disabled={apply.isPending || editM.isPending} onClick={() => apply.mutate(rec.id)}>
                {apply.isPending ? "Applying…" : "Apply approved items"}
              </button>
              <button className="btn btn-sm btn-ghost" disabled={generate.isPending} onClick={() => generate.mutate(rec.requirements ?? "")}>Regenerate</button>
            </div>
          )}
          {rec.status === "applied" && rec.result && <AppliedBanner ws={ws} rec={rec} source={source} onNew={canEdit ? () => generate.mutate("") : undefined} />}
          {(editM.error || apply.error) && <div className="error-box" role="alert" style={{ marginTop: 8 }}>{((editM.error || apply.error) as Error).message}</div>}
        </div>
      </header>

      {bp.datasets.map((p) => <DatasetSection key={p.dataset_id} plan={p} edit={edit} locked={locked} />)}

      {bp.relationships.length > 0 && (
        <section className="bp-section">
          <h4 className="bp-h4">Relationships <span className="faint">· overlap measured in the data</span></h4>
          {bp.relationships.map((r) => {
            const from = bp.datasets.find((d) => d.dataset_id === r.from_dataset);
            const to = bp.datasets.find((d) => d.dataset_id === r.to_dataset);
            const name = (p: DatasetPlan | undefined, k: string) => p?.columns.find((c) => c.key === k)?.display_name ?? k;
            return (
              <div key={r.id} className={`bp-card ${r.status}`}>
                <div className="bp-card-head">
                  <span className="mono">{name(from, r.from_column)} <span className="faint">({from?.title})</span> → {name(to, r.to_column)} <span className="faint">({to?.title})</span></span>
                  <span className={`pill ${r.verified ? "pill-aurora" : "pill-ember"}`}>{Math.round(r.match_ratio * 100)}% match</span>
                  <OriginChip origin={r.origin} />
                  <span style={{ marginLeft: "auto" }} />
                  <Decide status={r.status} disabled={locked} label="relationship" onDecide={(a) => edit({ action: a, target: "relationship", id: r.id })} />
                </div>
                <div className="bp-rationale">{r.rationale}</div>
              </div>
            );
          })}
        </section>
      )}

      {bp.dashboard.length > 0 && (
        <section className="bp-section">
          <h4 className="bp-h4">Dashboard plan <span className="faint">· built in the analytics phase</span></h4>
          <div className="panel-grid">
            {bp.dashboard.map((p) => (
              <div key={p.id} className={`bp-card ${p.status}`}>
                <div className="bp-card-head"><span className="op-kind mono">{p.chart.replace("_", " ")}</span><OriginChip origin={p.origin} />
                  <span style={{ marginLeft: "auto" }} />
                  <Decide status={p.status} disabled={locked} label={p.title} onDecide={(a) => edit({ action: a, target: "panel", id: p.id })} />
                </div>
                <div className="bp-card-title">{p.title}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {bp.assumptions.length > 0 && (
        <section className="bp-section">
          <h4 className="bp-h4">Assumptions</h4>
          {bp.assumptions.map((a) => (
            <div key={a.id} className={`bp-card ${a.status}`} style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <OriginChip origin={a.origin} /><Confidence value={a.confidence} />
              <span style={{ flex: 1 }}>{a.text}</span>
              <Decide status={a.status} disabled={locked} label="assumption" onDecide={(x) => edit({ action: x, target: "assumption", id: a.id })}
                onRemove={() => edit({ action: "remove", target: "assumption", id: a.id })} />
            </div>
          ))}
        </section>
      )}
    </motion.div>
  );
}

function AppliedBanner({ ws, rec, source, onNew }: { ws: number; rec: BlueprintRecord; source: Source; onNew?: () => void }) {
  const r = rec.result!;
  const filename = source.artifacts.find((a) => a.id === r.artifact_id)?.filename ?? "PRISM_workbook.xlsx";
  return (
    <div className="note" style={{ marginTop: 10, background: "var(--aurora-dim)", color: "var(--starlight)" }}>
      Applied — {r.datasets.map((d) => `v${d.version} (${fmtInt(d.row_count)} rows)`).join(", ")}.
      {r.unreviewed_items_skipped > 0 && ` ${r.unreviewed_items_skipped} unreviewed item(s) were not applied.`}
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="btn btn-sm btn-aurora" onClick={() => void downloadFile(`/workspaces/${ws}/artifacts/${r.artifact_id}/download`, filename)}>↓ Updated workbook</button>
        {onNew && <button className="btn btn-sm btn-ghost" onClick={onNew}>New blueprint</button>}
      </div>
    </div>
  );
}
