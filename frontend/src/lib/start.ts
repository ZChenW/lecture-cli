// The start dialog's three rows (plan GUI-3 item 3, design/F-dialog.reference.html); pure so it can
// be tested without a DOM.
import type { Config, QwenLive } from "./types";

export type LiveChoice = "whisper" | "qwen";
/** Whether live Qwen can be chosen: the GPU probe takes a few seconds the first time. */
export type QwenState = "checking" | "failed" | "missing" | "ready";
export interface LiveOption { enabled: boolean; aside: string; hint?: string }

export const FOOTER = "这门课会记住以上选择。";
export const isQwen = (model: string | null | undefined) => !!model && model.startsWith("qwen");

export function qwenState(live: QwenLive | null, failed: boolean): QwenState {
  if (failed) return "failed";
  if (!live) return "checking";
  return live.ready ? "ready" : "missing";
}

/** The Whisper model behind the option: the default when it is Whisper, else the course's remembered one. */
export function whisperModel(config: Config, remembered: string | undefined): string | null {
  if (!isQwen(config.asr_model)) return config.asr_model;
  return remembered && !isQwen(remembered) ? remembered : null;
}

/** The Qwen model behind the option: the default when it is Qwen, else the one the readiness check named. */
export function qwenModel(config: Config, live: QwenLive | null): string | null {
  return isQwen(config.asr_model) ? config.asr_model : live?.model ?? null;
}

/**
 * Why the option is missing and the command that fixes it. `./install.sh --with-qwen` installs the
 * environment but downloads no weights, so missing weights get `lecture prepare`.
 */
export function qwenMissing(live: QwenLive): { reason: string; command: string; after?: string } {
  if (!live.env_ready) return { reason: "未安装 Qwen 环境", command: live.install };
  if (!live.cached) return { reason: "未下载 Qwen 模型", command: `lecture prepare --asr-model ${live.model}` };
  return { reason: "没有可用的 NVIDIA GPU", command: live.install, after: "并检查 NVIDIA 驱动" };
}

/** The Qwen segment: selectable only when ready and the language is named; its small text says why not. */
export function qwenOption(language: string, state: QwenState, live: QwenLive | null): LiveOption {
  if (language === "auto") return { enabled: false, aside: "需指定语言", hint: "Qwen 实时转录需要指定英语或中文" };
  if (state === "checking") return { enabled: false, aside: "检查中", hint: "正在检查本机的 Qwen 环境" };
  if (state === "failed" || !live) return { enabled: false, aside: "无法检查", hint: "检查本机的 Qwen 环境失败" };
  if (state === "missing") {
    const gap = qwenMissing(live);
    const aside = !live.env_ready ? "未安装" : !live.cached ? "未下载" : "无可用 GPU";
    return { enabled: false, aside, hint: `${gap.reason}。运行 ${gap.command}${gap.after ? `，${gap.after}` : ""}` };
  }
  return { enabled: true, aside: language === "zh" ? "中文推荐" : "" };
}

export function whisperOption(model: string | null): LiveOption {
  return model ? { enabled: true, aside: "" } : { enabled: false, aside: "未设置", hint: "在设置「转录」一节选择 Whisper 模型" };
}

/**
 * The selected segment until the user picks one: the course's remembered model, else Qwen for a
 * Chinese lecture when it is ready, else the default model. While Qwen is still being checked the
 * remembered or default model shows, which is what the backend applies when no model is sent.
 */
export function defaultChoice(config: Config, remembered: string | undefined, language: string,
                              state: QwenState, qwen: LiveOption, whisper: LiveOption): LiveChoice {
  const want: LiveChoice = remembered ? (isQwen(remembered) ? "qwen" : "whisper")
    : language === "zh" && qwen.enabled ? "qwen" : isQwen(config.asr_model) ? "qwen" : "whisper";
  if (state === "checking" && language !== "auto") return want;
  if (want === "qwen" && !qwen.enabled && whisper.enabled) return "whisper";
  if (want === "whisper" && !whisper.enabled && qwen.enabled) return "qwen";
  return want;
}

/** A picked segment stays picked while it can be chosen; otherwise the default applies again. */
export function effectiveChoice(picked: LiveChoice | null, fallback: LiveChoice, qwen: LiveOption, whisper: LiveOption): LiveChoice {
  if (picked && (picked === "qwen" ? qwen : whisper).enabled) return picked;
  return fallback;
}

/**
 * What the start request carries for the live model. The backend remembers asr_model for the course,
 * so a model the user could not freely choose (Qwen not ready, or the language is 自动) is sent with
 * remember_asr_model: false and the course's remembered choice stays as it was. While Qwen is still
 * being checked nothing is sent and the backend applies the remembered model, as `lecture start` does.
 */
export function asrRequest(config: Config, choice: LiveChoice, models: { whisper: string | null; qwen: string | null },
                           state: QwenState, language: string, qwen: LiveOption, whisper: LiveOption):
    { asr_model?: string; remember_asr_model?: false } {
  if (config.asr_backend === "api") return {};
  if (state === "checking" && language !== "auto") return {};
  const model = choice === "qwen" ? models.qwen : models.whisper;
  if (!model) return {};
  return qwen.enabled && whisper.enabled ? { asr_model: model } : { asr_model: model, remember_asr_model: false };
}

/** The cloud row: one line of text, no choice. */
export function cloudLine(config: Config): string {
  return `云端 · ${config.asr_api_model}`;
}

/** 下课后: the course's remembered choice, else the setting. */
export function refineDefault(config: Config, remembered: string | undefined): "on" | "off" {
  if (remembered === "on" || remembered === "off") return remembered;
  return config.refine ? "on" : "off";
}
