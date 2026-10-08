import { describe, expect, it } from "vitest";
import { fixFor } from "./checks";
import type { Check } from "./types";

const item = (id: string, level: Check["level"], hint = "运行 lecture setup 设置课程目录"): Check =>
  ({ id, label: id, level, detail: "", hint });

describe("check fixes", () => {
  it("sends failing checks to the place in the GUI that fixes them", () => {
    expect(fixFor(item("courses_dir", "fail"))).toEqual({ target: "courses" });
    expect(fixFor(item("notes_service", "fail"))).toEqual({ target: "notes" });
    expect(fixFor(item("asr_weights", "warn"))).toEqual({ target: "asr" });
    expect(fixFor(item("microphone", "fail"))).toEqual({ target: "mic" });
    expect(fixFor(item("refine", "fail"))).toEqual({ target: "refine" });
    expect(fixFor(item("courses_dir", "ok"))).toBeNull();
  });

  it("never passes on the terminal hint", () => {
    for (const id of ["ffmpeg", "wpctl", "cjk_font", "something_new"]) {
      const fix = fixFor(item(id, "fail", "运行 ./install.sh"));
      expect(fix && "note" in fix && fix.note).toBeTruthy();
      expect(JSON.stringify(fix)).not.toMatch(/lecture |\.\/install|运行 /);
    }
  });

  it("explains what a missing CJK serif font changes, without commands", () => {
    const fix = fixFor({ id: "cjk_font", label: "中文字体", level: "warn", detail: "无衬线 Noto Sans CJK SC · 衬线 未找到",
      hint: "未找到中文衬线字体……例如 Arch 的 noto-fonts-cjk" });
    expect(fix && "note" in fix && fix.note).toMatch(/衬线/);
    expect(fix && "note" in fix && fix.note).toMatch(/阅读界面/);
    expect(JSON.stringify(fix)).not.toMatch(/noto-fonts-cjk|fonts-noto-cjk|运行 /);
  });
});
