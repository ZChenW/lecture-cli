<script lang="ts">
  import { api } from "../lib/api";
  import { app, message } from "../lib/state.svelte";
  import TopBar from "../components/TopBar.svelte";
  import Icon from "../components/Icon.svelte";

  // Until the reader exists, a note opens in the system's default Markdown application.
  let { course, file }: { course: string; file: string } = $props();
  let error = $state("");
  const path = $derived(`${app.boot?.config.courses_dir}/${course}/LectureNotes/${file}`);

  async function open(mode: "file" | "folder") {
    try {
      await api.open(mode === "file" ? path : path.slice(0, path.lastIndexOf("/")), mode);
      error = "";
    } catch (e) {
      error = message(e);
    }
  }
</script>

<TopBar>
  <a class="btn" href="#/"><Icon name="back" />返回首页</a>
</TopBar>
<main class="note">
  <p class="label mono">{course}</p>
  <h1>{file}</h1>
  <div class="row">
    <button type="button" class="btn" onclick={() => open("file")}>用默认程序打开</button>
    <button type="button" class="btn" onclick={() => open("folder")}>打开所在文件夹</button>
  </div>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</main>

<style>
  .note { max-width: 44em; margin: 0 auto; padding: 48px 44px; display: flex; flex-direction: column; gap: 20px; }
  h1 { font-family: var(--display); font-weight: 600; font-size: 32px; color: var(--fg); overflow-wrap: anywhere; }
</style>
