// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { citeOf, renderInline, renderMarkdown, renderNote } from "./markdown";

describe("renderMarkdown", () => {
  it("removes scripts, event handlers and inline styles from model output", () => {
    const html = renderMarkdown(
      '# 标题\n\n<script>alert(1)</script>\n\n<img src="x" onerror="alert(2)">\n\n<p style="color:red" onclick="x()">文字</p>\n\n[链接](javascript:alert(3))',
    );
    expect(html).toContain("<h1>标题</h1>");
    expect(html).not.toMatch(/<script|onerror|onclick|style=|href="javascript/i);
    expect(html).toContain("文字");
  });

  it("renders $…$ and $$…$$ as MathML and keeps transcript anchors", () => {
    const html = renderMarkdown('特征值 $\\lambda$ 满足\n\n$$\nA v = \\lambda v\n$$\n\n<a id="live-L12"></a>原文');
    expect(html).toContain("<math");
    expect(html).toContain('class="math-block"');
    expect(html).toContain('id="live-L12"');
    expect(html).not.toContain("style=");
  });

  it("leaves money alone and marks formulas that fail", () => {
    expect(renderMarkdown("花了 $5 和 $6")).not.toContain("<math");
    const broken = renderMarkdown("$\\frac{1$");
    expect(broken).toContain("公式无法渲染");
    expect(broken).toContain("\\frac{1");
  });

  it("styles source references and flags 待核对 lines", () => {
    const html = renderMarkdown("- 特征向量非零 [L212]\n- 待核对：符号约定 [L3–L5, L8]\n\n待核对：板书未出现\n\n[链接](https://x.example) [L]");
    expect(html).toContain('<span class="citation">[L212]</span>');
    expect(html).toContain('<span class="citation">[L3–L5, L8]</span>');
    expect(html).toContain('<li class="review">待核对：符号约定');
    expect(html).toContain('<p class="review">待核对：板书未出现</p>');
    expect(html).toContain('<a href="https://x.example">链接</a> [L]');
  });
});

describe("renderNote", () => {
  const note = `## 学习路线

- **链接数**：如何定义？ [refined-L81–L120](x.transcript.md#refined-L81)

## 详细课堂笔记

### 链接数 [refined-L81](x.transcript.md#refined-L81)

取双分量有向链环。[live-L214–216 00:46:01–00:46:20](x.transcript.md#live-L214) 另见 [L3–L5, L8]。

#### 例子

[外部链接](https://example.org)

$$\\frac{1}{2$$
`;

  it("turns source links into buttons that carry their range, and numbers headings for the contents", () => {
    const { html, toc } = renderNote(note, "refined");
    expect(toc).toEqual([
      { id: "sec-1", level: 2, text: "学习路线" },
      { id: "sec-2", level: 2, text: "详细课堂笔记" },
      { id: "sec-3", level: 3, text: "链接数" },
    ]);
    expect(html).toContain('<h4 id="sec-4">例子</h4>');
    expect(html).toContain('<sup class="cite"><button type="button" class="cite-ref" data-cite="refined:81:120"');
    expect(html).toContain('data-cite="live:214:216"');
    expect(html).toContain('>L214–L216</button></sup>');
    // A bare reference list points at the note's own version, from its first range.
    expect(html).toContain('data-cite="refined:3:5"');
    expect(html).not.toContain("x.transcript.md");
    expect(html).toContain('<a href="https://example.org">外部链接</a>');
  });

  it("shows a formula that fails as raw TeX with a mark, without breaking the page", () => {
    const { html } = renderNote(note);
    expect(html).toContain('<div class="math-block"><code class="math-error">\\frac{1}{2</code><span class="math-error-mark">公式无法渲染</span></div>');
  });

  it("still sanitises model output in reader mode", () => {
    const { html } = renderNote('## 标题\n\n<button onclick="x()" data-cite="live:1:1">伪造</button><img src=x onerror=alert(1)>');
    expect(html).not.toMatch(/onclick|onerror/);
  });

  it("parses reference targets and leaves other links alone", () => {
    expect(citeOf("a%20b.transcript.md#live-L12", "live-L12")).toEqual(
      { version: "live", first: 12, last: 12, label: "L12", title: "live-L12" });
    expect(citeOf("x.transcript.md#refined-L3", "refined-L3–L9 00:01:00.00–00:02:00.00")?.label).toBe("L3–L9");
    expect(citeOf("https://example.org", "live-L1")).toBeNull();
    // Links from the main note go through the attachment folder.
    expect(citeOf("%E5%8E%9F%E6%96%87%E4%B8%8E%E8%AE%B0%E5%BD%95/x.transcript.md#refined-L7", "refined-L7–L9")).toEqual(
      { version: "refined", first: 7, last: 9, label: "L7–L9", title: "refined-L7–L9" });
  });

  it("renders a title line inline", () => {
    expect(renderInline("**链接数** 与 $L$")).toMatch(/^<strong>链接数<\/strong> 与 <span class="katex"><math/);
  });
});
