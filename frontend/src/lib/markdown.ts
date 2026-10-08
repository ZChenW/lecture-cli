import DOMPurify from "dompurify";
import katex from "katex";
import MarkdownIt, { type StateBlock, type StateCore, type StateInline, type Token } from "markdown-it";

// Notes come from a language model, so the HTML they turn into is never trusted.
const escape = (text: string) => text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

function math(tex: string, displayMode: boolean): string {
  try {
    // MathML only: KaTeX's HTML output needs inline style attributes, which the CSP forbids.
    return katex.renderToString(tex, { displayMode, output: "mathml", throwOnError: true });
  } catch {
    return `<code class="math-error">${escape(tex.trim())}</code><span class="math-error-mark">公式无法渲染</span>`;
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

// Source references such as [L12] or [L3–L5, L8]: quiet metadata, not prose.
const CITATION = /^\[L\d+(?:\s*[–-]\s*L?\d+)?(?:\s*[,，、]\s*L\d+(?:\s*[–-]\s*L?\d+)?)*\]/;

function citation(state: StateInline, silent: boolean): boolean {
  if (state.src[state.pos] !== "[") return false;
  const match = state.src.slice(state.pos).match(CITATION);
  if (!match) return false;
  if (!silent) state.push("citation", "span", 0).content = match[0];
  state.pos += match[0].length;
  return true;
}

// Lines the notes model flags as uncertain ("待核对：…") are styled apart from the notes.
function flagReview(state: { tokens: { type: string; content: string; hidden: boolean; attrJoin(n: string, v: string): void }[] }) {
  state.tokens.forEach((token, index) => {
    if (token.type !== "inline" || !token.content.trimStart().startsWith("待核对")) return;
    const paragraph = state.tokens[index - 1];
    const item = state.tokens[index - 2];
    if (paragraph?.hidden && item?.type === "list_item_open") item.attrJoin("class", "review");
    else if (paragraph?.type === "paragraph_open") paragraph.attrJoin("class", "review");
  });
}

export interface TocEntry { id: string; level: 2 | 3; text: string }
export interface Cite { version: string; first: number; last: number; label: string; title: string }
// The reader's rendering mode: ids on headings, a table of contents, and clickable source references.
interface Env { reader?: { version: string; toc: TocEntry[]; prefix: string } }

// Links written by storage.linked_sources: [live-L12–L15](….transcript.md#live-L12).
const TRANSCRIPT_LINK = /\.transcript\.md#(live|refined)-L(\d+)$/;

/** A source link's target: its version, first and last segment, and a short label without the version. */
export function citeOf(href: string, text: string): Cite | null {
  const target = href.match(TRANSCRIPT_LINK);
  if (!target) return null;
  const range = text.match(/^(?:live|refined)-L(\d+)(?:[–—-]L?(\d+))?/);
  const first = Number(range?.[1] ?? target[2]);
  const last = Math.max(first, Number(range?.[2] ?? first));
  return { version: target[1], first, last, label: last > first ? `L${first}–L${last}` : `L${first}`, title: text.trim() };
}

function citeButton(cite: Cite): string {
  return `<sup class="cite"><button type="button" class="cite-ref" data-cite="${cite.version}:${cite.first}:${cite.last}" `
    + `title="${escape(cite.title)}" aria-label="原始转录 ${escape(cite.label)}">${escape(cite.label)}</button></sup>`;
}

function plainText(children: Token[]): string {
  return children.filter((t) => ["text", "code_inline", "math_inline"].includes(t.type)).map((t) => t.content).join("").trim();
}

function readerTokens(state: StateCore) {
  const reader = (state.env as Env).reader;
  if (!reader) return;
  for (const token of state.tokens) {
    if (token.type === "inline" && token.children) {
      const children: Token[] = [];
      for (let i = 0; i < token.children.length; i++) {
        const child = token.children[i];
        const close = child.type === "link_open" ? token.children.findIndex((t: Token, j: number) => j > i && t.type === "link_close") : -1;
        const cite = close > 0 ? citeOf(String(child.attrGet("href") ?? ""), plainText(token.children.slice(i + 1, close))) : null;
        if (!cite) {
          children.push(child);
          continue;
        }
        const ref = new state.Token("cite_ref", "sup", 0);
        ref.meta = { ...cite };
        children.push(ref);
        i = close;
      }
      token.children = children;
    }
  }
  // After the references are replaced, so their labels stay out of the contents.
  let count = 0;
  state.tokens.forEach((token, index) => {
    if (token.type !== "heading_open") return;
    const id = `${reader.prefix}-${++count}`;
    token.attrSet("id", id);
    const level = Number(token.tag.slice(1));
    const text = plainText(state.tokens[index + 1]?.children ?? []);
    if ((level === 2 || level === 3) && text) reader.toc.push({ id, level, text });
  });
}

const markdown = new MarkdownIt({ html: true, linkify: false });
markdown.inline.ruler.after("escape", "math_inline", inlineMath);
markdown.inline.ruler.before("link", "citation", citation);
markdown.core.ruler.push("review", flagReview);
markdown.core.ruler.push("reader", readerTokens);
markdown.renderer.rules.citation = (tokens, index, _options, env) => {
  const text = tokens[index].content;
  const reader = (env as Env | undefined)?.reader;
  if (!reader) return `<span class="citation">${escape(text)}</span>`;
  const range = text.match(/L(\d+)(?:\s*[–-]\s*L?(\d+))?/)!;
  return citeButton({ version: reader.version, first: Number(range[1]), last: Number(range[2] ?? range[1]),
    label: text.slice(1, -1), title: text.slice(1, -1) });
};
markdown.renderer.rules.cite_ref = (tokens, index) => citeButton(tokens[index].meta as unknown as Cite);
markdown.block.ruler.before("fence", "math_block", blockMath);
markdown.renderer.rules.math_inline = (tokens, index) => math(tokens[index].content, false);
markdown.renderer.rules.math_block = (tokens, index) => `<div class="math-block">${math(tokens[index].content, true)}</div>`;

const PURIFY = { USE_PROFILES: { html: true, mathMl: true }, FORBID_ATTR: ["style"] };

export function renderMarkdown(source: string): string {
  return DOMPurify.sanitize(markdown.render(source), PURIFY);
}

/**
 * A saved note for the reader: headings get ids (sec-1, sec-2, … or another prefix) and h2/h3 form the table of
 * contents; source references become buttons carrying "version:first:last" for the transcript panel.
 * Bare [L…] references use the note's source version.
 */
export function renderNote(source: string, version = "live", prefix = "sec"): { html: string; toc: TocEntry[] } {
  const env: Env = { reader: { version, toc: [], prefix } };
  return { html: DOMPurify.sanitize(markdown.render(source, env as Record<string, unknown>), PURIFY), toc: env.reader!.toc };
}

/** One line of Markdown (a title) without a paragraph around it. */
export function renderInline(source: string): string {
  return DOMPurify.sanitize(markdown.renderInline(source), PURIFY);
}
