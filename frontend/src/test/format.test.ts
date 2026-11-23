import { describe, expect, it } from "vitest";
import { fmtDate, fmtDuration, fmtLAK, fmtValue } from "../lib/format";

describe("format", () => {
  it("formats LAK with grouping and the kip sign", () => {
    expect(fmtLAK(1500000)).toBe("1,500,000 ₭");
    expect(fmtLAK(null)).toBe("—");
  });
  it("formats call durations as h:mm:ss, including > 24h", () => {
    expect(fmtDuration(330)).toBe("0:05:30");
    expect(fmtDuration(93600)).toBe("26:00:00");
    expect(fmtDuration(-61)).toBe("-0:01:01");
  });
  it("renders naive dataset dates without timezone shifting", () => {
    expect(fmtDate("2025-01-31T00:00:00.000")).toBe("2025-01-31");
    expect(fmtDate("2025-01-31T23:30:00.000", true)).toBe("2025-01-31 23:30");
  });
  it("formats by column type", () => {
    expect(fmtValue({ logical_type: "duration", unit: "seconds" }, 75)).toBe("0:01:15");
    expect(fmtValue({ logical_type: "integer", unit: "LAK" }, 2500)).toBe("2,500 ₭");
    expect(fmtValue({ logical_type: "number", unit: "percent" }, 0.125)).toBe("12.5%");
    expect(fmtValue({ logical_type: "text", unit: null }, "ວຽງຈັນ")).toBe("ວຽງຈັນ");
    expect(fmtValue({ logical_type: "text", unit: null }, null)).toBe("");
  });
});
