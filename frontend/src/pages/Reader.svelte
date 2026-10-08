<script lang="ts">
  import { tick } from "svelte";
  import Icon from "../components/Icon.svelte";
  import Contents from "../components/reader/Contents.svelte";
  import TranscriptList from "../components/reader/TranscriptList.svelte";
  import { api } from "../lib/api";
  import { renderInline, renderNote } from "../lib/markdown";
  import { citeTarget, currentSection, inRange, parseNote, parseTranscript, reviewBody, type CiteTarget } from "../lib/note";
  import { app, message } from "../lib/state.svelte";

  // 方案 B reader, simplified per plan 6.3: one column, contents on the left, sources in a side panel.
  let { course, file }: { course: string; file: string } = $props();
  const REFRESH_MS = 30_000;  // A note still being recorded is fetched again this often (plan M7).
  const BAR = 81;  // Top bar height including its rule; headings scroll to just below it.

  const path = $derived(`${app.boot?.config.courses_dir}/${course}/LectureNotes/${file}`);
  let content = $state<Record<string, string> | null>(null);
  let error = $state("");
  let openError = $state("");
  let panel = $state<"transcript" | "review" | null>(null);
  // What the panel shows while it slides closed.
  let shown = $state<"transcript" | "review">("transcript");
  let target = $state<CiteTarget | null>(null);
  let current = $state<string | null>(null);
  let article = $state<HTMLElement>();
  let reviewBox = $state<HTMLElement>();
  let panelBody = $state<HTMLElement>();
  let panelTitle = $state<HTMLElement>();
  let panelBox = $state<HTMLElement>();
  let trigger: HTMLElement | null = null;
  let menuOpen = $state(false);
  let menuBox = $state<HTMLElement>();
  let menuButton = $state<HTMLButtonElement>();

  let note = $derived(content ? parseNote(content.main, course) : null);
  let transcript = $derived(content?.transcript ? parseTranscript(content.transcript) : null);
  let rendered = $derived(note ? renderNote(note.body, transcript?.version ?? "live") : null);
  let review = $derived(content?.review ? renderNote(reviewBody(content.review), transcript?.version ?? "live", "review").html : "");
  let found = $derived(!target || !!transcript?.groups.some((group) => group.segments.some((s) => inRange(s, target))));

  async function load() {
    try {
      content = await api.noteContent(path);
      error = "";
      // Home's "N 处待核对" opens the note with the review panel showing.
      if (app.panel === "review" && content.review) {
        app.panel = null;
        tick().then(() => showPanel("review", null));
      }
    } catch (e) {
      // A refresh that fails keeps showing the last good copy.
      if (!content) error = message(e);
    }
  }
  load();

  $effect(() => {
    if (!note?.recording) return;
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  });

  const smooth = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth") as ScrollBehavior;

  async function showPanel(kind: "transcript" | "review", from: HTMLElement | null, cite: CiteTarget | null = null) {
    // A reference inside the review panel replaces the panel's content, so focus later returns to what opened it.
    if (!from || !panelBox?.contains(from)) trigger = from;
    target = cite;
    shown = kind;
    panel = kind;
    await tick();
    const hit = panelBody?.querySelector<HTMLElement>("li.hit");
    if (panelBody) panelBody.scrollTo({ top: hit ? hit.offsetTop - 12 : 0, behavior: hit ? smooth() : "auto" });
    // Keyboard and screen reader users land where the reference points.
    (hit ?? panelTitle)?.focus({ preventScroll: true });
  }

  function closePanel() {
    panel = null;
    target = null;
    trigger?.focus({ preventScroll: true });
    trigger = null;
  }

  const toggle = (kind: "transcript" | "review", event: MouseEvent) =>
    panel === kind ? closePanel() : showPanel(kind, event.currentTarget as HTMLElement);

  // References open the transcript panel instead of following a link; other links never replace the app.
  function follow(event: MouseEvent) {
    const element = event.target as HTMLElement;
    const ref = element.closest<HTMLElement>("button.cite-ref");
    if (ref) {
      const cite = citeTarget(ref.dataset.cite);
      if (cite && transcript) showPanel("transcript", ref, cite);
      return;
    }
    const link = element.closest<HTMLAnchorElement>("a[href]");
    if (!link) return;
    event.preventDefault();
    if (/^https?:\/\//i.test(link.getAttribute("href") ?? "")) window.open(link.href, "_blank", "noopener");
  }

  $effect(() => {
    const boxes = [article, reviewBox].filter((box): box is HTMLElement => !!box);
    boxes.forEach((box) => box.addEventListener("click", follow));
    return () => boxes.forEach((box) => box.removeEventListener("click", follow));
  });

  // The reference whose range is open turns to the accent colour (plan 6.3 rule 8).
  $effect(() => {
    const key = panel === "transcript" && target ? `${target.version}:${target.first}:${target.last}` : "";
    void rendered;
    article?.querySelectorAll<HTMLElement>("button.cite-ref").forEach((ref) => {
      ref.classList.toggle("active", !!key && ref.dataset.cite === key);
      ref.setAttribute("aria-controls", "reader-panel");
    });
  });

  function spy() {
    if (!rendered?.toc.length) return;
    const atEnd = innerHeight + scrollY >= document.documentElement.scrollHeight - 2;
    current = atEnd ? rendered.toc.at(-1)!.id : currentSection(rendered.toc.map((entry) => ({
      id: entry.id, top: document.getElementById(entry.id)?.getBoundingClientRect().top ?? Infinity,
    })), BAR + 40);
  }

  let frame = 0;
  const onscroll = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(spy);
  };

  $effect(() => {
    void rendered;
    tick().then(() => {
      // Headings take focus when jumped to from the contents, without joining the Tab order.
      article?.querySelectorAll<HTMLElement>("h2[id], h3[id]").forEach((heading) => (heading.tabIndex = -1));
      spy();
    });
  });

  function jump(id: string) {
    const heading = document.getElementById(id);
    if (!heading) return;
    heading.scrollIntoView({ behavior: smooth(), block: "start" });
    heading.focus({ preventScroll: true });
    current = id;
  }

  const OPEN_ITEMS = [
    { mode: "reveal", label: "在文件管理器中显示" },
    { mode: "terminal", label: "在终端中打开" },
    { mode: "editor", label: "用编辑器打开" },
  ] as const;

  async function toggleMenu() {
    menuOpen = !menuOpen;
    if (menuOpen) {
      await tick();
      menuBox?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    }
  }

  function closeMenu(refocus = true) {
    menuOpen = false;
    if (refocus) menuButton?.focus();
  }

  async function openWith(mode: "reveal" | "terminal" | "editor") {
    closeMenu();
    openError = "";
    try {
      await api.open(path, mode);
    } catch (e) {
      openError = message(e);
    }
  }

  // A menu button pattern: arrows move between the items, Esc closes and returns to the button.
  function menuKey(event: KeyboardEvent) {
    const items = [...(menuBox?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const index = items.indexOf(document.activeElement as HTMLElement);
    const go = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: items.length - 1 }[event.key];
    if (go != null) {
      event.preventDefault();
      items[(go + items.length) % items.length]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMenu();
    } else if (event.key === "Tab") {
      closeMenu(false);
    }
  }

  function onkey(event: KeyboardEvent) {
    if (event.key === "Escape" && panel) {
      event.preventDefault();
      closePanel();
    }
  }
</script>

<svelte:window onscroll={onscroll} onresize={onscroll} onkeydown={onkey} />
<svelte:document onpointerdown={(event) => menuOpen && menuBox && !menuBox.parentElement?.contains(event.target as Node) && closeMenu(false)} />

<div class="reader">
  <header class="bar">
    <div class="where">
      <a class="back" href="#/" aria-label="返回首页" title="返回首页"><Icon name="back" /></a>
      <span class="course">{course}</span>
    </div>
    <div class="actions">
      {#if content?.transcript}
        <button type="button" class="text-btn" aria-expanded={panel === "transcript"} aria-controls="reader-panel"
          onclick={(event) => toggle("transcript", event)}>原始转录</button>
      {/if}
      {#if content?.review}
        <button type="button" class="text-btn" aria-expanded={panel === "review"} aria-controls="reader-panel"
          onclick={(event) => toggle("review", event)}>待核对</button>
      {/if}
      <div class="open-menu">
        <button type="button" class="text-btn" bind:this={menuButton} aria-haspopup="menu" aria-expanded={menuOpen}
          aria-controls={menuOpen ? "open-menu" : undefined} onclick={toggleMenu}>打开…</button>
        {#if menuOpen}
          <div class="menu" id="open-menu" role="menu" aria-label="打开" tabindex="-1" bind:this={menuBox} onkeydown={menuKey}>
            {#each OPEN_ITEMS as item (item.mode)}
              <button type="button" role="menuitem" tabindex="-1" onclick={() => openWith(item.mode)}>{item.label}</button>
            {/each}
          </div>
        {/if}
      </div>
    </div>
  </header>

  <div class="layout" class:shifted={!!panel}>
    {#if rendered?.toc.length}
      <aside class="side"><Contents entries={rendered.toc} {current} onjump={jump} /></aside>
    {/if}
    <article class="note" bind:this={article}>
      {#if openError}<p class="error-text" role="alert">{openError}</p>{/if}
      {#if error}
        <div class="head">
          <h1>无法打开这份笔记</h1>
          <p class="about">{error}</p>
        </div>
      {:else if !note || !rendered}
        <p class="about">正在载入笔记…</p>
      {:else}
        <div class="head">
          {#if note.recording}<p class="live" role="status"><span class="dot" aria-hidden="true"></span>正在录制，内容会更新</p>{/if}
          <p class="meta">{note.meta.join(" · ")}</p>
          <!-- Sanitised by DOMPurify inside renderInline / renderNote. -->
          <h1>{@html renderInline(note.title)}</h1>
          {#if note.notices.length}
            <div class="notices">
              <span class="label">处理提示</span>
              {#each note.notices as notice (notice)}<p>{@html renderInline(notice)}</p>{/each}
            </div>
          {/if}
          {#if note.about.length}<p class="about">{note.about.join(" ")}</p>{/if}
        </div>
        <div class="prose">{@html rendered.html}</div>
      {/if}
    </article>
  </div>

  <aside id="reader-panel" class="panel" class:open={!!panel} inert={!panel} bind:this={panelBox}
    aria-label={shown === "review" ? "待核对与处理记录" : "原始转录"}>
    <div class="panel-head">
      <h2 tabindex="-1" bind:this={panelTitle}>{shown === "review" ? "待核对与处理记录" : "原始转录"}</h2>
      <button type="button" class="close" aria-label="关闭面板" title="关闭（Esc）" onclick={closePanel}><Icon name="fail" /></button>
    </div>
    <div class="panel-body" bind:this={panelBody}>
      {#if shown === "review"}
        <p class="review-about">以下是模型没有把握的地方。请对照课件或录音确认；程序不会自动修改它们。</p>
        <div class="prose small" bind:this={reviewBox}>{@html review}</div>
      {:else if transcript}
        {#if !found && target}<p class="missing">转录里没有 L{target.first}{target.last > target.first ? `–L${target.last}` : ""}（{target.version}）对应的片段</p>{/if}
        <TranscriptList view={transcript} {target} />
      {/if}
    </div>
  </aside>
</div>

<style>
  .reader { min-height: 100vh; background: #FBFBFA; color: #111111; }
  /* The one black rule of 方案 B sits under this bar (plan 6.3 rule 2). */
  .bar {
    position: sticky;
    top: 0;
    z-index: 3;
    height: 81px;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 0 48px;
    border-bottom: 1px solid #111111;
    background: #FBFBFA;
  }
  .where { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .back {
    flex: none;
    width: 44px;
    height: 44px;
    margin-left: -12px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 999px;
    color: #111111;
  }
  /* Ink, not the accent: the reader keeps the accent for references and the contents (rule 9). */
  .back:hover { color: #111111; background: #EFEFEC; }
  .course { font-size: 15px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .actions { flex: none; display: flex; align-items: center; gap: 4px; }
  .text-btn {
    min-height: 44px;
    padding: 0 12px;
    border: 0;
    background: none;
    font-size: 14px;
    color: #5C5C5A;
    cursor: pointer;
  }
  .text-btn:hover, .text-btn[aria-expanded="true"] {
    color: #111111;
    text-decoration: underline;
    text-decoration-thickness: 1px;
    text-underline-offset: 6px;
  }
  /* The light dropdown surface of plan N2.1. */
  .open-menu { position: relative; }
  .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 6px);
    z-index: 4;
    min-width: 220px;
    padding: 6px 0;
    display: flex;
    flex-direction: column;
    background: #FFFFFF;
    border: 1px solid #111111;
    box-shadow: 0 12px 32px rgba(17, 17, 17, 0.10);
    outline: none;
  }
  .menu button {
    min-height: 44px;
    padding: 0 14px;
    border: 0;
    background: transparent;
    font-size: 15px;
    text-align: left;
    white-space: nowrap;
    color: #111111;
    cursor: pointer;
  }
  .menu button:hover, .menu button:focus-visible { background: #F1F1EE; outline: none; }
  .review-about { margin: 16px 0 8px; font-size: 13px; line-height: 1.7; color: #5C5C5A; }

  .layout {
    display: grid;
    grid-template-columns: 200px minmax(0, 1fr) 200px;
    column-gap: 48px;
    padding: 52px 48px 120px;
  }
  .side { grid-column: 1; position: sticky; top: calc(81px + 52px); align-self: start; max-height: calc(100vh - 81px - 76px); overflow-y: auto; }
  .note { grid-column: 2; justify-self: center; width: 100%; max-width: 44em; min-width: 0; font-size: 17px; }
  /* Wide windows make room for the open panel instead of covering the text. */
  @media (min-width: 1360px) {
    .layout.shifted { grid-template-columns: 200px minmax(0, 1fr) 400px; padding-right: 0; }
  }
  @media (max-width: 1099px) {
    .layout { grid-template-columns: minmax(0, 1fr); padding: 40px 24px 96px; }
    .side { display: none; }
    .note { grid-column: 1; }
    .bar { padding: 0 24px; }
  }

  .head { display: flex; flex-direction: column; gap: 30px; margin-bottom: 56px; }
  .live { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #5C5C5A; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: #111111; }
  .meta { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; color: #5C5C5A; }
  h1 {
    font-family: var(--display);
    font-size: 48px;
    font-weight: 600;
    line-height: 1.2;
    letter-spacing: -0.01em;
    color: #111111;
    text-wrap: balance;
    overflow-wrap: break-word;
  }
  .notices { display: flex; flex-direction: column; gap: 6px; padding: 16px 0; border-top: 1px solid #D9D9D6; border-bottom: 1px solid #D9D9D6; }
  .notices .label { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; color: #5C5C5A; }
  .notices p { font-size: 15px; line-height: 1.7; color: #2A2A29; }
  .about { font-size: 13px; line-height: 1.7; color: #5C5C5A; }

  /* Note body: 17px, line height 1.9, sections spaced by white space rather than rules (rules 1, 2, 7). */
  .prose { font-family: var(--display); font-size: 17px; line-height: 1.9; color: #2A2A29; text-wrap: pretty; overflow-wrap: break-word; }
  .prose :global(:is(h2, h3, h4, h5, h6)) { color: #111111; font-weight: 600; scroll-margin-top: calc(81px + 24px); }
  .prose :global(h2) { margin: 56px 0 0; font-size: 28px; line-height: 1.35; }
  .prose :global(h3) { margin: 56px 0 0; font-size: 22px; line-height: 1.45; }
  .prose :global(h2 + h3) { margin-top: 28px; }
  .prose :global(h4) { margin: 36px 0 0; font-size: 18px; line-height: 1.6; }
  .prose :global(:is(h5, h6)) { margin: 28px 0 0; font-size: 17px; }
  .prose :global(:is(p, ul, ol, blockquote, pre, table, hr, .math-block)) { margin: 14px 0 0; }
  .prose > :global(:first-child) { margin-top: 0; }
  .prose :global(:is(ul, ol)) { padding-left: 1.4em; }
  .prose :global(li + li), .prose :global(li > :is(ul, ol)) { margin-top: 6px; }
  .prose :global(blockquote) { margin-left: 0; margin-right: 0; padding-left: 18px; border-left: 1px solid #D9D9D6; color: #5C5C5A; }
  .prose :global(code) { font-family: var(--mono); font-size: 0.85em; }
  .prose :global(pre) { overflow-x: auto; padding: 14px 16px; border: 1px solid #D9D9D6; font-size: 14px; line-height: 1.6; }
  .prose :global(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; font-size: 15px; line-height: 1.6; }
  .prose :global(:is(th, td)) { padding: 8px 12px; border-bottom: 1px solid #D9D9D6; text-align: left; vertical-align: top; }
  .prose :global(hr) { border: 0; border-top: 1px solid #D9D9D6; }
  .prose :global(a) { color: #111111; text-underline-offset: 3px; }
  .prose :global(a:hover) { color: #111111; text-decoration-thickness: 2px; }
  .prose :global(strong) { color: #111111; font-weight: 600; }
  .prose :global(.math-block) { padding: 16px 0; border-top: 1px solid #D9D9D6; border-bottom: 1px solid #D9D9D6; text-align: center; font-size: 22px; line-height: 1.5; color: #111111; overflow-x: auto; overflow-y: hidden; }
  /* STIX capitals are 0.66em tall, Chinese glyphs about 0.81em above the baseline: at 1.25em an
     inline formula stands as tall as the characters around it. Display math is already 22px. */
  .prose :global(.katex math:not([display="block"])) { font-size: 1.25em; }
  /* A formula KaTeX rejects shows its TeX and a small mark; the rest of the page renders normally. */
  .prose :global(.math-error) { font-family: var(--mono); font-size: 14px; color: #2A2A29; white-space: pre-wrap; overflow-wrap: anywhere; }
  .prose :global(.math-block .math-error) { display: block; }
  .prose :global(.math-error-mark) {
    display: inline-block;
    margin-left: 8px;
    padding: 0 6px;
    border: 1px solid #D9D9D6;
    font-family: var(--ui);
    font-size: 12px;
    line-height: 1.7;
    color: #5C5C5A;
    white-space: nowrap;
  }
  .prose :global(.math-block .math-error-mark) { margin: 8px 0 0; }
  .prose :global(.review) { font-size: 14px; line-height: 1.7; color: #5C5C5A; }
  /* References: small monospace superscripts, grey until hovered or open (rule 8). */
  .prose :global(sup.cite) { font-size: 11px; line-height: 0; vertical-align: super; }
  .prose :global(.cite-ref) {
    padding: 0 1px 0 3px;
    border: 0;
    background: none;
    font-family: var(--mono);
    font-size: 11px;
    line-height: 1;
    color: #767674;
    cursor: pointer;
  }
  .prose :global(.cite-ref:hover), .prose :global(.cite-ref.active) { color: #C42710; }
  .prose.small { font-size: 15px; line-height: 1.75; }
  .prose.small :global(h2) { margin-top: 32px; font-size: 18px; }
  .prose.small :global(h3) { margin-top: 24px; font-size: 16px; }

  /* Sources slide in from the right, 400px wide, closed by default (rule 5). */
  .panel {
    position: fixed;
    top: 81px;
    right: 0;
    bottom: 0;
    z-index: 2;
    width: min(400px, 100vw);
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    background: #FBFBFA;
    border-left: 1px solid #D9D9D6;
    transform: translateX(100%);
    visibility: hidden;
    transition: transform 240ms cubic-bezier(.2, .8, .2, 1), visibility 0s linear 240ms;
  }
  .panel.open { transform: none; visibility: visible; transition: transform 240ms cubic-bezier(.2, .8, .2, 1); }
  .panel-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px 12px 32px; border-bottom: 1px solid #D9D9D6; }
  .panel-head h2 { font-family: var(--mono); font-size: 12px; font-weight: 400; letter-spacing: 0.08em; color: #5C5C5A; }
  .close { width: 44px; height: 44px; display: inline-flex; align-items: center; justify-content: center; border: 0; border-radius: 999px; background: none; color: #111111; cursor: pointer; }
  .close:hover { background: #EFEFEC; }
  .panel-body { position: relative; flex: 1; overflow-y: auto; padding: 8px 32px 40px; }
  .missing { margin: 16px 0 0; font-size: 13px; line-height: 1.7; color: #5C5C5A; }
</style>
