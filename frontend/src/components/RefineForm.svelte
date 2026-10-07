<script lang="ts">
  import { api } from "../lib/api";
  import { app, message } from "../lib/state.svelte";
  import type { AsrModel } from "../lib/types";
  import ModelDownload from "./ModelDownload.svelte";

  const config = app.boot!.config;
  let refine = $state(config.refine);
  let model = $state(config.refine_model);
  let qwenPython = $state(config.qwen_python ?? "");
  let models = $state<AsrModel[]>([]);
  let error = $state("");
  let selected = $derived(models.find((m) => m.name === model));

  async function load() {
    try {
      models = (await api.asrModels()).filter((m) => m.family === "qwen");
    } catch (e) {
      error = message(e);
    }
  }
  load();

  export async function save(): Promise<boolean> {
    try {
      await api.saveConfig({ refine, refine_model: model, qwen_python: qwenPython.trim() || null });
      error = "";
      await load();
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }
</script>

<div class="stack">
  <label class="check">
    <input type="checkbox" bind:checked={refine} />
    下课后用 Qwen 重新转录整节课
  </label>
  <p class="hint">更准确的终稿，但下课后要多等几分钟；使用云端转录时不会进行。</p>
  <label class="field">
    <span>校正模型</span>
    <select class="input" bind:value={model}>
      {#each models as item}<option value={item.name}>{item.name}</option>{/each}
      {#if !models.some((m) => m.name === model)}<option value={model}>{model}</option>{/if}
    </select>
  </label>
  {#if selected && !selected.env_ready}
    <p class="warn-text">未找到 Qwen 运行环境：在项目目录运行 <code class="mono">./install-qwen.sh</code>，或在下面填写它的 Python 路径。</p>
  {:else if selected}
    <ModelDownload name={selected.name} cached={selected.cached} ondone={load} />
  {/if}
  <label class="field">
    <span>Qwen 环境的 Python（可选）</span>
    <input class="input mono" bind:value={qwenPython} placeholder="留空则使用项目内的 .venv-qwen" spellcheck="false" />
  </label>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>
