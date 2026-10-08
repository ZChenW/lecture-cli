<script lang="ts">
  import { api } from "../lib/api";
  import type { ListOption } from "../lib/listbox";
  import { app, message } from "../lib/state.svelte";
  import type { AsrModel } from "../lib/types";
  import Choices from "./Choices.svelte";
  import ModelDownload from "./ModelDownload.svelte";
  import Select from "./Select.svelte";

  const config = app.boot!.config;
  let refine = $state(config.refine ? "on" : "off");
  let model = $state(config.refine_model);
  let qwenPython = $state(config.qwen_python ?? "");
  let models = $state<AsrModel[]>([]);
  let error = $state("");
  let selected = $derived(models.find((m) => m.name === model));
  let options = $derived<ListOption[]>([
    ...models.map((m) => ({ value: m.name, label: m.name })),
    ...(models.some((m) => m.name === model) ? [] : [{ value: model, label: model }]),
  ]);

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
      await api.saveConfig({ refine: refine === "on", refine_model: model, qwen_python: qwenPython.trim() || null });
      error = "";
      await load();
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }
</script>

<div class="form">
  <div class="field">
    <span class="label">下课后</span>
    <Choices label="课后校正" bind:value={refine} options={[
      { value: "on", title: "重新转录整节课", text: "终稿更准确，但下课后要多等几分钟。使用云端转录时不会进行。" },
      { value: "off", title: "直接用实时转录", text: "下课后马上得到笔记，依据上课时的实时转录。" },
    ]} />
  </div>
  <div class="grid">
    <div class="field">
      <span id="refine-model">校正模型</span>
      <Select labelledby="refine-model" mono {options} bind:value={model} />
      {#if selected && !selected.env_ready}
        <span class="field-note warn-text">本地 Qwen 运行环境未安装，校正会被跳过</span>
      {:else if selected}
        <ModelDownload name={selected.name} cached={selected.cached} ondone={load} />
      {/if}
    </div>
    <label class="field">
      <span>Qwen 环境的 Python（可选）</span>
      <input class="input mono" bind:value={qwenPython} placeholder="留空则使用项目内的 .venv-qwen" spellcheck="false" />
    </label>
  </div>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .form { display: flex; flex-direction: column; gap: 28px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 28px 32px; }
  .warn-text.field-note { color: var(--warn); }
</style>
