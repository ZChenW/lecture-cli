import { describe, expect, it } from "vitest";
import { citeTarget, currentSection, inRange, parseNote, parseTranscript, reviewBody } from "./note";

// Shaped exactly like storage.Journal.render() output.
const MAIN = `# MATH421 · 2026-10-07T09:02:00-04:00

> 已结束 · 含待整理原文。自动课堂笔记；公式和听辨疑点需对照课件核实。
> 时间为录入音频的相对时间（不含暂停），L 为本次转录片段编号。

[原始转录](x.transcript.md) · [随堂记录](x.live.md)

> 记录提示：详细笔记未全部完成；已保留完成章节及剩余原始转录。

## 课后梳理

### 本课要点

- 链接数 [refined-L3](x.transcript.md#refined-L3)

## 学习路线

- **平面同痕与 Reidemeister 移动**：两个图何时表示同一个纽结？ [refined-L1–L80](x.transcript.md#refined-L1)
- **链接数**：如何定义？ [refined-L81–L120](x.transcript.md#refined-L81)

## 详细课堂笔记

### 平面同痕与 Reidemeister 移动

正文。

[待核对与处理记录](x.review.md)
`;

describe("parseNote", () => {
  it("splits the header into meta, notices and small print, and keeps the body from the first section", () => {
    const note = parseNote(MAIN, "MATH421");
    expect(note.meta).toEqual(["MATH421", "2026年10月7日 09:02"]);
    expect(note.title).toBe("平面同痕与 Reidemeister 移动");
    expect(note.notices).toEqual(["详细笔记未全部完成；已保留完成章节及剩余原始转录。"]);
    expect(note.about).toHaveLength(2);
    expect(note.recording).toBe(false);
    expect(note.body.startsWith("## 课后梳理")).toBe(true);
    expect(note.body).not.toContain("x.review.md");
    expect(note.body).not.toContain("原始转录](");
  });

  it("falls back to 课程 课堂笔记 without a learning path and flags a note still being written", () => {
    const live = `# LING200 · 2026-10-07T14:30:00-04:00\n\n> 演示：自造课堂文字，未录音，不是真实课程记录。\n\n> 记录中。自动课堂笔记。\n\n[原始转录](y.transcript.md)\n\n## 随堂预览\n\n尚无已整理内容。\n`;
    const note = parseNote(live, "LING200");
    expect(note.title).toBe("LING200 课堂笔记");
    expect(note.recording).toBe(true);
    expect(note.notices).toEqual(["演示：自造课堂文字，未录音，不是真实课程记录。"]);
    expect(note.body).toBe("## 随堂预览\n\n尚无已整理内容。");
  });

  it("copes with a file that has no sections at all", () => {
    const note = parseNote("随便写的一行", "X");
    expect(note.body).toBe("");
    expect(note.meta).toEqual(["X"]);
  });
});

const TRANSCRIPT = `# MATH421 · 原始转录

> 正文来源版本：refined。原始识别结果未经中文生成改写；时间不含暂停。

## live

<a id="live-L1"></a>

### live-L1 · 00:00:01.00–00:00:07.50

Last time we drew knot diagrams.

## refined

<a id="refined-L1"></a>

### refined-L1 · 00:00:00.00–00:00:30.00

Last time we drew knot diagrams and listed the moves.

<a id="refined-L2"></a>

### refined-L2 · 01:00:30.00–01:01:02.00

Any two diagrams of the same knot.
`;

describe("parseTranscript", () => {
  it("reads both versions with ids, times and text, and the version the notes cite", () => {
    const view = parseTranscript(TRANSCRIPT);
    expect(view.version).toBe("refined");
    expect(view.groups.map((g) => [g.version, g.segments.length])).toEqual([["live", 1], ["refined", 2]]);
    expect(view.groups[1].segments[1]).toEqual({ version: "refined", id: 2, time: "1:00:30–1:01:02", text: "Any two diagrams of the same knot." });
    expect(view.groups[0].segments[0].time).toBe("00:01–00:07");
  });

  it("selects a cited range within one version only", () => {
    const view = parseTranscript(TRANSCRIPT);
    const target = citeTarget("refined:1:2");
    expect(target).toEqual({ version: "refined", first: 1, last: 2 });
    expect(view.groups.flatMap((g) => g.segments).filter((s) => inRange(s, target)).map((s) => `${s.version}-${s.id}`))
      .toEqual(["refined-1", "refined-2"]);
    expect(citeTarget("other:1:2")).toBeNull();
    expect(citeTarget(undefined)).toBeNull();
  });
});

describe("reader helpers", () => {
  it("drops the review attachment's own title", () => {
    expect(reviewBody("# 待核对与处理记录\n\n## 处理提示\n\n文字\n")).toBe("## 处理提示\n\n文字");
  });

  it("marks the last section whose heading passed the reading line", () => {
    const tops = [{ id: "a", top: -400 }, { id: "b", top: 90 }, { id: "c", top: 600 }];
    expect(currentSection(tops, 120)).toBe("b");
    expect(currentSection(tops, 60)).toBe("a");
    expect(currentSection([{ id: "a", top: 300 }], 120)).toBe("a");
    expect(currentSection([], 120)).toBeNull();
  });
});
