// The saved note's structure, as written by storage.Journal.render(); pure so it can be tested.
import { formatNoteTime } from "./format";
import { segmentTime } from "./record";

export interface ParsedNote {
  /** "MATH421 · 2026年10月7日 09:02" parts. */
  meta: string[];
  title: string;
  /** 处理提示: the record warning and the demo disclaimer. */
  notices: string[];
  /** The remaining header lines (state, how to read times), shown as small print. */
  about: string[];
  /** From the first "## " section on, without the trailing link to the review attachment. */
  body: string;
  /** The session is still writing this file ("记录中"). */
  recording: boolean;
}

// The main file links its attachments; the reader has buttons for them instead.
const ATTACHMENT_LINE = /^\[[^\]]+\]\([^)]*\.(?:transcript|live|review)\.md\)(?:\s*·\s*\[[^\]]+\]\([^)]*\.md\))*\s*$/;

export function parseNote(main: string, course: string): ParsedNote {
  const lines = main.replace(/\r\n/g, "\n").split("\n");
  const first = lines.findIndex((line) => line.startsWith("## "));
  const head = first < 0 ? lines : lines.slice(0, first);
  const h1 = head.find((line) => line.startsWith("# "))?.slice(2).trim() ?? "";
  const started = h1.match(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[^\s]*)\s*$/)?.[1] ?? null;
  const notices: string[] = [];
  const about: string[] = [];
  for (const line of head) {
    if (!line.startsWith(">")) continue;
    const text = line.replace(/^>\s?/, "").trim();
    if (!text) continue;
    if (text.startsWith("记录提示：")) notices.push(text.slice("记录提示：".length));
    else if (text.startsWith("演示：")) notices.push(text);
    else about.push(text);
  }
  const body = (first < 0 ? [] : lines.slice(first)).filter((line) => !ATTACHMENT_LINE.test(line.trim())).join("\n").trim();
  return {
    meta: [course, started ? formatNoteTime(started) : ""].filter(Boolean),
    title: firstTopic(body) ?? `${course} 课堂笔记`,
    notices,
    about,
    body,
    recording: about.some((line) => line.startsWith("记录中")),
  };
}

/** The learning path's first topic: "- **标题**：问题 [refined-L1–L80](…)". */
function firstTopic(body: string): string | null {
  const start = body.search(/^## 学习路线\s*$/m);
  if (start < 0) return null;
  const section = body.slice(start).split("\n").slice(1);
  const end = section.findIndex((line) => line.startsWith("## "));
  const item = (end < 0 ? section : section.slice(0, end)).find((line) => /^- \*\*.+?\*\*/.test(line));
  return item?.match(/^- \*\*(.+?)\*\*/)?.[1].trim() || null;
}

export interface Segment { version: string; id: number; time: string; text: string }
export interface TranscriptView { version: string; intro: string[]; groups: { version: string; segments: Segment[] }[] }

const ANCHOR = /^<a id="(live|refined)-L(\d+)"><\/a>$/;

/** The transcript attachment: "## live" / "## refined" groups of anchored segments (storage.py). */
export function parseTranscript(text: string): TranscriptView {
  const view: TranscriptView = { version: "live", intro: [], groups: [] };
  let group: TranscriptView["groups"][number] | null = null;
  let segment: Segment | null = null;
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trim();
    const anchor = line.match(ANCHOR);
    if (/^## (live|refined)$/.test(line)) {
      group = { version: line.slice(3), segments: [] };
      view.groups.push(group);
      segment = null;
    } else if (anchor && group) {
      segment = { version: anchor[1], id: Number(anchor[2]), time: "", text: "" };
      group.segments.push(segment);
    } else if (segment && line.startsWith("### ") && !segment.time && !segment.text) {
      const [start, end] = (line.split(" · ")[1] ?? "").split(/[–—-]/);
      segment.time = [segmentTime(start), segmentTime(end)].filter(Boolean).join("–");
    } else if (segment && line) {
      segment.text = segment.text ? `${segment.text}\n${line}` : line;
    } else if (!group && line.startsWith(">")) {
      const intro = line.replace(/^>\s?/, "");
      view.intro.push(intro);
      view.version = intro.match(/正文来源版本：(live|refined)/)?.[1] ?? view.version;
    }
  }
  return view;
}

export interface CiteTarget { version: string; first: number; last: number }

/** data-cite="live:12:15" on a reference button. */
export function citeTarget(value: string | undefined | null): CiteTarget | null {
  const match = value?.match(/^(live|refined):(\d+):(\d+)$/);
  return match ? { version: match[1], first: Number(match[2]), last: Number(match[3]) } : null;
}

export function inRange(segment: Segment, target: CiteTarget | null): boolean {
  return !!target && segment.version === target.version && segment.id >= target.first && segment.id <= target.last;
}

/** Hides the review attachment's own title: the panel heading names it already. */
export function reviewBody(text: string): string {
  return text.replace(/^# [^\n]*\n+/, "").trim();
}

/** The section to mark in the contents: the last heading whose top has passed the reading line. */
export function currentSection(tops: { id: string; top: number }[], line: number): string | null {
  let current: string | null = tops[0]?.id ?? null;
  for (const { id, top } of tops) {
    if (top <= line) current = id;
    else break;
  }
  return current;
}
