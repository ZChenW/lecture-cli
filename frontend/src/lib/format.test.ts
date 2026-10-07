import { describe, expect, it } from "vitest";
import { asrSummary, bannerItems, formatElapsed, formatNoteDate, formatNoteTime, phaseLabel } from "./format";
import type { Config } from "./types";

describe("snapshot view mapping", () => {
  it("formats the timer as MM:SS, then H:MM:SS after an hour", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(2832.9)).toBe("47:12");
    expect(formatElapsed(3599)).toBe("59:59");
    expect(formatElapsed(3600)).toBe("1:00:00");
    expect(formatElapsed(-5)).toBe("00:00");
  });

  it("merges the error and notices into one banner list without repeats", () => {
    const asr = {
      status: "", device_label: null, model: null, level: 0, backlog_seconds: 0, queued_seconds: 0,
      error: "麦克风不可用",
      notices: [{ kind: "gain", text: "削波" }, { kind: "warning", text: "麦克风不可用" }, { kind: "device", text: "" }],
    };
    expect(bannerItems(asr)).toEqual([{ kind: "error", text: "麦克风不可用" }, { kind: "gain", text: "削波" }]);
    expect(bannerItems({ ...asr, error: null, notices: [] })).toEqual([]);
  });

  it("formats note times from file names", () => {
    expect(formatNoteTime("2026-10-07T09:05:00")).toBe("2026年10月7日 09:05");
    expect(formatNoteTime(null)).toBe("时间未知");
    expect(formatNoteDate("2026-10-07T09:05:00")).toBe("2026年10月7日");
    expect(formatNoteDate(null)).toBe("暂无笔记");
  });

  it("names controller phases and summarises the transcription setup", () => {
    expect(phaseLabel("finalizing")).toBe("正在生成终稿");
    expect(phaseLabel("unknown-phase")).toBe("unknown-phase");
    const presets = { groq: { label: "Groq", api_base: "", model: "" }, custom: { label: "自定义", api_base: "", model: "" } };
    const config = { asr_backend: "local", asr_model: "base.en", asr_provider: "groq",
      asr_api_base: "https://asr.example.edu/v1", asr_api_model: "whisper-1" } as Config;
    expect(asrSummary(config, presets)).toBe("本地 Whisper · base.en");
    expect(asrSummary({ ...config, asr_model: "qwen3-asr-1.7b" }, presets)).toBe("本地 Qwen · qwen3-asr-1.7b");
    expect(asrSummary({ ...config, asr_backend: "api" }, presets)).toBe("云端 API · Groq · whisper-1");
    expect(asrSummary({ ...config, asr_backend: "api", asr_provider: "custom" }, presets))
      .toBe("云端 API · asr.example.edu · whisper-1");
  });
});
