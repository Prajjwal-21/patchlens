import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatCount,
  formatDuration,
  formatMetric,
  formatPercent,
  humanizeStatus,
  pluralize,
  safeHttpUrl,
  severityTone,
  shortSha,
  statusTone,
  titleCase,
  verificationTone,
} from "@/lib/format";

describe("formatMetric", () => {
  it("renders missing metrics as UNKNOWN rather than zero", () => {
    expect(formatMetric(null)).toBe("UNKNOWN");
    expect(formatMetric(undefined)).toBe("UNKNOWN");
    expect(formatMetric("")).toBe("UNKNOWN");
    expect(formatMetric("0.5")).toBe("UNKNOWN");
    expect(formatMetric(Number.NaN)).toBe("UNKNOWN");
    expect(formatMetric(Number.POSITIVE_INFINITY)).toBe("UNKNOWN");
  });

  it("formats real numbers, including an explicit zero", () => {
    expect(formatMetric(0)).toBe("0.000");
    expect(formatMetric(0.81234)).toBe("0.812");
    expect(formatMetric(0.5, 2)).toBe("0.50");
  });
});

describe("formatPercent", () => {
  it("does not invent a percentage for missing values", () => {
    expect(formatPercent(null)).toBe("UNKNOWN");
    expect(formatPercent(undefined)).toBe("UNKNOWN");
    expect(formatPercent(Number.NaN)).toBe("UNKNOWN");
  });

  it("formats a ratio as a percentage", () => {
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatPercent(0.756)).toBe("75.6%");
  });
});

describe("formatDuration", () => {
  it("returns a dash for unknown durations", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
  });

  it("chooses a readable unit", () => {
    expect(formatDuration(0.4)).toBe("<1 ms");
    expect(formatDuration(42)).toBe("42 ms");
    expect(formatDuration(1_500)).toBe("1.5 s");
    expect(formatDuration(61_000)).toBe("1m 1s");
  });
});

describe("small presentation helpers", () => {
  it("formats counts without turning zero into a dash", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(null)).toBe("—");
    expect(formatCount(1_234)).toBe("1,234");
  });

  it("shortens SHAs and leaves short values alone", () => {
    expect(shortSha("0123456789abcdef")).toBe("012345678");
    expect(shortSha("abc")).toBe("abc");
    expect(shortSha(null)).toBe("—");
  });

  it("title-cases snake and kebab identifiers", () => {
    expect(titleCase("needs_manual_verification")).toBe("Needs Manual Verification");
    expect(titleCase("test-generation")).toBe("Test Generation");
  });

  it("pluralizes only for counts other than one", () => {
    expect(pluralize(1, "finding")).toBe("1 finding");
    expect(pluralize(2, "finding")).toBe("2 findings");
    expect(pluralize(2, "entry", "entries")).toBe("2 entries");
  });

  it("formats byte sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2_048)).toBe("2.0 KiB");
    expect(formatBytes(2_097_152)).toBe("2.0 MiB");
  });
});

describe("safeHttpUrl", () => {
  it("accepts absolute http(s) URLs only", () => {
    expect(safeHttpUrl("https://github.com/acme/repo/pull/1")).toBe(
      "https://github.com/acme/repo/pull/1",
    );
    expect(safeHttpUrl("http://example.test/x")).toBe("http://example.test/x");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,hi")).toBeNull();
    expect(safeHttpUrl("/relative/path")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });
});

describe("status vocabulary", () => {
  it("humanizes known states and preserves explicit non-results", () => {
    expect(humanizeStatus("not_run")).toBe("NOT RUN");
    expect(humanizeStatus("ai_suggested")).toBe("AI suggested");
    expect(humanizeStatus("execution_disabled")).toBe("Not executed");
    expect(humanizeStatus("succeeded")).toBe("Succeeded");
  });

  it("maps tones for statuses, severities and verification states", () => {
    expect(statusTone("failed")).toBe("danger");
    expect(statusTone("indexed")).toBe("success");
    expect(statusTone("execution_disabled")).toBe("neutral");
    expect(severityTone("critical")).toBe("danger");
    expect(severityTone("low")).toBe("info");
    expect(verificationTone("verified")).toBe("success");
    expect(verificationTone("needs_manual_verification")).toBe("warning");
    expect(verificationTone("unknown-value")).toBe("neutral");
  });
});
