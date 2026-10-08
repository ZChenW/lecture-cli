import type {
  AsrModel, Bootstrap, Check, Config, Course, Devices, KeyStatus, Note, Problem, Result, RunRecord, Snapshot, Task,
} from "./types";

/** Every failure, including a lost connection, arrives as one of these. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly field?: string,
    readonly problems: Problem[] = [],
    readonly log?: string,
  ) {
    super(message);
  }
}

type Fetch = typeof fetch;

export async function request<T>(method: string, path: string, body?: unknown, fetchImpl: Fetch = fetch): Promise<T> {
  let response: Response;
  try {
    response = await fetchImpl(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "无法连接界面后端，请确认 lecture gui 仍在运行");
  }
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    // Not JSON: only acceptable for a successful empty body.
  }
  if (!response.ok) {
    const error = (data as { error?: Record<string, unknown> } | null)?.error;
    if (error && typeof error.message === "string") {
      throw new ApiError(response.status, String(error.code ?? "error"), error.message,
        typeof error.field === "string" ? error.field : undefined,
        Array.isArray(error.problems) ? (error.problems as Problem[]) : [],
        typeof error.log === "string" ? error.log : undefined);
    }
    throw new ApiError(response.status, "http", `请求失败（HTTP ${response.status}）`);
  }
  return data as T;
}

const get = <T>(path: string) => request<T>("GET", path);
const send = <T>(method: string, path: string, body?: unknown) => request<T>(method, path, body ?? {});
const query = (params: Record<string, string>) => new URLSearchParams(params).toString();

export interface ServiceTest { provider?: string; api_base?: string; model?: string; key?: string }

export const api = {
  bootstrap: () => get<Bootstrap>("/api/bootstrap"),
  saveConfig: (changes: Partial<Config>) =>
    send<{ config: Config; problems: Problem[]; configured: boolean }>("PUT", "/api/config", changes),
  saveKey: (kind: "notes" | "asr", value: string) => send<KeyStatus>("PUT", `/api/keys/${kind}`, { value }),
  deleteKey: (kind: "notes" | "asr") => request<KeyStatus>("DELETE", `/api/keys/${kind}`),
  testService: (kind: "notes" | "asr", body: ServiceTest) => send<Result>("POST", `/api/test/${kind}`, body),
  courses: () => get<Course[]>("/api/courses"),
  addCourse: (name: string) => send<Course>("POST", "/api/courses", { name }),
  devices: () => get<Devices>("/api/devices"),
  asrModels: () => get<AsrModel[]>("/api/asr-models"),
  prepare: (name: string) => send<{ task: string }>("POST", `/api/asr-models/${encodeURIComponent(name)}/prepare`),
  task: (id: string) => get<Task>(`/api/tasks/${encodeURIComponent(id)}`),
  checks: () => get<Check[]>("/api/checks"),
  startRun: (course: string, overrides: Record<string, unknown> = {}) =>
    send<Snapshot>("POST", "/api/runs", { course, overrides }),
  activeRun: () => get<Snapshot | null>("/api/runs/active"),
  control: (action: "pause" | "resume" | "stop" | "skip-refine") => send<Snapshot>("POST", `/api/runs/active/${action}`),
  runs: () => get<RunRecord[]>("/api/runs"),
  notes: (course: string) => get<Note[]>(`/api/notes?${query({ course })}`),
  noteContent: (path: string) => get<Record<string, string>>(`/api/notes/content?${query({ path })}`),
  open: (path: string, mode: "reveal" | "terminal" | "editor") => send<{ ok: boolean }>("POST", "/api/open", { path, mode }),
  quit: () => send<{ ok: boolean }>("POST", "/api/quit"),
};

export interface Subscription { close(): void }
interface Handlers {
  snapshot?: (snapshot: Snapshot) => void;
  finished?: (record: RunRecord) => void;
  /** The stream dropped (or the run already ended before it opened); a reconnect follows. */
  lost?: () => void;
}
interface Source {
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  onerror: ((event: Event) => void) | null;
  close(): void;
}

/** Follows the active run's event stream; a dropped connection reconnects after one second. */
export function subscribe(
  handlers: Handlers,
  { url = "/api/runs/active/events", retryMs = 1000, open = (u: string): Source => new EventSource(u) } = {},
): Subscription {
  let closed = false;
  let source: Source | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const connect = () => {
    source = open(url);
    source.addEventListener("snapshot", (event) => handlers.snapshot?.(JSON.parse(event.data)));
    source.addEventListener("finished", (event) => {
      closed = true;  // The run is over; the server ends the stream on purpose.
      source?.close();
      handlers.finished?.(JSON.parse(event.data));
    });
    source.onerror = () => {
      source?.close();
      if (closed) return;
      handlers.lost?.();
      if (!closed) timer = setTimeout(connect, retryMs);
    };
  };
  connect();
  return {
    close() {
      closed = true;
      clearTimeout(timer);
      source?.close();
    },
  };
}
