import type { Check, Missing } from "./types";

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
  refine_service: "refine",
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
  // The detail line already says which of the two is missing.
  cjk_font: "请用系统的软件包管理器安装 Noto CJK 中文字体（含衬线的 Noto Serif CJK）。缺少衬线字体时，笔记阅读界面的标题和正文会改用无衬线字体。",
};

export const TARGET_LABELS: Record<FixTarget, string> = {
  courses: "课程目录", asr: "转录", refine: "课后校正", notes: "笔记服务", mic: "麦克风",
};

/** The settings sections in page order (design/D-settings.reference.html). */
export type Section = FixTarget | "open" | "checks";
export const SECTIONS: { id: Section; label: string }[] = [
  { id: "courses", label: "课程目录" },
  { id: "asr", label: "转录" },
  { id: "refine", label: "课后校正" },
  { id: "notes", label: "笔记服务" },
  { id: "mic", label: "麦克风" },
  { id: "open", label: "打开方式" },
  { id: "checks", label: "环境检查" },
];

/** What each missing item is called on home and in the start dialog, and where it is set. */
export const MISSING: Record<Missing, { label: string; section: FixTarget }> = {
  courses_dir: { label: "课程目录", section: "courses" },
  notes_key: { label: "笔记服务的 key", section: "notes" },
  asr_key: { label: "云端转录的 key", section: "asr" },
};

export type Fix = { target: FixTarget } | { note: string } | null;

export function fixFor(check: Check): Fix {
  if (check.level === "ok") return null;
  const target = FIXES[check.id];
  if (target) return { target };
  return { note: SYSTEM_NOTES[check.id] ?? "请在设置中检查相关选项。" };
}
