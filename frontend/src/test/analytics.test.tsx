import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, renderHook, screen } from "@testing-library/react";
import type { QueryResult } from "../api/types";
import { BarChart, ResultTable, SegmentedBar, _test, canDraw, toCsv, useEntityColors, SERIES } from "../components/analytics/Charts";
import { fmtChangePct, fmtPeriod, fmtResult } from "../lib/format";

function result(over: Partial<QueryResult> = {}): QueryResult {
  return {
    spec: { dataset_id: 1, measures: [{ aggregation: "sum", column: "cost" }], dimensions: [{ column: "branch" }], filters: [], limit: 200, analysis: "none" },
    description: "Total cost by branch.",
    columns: [
      { key: "d0", label: "Branch", kind: "dimension", type: "text", format: "text", grain: null, column: "branch" },
      { key: "m0", label: "Total cost", kind: "measure", type: "number", format: "lak", grain: null, column: null },
    ],
    rows: [["ສະຫວັນນະເຂດ", 85500], ["ຫຼວງພະບາງ", 80000], ["ວຽງຈັນ", 66000]],
    row_count: 3,
    truncated: false,
    matched_rows: 61,
    total_rows: 61,
    sql: 'SELECT "branch" AS d0, SUM("cost") AS m0 FROM data GROUP BY 1',
    dataset_id: 1,
    dataset_name: "Calls",
    dataset_version: 1,
    computed_at: "2026-11-24T07:00:00Z",
    elapsed_ms: 3,
    ...over,
  };
}

describe("result formatting", () => {
  it("formats by column format and time grain", () => {
    expect(fmtResult(85500, { format: "lak", grain: null })).toBe("85,500 ₭");
    expect(fmtResult(0.2623, { format: "percent", grain: null })).toBe("26.2%");
    expect(fmtResult(861, { format: "duration", grain: null })).toBe("14:21");
    expect(fmtResult(3725, { format: "duration", grain: null })).toBe("1:02:05");
    expect(fmtResult(null, { format: "text", grain: null })).toBe("(blank)");
    expect(fmtResult("2025-03-01", { format: "date", grain: "month" })).toBe("Mar 2025");
    expect(fmtPeriod("2025-04-01", "quarter")).toBe("Q2 2025");
    expect(fmtChangePct(0.3333)).toBe("+33.3%");
    expect(fmtChangePct(-0.0526)).toBe("−5.3%");
  });
});

describe("chart choice", () => {
  it("only draws a chart the data supports, otherwise the table", () => {
    const r = result();
    expect(canDraw("bar", r)).toBe(true);
    expect(canDraw("line", r)).toBe(false); // not a date dimension
    expect(canDraw("kpi_card", r)).toBe(false);
    expect(canDraw("bar", result({ rows: [["a", -5]] }))).toBe(false); // negative bars fall back to the table
  });
  it("nice axis maximum", () => {
    expect(_test.niceMax(87)).toBe(100);
    expect(_test.niceMax(24)).toBe(25);
  });
});

describe("bars", () => {
  it("click cross-filters with the source column and raw value; labels carry values", () => {
    const onPick = vi.fn();
    render(<BarChart result={result()} onPick={onPick} />);
    const btn = screen.getByRole("button", { name: /ຫຼວງພະບາງ: 80,000 ₭/ });
    fireEvent.click(btn);
    expect(onPick).toHaveBeenCalledWith({ column: "branch", value: "ຫຼວງພະບາງ" });
  });
  it("selected bar is pressed and the others are dimmed", () => {
    render(<BarChart result={result()} onPick={() => {}} selected="ວຽງຈັນ" />);
    expect(screen.getByRole("button", { name: /ວຽງຈັນ/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: /ສະຫວັນນະເຂດ/ }).className).toContain("dim");
  });
});

describe("part-to-whole", () => {
  it("folds beyond six parts into Other and lists shares", () => {
    const rows = Array.from({ length: 8 }, (_, i) => [`t${i}`, 10 - i]);
    const colorOf = (k: string) => (k === "t0" ? SERIES[0] : SERIES[1]);
    render(<SegmentedBar result={result({ rows })} colorOf={colorOf} />);
    expect(screen.getByText("Other")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
  });
});

describe("entity colours", () => {
  it("follow the entity, never its rank, and never cycle", () => {
    const { result: hook } = renderHook(() => useEntityColors());
    const colorOf = hook.current;
    const a = colorOf("ວຽງຈັນ");
    const b = colorOf("ປາກເຊ");
    expect(colorOf("ວຽງຈັນ")).toBe(a);
    expect(a).not.toBe(b);
    for (let i = 0; i < 10; i++) colorOf(`x${i}`);
    expect(colorOf("x9")).toBe("#5b6782"); // past eight slots: neutral, not a repeated hue
  });
});

describe("table and CSV", () => {
  it("renders every row and exports UTF-8 CSV with a BOM for Excel", () => {
    render(<ResultTable result={result()} />);
    expect(screen.getByText("85,500 ₭")).toBeTruthy();
    const csv = toCsv(result({ rows: [['ທ່ານ "A", B', 1]] }));
    expect(csv.startsWith("﻿Branch,Total cost")).toBe(true);
    expect(csv).toContain('"ທ່ານ ""A"", B",1');
  });
});
