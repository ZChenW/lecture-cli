// Plan N3.1: the start dialog's "这堂课改用 Qwen" row; pure so it can be tested without a DOM.
import type { Config, QwenLive } from "./types";

export const QWEN_SUGGESTION = "中文课堂用 Qwen 实时转录更准，标点和分段也更好";

const isQwen = (model: string | undefined) => !!model && model.startsWith("qwen");

/** Shown for a Chinese lecture whose live model would be Whisper; cloud transcription has no local model. */
export function qwenRowShown(config: Config, language: string): boolean {
  return config.asr_backend !== "api" && language === "zh" && !isQwen(config.asr_model);
}

/** The course's remembered choice wins; otherwise the switch starts on exactly when Qwen is ready. */
export function qwenDefault(remembered: string | undefined, ready: boolean): boolean {
  return remembered ? isQwen(remembered) : ready;
}

/**
 * The live model this lecture will use, for the dialog's 转录 line and the start request. With the
 * row shown the switch decides (Qwen only when ready); otherwise the backend applies the course's
 * remembered model, as `lecture start` does.
 */
export function liveModel(config: Config, remembered: string | undefined, shown: boolean,
                          live: QwenLive | null, on: boolean): string {
  if (config.asr_backend === "api") return config.asr_model;
  if (shown) return live?.ready && on ? live.model : config.asr_model;
  return remembered ?? config.asr_model;
}

/** Only a lecture that showed the row sends asr_model; the backend remembers it for the course. */
export function asrOverride(config: Config, remembered: string | undefined, shown: boolean,
                            live: QwenLive | null, on: boolean): { asr_model?: string } {
  if (!shown || !live) return {};
  return { asr_model: liveModel(config, remembered, shown, live, on) };
}
