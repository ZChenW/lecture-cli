// Shapes returned by lecture_cli/gui/server.py.
export type Level = "ok" | "warn" | "fail";

export interface Problem { field: string; message: string }
export interface KeyStatus { set: boolean; source: "env" | "file" | null; tail: string | null }
export interface Keys { notes: KeyStatus; asr: KeyStatus }
export interface Result { ok: boolean; level: Level; message: string }
export interface Check { id: string; label: string; level: Level; detail: string; hint: string }

export interface Config {
  courses_dir: string | null;
  notes_provider: string;
  notes_api_base: string;
  notes_model: string;
  notes_extra_body: Record<string, unknown>;
  asr_backend: "local" | "api";
  asr_model: string;
  asr_device: "auto" | "cuda" | "cpu";
  asr_provider: string;
  asr_api_base: string;
  asr_api_model: string;
  language: string;
  interval: number;
  device: string | number | null;
  refine: boolean;
  refine_model: string;
  auto_gain: boolean;
  qwen_python: string | null;
  [key: string]: unknown;
}

export interface NotesPreset { label: string; api_base: string; model: string; extra_body: Record<string, unknown> }
export interface AsrPreset { label: string; api_base: string; model: string }

export interface Bootstrap {
  version: string;
  configured: boolean;
  problems: Problem[];
  config: Config;
  keys: Keys;
  active_run: Snapshot | null;
  presets: { notes: Record<string, NotesPreset>; asr: Record<string, AsrPreset> };
}

export interface Course { name: string; path: string; notes_count: number; last_note: string | null; has_glossary: boolean }
export interface Note {
  name: string; path: string; started: string | null; kind: string | null;
  attachments: { transcript: boolean; live: boolean; review: boolean };
}
export interface AsrModel { name: string; family: "whisper" | "qwen"; cached: boolean; env_ready: boolean }
export interface Device { index: number; name: string; channels: number; default: boolean }
export interface Devices { devices: Device[]; selected: string | number | null; notes: Record<string, string> }
export interface Task { id: string; status: "running" | "done" | "failed"; exit_code: number | null; output: string[] }

export interface Notice { kind: string; text: string }
export interface Stage { name: string; start: number; end: number | null }
export interface Snapshot {
  run_id: string | null;
  course: string | null;
  output: string | null;
  started: string | null;
  phase: string;
  phase_since: number | null;
  input: string;
  paused: boolean;
  can_skip: boolean;
  elapsed_seconds: number;
  asr: {
    status: string; device_label: string | null; model: string | null; level: number;
    backlog_seconds: number; queued_seconds: number; notices: Notice[]; error: string | null;
  };
  transcript: { count: number; tail: { id: number; start: string; end: string; text: string }[]; pending: string };
  notes: { status: string; worker_alive: boolean | null; unprocessed_segments: number; updated: number | null; latest: string | null };
  refine: { enabled: boolean; status: string | null; reason: string | null; progress?: number | null; eta_seconds?: number | null };
  closing_estimate_seconds?: number | null;
  stages: Stage[];
}

export interface RunRecord {
  run_id: string; course: string; output: string; status: string; exit_code?: number;
  warnings?: string[]; log?: string | null; log_tail?: string; workspace_kept?: string | null;
  stages?: { name: string; seconds: number }[]; started?: string | null;
  flags?: { refinement_failed: boolean; refinement_skipped?: boolean; has_fallback: boolean; detail_incomplete: boolean };
  audio_seconds?: number | null;
  [key: string]: unknown;
}
