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
  <button type="button" class="btn primary" disabled={!selected} onclick={() => (starting = true)}>开始上课</button>
  <button type="button" class="btn icon-btn" aria-label="设置" title="设置" onclick={() => navigate("settings")}>
    <Icon name="settings" />
  </button>
</TopBar>

<div class="cols">
  <nav class="courses" aria-label="课程">
    <p class="label mono">课程</p>
    {#if error}<p class="error-text" role="alert">{error}</p>{/if}
    {#if courses?.length === 0}<p class="hint">还没有课程，先新建一门。</p>{/if}
    <ul>
      {#each courses ?? [] as course (course.name)}
        <li>
          <button type="button" class="course" class:current={course.name === selected?.name}
            aria-current={course.name === selected?.name ? "true" : undefined} onclick={() => (app.course = course.name)}>
            <span class="name">{course.name}</span>
            <span class="meta mono">{formatNoteDate(course.last_note)}</span>
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
      <h1>{selected.name}</h1>
      <p class="meta mono">{selected.notes_count} 份笔记</p>
      {#if notesError}<p class="error-text" role="alert">{notesError}</p>{/if}
      {#if notes?.length === 0}
        <p class="empty">这门课还没有笔记。点右上角“开始上课”录下第一节。</p>
      {/if}
      <ul>
        {#each notes ?? [] as note (note.name)}
          <li>
            <a href={routeHash("notes", selected.name, note.name)}>
              <span class="time mono">{formatNoteTime(note.started)}</span>
              <span class="kind">{note.kind ?? note.name}</span>
              {#if note.attachments.review}<span class="tag mono">待核对</span>{/if}
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
  .course {
    width: 100%;
    min-height: 56px;
    padding: 8px 0;
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
  .course.current .name { color: var(--fg); font-weight: 600; }
  .meta { font-size: 12px; color: var(--faint); }
  .add-btn { display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; }
  .add { padding-top: 8px; }
  h1 { font-family: var(--display); font-weight: 600; font-size: 48px; line-height: 1.1; color: var(--fg); }
  .notes > .meta { margin: 8px 0 32px; }
  .empty { color: var(--muted); font-family: var(--display); font-size: 17px; }
  .notes li a {
    display: flex;
    align-items: baseline;
    gap: 20px;
    min-height: 56px;
    padding: 16px 0;
    border-top: 1px solid var(--line);
    text-decoration: none;
    color: var(--text);
  }
  .notes li:last-child a { border-bottom: 1px solid var(--line); }
  .time { font-size: 13px; color: var(--muted); flex: 0 0 auto; }
  .kind { font-family: var(--display); font-size: 17px; flex: 1; }
  .notes li a:hover .kind { color: var(--accent); }
  .tag { font-size: 12px; color: var(--warn); border: 1px solid currentColor; border-radius: 2px; padding: 0 6px; }
  @media (max-width: 900px) {
    .cols { flex-direction: column; gap: 32px; padding: 24px 20px; }
    .courses { flex: none; }
  }
</style>
