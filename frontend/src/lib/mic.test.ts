import { describe, expect, it } from "vitest";
import { CELLS, cells, levelShareDb, parseEvent, resultText, watchLevel } from "./mic";

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

function run(fetcher: typeof fetch, test = false) {
  const seen: { name: string; value?: unknown }[] = [];
  return new Promise<typeof seen>((resolve) => {
    watchLevel({
      level: (value) => seen.push({ name: "level", value }),
      still: (value) => seen.push({ name: "still", value }),
      result: (value) => seen.push({ name: "result", value }),
      busy: () => seen.push({ name: "busy" }),
      error: (value) => seen.push({ name: "error", value }),
      end: () => resolve(seen),
    }, { test, fetcher });
  });
}

describe("level bar", () => {
  it("spans -70 to 0 dBFS", () => {
    expect(levelShareDb(-90)).toBe(0);
    expect(levelShareDb(-35)).toBeCloseTo(0.5);
    expect(levelShareDb(3)).toBe(1);
  });
  it("lights cells for the RMS and marks a peak above them", () => {
    expect(cells(null, null)).toEqual({ lit: 0, peak: -1 });
    const shown = cells({ rms: -35, peak: -20 }, -14);
    expect(shown.lit).toBe(CELLS / 2);
    expect(shown.peak).toBeGreaterThan(shown.lit);
    expect(cells({ rms: -10, peak: -10 }, -40).peak).toBe(-1);  // A peak under the bar is hidden.
  });
  it("words the test result", () => {
    expect(resultText({ passed: true, floor: -60, peak: -20, rise: 40 })).toBe("麦克风正常：讲话时比底噪高出 40 dB");
    expect(resultText({ passed: false, floor: -32, peak: -20.4, rise: 11.6 })).toBe("没有检测到明显的声音变化：讲话时只比底噪高出 12 dB");
  });
});

describe("watchLevel", () => {
  it("parses events split across chunks and skips keepalives", async () => {
    expect(parseEvent(": keepalive")).toBeNull();
    const seen = await run(streamed([
      'event: level\ndata: {"rms": -32, "pe', 'ak": -20}\n\n: keepalive\n\n',
      'event: still\ndata: {"still": true}\n\nevent: level\ndata: {"rms": -31, "peak": -19}\n\n',
    ]));
    expect(seen).toEqual([
      { name: "level", value: { rms: -32, peak: -20 } },
      { name: "still", value: true },
      { name: "level", value: { rms: -31, peak: -19 } },
    ]);
  });
  it("asks for the test and passes its result on", async () => {
    let asked = "";
    const inner = streamed(['event: result\ndata: {"passed": false, "floor": -32, "peak": -20, "rise": 12}\n\n']);
    const fetcher = ((url: string, init?: RequestInit) => {
      asked = url;
      return inner(url, init);
    }) as unknown as typeof fetch;
    const seen = await run(fetcher, true);
    expect(asked).toBe("/api/mic/level?test=1");
    expect(seen).toEqual([{ name: "result", value: { passed: false, floor: -32, peak: -20, rise: 12 } }]);
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
