import { describe, expect, it, vi } from "vitest";
import { ApiError, request, subscribe } from "./api";

const reply = (status: number, body: unknown) =>
  vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status }));

describe("request", () => {
  it("returns JSON and sends bodies as JSON", async () => {
    const fetchImpl = reply(200, { ok: true });
    expect(await request("PUT", "/api/config", { interval: 30 }, fetchImpl)).toEqual({ ok: true });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("PUT");
    expect(init.body).toBe('{"interval":30}');
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("turns the backend error format into ApiError with field, problems and log", async () => {
    const fetchImpl = reply(422, {
      error: { code: "invalid_config", message: "间隔必须在 1–3600 秒之间", field: "interval",
        problems: [{ field: "interval", message: "间隔必须在 1–3600 秒之间" }], log: "tail" },
    });
    const error = (await request("PUT", "/api/config", {}, fetchImpl).then(() => null, (e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "invalid_config", field: "interval", log: "tail",
      message: "间隔必须在 1–3600 秒之间" });
    expect(error.problems).toHaveLength(1);
  });

  it("reports non-JSON failures and lost connections in Chinese", async () => {
    const html = vi.fn(async () => new Response("<html>", { status: 502 }));
    await expect(request("GET", "/api/x", undefined, html)).rejects.toMatchObject({ status: 502, code: "http" });
    const down = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    await expect(request("GET", "/api/x", undefined, down)).rejects.toMatchObject({
      status: 0, code: "network", message: expect.stringContaining("无法连接"),
    });
  });
});

class FakeSource {
  static made: FakeSource[] = [];
  listeners: Record<string, (event: MessageEvent) => void> = {};
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  constructor(readonly url: string) { FakeSource.made.push(this); }
  addEventListener(type: string, listener: (event: MessageEvent) => void) { this.listeners[type] = listener; }
  emit(type: string, data: unknown) { this.listeners[type]({ data: JSON.stringify(data) } as MessageEvent); }
  close() { this.closed = true; }
}

describe("subscribe", () => {
  it("delivers snapshots, reconnects after one second, and stops after finished", () => {
    vi.useFakeTimers();
    FakeSource.made = [];
    const snapshots: unknown[] = [];
    const finished: unknown[] = [];
    let lost = 0;
    subscribe({ snapshot: (s) => snapshots.push(s), finished: (r) => finished.push(r), lost: () => lost++ },
      { open: (url) => new FakeSource(url) });
    FakeSource.made[0].emit("snapshot", { phase: "recording" });
    FakeSource.made[0].onerror!(new Event("error"));
    expect(FakeSource.made[0].closed).toBe(true);
    vi.advanceTimersByTime(999);
    expect(FakeSource.made).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSource.made).toHaveLength(2);
    FakeSource.made[1].emit("finished", { status: "done" });
    FakeSource.made[1].onerror!(new Event("error"));
    vi.advanceTimersByTime(5000);
    expect(FakeSource.made).toHaveLength(2);
    expect(snapshots).toEqual([{ phase: "recording" }]);
    expect(finished).toEqual([{ status: "done" }]);
    expect(lost).toBe(1);  // Only the real drop; the server ending a finished stream is not a loss.
    vi.useRealTimers();
  });

  it("does not reconnect once closed by the page", () => {
    vi.useFakeTimers();
    FakeSource.made = [];
    const subscription = subscribe({}, { open: (url) => new FakeSource(url) });
    subscription.close();
    FakeSource.made[0].onerror?.(new Event("error"));
    vi.advanceTimersByTime(5000);
    expect(FakeSource.made).toHaveLength(1);
    vi.useRealTimers();
  });
});
