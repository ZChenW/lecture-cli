<script lang="ts">
  import { api } from "../lib/api";
  import { MISSING } from "../lib/checks";
  import { formatNoteDate, formatNoteTime } from "../lib/format";
  import { navigate, routeHash } from "../lib/router";
  import { app, message } from "../lib/state.svelte";
  import type { Course, Note } from "../lib/types";
  import Icon from "../components/Icon.svelte";
  import StartDialog from "../components/StartDialog.svelte";
  import TopBar from "../components/TopBar.svelte";

  let courses = $state<Course[] | null>(null);
  let notes = $state<Note[] | null>(null);
  let error = $state("");
  let notesError = $state("");
  let adding = $state(false);
  let newName = $state("");
  let addError = $state("");
  let starting = $state(false);
  let selected = $derived(courses?.find((c) => c.name === app.course) ?? courses?.[0] ?? null);
  // Home opens even when setup is incomplete (plan N2.4) and says what is still missing.
  let missing = $derived(app.boot?.missing ?? []);
  let noFolder = $derived(missing.includes("courses_dir"));
  let rowError = $state("");
  let armed = $state<string | null>(null);
  let leaving = $state<string[]>([]);
  let disarm: ReturnType<typeof setTimeout> | undefined;

  function settings(section: string) {
    app.section = section;
    navigate("settings");
  }

  async function reveal(note: Note, mode: "reveal" | "terminal") {
    rowError = "";
    try {
      await api.open(note.path, mode);
    } catch (e) {
      rowError = message(e);
    }
  }

  // Two presses within three seconds; the note and its attachments go to the desktop trash.
  async function remove(note: Note) {
    clearTimeout(disarm);
    if (armed !== note.name) {
      armed = note.name;
      disarm = setTimeout(() => (armed = null), 3000);
      return;
    }
    armed = null;
    rowError = "";
    try {
      await api.deleteNote(note.path);
      leaving = [...leaving, note.name];
      // The row fades out, then leaves the list.
      setTimeout(() => {
        notes = notes?.filter((n) => n.name !== note.name) ?? null;
        leaving = leaving.filter((name) => name !== note.name);
        loadCourses();
      }, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 320);
    } catch (e) {
      rowError = message(e);
    }
  }

  async function loadCourses() {
    if (noFolder) {
      courses = [];
      return;
    }
    try {
      courses = await api.courses();
      error = "";
    } catch (e) {
      error = message(e);
    }
  }
  loadCourses();

  $effect(() => {
    const name = selected?.name;
    notes = null;
    if (!name) return;
    api.notes(name).then((list) => {
      if (selected?.name !== name) return;
      notes = list;  // The backend lists newest first.
      notesError = "";
    }).catch((e) => (notesError = message(e)));
  });

  async function add(event: SubmitEvent) {
    event.preventDefault();
    try {
      const course = await api.addCourse(newName);
      newName = "";
      adding = false;
      addError = "";
      app.course = course.name;
      await loadCourses();
    } catch (e) {
      addError = message(e);
    }
  }
</script>

<TopBar>
  <button type="button" class="btn icon-btn has-tip" aria-label="设置" onclick={() => navigate("settings")}>
    <Icon name="settings" /><span class="tip" aria-hidden="true">设置</span>
  </button>
</TopBar>

