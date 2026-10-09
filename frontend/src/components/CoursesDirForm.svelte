<script lang="ts">
  import { api } from "../lib/api";
  import { app, message, refresh } from "../lib/state.svelte";
  import type { Course } from "../lib/types";

  // Settings saves through its section button; the wizard saves on "下一步".
  let { ready = $bindable(false) }: { ready?: boolean } = $props();
  let path = $state(app.boot?.config.courses_dir ?? "");
  let saved = $state(app.boot?.config.courses_dir ?? "");
  let courses = $state<Course[] | null>(null);
  let error = $state("");
  let newCourse = $state("");
  let courseError = $state("");

  const valid = () => !!saved && !app.boot?.problems.some((p) => p.field === "courses_dir");
  $effect(() => { ready = valid() && path.trim() === saved; });

  async function loadCourses() {
    try {
      courses = await api.courses();
    } catch {
      courses = null;
    }
  }
  if (valid()) loadCourses();

  /** Saves the folder when it changed; the backend rejects one that does not exist. */
  export async function save(): Promise<boolean> {
    if (path.trim() === saved && valid()) return true;
    try {
      const result = await api.saveConfig({ courses_dir: path.trim() });
      saved = path = result.config.courses_dir ?? "";
      error = "";
      await refresh();
      await loadCourses();
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }

  async function create(event: SubmitEvent) {
    event.preventDefault();
    try {
      await api.addCourse(newCourse);
      newCourse = "";
      courseError = "";
      await loadCourses();
    } catch (e) {
      courseError = message(e);
    }
  }
</script>

<div class="form">
  <div class="field">
    <label for="courses-dir">位置</label>
    <input id="courses-dir" class="input path" bind:value={path} placeholder="/home/你/Courses" spellcheck="false"
      title="每个子文件夹是一门课，笔记保存到 子文件夹 / LectureNotes"
      onkeydown={(e) => e.key === "Enter" && save()} />
  </div>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
  {#if courses && path.trim() === saved}
    <p class="status" role="status">已找到 {courses.length} 门课程</p>
    <form class="field" data-own-save onsubmit={create}>
      <label for="new-course">{courses.length ? "新建课程" : "新建第一门课"}</label>
      <div class="line">
        <input id="new-course" class="input" bind:value={newCourse} placeholder="例如 MATH421" />
        <button type="submit" class="text-action" disabled={!newCourse.trim()}>新建</button>
      </div>
    </form>
    {#if courseError}<p class="error-text" role="alert">{courseError}</p>{/if}
  {/if}
</div>

<style>
  .form { display: flex; flex-direction: column; gap: 18px; }
  .path { font-family: var(--mono); color: var(--fg); }
  .status { margin: 0; font-size: 13px; color: var(--label); }
  .line { display: flex; align-items: center; gap: 12px; max-width: 420px; }
</style>
