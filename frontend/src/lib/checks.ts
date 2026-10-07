import type { Check } from "./types";

export type FixTarget = "courses" | "asr" | "refine" | "notes" | "mic";

// Where the GUI fixes each check. The backend's hints name terminal commands (lecture doctor
// prints them), so the GUI never shows them. tests/test_frontend_build.py keeps this in step
// with lecture_cli/checks.py.
export const FIXES: Record<string, FixTarget | null> = {
  courses_dir: "courses",
  notes_key: "notes",
  notes_service: "notes",
  asr_key: "asr",
  asr_service: "asr",
  whisperlivekit: "asr",
  asr_device: "asr",
  asr_weights: "asr",
  refine: "refine",
  microphone: "mic",
  mic_volume: "mic",
  ffmpeg: null,
  wpctl: null,
  cjk_font: null,
};

// System software the GUI cannot install.
const SYSTEM_NOTES: Record<string, string> = {
  ffmpeg: "需要系统软件 FFmpeg，请用系统的软件包管理器安装。",
  wpctl: "安装系统组件 WirePlumber 后才能自动调节麦克风音量。",
  cjk_font: "建议安装 Noto CJK 中文字体，否则中文可能显示不全。",
};

export const TARGET_LABELS: Record<FixTarget, string> = {
  courses: "课程目录", asr: "转录", refine: "课后校正", notes: "笔记服务", mic: "麦克风",
};

export type Fix = { target: FixTarget } | { note: string } | null;

export function fixFor(check: Check): Fix {
  if (check.level === "ok") return null;
  const target = FIXES[check.id];
  if (target) return { target };
  return { note: SYSTEM_NOTES[check.id] ?? "请在设置中检查相关选项。" };
}
