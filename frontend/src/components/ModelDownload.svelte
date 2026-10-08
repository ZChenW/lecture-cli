<script lang="ts">
  import { api } from "../lib/api";
  import { message } from "../lib/state.svelte";
  import type { Task } from "../lib/types";

  // The 12px line under the model dropdown ("已下载" in both references).
  let { name, cached, ondone }: { name: string; cached: boolean; ondone: () => void } = $props();
  let task = $state<Task | null>(null);
  let error = $state("");

  async function start() {
    error = "";
    try {
      const { task: id } = await api.prepare(name);
      task = await api.task(id);
      while (task.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        task = await api.task(id);
      }
      if (task.status === "done") ondone();
    } catch (e) {
      error = message(e);
    }
  }
</script>

{#if cached}
  <span class="field-note">已下载</span>
{:else if task?.status === "running"}
  <span class="field-note" role="status">正在下载…{task.output.at(-1) ? ` ${task.output.at(-1)}` : ""}</span>
{:else}
  <span class="field-note line">尚未下载<button type="button" class="link-btn small" onclick={start}>下载模型</button></span>
{/if}
{#if task?.status === "failed"}
  <p class="error-text" role="alert">下载失败（退出码 {task.exit_code}）</p>
  <pre class="log">{task.output.join("\n")}</pre>
{/if}
{#if error}<p class="error-text" role="alert">{error}</p>{/if}

<style>
  .line { display: inline-flex; align-items: center; gap: 12px; }
  .small { min-height: 44px; margin-block: -14px; font-size: 12px; color: var(--fg); }
</style>
