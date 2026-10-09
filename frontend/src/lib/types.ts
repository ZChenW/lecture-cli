// Shapes returned by lecture_cli/gui/server.py.
export type Level = "ok" | "warn" | "fail";

export interface Problem { field: string; message: string }
// variable: the environment variable a key came from; stored: a key file holds the same value.
export interface KeyStatus {
  set: boolean; source: "env" | "file" | null; tail: string | null; variable?: string | null; stored?: boolean;
}
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
  refine_backend?: "local" | "api";
  refine_api_model?: string;
  auto_gain: boolean;
  qwen_python: string | null;
  onboarded?: boolean;
  course_settings?: Record<string, { language?: string; asr_model?: string; refine?: string }>;
  [key: string]: unknown;
}

export interface NotesPreset { label: string; api_base: string; model: string; extra_body: Record<string, unknown> }
export interface AsrPreset { label: string; api_base: string; model: string }

/** What a lecture cannot start without (config.REQUIRED). */
export type Missing = "courses_dir" | "notes_key" | "asr_key";

export type OpenerKind = "file_manager" | "terminal" | "editor";
export interface OpenerInfo {
  label: string; programs: string[]; available: string[]; selected: string | null; effective: string | null; custom: boolean;
}
export type Openers = Record<OpenerKind, OpenerInfo>;

export interface Bootstrap {
  version: string;
  configured: boolean;
  missing: Missing[];
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
  /** Plan GUI-3 item 4: unticked review points, and all of them. */
  review_count?: number;
  review_total?: number;
  /** Plan GUI-4 Q1.2: the recording recognised nothing. */
  empty?: boolean;
}
export interface ReviewSource { version: "live" | "refined" | null; first: number; last: number; label: string }
export interface ReviewItem { id: string; text: string; sources: ReviewSource[]; checked: boolean }
/** GET/PUT /api/notes/review: the points to check, and the processing notices shown after them. */
export interface Review { items: ReviewItem[]; notices: string[] }
export interface AsrModel { name: string; family: "whisper" | "qwen"; cached: boolean; env_ready: boolean }
/** Plan N3.1: GET /api/qwen-live, whether a Chinese lecture can switch to live Qwen. */
export interface QwenLive { model: string; env_ready: boolean; cached: boolean; gpu: boolean; ready: boolean; install: string }
export interface Device { index: number; name: string; channels: number; default: boolean }
export interface Devices { devices: Device[]; selected: string | number | null; notes: Record<string, string> }
export interface Task { id: string; status: "running" | "done" | "failed"; exit_code: number | null; output: string[] }

export interface Notice { kind: string; text: string; id?: number }
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
    /** Plan N3.4: 30 s of weak input while someone talks; GUI only, never in the note. */
    weak_input?: string | null;
    /** Plan GUI-4 Q2.3: the latest automatic mic volume change; GUI only, never in the note. */
    gain_change?: { id: number; text: string } | null;
    /** PLAN-GUI-5 R2.1: the volume the automatic adjustment keeps now, 0–1. */
    mic_volume?: number | null;
    /** PLAN-GUI-5 R2.3: shown after the opening adjustment when the microphone looks dead. */
    mic_verdict?: string | null;
  };
  transcript: {
    count: number; tail: { id: number; start: string; end: string; text: string }[]; pending: string;
    /** Plan GUI-4 Q3.1: end of the latest segment with text, in recorded seconds; null before any. */
    last_text_seconds?: number | null;
  };
  notes: { status: string; worker_alive: boolean | null; unprocessed_segments: number; updated: number | null; latest: string | null };
  refine: { enabled: boolean; status: string | null; reason: string | null; progress?: number | null; eta_seconds?: number | null };
  closing_estimate_seconds?: number | null;
  stages: Stage[];
}

export interface RunRecord {
  run_id: string; course: string; output: string; status: string; exit_code?: number;
  warnings?: string[]; log?: string | null; log_tail?: string; workspace_kept?: string | null;
  stages?: { name: string; seconds: number }[]; started?: string | null;
  flags?: { refinement_failed: boolean; refinement_skipped?: boolean; has_fallback: boolean; detail_incomplete: boolean; empty?: boolean };
  audio_seconds?: number | null;
  [key: string]: unknown;
}

/** Plan GUI-4 Q1.5: GET /api/mic/level, one event every 100 ms. */
export interface MicLevel { rms: number; peak: number; clipped?: boolean }
/** PLAN-GUI-5 R2.3: "high", "dead" or "" (fine, nothing shown). */
export interface MicVerdict { verdict: "high" | "dead" | ""; text: string }
