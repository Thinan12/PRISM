import type { ColumnMeta, LogicalType } from "../api/types";

export const TZ = "Asia/Vientiane";

const intFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const decFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export function fmtInt(n: number | null | undefined): string {
  return n == null ? "—" : intFmt.format(n);
}

export function fmtNumber(n: number | null | undefined): string {
  return n == null ? "—" : decFmt.format(n);
}

export function fmtLAK(n: number | null | undefined): string {
  return n == null ? "—" : `${intFmt.format(Math.round(n))} ₭`;
}

export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(sec)) return "—";
  const s = Math.round(Math.abs(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return `${sec < 0 ? "-" : ""}${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

/** Dataset dates are timezone-naive local (Asia/Vientiane) values: render as-is. */
export function fmtDate(v: string | null | undefined, withTime = false): string {
  if (!v) return "—";
  const m = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(v);
  if (!m) return v;
  return withTime && m[2] ? `${m[1]} ${m[2]}` : m[1];
}

export function fmtTimestamp(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: TZ,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

export function fmtValue(col: Pick<ColumnMeta, "logical_type" | "unit"> | undefined, v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  if (!col) return String(v);
  switch (col.logical_type) {
    case "duration":
      return fmtDuration(Number(v));
    case "date":
      return fmtDate(String(v));
    case "datetime":
      return fmtDate(String(v), true);
    case "integer":
    case "number":
      if (col.unit === "LAK") return fmtLAK(Number(v));
      if (col.unit === "percent") return `${decFmt.format(Number(v) * 100)}%`;
      return col.logical_type === "integer" ? fmtInt(Number(v)) : fmtNumber(Number(v));
    case "boolean":
      return v ? "Yes" : "No";
    default:
      return String(v);
  }
}

export function isNumericType(t: LogicalType): boolean {
  return t === "integer" || t === "number" || t === "duration";
}

export const TYPE_META: Record<LogicalType, { glyph: string; label: string; color: string }> = {
  text: { glyph: "Aa", label: "Text", color: "var(--t-text)" },
  integer: { glyph: "#", label: "Integer", color: "var(--t-number)" },
  number: { glyph: "1.5", label: "Number", color: "var(--t-number)" },
  boolean: { glyph: "✓", label: "Yes/No", color: "var(--t-boolean)" },
  date: { glyph: "▦", label: "Date", color: "var(--t-date)" },
  datetime: { glyph: "◷", label: "Date & time", color: "var(--t-date)" },
  duration: { glyph: "⧗", label: "Duration", color: "var(--t-duration)" },
  phone: { glyph: "☏", label: "Phone", color: "var(--t-phone)" },
  empty: { glyph: "∅", label: "Empty", color: "var(--t-empty)" },
};

export function fmtKpi(k: { value: number | null; format: "number" | "lak" | "duration" | "percent" }): string {
  if (k.value == null) return "—";
  switch (k.format) {
    case "lak":
      return fmtLAK(k.value);
    case "duration":
      return fmtDuration(k.value);
    case "percent":
      return `${fmtNumber(k.value * 100)}%`;
    default:
      return Number.isInteger(k.value) ? fmtInt(k.value) : fmtNumber(k.value);
  }
}
