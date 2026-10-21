import { describe, expect, it } from "vitest";

import { pathAllowed } from "@/app/api/backend/[...path]/route";

describe("dashboard backend proxy allowlist", () => {
  it("allows only known backend prefixes", () => {
    expect(pathAllowed(["api", "v1"])).toBe(true);
    expect(pathAllowed(["api", "v1", "reviews"])).toBe(true);
    expect(pathAllowed(["healthz"])).toBe(true);
    expect(pathAllowed(["readyz"])).toBe(true);
  });

  it("rejects unrelated or prefix-confusion paths", () => {
    expect(pathAllowed([])).toBe(false);
    expect(pathAllowed(["api", "v2", "reviews"])).toBe(false);
    expect(pathAllowed(["api", "v1evil", "reviews"])).toBe(false);
    expect(pathAllowed(["healthz", "extra"])).toBe(false);
    expect(pathAllowed(["admin"])).toBe(false);
  });

  it("rejects traversal, encoded traversal and empty segments", () => {
    expect(pathAllowed(["api", "v1", "..", "admin"])).toBe(false);
    expect(pathAllowed(["api", "v1", "."])).toBe(false);
    expect(pathAllowed(["api", "v1", "%2e%2e"])).toBe(false);
    expect(pathAllowed(["api", "v1", "%2fetc"])).toBe(false);
    expect(pathAllowed(["api", "v1", "a%5cb"])).toBe(false);
    expect(pathAllowed(["api", "v1", "a%00b"])).toBe(false);
    expect(pathAllowed(["api", "v1", ""])).toBe(false);
    expect(pathAllowed(["api", "v1", "a\\b"])).toBe(false);
  });
});
