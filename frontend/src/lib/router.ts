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

/** Where the app opens: a running lecture wins, then an unfinished setup, otherwise the requested page. */
export function startRoute(boot: { active_run: unknown; configured: boolean }, hash: string): string {
  const route = parseRoute(hash);
  if (boot.active_run) return routeHash("record");
  if (!boot.configured && route.name !== "onboarding" && route.name !== "settings") return routeHash("onboarding");
  if (boot.configured && (route.name === "onboarding" || route.name === "record")) return routeHash("home");
  return hash || routeHash("home");
}