<div class="cols">
  <nav class="courses" class:none={noFolder && !error} aria-label="课程">
    <p class="label">课程</p>
    {#if error}<p class="error-text" role="alert">{error}</p>{/if}
    {#if courses?.length === 0 && !noFolder}<p class="hint">还没有课程，先新建一门。</p>{/if}
    <ul>
      {#each courses ?? [] as course (course.name)}
        <li>
          <button type="button" class="course" class:current={course.name === selected?.name}
            aria-current={course.name === selected?.name ? "true" : undefined} onclick={() => (app.course = course.name)}>
            <span class="name">{course.name}</span>
            <span class="meta">{formatNoteDate(course.last_note)}</span>
          </button>
        </li>
      {/each}
    </ul>
    {#if adding}
      <form class="stack add" onsubmit={add}>
        <label class="field">
          <span>课程名称</span>
          <!-- svelte-ignore a11y_autofocus -->
          <input class="input" bind:value={newName} autofocus />
        </label>
        <div class="row">
          <button type="submit" class="btn" disabled={!newName.trim()}>新建</button>
          <button type="button" class="link-btn" onclick={() => { adding = false; addError = ""; }}>取消</button>
        </div>
        {#if addError}<p class="error-text" role="alert">{addError}</p>{/if}
      </form>
    {:else if !noFolder}
      <button type="button" class="link-btn add-btn" onclick={() => (adding = true)}><Icon name="plus" size={16} />新建课程</button>
    {/if}
  </nav>

  <main class="notes">
    {#if noFolder}
      <section class="empty-state" aria-labelledby="empty-title">
        <h1 id="empty-title">还没有课程目录</h1>
        <p>选一个文件夹，它的每个子文件夹是一门课；笔记保存在各门课的 LectureNotes 里。</p>
        <button type="button" class="btn primary large" onclick={() => settings("courses")}>选择课程目录</button>
        {#if missing.length > 1}
          <p class="missing">另外还差：{#each missing.filter((m) => m !== "courses_dir") as item, i (item)}{#if i}、{/if}<a
            href="#/settings" onclick={(event) => { event.preventDefault(); settings(MISSING[item].section); }}>{MISSING[item].label}</a>{/each}</p>
        {/if}
      </section>
    {:else if selected}
      <div class="title-row">
        <h1>{selected.name}</h1>
        <button type="button" class="btn primary large" onclick={() => (starting = true)}>开始上课</button>
      </div>
      {#if missing.length}
        <p class="missing">还差：{#each missing as item, i (item)}{#if i}、{/if}<a href="#/settings"
          onclick={(event) => { event.preventDefault(); settings(MISSING[item].section); }}>{MISSING[item].label}</a>{/each}</p>
      {/if}
      <p class="meta count">{selected.notes_count}份笔记</p>
      {#if notesError}<p class="error-text" role="alert">{notesError}</p>{/if}
      {#if rowError}<p class="error-text row-error" role="alert">{rowError}</p>{/if}
      {#if notes?.length === 0}
        <p class="empty">这门课还没有笔记。点“开始上课”录下第一节。</p>
      {/if}
      <ul>
        {#each notes ?? [] as note, i (note.name)}
          {@const href = routeHash("notes", selected.name, note.name)}
          {@const count = note.review_count ?? 0}
          <li class:leaving={leaving.includes(note.name)} class:armed={armed === note.name}>
            <a class="open" {href}>
              <span class="time">{formatNoteTime(note.started)}</span>
              <span class="kind">{note.kind ?? note.name}</span>
            </a>
            {#if note.empty}
              <!-- Plan GUI-4 Q1.2: the recording recognised nothing. -->
              <span class="no-content">无内容<span class="visually-hidden">：这次录制没有识别出任何内容</span></span>
            {/if}
            {#if count > 0}
              <a class="review has-tip" {href} onclick={() => (app.panel = "review")} aria-describedby="review-tip-{i}">
                <span class="review-dot" aria-hidden="true"></span>{count} 处待核对
                <span class="tip" role="tooltip" id="review-tip-{i}">模型没有把握的地方，需要你对照课件确认</span>
              </a>
            {/if}
            <div class="tools">
              <button type="button" class="tool has-tip" aria-label="在文件管理器中显示" onclick={() => reveal(note, "reveal")}>
                <Icon name="folder" /><span class="tip" aria-hidden="true">在文件管理器中显示</span>
              </button>
              <button type="button" class="tool has-tip" aria-label="在终端中打开" onclick={() => reveal(note, "terminal")}>
                <Icon name="terminal" /><span class="tip" aria-hidden="true">在终端中打开</span>
              </button>
              <button type="button" class="tool delete has-tip" class:confirm={armed === note.name}
                aria-label={armed === note.name ? "确认删除，移入回收站，可以找回" : "删除"} onclick={() => remove(note)}>
                <Icon name="trash" />
                <!-- Plan GUI-3 item 1: a short second step; the tooltip says where the note goes. -->
                {#if armed === note.name}<span class="confirm-text" aria-hidden="true">确认删除</span>
                  <span class="tip" aria-hidden="true">移入回收站，可以找回</span>
                {:else}<span class="tip" aria-hidden="true">删除</span>{/if}
              </button>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </main>
</div>

{#if starting && selected}
  <StartDialog course={selected.name} {missing} onclose={() => (starting = false)} onfix={settings} />
{/if}

<style>
  .cols { display: flex; gap: 56px; padding: 40px 44px; }
  .courses { flex: 0 0 260px; display: flex; flex-direction: column; gap: 12px; }
  /* No courses folder: the empty state on the right says everything. */
  .courses.none { display: none; }
  .notes { flex: 1; min-width: 0; max-width: 44em; }
  ul { list-style: none; margin: 0; padding: 0; }
  .meta { font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .course {
    position: relative;
    width: 100%;
    min-height: 56px;
    padding: 8px 0 8px 16px;
    border: none;
    border-bottom: 1px solid var(--line);
    background: none;
    text-align: left;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    color: var(--text);
  }
  .course .name { font-family: var(--display); font-size: 18px; }
  .course:hover .name { color: var(--fg); }
  /* 方案 B allows the accent on the current item: a bar marks the selected course. */
  .course.current::before {
    content: "";
    position: absolute;
    left: 0;
    top: 10px;
    bottom: 10px;
    width: 3px;
    background: var(--accent);
  }
  .course.current .name { color: var(--fg); font-weight: 600; }
  .add-btn { display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; }
  .add { padding-top: 8px; }
  .title-row { display: flex; align-items: center; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
  h1 { font-family: var(--display); font-weight: 600; font-size: 48px; line-height: 1.1; color: var(--fg); overflow-wrap: anywhere; }
  .count { margin: 8px 0 32px; }
  .empty { color: var(--muted); font-family: var(--display); font-size: 17px; }
  .missing { margin: 10px 0 0; font-size: 14px; color: var(--warn); }
  .missing a { color: var(--warn); text-underline-offset: 3px; }
  .missing a:hover { color: var(--fg); }
  .empty-state { display: flex; flex-direction: column; align-items: flex-start; gap: 20px; padding-top: 24px; }
  .empty-state p:not(.missing) { max-width: 32em; color: var(--muted); font-family: var(--display); font-size: 17px; line-height: 1.7; }
  .row-error { margin-bottom: 12px; }
  .notes ul { margin: 0 -12px; }
  .notes li {
    position: relative;
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 56px;
    padding: 0 4px 0 12px;
    border-top: 1px solid var(--line);
    transition: opacity 300ms ease;
  }
  .notes li:last-child { border-bottom: 1px solid var(--line); }
  .notes li:hover, .notes li:focus-within { background: #f3f3f1; }
  .notes li.leaving { opacity: 0; }
  .open {
    flex: 1;
    min-width: 0;
    align-self: stretch;
    display: flex;
    align-items: center;
    gap: 20px;
    padding: 14px 0;
    text-decoration: none;
    color: var(--text);
  }
  .open:focus-visible { outline-offset: -2px; }
  .time { font-size: 13px; color: var(--muted); flex: 0 0 auto; font-variant-numeric: tabular-nums; }
  .kind { font-family: var(--display); font-size: 17px; flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .open:hover .kind { color: var(--fg); }
  .review {
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 44px;
    padding: 0 8px;
    font-size: 13px;
    color: var(--warn);
    text-decoration: none;
  }
  .review:hover { color: var(--warn); text-decoration: underline; text-underline-offset: 3px; }
  .no-content {
    flex: none;
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 8px;
    font-size: 13px;
    color: var(--muted);
  }
  .review-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  /* Shown on hover or keyboard focus; still in the Tab order when hidden. */
  .tools { flex: none; display: flex; align-items: center; opacity: 0; transition: opacity 120ms ease; }
  li:hover .tools, li:focus-within .tools, li.armed .tools { opacity: 1; }
  .tool {
    min-width: 44px;
    height: 44px;
    padding: 0 12px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    border: 0;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
  }
  .tool:hover { color: var(--fg); }
  .tool:focus-visible { outline-offset: -2px; }
  .tool.confirm { color: var(--error); }
  .confirm-text { font-size: 13px; white-space: nowrap; }
  /* Touch screens have no hover: the tools stay visible. */
  @media (hover: none) { .tools { opacity: 1; } }
  @media (max-width: 900px) {
    .cols { flex-direction: column; gap: 32px; padding: 24px 20px; }
    .courses { flex: none; }
  }
</style>
