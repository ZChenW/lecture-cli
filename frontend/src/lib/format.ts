import type { AsrPreset, Config, Notice, Snapshot } from "./types";

const pad = (value: number) => String(value).padStart(2, "0");

/** MM:SS within the first hour, H:MM:SS after it (the recording timer). */
export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours ? `${hours}:${pad(minutes)}:${pad(total % 60)}` : `${pad(minutes)}:${pad(total % 60)}`;
}

/** "2026-10-07T14:30:00" → "2026年10月7日": one Chinese date style for the course list and the notes list. */
export function formatNoteDate(started: string | null): string {
  const match = started?.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日` : "暂无笔记";
}

/** The same date followed by the time: "2026年10月7日 14:30". */
export function formatNoteTime(started: string | null): string {
  const match = started?.match(/^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})/);
  return match ? `${formatNoteDate(started)} ${match[1]}:${match[2]}` : "时间未知";
}

/** One banner list: the error first, then notices, without repeating the same text. */
export function bannerItems(asr: Snapshot["asr"]): Notice[] {
  const items: Notice[] = asr.error ? [{ kind: "error", text: asr.error }] : [];
  for (const notice of asr.notices) {
    if (notice.text && !items.some((item) => item.text === notice.text)) items.push(notice);
  }
  return items;
}

const PHASES: Record<string, string> = {
  starting: "正在启动", recording: "正在录制", draining: "正在处理剩余转录", refining: "课后校正中",
  finalizing: "正在生成终稿", saving: "正在保存",
};

export function phaseLabel(phase: string): string {
  return PHASES[phase] ?? phase;
}

/** "本地 Whisper · base.en" / "云端 API · Groq · whisper-1": the start dialog's one-line summary. */
export function asrSummary(config: Config, presets: Record<string, AsrPreset>): string {
  if (config.asr_backend === "api") {
    const service = config.asr_provider in presets && config.asr_provider !== "custom"
      ? presets[config.asr_provider].label : hostOf(config.asr_api_base);
    return `云端 API · ${service} · ${config.asr_api_model}`;
  }
  return `${config.asr_model.startsWith("qwen") ? "本地 Qwen" : "本地 Whisper"} · ${config.asr_model}`;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "自定义服务";
  }
}
