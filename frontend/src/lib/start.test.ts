import { describe, expect, it } from "vitest";
import { asrOverride, liveModel, qwenDefault, qwenRowShown } from "./start";
import type { Config, QwenLive } from "./types";

const config = { asr_backend: "local", asr_model: "small" } as Config;
const ready: QwenLive = { model: "qwen3-asr-1.7b", env_ready: true, cached: true, gpu: true, ready: true,
  install: "./install.sh --with-qwen" };
const missing: QwenLive = { ...ready, gpu: false, ready: false };

describe("start dialog Qwen row (plan N3.1)", () => {
  it("shows only for Chinese with a local Whisper live model", () => {
    expect(qwenRowShown(config, "zh")).toBe(true);
    expect(qwenRowShown(config, "en")).toBe(false);
    expect(qwenRowShown({ ...config, asr_model: "qwen3-asr-0.6b" }, "zh")).toBe(false);
    expect(qwenRowShown({ ...config, asr_backend: "api" } as Config, "zh")).toBe(false);
  });

  it("starts on when ready, unless the course remembered a choice", () => {
    expect(qwenDefault(undefined, true)).toBe(true);
    expect(qwenDefault(undefined, false)).toBe(false);
    expect(qwenDefault("small", true)).toBe(false);
    expect(qwenDefault("qwen3-asr-1.7b", true)).toBe(true);
  });

  it("sends the model only when the row was shown, and never Qwen when it is not ready", () => {
    expect(asrOverride(config, undefined, true, ready, true)).toEqual({ asr_model: "qwen3-asr-1.7b" });
    expect(asrOverride(config, undefined, true, ready, false)).toEqual({ asr_model: "small" });
    expect(asrOverride(config, "qwen3-asr-1.7b", true, missing, true)).toEqual({ asr_model: "small" });
    expect(asrOverride(config, undefined, true, null, true)).toEqual({});
    expect(asrOverride(config, "qwen3-asr-1.7b", false, ready, true)).toEqual({});
  });

  it("names the model the lecture will really use", () => {
    expect(liveModel(config, undefined, true, ready, true)).toBe("qwen3-asr-1.7b");
    expect(liveModel(config, "qwen3-asr-1.7b", false, null, false)).toBe("qwen3-asr-1.7b");
    expect(liveModel(config, undefined, false, null, false)).toBe("small");
    expect(liveModel({ ...config, asr_backend: "api", asr_model: "x" } as Config, "qwen3-asr-1.7b", false, null, false)).toBe("x");
  });
});
