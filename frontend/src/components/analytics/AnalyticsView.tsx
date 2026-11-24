import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { downloadPost } from "../../api/client";
import {
  getQuestion,
  useAiStatus,
  useAsk,
  useCreateDashboard,
  useDashboardData,
  useDashboards,
  useFilterOptions,
  useInsights,
  useQuestions,
  useSaveDashboard,
} from "../../api/hooks";
import type { ActiveFilter, Dashboard, DashboardContent, DashPanel, Dataset, FilterOption, Question, Source } from "../../api/types";
import { fmtInt } from "../../lib/format";
import { PrismMark } from "../PrismMark";
import { Chart, ResultTable, useEntityColors, type Pick_ } from "./Charts";
import { PanelCard, SqlBlock } from "./PanelCard";

const leaf = (label: string) => label.split(" / ").pop() ?? label;

// ---------------------------------------------------------------- ask

function suggestions(ds: Dataset | undefined): string[] {
  if (!ds) return [];
  const cols = ds.columns;
  const lak = cols.find((c) => c.unit === "LAK");
  const cat = cols.find((c) => c.logical_type === "text" && c.semantic_hint === "category");
  const date = cols.find((c) => c.logical_type === "date" || c.logical_type === "datetime");
  const out: string[] = [];
  if (lak && cat) out.push(`Total ${leaf(lak.label)} by ${leaf(cat.label)}`);
  if (date) out.push("How many records per month?");
  const branch = cols.find((c) => /ສາຂາ/.test(c.label));
  if (branch) out.push("ສາຂາໃດມີການໂທຫຼາຍທີ່ສຸດ?");
  if (date) out.push("Any unusual days?");
  return out.slice(0, 4);
}

