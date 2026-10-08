// Plan N4: the settings choice "本机 Qwen / 云端 API / 不校正" over refine + refine_backend.
import type { Config } from "./types";

export type RefineChoice = "local" | "api" | "off";

export const UPLOAD_NOTICE = "课堂音频会上传到转录服务";
export const DEFAULT_API_MODEL = "whisper-large-v3";

export function refineChoice(config: Pick<Config, "refine" | "refine_backend">): RefineChoice {
  if (!config.refine) return "off";
  return config.refine_backend === "api" ? "api" : "local";
}

/** The configuration fields the choice writes. "不校正" keeps the saved way of refining, so turning
 *  refinement back on (also per lecture in the start dialog) returns to it. */
export function refineChanges(choice: RefineChoice, config: Pick<Config, "refine_backend">,
  fields: { model: string; qwenPython: string; apiModel: string }): Partial<Config> {
  return {
    refine: choice !== "off",
    refine_backend: choice === "off" ? (config.refine_backend ?? "local") : choice,
    refine_model: fields.model,
    qwen_python: fields.qwenPython.trim() || null,
    refine_api_model: fields.apiModel.trim() || DEFAULT_API_MODEL,
  };
}

/** The start dialog's "on" option names what will run after class. */
export function refineTitle(config: Pick<Config, "refine_backend">): string {
  return config.refine_backend === "api" ? "用云端 API 校正转录" : "用 Qwen 校正转录";
}
