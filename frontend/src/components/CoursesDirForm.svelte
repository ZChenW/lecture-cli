<script lang="ts">
  import { api } from "../lib/api";
  import { app, message, refresh } from "../lib/state.svelte";
  import type { Course } from "../lib/types";

  // Settings saves through its section button, so the inline check button is the wizard's alone.
  let { ready = $bindable(false), checkButton = true }: { ready?: boolean; checkButton?: boolean } = $props();
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

<div class="stack">
  <div class="field">
    <label for="courses-dir">课程目录</label>
    <div class="row">
      <input id="courses-dir" class="input grow" bind:value={path} placeholder="/home/你/Courses" spellcheck="false"
        onkeydown={(e) => e.key === "Enter" && save()} />
      {#if checkButton}<button type="button" class="btn" onclick={save}>检查并保存</button>{/if}
    </div>
  </div>
  <p class="hint">每个子文件夹是一门课，笔记保存到 子文件夹/LectureNotes。</p>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
  {#if courses && path.trim() === saved}
    <p class="ok-text" role="status">检测到 {courses.length} 门课程{courses.length ? `：${courses.map((c) => c.name).join("、")}` : ""}</p>
    <form class="row" onsubmit={create}>
      <label class="field grow">
        <span>{courses.length ? "新建课程" : "新建第一门课"}</span>
        <input class="input" bind:value={newCourse} placeholder="例如 MATH421" />
      </label>
      <button type="submit" class="btn align-end" disabled={!newCourse.trim()}>新建</button>
    </form>
    {#if courseError}<p class="error-text" role="alert">{courseError}</p>{/if}
  {/if}
</div>

<style>
  .grow { flex: 1 1 260px; min-width: 0; }
  .align-end { align-self: flex-end; }
</style>
