import { describe, expect, it } from "vitest";
import { asrSummary, bannerItems, formatElapsed, formatNoteDate, formatNoteTime, phaseLabel, weakLabel } from "./format";
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
      notices: [{ kind: "device", text: "使用 CPU" }, { kind: "warning", text: "麦克风不可用" }, { kind: "device", text: "" }],
    };
    expect(bannerItems(asr)).toEqual([{ kind: "error", text: "麦克风不可用" }, { kind: "device", text: "使用 CPU" }]);
    expect(bannerItems({ ...asr, error: null, notices: [] })).toEqual([]);
  });

  it("keeps the mic volume notice out of the banners unless the microphone needs a hand", () => {
    const asr = { status: "", device_label: null, model: null, level: 0, backlog_seconds: 0, queued_seconds: 0, error: null };
    expect(bannerItems({ ...asr, notices: [{ kind: "gain", text: "80% · 自动降低削波音量已启用" }] })).toEqual([]);
    expect(bannerItems({ ...asr, notices: [{ kind: "gain", text: "检测到削波，麦克风音量 80% → 63%" }] })).toEqual([]);
    const muted = { kind: "gain", text: "默认麦克风已静音；请手动取消静音，自动调节不会取消静音。" };
    expect(bannerItems({ ...asr, notices: [muted] })).toEqual([muted]);
  });

  it("adds the weak-input notice as its own closable banner (plan N3.4)", () => {
    const asr = { status: "", device_label: null, model: null, level: 0, backlog_seconds: 0, queued_seconds: 0, error: null,
      notices: [{ kind: "warning", text: "音频设备报告 2 次输入丢帧" }] };
    const weak = "收到的声音很弱，转录可能不准。请把麦克风靠近讲话人，或调高输入音量";
    expect(bannerItems({ ...asr, weak_input: weak })).toEqual([asr.notices[0], { kind: "weak", text: weak }]);
    expect(bannerItems({ ...asr, weak_input: null })).toEqual(asr.notices);
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

describe("weak_input in the mic cell (plan N3.4, GUI-3 item 5)", () => {
  it("names which notice the field carries", () => {
    expect(weakLabel("还没有听到讲话。如果已经开始上课，请把麦克风靠近讲话人，或调高输入音量")).toBe("还没听到讲话");
    expect(weakLabel("麦克风几乎没有信号，请检查是否选对了麦克风、是否被静音")).toBe("几乎没有信号");
    expect(weakLabel("收到的声音很弱，转录可能不准。请把麦克风靠近讲话人，或调高输入音量")).toBe("声音很弱");
    expect(weakLabel("收到的声音几乎没有变化，可能只是噪声。请检查麦克风是否正常、是否选对了输入设备")).toBe("可能只是噪声");
  });
});
