<script lang="ts">
  import { api } from "../lib/api";
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

  async function loadCourses() {
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
  <button type="button" class="btn icon-btn" aria-label="设置" title="设置" onclick={() => navigate("settings")}>
    <Icon name="settings" />
  </button>
</TopBar>

<div class="cols">
  <nav class="courses" aria-label="课程">
    <p class="label">课程</p>
    {#if error}<p class="error-text" role="alert">{error}</p>{/if}
    {#if courses?.length === 0}<p class="hint">还没有课程，先新建一门。</p>{/if}
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
    {:else}
      <button type="button" class="link-btn add-btn" onclick={() => (adding = true)}><Icon name="plus" size={16} />新建课程</button>
    {/if}
  </nav>

  <main class="notes">
    {#if selected}
      <div class="title-row">
        <h1>{selected.name}</h1>
        <button type="button" class="btn primary large" onclick={() => (starting = true)}>开始上课</button>
      </div>
      <p class="meta count">{selected.notes_count}份笔记</p>
      {#if notesError}<p class="error-text" role="alert">{notesError}</p>{/if}
      {#if notes?.length === 0}
        <p class="empty">这门课还没有笔记。点“开始上课”录下第一节。</p>
      {/if}
      <ul>
        {#each notes ?? [] as note (note.name)}
          <li>
            <a href={routeHash("notes", selected.name, note.name)}>
              <span class="time">{formatNoteTime(note.started)}</span>
              <span class="kind">{note.kind ?? note.name}</span>
              {#if note.attachments.review}<span class="review"><span class="review-dot" aria-hidden="true"></span>待核对</span>{/if}
              <span class="arrow"><Icon name="next" size={18} /></span>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </main>
</div>

{#if starting && selected}
  <StartDialog course={selected.name} onclose={() => (starting = false)} />
{/if}

<style>
  .cols { display: flex; gap: 56px; padding: 40px 44px; }
  .courses { flex: 0 0 260px; display: flex; flex-direction: column; gap: 12px; }
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
  .notes ul { margin: 0 -12px; }
  .notes li a {
    display: flex;
    align-items: center;
    gap: 20px;
    min-height: 56px;
    padding: 14px 12px;
    border-top: 1px solid var(--line);
    text-decoration: none;
    color: var(--text);
  }
  .notes li:last-child a { border-bottom: 1px solid var(--line); }
  .notes li a:hover { background: #f3f3f1; }
  .time { font-size: 13px; color: var(--muted); flex: 0 0 auto; font-variant-numeric: tabular-nums; }
  .kind { font-family: var(--display); font-size: 17px; flex: 1; }
  .notes li a:hover .kind { color: var(--fg); }
  .review { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--warn); }
  .review-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  .arrow { display: inline-flex; color: var(--faint); transition: transform 120ms ease; }
  .notes li a:hover .arrow { color: var(--accent); transform: translateX(3px); }
  @media (max-width: 900px) {
    .cols { flex-direction: column; gap: 32px; padding: 24px 20px; }
    .courses { flex: none; }
  }
</style>