function AskBar({ ws, datasets, datasetId, setDatasetId, onAnswer, inputRef }: {
  ws: number;
  datasets: Dataset[];
  datasetId: number | undefined;
  setDatasetId: (id: number) => void;
  onAnswer: (q: Question) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  const [text, setText] = useState("");
  const [quick, setQuick] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const ask = useAsk(ws, datasetId);
  const ai = useAiStatus(ws);
  const history = useQuestions(ws, datasetId);
  const st = ai.data;
  const aiReady = !!st?.configured && st.reachable !== false && !st.detail;
  const ds = datasets.find((d) => d.id === datasetId);

  useEffect(() => {
    if (!ask.isPending) return;
    setElapsed(0);
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [ask.isPending]);

  function submit(q: string) {
    const question = q.trim();
    if (question.length < 2 || ask.isPending) return;
    ask.mutate({ question, engine: quick || !aiReady ? "rules" : "auto" }, { onSuccess: onAnswer });
  }

  return (
    <div className="ask hull">
      <form className="ask-row" onSubmit={(e) => { e.preventDefault(); submit(text); }}>
        <span className="ask-glyph" aria-hidden>✦</span>
        <input
          ref={inputRef}
          className="ask-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask about this data — in Lao or English"
          aria-label="Ask a question about the data"
          maxLength={500}
          disabled={ask.isPending}
        />
        {datasets.length > 1 && (
          <select className="input input-sm" value={datasetId} onChange={(e) => setDatasetId(Number(e.target.value))} aria-label="Table to ask about">
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.name.length > 40 ? d.name.slice(0, 39) + "…" : d.name}</option>)}
          </select>
        )}
        <button className="btn btn-primary btn-sm" type="submit" disabled={ask.isPending || text.trim().length < 2}>
          {ask.isPending ? `Working… ${elapsed}s` : "Ask"}
        </button>
      </form>
      <div className="ask-meta">
        {suggestions(ds).map((s) => (
          <button key={s} className="chip" onClick={() => { setText(s); submit(s); }} disabled={ask.isPending}>{s}</button>
        ))}
        <span className="ask-engine">
          {aiReady ? (
            <label className="faint">
              <input type="checkbox" checked={quick} onChange={(e) => setQuick(e.target.checked)} /> quick (rules only)
              <span className="pill pill-nebula" style={{ marginLeft: 8 }}><span className="dot" />{st!.model}</span>
            </label>
          ) : (
            <span className="pill"><span className="dot" />rule-based interpreter</span>
          )}
        </span>
      </div>
      {ask.isPending && aiReady && !quick && elapsed > 8 && (
        <div className="faint ask-wait">The local AI model is reading the question… (PRISM computes the answer itself once it knows what you asked.)</div>
      )}
      {ask.error && <div className="error-box" style={{ marginTop: 8 }}>{(ask.error as Error).message}</div>}
      {!!history.data?.length && (
        <details className="ask-history">
          <summary>Recent questions ({history.data.length})</summary>
          <ul>
            {history.data.map((q) => (
              <li key={q.id}>
                <button className="linkish" onClick={() => void getQuestion(ws, q.id).then(onAnswer)}>{q.question}</button>
                <span className="faint"> · {q.status}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function AnswerCard({ q, onClose, onPin, canPin }: { q: Question; onClose: () => void; onPin?: () => void; canPin: boolean }) {
  const [view, setView] = useState<"chart" | "table" | "sql">("chart");
  const colorOf = useEntityColors();
  const r = q.result;
  return (
    <motion.section className="answer hull" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: { duration: 0.12 } }} aria-label="Answer">
      <div className="answer-head">
        <div className="answer-q">“{q.question}”</div>
        <button className="icon-btn" onClick={onClose} aria-label="Close answer">✕</button>
      </div>
      {q.status === "answered" && r ? (
        <>
          <p className="answer-text">{q.answer}</p>
          <div className="answer-how">
            <span className={`pill ${q.engine === "ai" ? "pill-nebula" : ""}`}><span className="dot" />{q.engine === "ai" ? `AI-interpreted · ${q.model}` : "Rule-based interpretation"}</span>
            <span className="dim">PRISM computed: {r.description}</span>
          </div>
          {!!q.notes?.length && <div className="faint answer-notes">{q.notes.join(" ")}</div>}
          {q.fallback_reason && <div className="ember-text answer-notes">{q.fallback_reason}</div>}
          <div className="seg answer-views" role="group" aria-label="View">
            {(["chart", "table", "sql"] as const).map((v) => (
              <button key={v} className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => setView(v)}>{v === "sql" ? "SQL" : v[0].toUpperCase() + v.slice(1)}</button>
            ))}
            {canPin && onPin && <button onClick={onPin}>＋ Pin to dashboard</button>}
          </div>
          <div className="answer-body">
            {view === "chart" && <Chart chart={q.chart ?? "table"} result={r} title={q.question} colorOf={colorOf} />}
            {view === "table" && <ResultTable result={r} />}
            {view === "sql" && <SqlBlock result={r} />}
          </div>
        </>
      ) : q.status === "unanswerable" ? (
        <div className="answer-cant"><strong>PRISM can't answer this from the data.</strong> {q.answer}</div>
      ) : (
        <div className="error-box">{q.error ?? "The question could not be answered."}</div>
      )}
    </motion.section>
  );
}

// ---------------------------------------------------------------- filters

function FilterBar({ options, bar, setBar, cross, removeCross, clearAll }: {
  options: FilterOption[];
  bar: Record<string, ActiveFilter>;
  setBar: (col: string, f: ActiveFilter | null) => void;
  cross: ActiveFilter[];
  removeCross: (i: number) => void;
  clearAll: () => void;
}) {
  const any = Object.keys(bar).length + cross.length > 0;
  return (
    <div className="filter-bar" role="region" aria-label="Filters">
      {options.map((o) => {
        if (o.type === "date" || o.type === "datetime") {
          const cur = bar[o.column]?.condition.values ?? [];
          const from = (cur[0] as string) ?? "";
          const to = (cur[1] as string) ?? "";
          const update = (a: string, b: string) => {
            const lo = a || (o.min as string);
            const hi = b || (o.max as string);
            setBar(o.column, a || b ? { dataset_id: o.dataset_id, condition: { column: o.column, op: "between", values: [lo, hi] } } : null);
          };
          return (
            <div key={o.column} className="filter">
              <span className="filter-label">{leaf(o.label)}</span>
              <input type="date" className="input input-sm" aria-label={`${leaf(o.label)} from`} min={o.min as string} max={o.max as string}
                     value={from && from !== o.min ? from : ""} onChange={(e) => update(e.target.value, to)} />
              <span className="faint">–</span>
              <input type="date" className="input input-sm" aria-label={`${leaf(o.label)} to`} min={o.min as string} max={o.max as string}
                     value={to && to !== o.max ? to : ""} onChange={(e) => update(from, e.target.value)} />
            </div>
          );
        }
        if (!o.values) return null;
        const cur = bar[o.column]?.condition.values[0] as string | undefined;
        return (
          <label key={o.column} className="filter">
            <span className="filter-label">{leaf(o.label)}</span>
            <select className="input input-sm" value={cur ?? ""} onChange={(e) => setBar(o.column, e.target.value ? { dataset_id: o.dataset_id, condition: { column: o.column, op: "eq", values: [e.target.value] } } : null)}>
              <option value="">All</option>
              {o.values.map((v) => <option key={v.value} value={v.value}>{v.value} ({fmtInt(v.count)})</option>)}
            </select>
          </label>
        );
      })}
      {cross.map((c, i) => (
        <span key={i} className="chip chip-on">
          {String(c.condition.values[0] ?? "(blank)")}
          <button className="chip-x" onClick={() => removeCross(i)} aria-label={`Remove filter ${String(c.condition.values[0])}`}>✕</button>
        </span>
      ))}
      {any && <button className="linkish" onClick={clearAll}>Clear filters</button>}
    </div>
  );
}

// ---------------------------------------------------------------- findings

function Findings({ ws, ds }: { ws: number; ds: Dataset | undefined }) {
  const { data = [] } = useInsights(ws, ds?.id, ds?.current_version);
  const [open, setOpen] = useState<number | null>(null);
  if (!data.length) return null;
  return (
    <div className="findings">
      {data.map((f, i) => (
        <div key={i} className={`finding hull ${f.kind === "anomaly" && !/^No unusual/.test(f.text) ? "warn" : ""}`}>
          <div className="finding-kind">{f.title}</div>
          <div className="finding-text">{f.text}</div>
          <button className="linkish" onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}>{open === i ? "Hide SQL" : "SQL"}</button>
          {open === i && <pre className="sql">{f.sql}</pre>}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- canvas

export function AnalyticsView({ ws, source, datasets, canEdit, askFocus }: {
  ws: number;
  source: Source;
  datasets: Dataset[];
  canEdit: boolean;
  askFocus?: number;
}) {
  const reduce = useReducedMotion();
  const dashboards = useDashboards(ws, source.id);
  const create = useCreateDashboard(ws, source.id);
  const save = useSaveDashboard(ws, source.id);
  const [dashId, setDashId] = useState<number>();
  const dash: Dashboard | undefined = dashboards.data?.find((d) => d.id === dashId) ?? dashboards.data?.[0];
  const primary = useMemo(() => [...datasets].sort((a, b) => b.version.row_count - a.version.row_count)[0], [datasets]);
  const [askDs, setAskDs] = useState<number | undefined>(primary?.id);
  useEffect(() => { if (!askDs && primary) setAskDs(primary.id); }, [primary, askDs]);
  const [answer, setAnswer] = useState<Question | null>(null);
  const [bar, setBarState] = useState<Record<string, ActiveFilter>>({});
  const [cross, setCross] = useState<ActiveFilter[]>([]);
  const filters = useMemo(() => [...Object.values(bar), ...cross], [bar, cross]);
  const data = useDashboardData(ws, dash, filters);
  const options = useFilterOptions(ws, dash);
  const [draft, setDraft] = useState<DashboardContent | null>(null);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const colorOf = useEntityColors();
  const askRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (askFocus) askRef.current?.focus(); }, [askFocus]);
  useEffect(() => { setBarState({}); setCross([]); setDraft(null); }, [dash?.id]);

  const editing = draft !== null;
  const content = draft ?? dash?.content;
  const byPanel = new Map((data.data?.panels ?? []).map((p) => [p.panel_id, p]));

  function pick(panel: DashPanel, p: Pick_) {
    setCross((cur) => {
      const same = cur.findIndex((c) => c.source_panel === panel.id && c.condition.column === p.column);
      if (same >= 0 && cur[same].condition.values[0] === p.value) return cur.filter((_, i) => i !== same);
      const next = cur.filter((c) => c.condition.column !== p.column);
      const cond = p.value === null || p.value === undefined
        ? { column: p.column, op: "is_null" as const, values: [] }
        : { column: p.column, op: "eq" as const, values: [p.value as string] };
      return [...next, { dataset_id: panel.query.dataset_id, condition: cond, source_panel: panel.id }];
    });
  }
  const selectedFor = (panel: DashPanel) => {
    const c = cross.find((f) => f.source_panel === panel.id);
    return c ? (c.condition.op === "is_null" ? null : c.condition.values[0]) : undefined;
  };

  async function saveContent(next: DashboardContent, nextName?: string) {
    if (!dash) return;
    setMsg(null);
    try {
      await save.mutateAsync({ id: dash.id, revision: dash.revision, name: nextName, content: next });
      setDraft(null);
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  function pin(q: Question) {
    if (!dash || !q.spec || !content) return;
    const id = `q${q.id}`;
    if (content.panels.some((p) => p.id === id)) return setMsg("This answer is already on the dashboard.");
    const chart = q.chart ?? "table";
    const panel: DashPanel = { id, title: q.question.slice(0, 120), chart, query: q.spec, width: chart === "kpi_card" ? 3 : 6 };
    if (editing) setDraft({ ...content, panels: [...content.panels, panel] });
    else void saveContent({ ...content, panels: [...content.panels, panel] });
  }

  const exportXlsx = () => dash && void downloadPost(`/workspaces/${ws}/dashboards/${dash.id}/export`, { filters }, `${dash.name}.xlsx`).catch((e) => setMsg((e as Error).message));

  return (
    <div className="analytics">
      <AskBar ws={ws} datasets={datasets} datasetId={askDs} setDatasetId={setAskDs} onAnswer={setAnswer} inputRef={askRef} />
      <AnimatePresence mode="wait" initial={false}>
        {answer && <AnswerCard key={answer.id} q={answer} onClose={() => setAnswer(null)} canPin={canEdit && !!dash} onPin={() => pin(answer)} />}
      </AnimatePresence>

      {dashboards.isLoading ? (
        <div className="faint" style={{ padding: 16 }}>Loading dashboards…</div>
      ) : !dash ? (
        <div className="dash-empty">
          <PrismMark size={48} animate={!reduce} />
          <h2 className="display">No dashboard yet</h2>
          <p className="dim">PRISM builds one from the Blueprint's dashboard plan (or from its own rules when no Blueprint is applied). Every panel is a validated query with inspectable SQL.</p>
          {canEdit ? (
            <button className="btn btn-primary" disabled={create.isPending} onClick={() => create.mutate(undefined, { onSuccess: (d) => setDashId(d.id) })}>
              {create.isPending ? "Building…" : "Generate dashboard"}
            </button>
          ) : <p className="faint">Ask an editor to generate one. You can already ask questions above.</p>}
          {create.error && <div className="error-box">{(create.error as Error).message}</div>}
          <Findings ws={ws} ds={primary} />
        </div>
      ) : (
        <>
          <div className="dash-toolbar">
            {editing ? (
              <input className="input dash-name-input" value={name} onChange={(e) => setName(e.target.value)} aria-label="Dashboard name" maxLength={200} />
            ) : (
              <h2 className="display dash-name">{dash.name}</h2>
            )}
            {(dashboards.data?.length ?? 0) > 1 && !editing && (
              <select className="input input-sm" value={dash.id} onChange={(e) => setDashId(Number(e.target.value))} aria-label="Dashboard">
                {dashboards.data!.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            )}
            <span className="pill">{dash.origin === "blueprint" ? "from Blueprint" : dash.origin === "rules" ? "from PRISM rules" : "edited"} · rev {dash.revision}</span>
            <div className="dash-actions">
              {data.isFetching && <span className="faint" role="status">Computing…</span>}
              {editing ? (
                <>
                  <button className="btn btn-sm btn-ghost" onClick={() => { setDraft(null); setMsg(null); }}>Cancel</button>
                  <button className="btn btn-sm btn-primary" disabled={save.isPending} onClick={() => void saveContent(draft!, name.trim() || undefined)}>{save.isPending ? "Saving…" : "Save dashboard"}</button>
                </>
              ) : (
                <>
                  <button className="btn btn-sm" onClick={exportXlsx}>↓ Excel</button>
                  {canEdit && <button className="btn btn-sm" onClick={() => { setDraft(structuredClone(dash.content)); setName(dash.name); }}>Edit</button>}
                  {canEdit && <button className="btn btn-sm btn-ghost" disabled={create.isPending} onClick={() => create.mutate(undefined, { onSuccess: (d) => setDashId(d.id) })} title="Generate another dashboard from the current Blueprint">＋ New</button>}
                </>
              )}
            </div>
          </div>
          {msg && <div className="error-box" style={{ margin: "0 0 10px" }}>{msg}</div>}
          {!!dash.notes?.length && <div className="faint">{dash.notes.join(" ")}</div>}
          {!editing && options.data && (
            <FilterBar
              options={options.data}
              bar={bar}
              setBar={(col, f) => setBarState((b) => { const n = { ...b }; if (f) n[col] = f; else delete n[col]; return n; })}
              cross={cross}
              removeCross={(i) => setCross((c) => c.filter((_, j) => j !== i))}
              clearAll={() => { setBarState({}); setCross([]); }}
            />
          )}
          {!editing && cross.length === 0 && Object.keys(bar).length === 0 && <Findings ws={ws} ds={primary} />}
          {editing && <p className="faint">Rename panels, change chart types and widths, reorder or remove panels. Pin answers from the question bar to add panels. Every panel is re-validated against the data when you save.</p>}
          <div className="dash-grid">
            {content!.panels.map((p, i) => (
              <PanelCard
                key={p.id}
                panel={p}
                data={byPanel.get(p.id)}
                editing={editing}
                colorOf={colorOf}
                selected={selectedFor(p)}
                onPick={(x) => pick(p, x)}
                first={i === 0}
                last={i === content!.panels.length - 1}
                onChange={(np) => setDraft({ ...content!, panels: content!.panels.map((x) => (x.id === p.id ? np : x)) })}
                onRemove={() => setDraft({ ...content!, panels: content!.panels.filter((x) => x.id !== p.id) })}
                onMove={(dir) => {
                  const arr = [...content!.panels];
                  const j = i + dir;
                  [arr[i], arr[j]] = [arr[j], arr[i]];
                  setDraft({ ...content!, panels: arr });
                }}
              />
            ))}
            {!content!.panels.length && <div className="faint">This dashboard has no panels. Ask a question and pin the answer.</div>}
          </div>
        </>
      )}
    </div>
  );
}
