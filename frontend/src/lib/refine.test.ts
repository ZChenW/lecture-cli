import { describe, expect, it } from "vitest";
import { DEFAULT_API_MODEL, refineChanges, refineChoice, refineTitle } from "./refine";

const fields = { model: "qwen3-asr-1.7b", qwenPython: "  ", apiModel: " whisper-large-v3 " };

describe("refine settings (plan N4)", () => {
  it("reads the three-way choice from refine and refine_backend", () => {
    expect(refineChoice({ refine: false, refine_backend: "api" })).toBe("off");
    expect(refineChoice({ refine: true, refine_backend: "local" })).toBe("local");
    expect(refineChoice({ refine: true, refine_backend: "api" })).toBe("api");
    // Configurations from before N4 have no refine_backend: local, the default.
    expect(refineChoice({ refine: true } as never)).toBe("local");
  });

  it("writes refine and refine_backend; 不校正 keeps the saved backend", () => {
    expect(refineChanges("api", { refine_backend: "local" }, fields)).toEqual({
      refine: true, refine_backend: "api", refine_model: "qwen3-asr-1.7b", qwen_python: null,
      refine_api_model: "whisper-large-v3" });
    expect(refineChanges("local", { refine_backend: "api" }, fields)).toMatchObject({ refine: true, refine_backend: "local" });
    expect(refineChanges("off", { refine_backend: "api" }, fields)).toMatchObject({ refine: false, refine_backend: "api" });
    expect(refineChanges("off", {} as never, fields)).toMatchObject({ refine: false, refine_backend: "local" });
  });

  it("an emptied model field falls back to whisper-large-v3, not turbo", () => {
    expect(DEFAULT_API_MODEL).toBe("whisper-large-v3");
    expect(refineChanges("api", { refine_backend: "api" }, { ...fields, apiModel: " " }).refine_api_model).toBe("whisper-large-v3");
  });

  it("the start dialog names the backend", () => {
    expect(refineTitle({ refine_backend: "api" })).toBe("用云端 API 校正转录");
    expect(refineTitle({ refine_backend: "local" })).toBe("用 Qwen 校正转录");
  });
});
