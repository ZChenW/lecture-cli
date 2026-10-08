import { describe, expect, it } from "vitest";
import {
  asrRequest, cloudLine, defaultChoice, effectiveChoice, qwenMissing, qwenModel, qwenOption, qwenState,
  refineDefault, whisperModel, whisperOption,
} from "./start";
import type { Config, QwenLive } from "./types";

const config = { asr_backend: "local", asr_model: "small", asr_api_model: "whisper-large-v3-turbo", refine: true } as Config;
const qwenDefault = { ...config, asr_model: "qwen3-asr-0.6b" } as Config;
const ready: QwenLive = { model: "qwen3-asr-1.7b", env_ready: true, cached: true, gpu: true, ready: true,
  install: "./install.sh --with-qwen" };
const noGpu: QwenLive = { ...ready, gpu: false, ready: false };
const notInstalled: QwenLive = { ...noGpu, env_ready: false, cached: false };

describe("start dialog live row (plan GUI-3 item 3)", () => {
  it("offers Qwen only when ready and the language is named, saying why not", () => {
    expect(qwenOption("zh", "ready", ready)).toEqual({ enabled: true, aside: "中文推荐" });
    expect(qwenOption("en", "ready", ready)).toEqual({ enabled: true, aside: "" });
    expect(qwenOption("auto", "ready", ready)).toMatchObject({ enabled: false, aside: "需指定语言" });
    expect(qwenOption("zh", "missing", notInstalled)).toEqual(
      { enabled: false, aside: "未安装", hint: "未安装 Qwen 环境。运行 ./install.sh --with-qwen" });
    expect(qwenOption("zh", "missing", { ...noGpu, cached: false })).toMatchObject(
      { aside: "未下载", hint: "未下载 Qwen 模型。运行 lecture prepare --asr-model qwen3-asr-1.7b" });
    expect(qwenOption("zh", "missing", noGpu)).toMatchObject({ aside: "无可用 GPU" });
    expect(qwenOption("zh", "checking", null)).toMatchObject({ enabled: false, aside: "检查中" });
    expect(qwenOption("zh", "failed", null)).toMatchObject({ enabled: false, aside: "无法检查" });
    expect(qwenState(null, false)).toBe("checking");
    expect(qwenState(null, true)).toBe("failed");
    expect(qwenState(noGpu, false)).toBe("missing");
    expect(qwenState(ready, false)).toBe("ready");
  });

  it("names the models behind both segments", () => {
    expect(whisperModel(config, undefined)).toBe("small");
    expect(whisperModel(config, "qwen3-asr-1.7b")).toBe("small");
    expect(whisperModel(qwenDefault, "base")).toBe("base");
    expect(whisperModel(qwenDefault, undefined)).toBeNull();
    expect(whisperOption(null)).toMatchObject({ enabled: false, aside: "未设置" });
    expect(qwenModel(config, ready)).toBe("qwen3-asr-1.7b");
    expect(qwenModel(qwenDefault, ready)).toBe("qwen3-asr-0.6b");
    expect(qwenModel(config, null)).toBeNull();
  });

  it("selects the remembered model, else Qwen for Chinese when ready, else the default", () => {
    const w = whisperOption("small");
    const q = qwenOption("zh", "ready", ready);
    expect(defaultChoice(config, undefined, "zh", "ready", q, w)).toBe("qwen");
    expect(defaultChoice(config, "small", "zh", "ready", q, w)).toBe("whisper");
    expect(defaultChoice(config, undefined, "en", "ready", qwenOption("en", "ready", ready), w)).toBe("whisper");
    expect(defaultChoice(config, "qwen3-asr-1.7b", "en", "ready", qwenOption("en", "ready", ready), w)).toBe("qwen");
    // Not ready: the remembered Qwen cannot run, Whisper shows.
    expect(defaultChoice(config, "qwen3-asr-1.7b", "zh", "missing", qwenOption("zh", "missing", noGpu), w)).toBe("whisper");
    // 自动: Qwen cannot be chosen.
    expect(defaultChoice(config, "qwen3-asr-1.7b", "auto", "ready", qwenOption("auto", "ready", ready), w)).toBe("whisper");
    // Still checking: what the backend applies when nothing is sent.
    expect(defaultChoice(config, "qwen3-asr-1.7b", "zh", "checking", qwenOption("zh", "checking", null), w)).toBe("qwen");
    expect(defaultChoice(config, undefined, "zh", "checking", qwenOption("zh", "checking", null), w)).toBe("whisper");
  });

  it("keeps a picked segment only while it can be chosen", () => {
    const w = whisperOption("small");
    expect(effectiveChoice("qwen", "whisper", qwenOption("zh", "ready", ready), w)).toBe("qwen");
    expect(effectiveChoice("qwen", "whisper", qwenOption("auto", "ready", ready), w)).toBe("whisper");
    expect(effectiveChoice(null, "qwen", qwenOption("zh", "ready", ready), w)).toBe("qwen");
  });

  it("never overwrites the remembered model with a choice the user could not make", () => {
    const models = { whisper: "small", qwen: "qwen3-asr-1.7b" };
    const w = whisperOption("small");
    const zhReady = qwenOption("zh", "ready", ready);
    expect(asrRequest(config, "qwen", models, "ready", "zh", zhReady, w)).toEqual({ asr_model: "qwen3-asr-1.7b" });
    expect(asrRequest(config, "whisper", models, "ready", "zh", zhReady, w)).toEqual({ asr_model: "small" });
    // Qwen not ready (the previous round's defect): Whisper for this lecture, the course keeps its choice.
    expect(asrRequest(config, "whisper", models, "missing", "zh", qwenOption("zh", "missing", noGpu), w))
      .toEqual({ asr_model: "small", remember_asr_model: false });
    expect(asrRequest(config, "whisper", models, "failed", "zh", qwenOption("zh", "failed", null), w))
      .toEqual({ asr_model: "small", remember_asr_model: false });
    expect(asrRequest(config, "whisper", models, "ready", "auto", qwenOption("auto", "ready", ready), w))
      .toEqual({ asr_model: "small", remember_asr_model: false });
    // Still checking: nothing is sent, the backend applies the remembered model.
    expect(asrRequest(config, "qwen", models, "checking", "zh", qwenOption("zh", "checking", null), w)).toEqual({});
    expect(asrRequest({ ...config, asr_backend: "api" } as Config, "qwen", models, "ready", "zh", zhReady, w)).toEqual({});
  });

  it("names what is missing and the command that fixes it", () => {
    expect(qwenMissing(notInstalled)).toEqual({ reason: "未安装 Qwen 环境", command: "./install.sh --with-qwen" });
    expect(qwenMissing(noGpu)).toEqual(
      { reason: "没有可用的 NVIDIA GPU", command: "./install.sh --with-qwen", after: "并检查 NVIDIA 驱动" });
  });

  it("shows cloud transcription as one line and remembers 下课后 per course", () => {
    expect(cloudLine({ ...config, asr_backend: "api" } as Config)).toBe("云端 · whisper-large-v3-turbo");
    expect(refineDefault(config, undefined)).toBe("on");
    expect(refineDefault(config, "off")).toBe("off");
    expect(refineDefault({ ...config, refine: false } as Config, "on")).toBe("on");
    expect(refineDefault({ ...config, refine: false } as Config, "junk")).toBe("off");
  });
});
