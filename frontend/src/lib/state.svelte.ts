import { ApiError, api } from "./api";
import type { Bootstrap } from "./types";

/** The bootstrap payload, shared by every page and refreshed after saves that change it. */
// course: the home page selection, kept while visiting settings or a note.
export const app = $state<{ boot: Bootstrap | null; course: string | null }>({ boot: null, course: null });

export async function refresh(): Promise<Bootstrap> {
  app.boot = await api.bootstrap();
  return app.boot;
}

export function message(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error);
}
