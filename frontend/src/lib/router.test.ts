import { describe, expect, it } from "vitest";
import { parseRoute, routeHash, startRoute } from "./router";

describe("hash router", () => {
  it("parses the five routes and falls back to home", () => {
    expect(parseRoute("")).toEqual({ name: "home", params: [] });
    expect(parseRoute("#/")).toEqual({ name: "home", params: [] });
    expect(parseRoute("#/onboarding").name).toBe("onboarding");
    expect(parseRoute("#/record").name).toBe("record");
    expect(parseRoute("#/settings").name).toBe("settings");
    expect(parseRoute("#/nowhere")).toEqual({ name: "home", params: [] });
  });

  it("round-trips note routes with Chinese and spaces", () => {
    const hash = routeHash("notes", "数学 421", "2026-10-07_143000-课堂笔记-a1b2c3.md");
    expect(parseRoute(hash)).toEqual({ name: "notes", params: ["数学 421", "2026-10-07_143000-课堂笔记-a1b2c3.md"] });
  });

  it("opens a running lecture first, then a first start that was not skipped, else the requested page", () => {
    const fresh = { active_run: null, configured: false, config: { onboarded: false } };
    const skipped = { active_run: null, configured: false, config: { onboarded: true } };
    const ready = { active_run: null, configured: true, config: { onboarded: false } };
    expect(startRoute({ active_run: { phase: "recording" }, configured: false }, "#/settings")).toBe("#/record");
    expect(startRoute(fresh, "")).toBe("#/onboarding");
    expect(startRoute(fresh, "#/")).toBe("#/onboarding");
    expect(startRoute(fresh, "#/settings")).toBe("#/settings");
    // Skipping the wizard opens home even though settings are incomplete.
    expect(startRoute(skipped, "")).toBe("#/");
    expect(startRoute(skipped, "#/notes/a/b.md")).toBe("#/notes/a/b.md");
    expect(startRoute(skipped, "#/record")).toBe("#/");
    expect(startRoute(ready, "")).toBe("#/");
    expect(startRoute(ready, "#/record")).toBe("#/");
    expect(startRoute(ready, "#/settings")).toBe("#/settings");
    // The wizard can be reopened on purpose.
    expect(startRoute(ready, "#/onboarding")).toBe("#/onboarding");
  });
});
