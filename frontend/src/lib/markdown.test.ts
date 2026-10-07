// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

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
