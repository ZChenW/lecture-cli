import { describe, expect, it } from "vitest";
import { CELLS, cells, levelShareDb, overloaded, parseEvent, watchLevel } from "./mic";

function streamed(chunks: string[], status = 200): typeof fetch {
  return (async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
  }) as unknown as typeof fetch;
}

function run(fetcher: typeof fetch) {
  const seen: { name: string; value?: unknown }[] = [];
  return new Promise<typeof seen>((resolve) => {
    watchLevel({
      level: (value) => seen.push({ name: "level", value }),
      verdict: (value) => seen.push({ name: "verdict", value }),
      busy: () => seen.push({ name: "busy" }),
      error: (value) => seen.push({ name: "error", value }),
      end: () => resolve(seen),
    }, { fetcher });
  });
}

describe("level bar", () => {
  it("spans -60 to 0 dBFS, linear in decibels", () => {
    expect(levelShareDb(-90)).toBe(0);
    expect(levelShareDb(-60)).toBe(0);
    expect(levelShareDb(-30)).toBeCloseTo(0.5);
    expect(levelShareDb(-15)).toBeCloseTo(0.75);
    expect(levelShareDb(3)).toBe(1);
  });
  it("lights cells for the RMS and marks a peak above them", () => {
    expect(cells(null, null)).toEqual({ lit: 0, peak: -1 });
    const shown = cells({ rms: -30, peak: -20 }, -14);
    expect(shown.lit).toBe(CELLS / 2);
    expect(shown.peak).toBeGreaterThan(shown.lit);
    expect(cells({ rms: -10, peak: -10 }, -40).peak).toBe(-1);  // A peak under the bar is hidden.
  });
  it("is overloaded while any level of the last second clipped", () => {
    const quiet = { rms: -40, peak: -30, clipped: false };
    const clip = { rms: -3, peak: 0, clipped: true };
    expect(overloaded([])).toBe(false);
    expect(overloaded([quiet, quiet])).toBe(false);
    expect(overloaded([quiet, clip, quiet])).toBe(true);
    expect(overloaded([clip, ...Array(9).fill(quiet)])).toBe(true);   // ten levels: still within 1 s
    expect(overloaded([clip, ...Array(10).fill(quiet)])).toBe(false); // eleven: it has passed
    expect(overloaded([{ rms: -3, peak: 0 }])).toBe(false);           // an older server without the field
  });
});

describe("watchLevel", () => {
  it("parses events split across chunks and skips keepalives", async () => {
    expect(parseEvent(": keepalive")).toBeNull();
    const seen = await run(streamed([
      'event: level\ndata: {"rms": -32, "pe', 'ak": -20}\n\n: keepalive\n\n',
      'event: verdict\ndata: {"verdict": "high", "text": "麦克风音量过高，开始上课时会自动调低"}\n\nevent: level\ndata: {"rms": -31, "peak": -19, "clipped": true}\n\n',
    ]));
    expect(seen).toEqual([
      { name: "level", value: { rms: -32, peak: -20 } },
      { name: "verdict", value: { verdict: "high", text: "麦克风音量过高，开始上课时会自动调低" } },
      { name: "level", value: { rms: -31, peak: -19, clipped: true } },
    ]);
  });
  it("asks for the plain level stream, never a test", async () => {
    let asked = "";
    const inner = streamed(['event: verdict\ndata: {"verdict": "", "text": ""}\n\n']);
    const fetcher = ((url: string, init?: RequestInit) => {
      asked = url;
      return inner(url, init);
    }) as unknown as typeof fetch;
    const seen = await run(fetcher);
    expect(asked).toBe("/api/mic/level");
    expect(seen).toEqual([{ name: "verdict", value: { verdict: "", text: "" } }]);
  });
  it("reports a refusal with the server's message", async () => {
    const fetcher = (async () => new Response(JSON.stringify({ error: { code: "busy", message: "录制进行中，不能同时测试麦克风" } }),
      { status: 409, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    expect(await run(fetcher)).toEqual([{ name: "error", value: "录制进行中，不能同时测试麦克风" }]);
  });
  it("stops when a lecture starts", async () => {
    expect(await run(streamed(["event: busy\ndata: {}\n\n"]))).toEqual([{ name: "busy" }]);
  });
  it("close() aborts the request and ends once", async () => {
    let signal: AbortSignal | undefined;
    let ends = 0;
    const fetcher = ((_: string, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      return new Promise(() => {});
    }) as unknown as typeof fetch;
    const watch = watchLevel({ end: () => (ends += 1), error: () => { throw new Error("no error expected"); } }, { fetcher });
    watch.close();
    watch.close();
    expect(signal?.aborted).toBe(true);
    expect(ends).toBe(1);
  });
});
