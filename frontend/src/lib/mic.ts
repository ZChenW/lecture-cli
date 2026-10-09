// The live input level (GET /api/mic/level) and its verdict (PLAN-GUI-5 R2.3). fetch rather than
// EventSource, so a refusal (409 during a lecture, 503 for a device that cannot open) arrives with
// the server's own message. PLAN-GUI-5 R2.7: no speak-now check any more; a lecture sets its own volume.
import type { MicLevel, MicVerdict } from "./types";

export const STILL_HINT = "麦克风可能没有在工作：声音没有变化";
/** The bar spans -60 to 0 dBFS in this many cells, linear in decibels (R2.3). */
export const CELLS = 28;
export const LOW_DBFS = -60;
/** "过载" while any level of the last second clipped: ten 100 ms levels. */
export const OVERLOAD_LEVELS = 10;

/** Share of the bar for a level in dBFS: -60 and below is empty, 0 is full. */
export function levelShareDb(dbfs: number): number {
  return Math.max(0, Math.min(1, (dbfs - LOW_DBFS) / -LOW_DBFS));
}

/** How many cells are lit, and which cell holds the recent peak (-1 for none). */
export function cells(level: MicLevel | null, peakHold: number | null): { lit: number; peak: number } {
  const lit = level ? Math.round(levelShareDb(level.rms) * CELLS) : 0;
  const peak = peakHold == null ? -1 : Math.min(CELLS - 1, Math.round(levelShareDb(peakHold) * CELLS) - 1);
  return { lit, peak: peak >= lit ? peak : -1 };
}

/** Whether the recent levels (oldest first) clipped within the last second. */
export function overloaded(recent: MicLevel[]): boolean {
  return recent.slice(-OVERLOAD_LEVELS).some((level) => level.clipped === true);
}

export interface LevelHandlers {
  level?: (level: MicLevel) => void;
  /** The server's verdict on the first 2 s; text is empty when the microphone is fine. */
  verdict?: (verdict: MicVerdict) => void;
  /** A lecture started; the server let go of the microphone. */
  busy?: () => void;
  error?: (message: string) => void;
  /** The stream ended (after a busy event, an error or close()). */
  end?: () => void;
}

/** Parses one server-sent event block ("event: x\ndata: {...}"); comments give null. */
export function parseEvent(block: string): { event: string; data: unknown } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    else if (line.startsWith("data: ")) data.push(line.slice(6));
  }
  return data.length ? { event, data: JSON.parse(data.join("\n")) } : null;
}

export function watchLevel(handlers: LevelHandlers, { fetcher = fetch }: { fetcher?: typeof fetch } = {}): { close(): void } {
  const abort = new AbortController();
  let ended = false;
  const end = () => {
    if (!ended) {
      ended = true;
      handlers.end?.();
    }
  };
  (async () => {
    try {
      const response = await fetcher("/api/mic/level", { signal: abort.signal, credentials: "same-origin" });
      if (!response.ok || !response.body) {
        let text = `HTTP ${response.status}`;
        try {
          text = (await response.json())?.error?.message ?? text;
        } catch { /* not JSON */ }
        handlers.error?.(text);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut: number;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const parsed = parseEvent(buffer.slice(0, cut));
          buffer = buffer.slice(cut + 2);
          if (!parsed) continue;
          if (parsed.event === "level") handlers.level?.(parsed.data as MicLevel);
          else if (parsed.event === "verdict") handlers.verdict?.(parsed.data as MicVerdict);
          else if (parsed.event === "busy") handlers.busy?.();
        }
      }
    } catch (e) {
      if (!abort.signal.aborted) handlers.error?.("无法读取麦克风电平");
    } finally {
      end();
    }
  })();
  return {
    close() {
      abort.abort();
      end();
    },
  };
}
