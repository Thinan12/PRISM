import { useLayoutEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import type { ColumnProfile, Dataset, Issue, Source } from "../api/types";
import { TYPE_META, fmtInt } from "../lib/format";

export type Selection =
  | { kind: "column"; datasetId: number; key: string }
  | { kind: "dataset"; datasetId: number }
  | { kind: "source" }
  | null;

interface Props {
  source: Source;
  datasets: Dataset[];
  selection: Selection;
  onSelect: (s: Selection) => void;
}

interface Line {
  id: string;
  d: string;
}

function issuesFor(ds: Dataset, key: string): Issue[] {
  return ds.profile.issues.filter((i) => i.column === key && i.severity !== "info");
}

export function SchemaMap({ source, datasets, selection, onSelect }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const srcRef = useRef<HTMLButtonElement>(null);
  const cardRefs = useRef<Map<number, HTMLElement>>(new Map());
  const [lines, setLines] = useState<Line[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const reduce = useReducedMotion();

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => {
      const base = el.getBoundingClientRect();
      const s = srcRef.current?.getBoundingClientRect();
      if (!s) return;
      const x1 = s.right - base.left + el.scrollLeft;
      const y1 = s.top + s.height / 2 - base.top + el.scrollTop;
      const next: Line[] = [];
      for (const ds of datasets) {
        const c = cardRefs.current.get(ds.id);
        if (!c) continue;
        const r = c.getBoundingClientRect();
        const x2 = r.left - base.left + el.scrollLeft;
        const y2 = r.top + 28 - base.top + el.scrollTop;
        const mx = (x1 + x2) / 2;
        next.push({ id: String(ds.id), d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}` });
      }
      setLines(next);
      setSize({ w: el.scrollWidth, h: el.scrollHeight });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    cardRefs.current.forEach((c) => ro.observe(c));
    return () => ro.disconnect();
  }, [datasets]);

  const sheets = source.summary?.sheets ?? [];

  return (
    <div className="schema-map" ref={wrap}>
      <svg className="schema-lines" width={size.w} height={size.h} aria-hidden="true">
        <defs>
          <linearGradient id="beam" x1="0" x2="1">
            <stop offset="0%" stopColor="#E8EDF7" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#3EE6B0" stopOpacity="0.8" />
          </linearGradient>
        </defs>
        {lines.map((l, i) => (
          <motion.path
            key={l.id}
            d={l.d}
            fill="none"
            stroke="url(#beam)"
            strokeWidth={1.5}
            initial={reduce ? false : { pathLength: 0, opacity: 0 }}
            animate={{ pathLength: 1, opacity: 1 }}
            transition={{ duration: 0.8, delay: 0.15 + i * 0.12, ease: "easeInOut" }}
          />
        ))}
      </svg>

      <div className="schema-source-col">
        <motion.button
          ref={srcRef}
          className={`schema-source hull ${selection?.kind === "source" ? "selected" : ""}`}
          onClick={() => onSelect({ kind: "source" })}
          initial={reduce ? false : { opacity: 0, x: -12 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.4 }}
        >
          <span className="kind-badge">{source.kind}</span>
          <div className="schema-source-name">{source.filename}</div>
          <div className="faint" style={{ fontSize: 12 }}>
            {sheets.length > 0 ? `${sheets.length} sheet${sheets.length > 1 ? "s" : ""} · ` : ""}
            {datasets.length} table{datasets.length !== 1 ? "s" : ""} found
          </div>
          {sheets.length > 0 && (
            <ul className="schema-sheets">
              {sheets.map((s) => (
                <li key={s.name}>
                  <span>{s.name}</span>
                  <span className="faint mono">{s.rows}×{s.cols}{s.merged_ranges ? ` · ${s.merged_ranges} merged` : ""}</span>
                </li>
              ))}
            </ul>
          )}
        </motion.button>
      </div>

      <div className="schema-tables">
        {datasets.map((ds, i) => {
          const prof = new Map<string, ColumnProfile>(ds.profile.columns.map((c) => [c.key, c]));
          const dsSelected = selection?.kind === "dataset" && selection.datasetId === ds.id;
          const score = ds.profile.quality_score;
          return (
            <motion.section
              key={ds.id}
              ref={(el: HTMLElement | null) => {
                if (el) cardRefs.current.set(ds.id, el);
                else cardRefs.current.delete(ds.id);
              }}
              className={`schema-card hull ${dsSelected ? "selected" : ""}`}
              initial={reduce ? false : { opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.45, delay: 0.25 + i * 0.12 }}
              aria-label={`Table ${ds.name}`}
            >
              <button className="schema-card-head" onClick={() => onSelect({ kind: "dataset", datasetId: ds.id })}>
                <div style={{ minWidth: 0 }}>
                  <div className="schema-card-title">{ds.name}</div>
                  <div className="faint" style={{ fontSize: 12 }}>
                    {ds.lineage.sheet} · rows {ds.lineage.first_row}–{ds.lineage.last_row} · header row {ds.lineage.header_rows.join("+") || "—"}
                  </div>
                </div>
                <div className="schema-card-stats">
                  <span className="mono">{fmtInt(ds.profile.row_count)} rows</span>
                  <span className={`pill ${score >= 85 ? "pill-aurora" : "pill-ember"}`} title="Quality score (0–100)">
                    <span className="dot" />Q {score.toFixed(0)}
                  </span>
                </div>
              </button>
              <ul className="schema-cols" role="list">
                {ds.columns.map((c) => {
                  const p = prof.get(c.key);
                  const t = TYPE_META[c.logical_type];
                  const completeness = p && ds.profile.row_count ? 1 - p.missing / ds.profile.row_count : 1;
                  const warn = issuesFor(ds, c.key);
                  const sel = selection?.kind === "column" && selection.datasetId === ds.id && selection.key === c.key;
                  return (
                    <li key={c.key}>
                      <button
                        className={`schema-col ${sel ? "selected" : ""}`}
                        onClick={() => onSelect({ kind: "column", datasetId: ds.id, key: c.key })}
                        aria-pressed={sel}
                      >
                        <span className="glyph" style={{ color: t.color }} title={t.label}>{t.glyph}</span>
                        <span className="col-label">{c.label}</span>
                        <span className="col-tags">
                          {c.unit === "LAK" && <span className="tag">₭</span>}
                          {c.semantic_hint === "category" && <span className="tag">cat</span>}
                          {c.semantic_hint === "identifier" && <span className="tag">id</span>}
                          {warn.length > 0 && <span className="ember-dot" title={warn.map((w) => w.message).join("\n")} />}
                        </span>
                        <span className="meter" title={`${Math.round(completeness * 100)}% filled`}>
                          <span style={{ width: `${Math.max(2, completeness * 100)}%`, background: completeness < 0.8 ? "var(--ember)" : "var(--aurora)" }} />
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              {ds.lineage.excluded_rows.length > 0 && (
                <div className="schema-card-foot faint">
                  {ds.lineage.excluded_rows.length} non-data row{ds.lineage.excluded_rows.length > 1 ? "s" : ""} set aside (totals, notes, blanks) — none discarded
                </div>
              )}
            </motion.section>
          );
        })}
      </div>
    </div>
  );
}
