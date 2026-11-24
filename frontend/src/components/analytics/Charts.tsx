import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ChartKind, QueryResult } from "../../api/types";
import { fmtChangePct, fmtInt, fmtResult } from "../../lib/format";

/* Chart marks follow the data-viz spec: one validated categorical order (dark steps checked
   against the PRISM hull surface), thin bars with a rounded data end, 2px lines, rings on dots,
   values in text colours, hover + keyboard tooltips, and a table view for every chart. */
export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"];
const OTHER = "#5b6782";

export type Pick_ = { column: string; value: unknown };

type Row = Record<string, unknown>;
const asRows = (r: QueryResult): Row[] => r.rows.map((row) => Object.fromEntries(r.columns.map((c, i) => [c.key, row[i]])));

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    setW(el.clientWidth || 600);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function Tip({ x, y, children, width }: { x: number; y: number; children: ReactNode; width: number }) {
  const left = Math.min(Math.max(8, x + 12), Math.max(8, width - 190));
  return (
    <div className="viz-tip" style={{ left, top: Math.max(0, y - 8) }} role="status">
      {children}
    </div>
  );
}

// ---------------------------------------------------------------- stat tile

export function StatTile({ result, label }: { result: QueryResult; label: string }) {
  const m = result.columns.find((c) => c.kind === "measure")!;
  const v = result.rows[0]?.[result.columns.indexOf(m)];
  const filtered = result.matched_rows !== result.total_rows;
  return (
    <div className="stat-tile">
      <div className="stat-tile-value">{v === null || v === undefined ? "—" : fmtResult(v, m)}</div>
      <div className="stat-tile-label">{label}</div>
      {filtered && <div className="stat-tile-sub">from {fmtInt(result.matched_rows)} of {fmtInt(result.total_rows)} records</div>}
    </div>
  );
}

// ---------------------------------------------------------------- bars (horizontal: long Lao labels read well)

