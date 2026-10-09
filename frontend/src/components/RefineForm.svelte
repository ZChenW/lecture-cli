<script lang="ts">
  // Plan N4: "本机 Qwen / 云端 API / 不校正". Cloud refinement uses the transcription service's
  // address and key (the key only reaches the refinement process) and says that audio is uploaded.
  import { api } from "../lib/api";
  import type { ListOption } from "../lib/listbox";
  import { DEFAULT_API_MODEL, UPLOAD_NOTICE, refineChanges, refineChoice, type RefineChoice } from "../lib/refine";
  import { app, message } from "../lib/state.svelte";
  import type { AsrModel, Result } from "../lib/types";
  import Choices from "./Choices.svelte";
  import KeyField from "./KeyField.svelte";
  import ModelDownload from "./ModelDownload.svelte";
  import Select from "./Select.svelte";
  import ServiceTest from "./ServiceTest.svelte";

  const config = app.boot!.config;
  let choice = $state<RefineChoice>(refineChoice(config));
  let model = $state(config.refine_model);
  let apiModel = $state(config.refine_api_model ?? DEFAULT_API_MODEL);
  let qwenPython = $state(config.qwen_python ?? "");
  let keyStatus = $state(app.boot!.keys.asr);
  let key = $state("");
  let keyField = $state<KeyField>();
  let models = $state<AsrModel[]>([]);
  // Plan GUI-3 item 2: test the saved transcription service with the refinement model.
  let tester = $state<ServiceTest>();
  let tested = $state<(Result & { signature: string }) | null>(null);
  let testing = $state(false);
  let signature = $derived(JSON.stringify([apiModel.trim() || DEFAULT_API_MODEL, key, keyStatus.set]));
  let error = $state("");
  let selected = $derived(models.find((m) => m.name === model));
  let options = $derived<ListOption[]>([
    ...models.map((m) => ({ value: m.name, label: m.name })),
    ...(models.some((m) => m.name === model) ? [] : [{ value: model, label: model }]),
  ]);
  // Cloud live transcription keeps no recording to refine (cli.session).
  const cloudLive = config.asr_backend === "api";

  async function load() {
    try {
      models = (await api.asrModels()).filter((m) => m.family === "qwen");
    } catch (e) {
      error = message(e);
    }
  }
  load();

  // The address is set in 转录; the link goes there like the settings list does.
  function toAsr(event: MouseEvent) {
    event.preventDefault();
    document.getElementById("asr")?.scrollIntoView({ block: "start" });
    document.getElementById("asr-title")?.focus({ preventScroll: true });
  }

  export async function save(): Promise<boolean> {
    try {
      if (choice === "api" && keyField && !(await keyField.save())) return false;
      await api.saveConfig(refineChanges(choice, config, { model, qwenPython, apiModel }));
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
    <!-- PLAN-GUI-5 R1: the explanations are tooltips; the upload notice is the one line kept. -->
    <Choices label="课后校正" bind:value={choice} options={[
      { value: "local", title: "本机 Qwen", tag: "中文推荐", tip: "在本机把整节课重新转录一遍，终稿更准确，但下课后要多等几分钟" },
      { value: "api", title: "云端 API", text: UPLOAD_NOTICE, tip: "把录音分段上传到转录服务重新转录，不占用本机显卡；地址和 key 与「转录」一节相同" },
      { value: "off", title: "不校正", tip: "下课后马上得到笔记，依据上课时的实时转录" },
    ]} />
    {#if cloudLive && choice !== "off"}
      <span class="field-note">实时转录使用云端 API 时，下课后不进行校正。</span>
    {/if}
  </div>

  {#if choice === "api"}
    <div class="grid">
      <label class="field">
        <span>校正模型</span>
        <input class="input mono" bind:value={apiModel} placeholder={DEFAULT_API_MODEL} spellcheck="false" />
      </label>
      <div class="field">
        <span id="refine-service">转录服务</span>
        <!-- Not editable here, so it is plain text, not something that looks like a field. -->
        <p class="readonly">
          <span class="mono address">{config.asr_api_base}</span>
          <a href="#/settings" onclick={toAsr}>在「转录」一节修改</a>
        </p>
      </div>
    </div>
    <KeyField kind="asr" bind:status={keyStatus} bind:value={key} bind:this={keyField} {testing} ontest={() => tester?.run()} />
    <ServiceTest bind:this={tester} kind="asr" provider={config.asr_provider} apiBase={config.asr_api_base}
      model={apiModel.trim() || DEFAULT_API_MODEL} {key} {signature} bind:result={tested} bind:busy={testing} />
  {:else if choice === "local"}
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
  {/if}
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .form { display: flex; flex-direction: column; gap: 28px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 28px 32px; }
  .warn-text.field-note { color: var(--warn); }
  /* One line of monospace text and a link, like the other read-only details. */
  .readonly { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 16px; min-height: 44px; margin: 0; padding-top: 10px; font-size: 14px; }
  .readonly .address { color: var(--text); overflow-wrap: anywhere; }
  .readonly a { color: var(--fg); text-underline-offset: 4px; white-space: nowrap; }
</style>
