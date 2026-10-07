<script lang="ts">
  import { api } from "../lib/api";
  import { message } from "../lib/state.svelte";
  import type { Task } from "../lib/types";
  import Icon from "./Icon.svelte";

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

<div class="row">
  {#if cached}
    <span class="ok-text">{name} 已下载</span>
  {:else if task?.status === "running"}
    <span class="hint" role="status">正在下载 {name}…{task.output.at(-1) ? ` ${task.output.at(-1)}` : ""}</span>
  {:else}
    <span class="warn-text">{name} 尚未下载</span>
    <button type="button" class="btn" onclick={start}><Icon name="download" />下载模型</button>
  {/if}
</div>
{#if task?.status === "failed"}
  <p class="error-text" role="alert">下载失败（退出码 {task.exit_code}）</p>
  <pre class="log">{task.output.join("\n")}</pre>
{/if}
{#if error}<p class="error-text" role="alert">{error}</p>{/if}