export function BarChart({ result, selected, onPick }: { result: QueryResult; selected?: unknown; onPick?: (p: Pick_) => void }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const d = result.columns.find((c) => c.kind === "dimension")!;
  const m = result.columns.find((c) => c.kind === "measure")!;
  const share = result.columns.find((c) => c.kind === "share");
  const rows = asRows(result);
  const max = Math.max(0, ...rows.map((r) => Number(r[m.key] ?? 0)));
  const hasSel = selected !== undefined;
  return (
    <div className="viz-bars" ref={ref} onPointerLeave={() => setHover(null)}>
      {rows.map((r, i) => {
        const v = Number(r[m.key] ?? 0);
        const pct = max > 0 ? Math.max(0, v) / max : 0;
        const isSel = hasSel && r[d.key] === selected;
        const label = fmtResult(r[d.key], d);
        return (
          <button
            key={i}
            type="button"
            className={`viz-bar-row ${hasSel && !isSel ? "dim" : ""}`}
            aria-label={`${label}: ${fmtResult(r[m.key], m)}${onPick ? ". Select to filter the dashboard" : ""}`}
            aria-pressed={onPick ? isSel : undefined}
            onClick={() => onPick && d.column && onPick({ column: d.column, value: r[d.key] })}
            onPointerMove={(e) => {
              const box = (ref.current as HTMLDivElement).getBoundingClientRect();
              setHover({ i, x: e.clientX - box.left, y: e.clientY - box.top });
            }}
            onFocus={(e) => setHover({ i, x: 120, y: (e.currentTarget as HTMLElement).offsetTop })}
            onBlur={() => setHover(null)}
          >
            <span className="viz-bar-label" title={label}>{label}</span>
            <span className="viz-bar-track">
              {/* the plot keeps room on the right for the tip label, so bar length stays proportional */}
              <span className="viz-bar-plot">
                <span className="viz-bar" style={{ width: `${(pct * 100).toFixed(2)}%`, background: SERIES[0] }} />
                <span className="viz-bar-value">{fmtResult(r[m.key], m)}</span>
              </span>
            </span>
          </button>
        );
      })}
      {hover && rows[hover.i] && (
        <Tip x={hover.x} y={hover.y} width={width}>
          <strong>{fmtResult(rows[hover.i][m.key], m)}</strong>
          <span>{fmtResult(rows[hover.i][d.key], d)}</span>
          {share && <span className="faint">{fmtResult(rows[hover.i][share.key], share)} of total</span>}
        </Tip>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- line (time) with crosshair; anomalies in Ember

export function LineChart({ result }: { result: QueryResult }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [idx, setIdx] = useState<number | null>(null);
  const d = result.columns.find((c) => c.kind === "dimension")!;
  const m = result.columns.find((c) => c.kind === "measure")!;
  const anomaly = result.columns.some((c) => c.kind === "anomaly");
  const changePct = result.columns.find((c) => c.kind === "change_pct");
  const rows = asRows(result).filter((r) => r[d.key] !== null);
  const H = 220;
  const pad = { l: 8, r: 64, t: 14, b: 26 };
  const xs = rows.map((r) => Date.parse(String(r[d.key])));
  const ys = rows.map((r) => Number(r[m.key] ?? 0));
  const typical = anomaly && rows.length ? Number(rows[0].typical ?? NaN) : NaN;
  const yMaxRaw = Math.max(1, ...ys, Number.isFinite(typical) ? typical : 0);
  const yMax = niceMax(yMaxRaw);
  const yMin = Math.min(0, ...ys);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const sx = (x: number) => pad.l + (x1 === x0 ? (width - pad.l - pad.r) / 2 : ((x - x0) / (x1 - x0)) * (width - pad.l - pad.r));
  const sy = (y: number) => pad.t + (1 - (y - yMin) / (yMax - yMin || 1)) * (H - pad.t - pad.b);
  const path = rows.map((_, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(ys[i]).toFixed(1)}`).join("");
  const integral = ys.every((v) => Number.isInteger(v));
  const ticks = [0, 0.5, 1].map((f) => yMin + f * (yMax - yMin)).map((t) => (integral ? Math.round(t) : t));
  const last = rows.length - 1;
  const flagged = anomaly ? rows.map((r, i) => (r.anomaly ? i : -1)).filter((i) => i >= 0) : [];
  const xLabels = rows.length > 2 ? [0, Math.floor(last / 2), last] : rows.map((_, i) => i);

  function onMove(clientX: number) {
    const box = (ref.current as HTMLDivElement).getBoundingClientRect();
    const x = clientX - box.left;
    let best = 0;
    for (let i = 1; i < rows.length; i++) if (Math.abs(sx(xs[i]) - x) < Math.abs(sx(xs[best]) - x)) best = i;
    setIdx(best);
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowRight") setIdx((i) => Math.min(last, (i ?? -1) + 1));
    else if (e.key === "ArrowLeft") setIdx((i) => Math.max(0, (i ?? last + 1) - 1));
    else if (e.key === "Home") setIdx(0);
    else if (e.key === "End") setIdx(last);
    else return;
    e.preventDefault();
  }

  if (!rows.length) return <div className="faint viz-empty">No dated records.</div>;
  return (
    <div className="viz-line" ref={ref}>
      <svg
        width={width}
        height={H}
        role="img"
        tabIndex={0}
        aria-label={`${m.label} by ${d.label}, ${rows.length} points. Use arrow keys to read values.`}
        onPointerMove={(e) => onMove(e.clientX)}
        onPointerLeave={() => setIdx(null)}
        onKeyDown={onKey}
        onBlur={() => setIdx(null)}
      >
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={pad.l} x2={width - pad.r} y1={sy(t)} y2={sy(t)} className="viz-grid" />
            {i > 0 && <text x={pad.l} y={sy(t) - 4} className="viz-axis">{fmtResult(t, m)}</text>}
          </g>
        ))}
        {Number.isFinite(typical) && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={sy(typical)} y2={sy(typical)} className="viz-ref" />
            <text x={width - pad.r} y={sy(typical) - 4} className="viz-axis" textAnchor="end">typical {fmtResult(typical, m)}</text>
          </g>
        )}
        <path d={path} fill="none" stroke={SERIES[0]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {rows.length <= 40 && rows.map((_, i) => <circle key={i} cx={sx(xs[i])} cy={sy(ys[i])} r={2.5} fill={SERIES[0]} />)}
        {flagged.map((i) => (
          <circle key={`a${i}`} cx={sx(xs[i])} cy={sy(ys[i])} r={5} fill="var(--ember)" stroke="var(--hull-1)" strokeWidth={2}>
            <title>Unusual: {fmtResult(rows[i][d.key], d)}</title>
          </circle>
        ))}
        <circle cx={sx(xs[last])} cy={sy(ys[last])} r={4} fill={SERIES[0]} stroke="var(--hull-1)" strokeWidth={2} />
        <text x={sx(xs[last]) + 8} y={sy(ys[last]) - 8} className="viz-value">{fmtResult(ys[last], m)}</text>
        {xLabels.map((i) => (
          <text key={`x${i}`} x={sx(xs[i])} y={H - 6} className="viz-axis" textAnchor={i === 0 ? "start" : i === last ? "end" : "middle"}>
            {fmtResult(rows[i][d.key], d)}
          </text>
        ))}
        {idx !== null && <line x1={sx(xs[idx])} x2={sx(xs[idx])} y1={pad.t} y2={H - pad.b} className="viz-cross" />}
        {idx !== null && <circle cx={sx(xs[idx])} cy={sy(ys[idx])} r={4} fill={SERIES[0]} stroke="var(--hull-1)" strokeWidth={2} />}
      </svg>
      {flagged.length > 0 && (
        <div className="viz-legend">
          <span><i className="viz-key-dot" style={{ background: "var(--ember)" }} />⚠ unusual {d.grain ?? "period"} ({flagged.length})</span>
        </div>
      )}
      {idx !== null && rows[idx] && (
        <Tip x={sx(xs[idx])} y={sy(ys[idx])} width={width}>
          <strong>{fmtResult(rows[idx][m.key], m)}</strong>
          <span>{fmtResult(rows[idx][d.key], d)}</span>
          {changePct && idx > 0 && <span className="faint">{fmtChangePct(rows[idx][changePct.key])} vs previous</span>}
          {anomaly && rows[idx].anomaly ? <span className="ember-text">⚠ unusual (z = {Number(rows[idx].z).toFixed(1)})</span> : null}
        </Tip>
      )}
    </div>
  );
}

function niceMax(v: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const k of [1, 2, 2.5, 5, 10]) if (k * p >= v) return k * p;
  return 10 * p;
}

// ---------------------------------------------------------------- part-to-whole: tall segmented bar (≤ 6 parts + Other)

export function SegmentedBar({ result, colorOf, selected, onPick }: {
  result: QueryResult;
  colorOf: (key: string) => string;
  selected?: unknown;
  onPick?: (p: Pick_) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const d = result.columns.find((c) => c.kind === "dimension")!;
  const m = result.columns.find((c) => c.kind === "measure")!;
  const rows = asRows(result);
  const total = rows.reduce((s, r) => s + Number(r[m.key] ?? 0), 0);
  const parts = useMemo(() => {
    const sorted = [...rows].sort((a, b) => Number(b[m.key] ?? 0) - Number(a[m.key] ?? 0));
    const head = sorted.slice(0, sorted.length > 6 ? 5 : 6).map((r) => ({ key: String(r[d.key]), raw: r[d.key], v: Number(r[m.key] ?? 0), other: false }));
    const rest = sorted.slice(head.length).reduce((s, r) => s + Number(r[m.key] ?? 0), 0);
    if (sorted.length > head.length) head.push({ key: "Other", raw: undefined, v: rest, other: true });
    return head;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);
  if (total <= 0) return <div className="faint viz-empty">Nothing to divide.</div>;
  const hasSel = selected !== undefined;
  return (
    <div className="viz-seg">
      <div className="viz-seg-bar" onPointerLeave={() => setHover(null)}>
        {parts.map((p, i) => (
          <button
            key={p.key}
            type="button"
            className={`viz-seg-part ${hasSel && p.raw !== selected ? "dim" : ""}`}
            style={{ flexGrow: p.v, background: p.other ? OTHER : colorOf(p.key) }}
            aria-label={`${p.other ? "Other" : fmtResult(p.raw, d)}: ${fmtResult(p.v, m)}, ${((p.v / total) * 100).toFixed(1)}%`}
            onPointerEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
            onClick={() => !p.other && onPick && d.column && onPick({ column: d.column, value: p.raw })}
          />
        ))}
      </div>
      <ul className="viz-seg-legend">
        {parts.map((p, i) => (
          <li key={p.key} className={hover === i ? "hot" : ""}>
            <i className="viz-key-line" style={{ background: p.other ? OTHER : colorOf(p.key) }} />
            <span className="name">{p.other ? "Other" : fmtResult(p.raw, d)}</span>
            <span className="num">{fmtResult(p.v, m)}</span>
            <span className="num faint">{((p.v / total) * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- table view (every chart has one)

export function ResultTable({ result, max = 200 }: { result: QueryResult; max?: number }) {
  const cols = result.columns.filter((c) => c.kind !== "z");
  const idx = cols.map((c) => result.columns.indexOf(c));
  return (
    <div className="viz-table-wrap">
      <table className="viz-table">
        <thead>
          <tr>{cols.map((c) => <th key={c.key} className={c.kind === "dimension" ? "" : "num"}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {result.rows.slice(0, max).map((r, ri) => (
            <tr key={ri} className={cols.some((c, i) => c.kind === "anomaly" && r[idx[i]]) ? "anomalous" : ""}>
              {cols.map((c, i) => (
                <td key={c.key} className={c.kind === "dimension" ? "" : "num"}>
                  {c.kind === "change_pct" ? fmtChangePct(r[idx[i]]) : c.kind === "anomaly" ? (r[idx[i]] ? "⚠ yes" : "") : fmtResult(r[idx[i]], c)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {(result.truncated || result.rows.length > max) && <div className="faint viz-note">Showing the first {Math.min(max, result.rows.length)} rows.</div>}
    </div>
  );
}

// ---------------------------------------------------------------- chooser

export function canDraw(chart: ChartKind, r: QueryResult): boolean {
  const dims = r.columns.filter((c) => c.kind === "dimension");
  const meas = r.columns.filter((c) => c.kind === "measure");
  if (chart === "kpi_card") return dims.length === 0 && meas.length >= 1 && r.rows.length === 1;
  if (chart === "line") return dims.length === 1 && (dims[0].type === "date" || dims[0].type === "datetime");
  if (chart === "bar" || chart === "donut") {
    if (dims.length !== 1 || meas.length < 1) return false;
    const mi = r.columns.indexOf(meas[0]);
    return r.rows.every((row) => row[mi] === null || Number(row[mi]) >= 0);
  }
  return false;
}

export function Chart({ chart, result, title, colorOf, selected, onPick }: {
  chart: ChartKind;
  result: QueryResult;
  title: string;
  colorOf: (key: string) => string;
  selected?: unknown;
  onPick?: (p: Pick_) => void;
}) {
  if (!result.rows.length) return <div className="faint viz-empty">No records match.</div>;
  if (!canDraw(chart, result)) return <ResultTable result={result} />;
  if (chart === "kpi_card") return <StatTile result={result} label={title} />;
  if (chart === "line") return <LineChart result={result} />;
  if (chart === "donut") return <SegmentedBar result={result} colorOf={colorOf} selected={selected} onPick={onPick} />;
  return <BarChart result={result} selected={selected} onPick={onPick} />;
}

/** Stable colour per entity (colour follows the entity, never its rank — filters don't repaint
 *  survivors). Slots are never cycled: entities past the eighth get the neutral "Other" grey. */
export function useEntityColors(): (key: string) => string {
  const map = useRef(new Map<string, string>());
  return (key: string) => {
    let c = map.current.get(key);
    if (!c) {
      c = map.current.size < SERIES.length ? SERIES[map.current.size] : OTHER;
      map.current.set(key, c);
    }
    return c;
  };
}

export function toCsv(r: QueryResult): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return "﻿" + [r.columns.map((c) => esc(c.label)).join(","), ...r.rows.map((row) => row.map(esc).join(","))].join("\n");
}

export const _test = { niceMax };
