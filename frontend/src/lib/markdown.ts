import DOMPurify from "dompurify";
import katex from "katex";
import MarkdownIt, { type StateBlock, type StateInline } from "markdown-it";

// Notes come from a language model, so the HTML they turn into is never trusted.
const escape = (text: string) => text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

function math(tex: string, displayMode: boolean): string {
  try {
    // MathML only: KaTeX's HTML output needs inline style attributes, which the CSP forbids.
    return katex.renderToString(tex, { displayMode, output: "mathml", throwOnError: true });
  } catch {
    return `<code class="math-error">${escape(tex)}</code><span class="math-error-mark">公式无法渲染</span>`;
  }
}

function inlineMath(state: StateInline, silent: boolean): boolean {
  const start = state.pos;
  if (state.src[start] !== "$" || state.src[start + 1] === "$") return false;
  const end = state.src.indexOf("$", start + 1);
  // "$5 and $6" is money, not math: require no space just inside the delimiters.
  if (end < 0 || /\s/.test(state.src[start + 1] ?? " ") || /\s/.test(state.src[end - 1])) return false;
  if (!silent) state.push("math_inline", "math", 0).content = state.src.slice(start + 1, end);
  state.pos = end + 1;
  return true;
}

function blockMath(state: StateBlock, startLine: number, endLine: number, silent: boolean): boolean {
  const first = state.src.slice(state.bMarks[startLine] + state.tShift[startLine], state.eMarks[startLine]);
  if (!first.startsWith("$$")) return false;
  if (silent) return true;
  let body = first.slice(2);
  let line = startLine;
  while (!body.trimEnd().endsWith("$$") && ++line < endLine) {
    body += "\n" + state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
  }
  const token = state.push("math_block", "math", 0);
  token.content = body.trimEnd().replace(/\$\$$/, "");
  token.map = [startLine, line + 1];
  state.line = line + 1;
  return true;
}

const markdown = new MarkdownIt({ html: true, linkify: false });
markdown.inline.ruler.after("escape", "math_inline", inlineMath);
markdown.block.ruler.before("fence", "math_block", blockMath);
markdown.renderer.rules.math_inline = (tokens, index) => math(tokens[index].content, false);
markdown.renderer.rules.math_block = (tokens, index) => `<div class="math-block">${math(tokens[index].content, true)}</div>`;

export function renderMarkdown(source: string): string {
  return DOMPurify.sanitize(markdown.render(source), {
    USE_PROFILES: { html: true, mathMl: true },
    FORBID_ATTR: ["style"],
  });
}
