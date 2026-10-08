export type RouteName = "onboarding" | "home" | "record" | "notes" | "settings";
export interface Route { name: RouteName; params: string[] }

/** Hash routes: #/onboarding, #/, #/record, #/notes/<课程>/<文件>, #/settings. */
export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const name = parts[0] ?? "";
  if (name === "onboarding" || name === "record" || name === "settings") return { name, params: [] };
  if (name === "notes" && parts.length === 3) return { name, params: parts.slice(1) };
  return { name: "home", params: [] };
}

export function routeHash(name: RouteName, ...params: string[]): string {
  return name === "home" ? "#/" : `#/${[name, ...params].map(encodeURIComponent).join("/")}`;
}

export function navigate(name: RouteName, ...params: string[]): void {
  location.hash = routeHash(name, ...params);
}

/**
 * Where the app opens: a running lecture wins; a first start that was neither finished nor skipped
 * goes to the wizard. Home is not gated on a complete setup (plan N2.4): it says what is missing.
 */
export function startRoute(boot: { active_run: unknown; configured: boolean; config?: { onboarded?: unknown } },
  hash: string): string {
  const route = parseRoute(hash);
  if (boot.active_run) return routeHash("record");
  const fresh = !boot.configured && !boot.config?.onboarded;
  if (fresh && route.name !== "onboarding" && route.name !== "settings") return routeHash("onboarding");
  if (route.name === "record") return routeHash("home");
  return hash || routeHash("home");
}
