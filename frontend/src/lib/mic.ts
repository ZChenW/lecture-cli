// Plan GUI-4 Q1.5: the live input level (GET /api/mic/level) and the 5-second microphone test.
// fetch rather than EventSource, so a refusal (409 during a lecture, 503 for a device that cannot
// open) arrives with the server's own message.
import type { MicLevel, MicTestResult } from "./types";

export const HINT = "说句话，电平条应该跟着动";
export const STILL_HINT = "麦克风可能没有在工作：声音没有变化";
export const FAILED = "没有检测到明显的声音变化";
export const ADVICE = [
  "确认选对了输入设备，并且没有被静音、输入音量不是 0",
  "ThinkPad 等笔记本请在 BIOS 的 Security → I/O Port Access 里确认 Microphone 已启用",
  "可以改用耳机麦克风、USB 麦克风，或用手机录音后在终端用 --audio-file 导入",
];
/** The bar spans -70 to 0 dBFS in this many cells. */
export const CELLS = 28;
const LOW_DBFS = -70;

/** Share of the bar for a level in dBFS: -70 and below is empty, 0 is full. */
export function levelShareDb(dbfs: number): number {
  return Math.max(0, Math.min(1, (dbfs - LOW_DBFS) / -LOW_DBFS));
}

/** How many cells are lit, and which cell holds the recent peak (-1 for none). */
export function cells(level: MicLevel | null, peakHold: number | null): { lit: number; peak: number } {
  const lit = level ? Math.round(levelShareDb(level.rms) * CELLS) : 0;
  const peak = peakHold == null ? -1 : Math.min(CELLS - 1, Math.round(levelShareDb(peakHold) * CELLS) - 1);
  return { lit, peak: peak >= lit ? peak : -1 };
}

export function resultText(result: MicTestResult): string {
  return result.passed
    ? `麦克风正常：讲话时比底噪高出 ${Math.round(result.rise)} dB`
    : `${FAILED}：讲话时只比底噪高出 ${Math.max(0, Math.round(result.rise))} dB`;
}

export interface LevelHandlers {
  level?: (level: MicLevel) => void;
  still?: (still: boolean) => void;
  result?: (result: MicTestResult) => void;
  /** A lecture started; the server let go of the microphone. */
  busy?: () => void;
  error?: (message: string) => void;
  /** The stream ended (after a test's result, a busy event, an error or close()). */
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

export function watchLevel(handlers: LevelHandlers, { test = false, fetcher = fetch }: { test?: boolean; fetcher?: typeof fetch } = {}): { close(): void } {
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
      const response = await fetcher(`/api/mic/level${test ? "?test=1" : ""}`, { signal: abort.signal, credentials: "same-origin" });
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
          else if (parsed.event === "still") handlers.still?.((parsed.data as { still: boolean }).still);
          else if (parsed.event === "result") handlers.result?.(parsed.data as MicTestResult);
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
