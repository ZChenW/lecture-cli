// View models for the recording and closing screens; pure so they can be tested without a DOM.
import { formatElapsed } from "./format";
import type { Snapshot } from "./types";

export const WAVE_BARS = 45;  // The reference waveform: x = 4, 12, … 356 in a 360-wide view box.
export const BACKLOG_WARN_SECONDS = 30;
export const CLOSING_PHASES = ["draining", "refining", "finalizing", "saving"];

/** Bar height share, on the terminal meter's scale (level × 150 / 20). */
export function levelShare(level: number): number {
  return Math.max(0, Math.min(1, (level || 0) * 7.5));
}

export function pushLevel(samples: number[], level: number): number[] {
  return [...samples, levelShare(level)].slice(-WAVE_BARS);
}

/**
 * One path per bar, filling from the left as in the reference, where the slots still to come are
 * short and faint; once all 45 are sampled the newest sits at the right and older ones scroll off.
 */
export function waveBars(samples: number[], paused: boolean): { d: string; sampled: boolean }[] {
  return Array.from({ length: WAVE_BARS }, (_, i) => {
    const sampled = i < samples.length;
    const share = sampled && !paused ? samples[i] : 0;
    const half = 2 + 22 * share;  // 4 to 48 units tall, centred on y = 28 as in the reference.
    return { d: `M${4 + 8 * i} ${28 - half}v${2 * half}`, sampled };
  });
}

/** "00:46:58.20" → "46:58"; "01:02:03.00" → "1:02:03". */
export function segmentTime(stamp: string | null | undefined): string {
  const match = stamp?.match(/^(\d+):(\d{2}):(\d{2})/);
  return match ? formatElapsed(Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3])) : "";
}

export interface Lyrics { older: string[]; current: string; live: boolean; marker: string; key: string }

/** Four older lines (oldest first) and the large current line: the pending text, else the last confirmed segment. */
export function lyrics(transcript: Snapshot["transcript"]): Lyrics {
  const tail = transcript.tail.filter((segment) => segment.text);
  const live = transcript.pending.trim() !== "";
  const confirmed = live ? tail : tail.slice(0, -1);
  const last = tail.at(-1);
  return {
    older: confirmed.slice(-4).map((segment) => segment.text),
    current: live ? transcript.pending.trim() : last?.text ?? "",
    live,
    marker: last ? `L${last.id} · ${segmentTime(last.start)}` : "",
    key: String(last?.id ?? 0),
  };
}

/** Long pending text keeps its newest words in view. */
export function tailText(text: string, limit = 140): string {
  return text.length > limit ? "…" + text.slice(-limit).replace(/^\S*\s/, "") : text;
}

export function updatedAgo(updated: number | null, now: number): string {
  if (!updated) return "";
  const minutes = Math.floor(Math.max(0, now - updated * 1000) / 60000);
  return minutes < 1 ? "刚刚更新" : `${minutes} 分钟前更新`;
}

export function micPercent(level: number): string {
  return `${Math.round(levelShare(level) * 100)}%`;
}

/** "编写详细笔记 2/7" → 2/7; text without a fraction has no progress bar. */
export function progressOf(status: string | null | undefined): { done: number; total: number } | null {
  const match = status?.match(/(\d+)\s*\/\s*(\d+)/);
  if (!match || Number(match[2]) === 0) return null;
  return { done: Math.min(Number(match[1]), Number(match[2])), total: Number(match[2]) };
}

export interface ClosingStage {
  key: string; label: string; state: "done" | "current" | "pending"; status: string;
  seconds: number | null; progress: { done: number; total: number } | null;
}

/**
 * The closing list. Controller stages are "录制与转录" (recording plus draining), "离线校正" and
 * "课后笔记"; draining has no stage of its own, so its start comes from the phase_since we saw.
 */
export function closingStages(snapshot: Snapshot, drainStart: number | null): ClosingStage[] {
  const stage = (name: string) => snapshot.stages.find((s) => s.name === name);
  const span = (name: string) => {
    const found = stage(name);
    return found && found.end != null ? found.end - found.start : null;
  };
  const order = ["draining", ...(snapshot.refine.enabled ? ["refining"] : []), "finalizing", "saving"];
  const current = order.indexOf(snapshot.phase);
  const drainEnd = (stage("离线校正") ?? stage("课后笔记"))?.start;
  const rows: Record<string, Omit<ClosingStage, "key" | "state">> = {
    draining: { label: "完成末尾转录", status: snapshot.asr.status, progress: null,
      seconds: drainStart != null && drainEnd != null ? drainEnd - drainStart : null },
    refining: { label: "课后离线校正", status: snapshot.refine.status ?? "", progress: null, seconds: span("离线校正") },
    finalizing: { label: "编写详细笔记", status: snapshot.notes.status, progress: progressOf(snapshot.notes.status),
      seconds: span("课后笔记") },
    saving: { label: "保存", status: "正在写入笔记文件", progress: null, seconds: null },
  };
  return order.map((key, index) => ({
    key, ...rows[key],
    state: index < current ? "done" : index === current ? "current" : "pending",
  }));
}

export function stageTime(seconds: number | null): string {
  return seconds == null ? "" : formatElapsed(seconds);
}

/** Keys pressed while typing belong to the field, not to the recording controls. */
export function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return !!element && (element.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName));
}
