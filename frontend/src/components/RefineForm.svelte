<script lang="ts">
  // Plan N4: "本机 Qwen / 云端 API / 不校正". Cloud refinement uses the transcription service's
  // address and key (the key only reaches the refinement process) and says that audio is uploaded.
  import { api } from "../lib/api";
  import type { ListOption } from "../lib/listbox";
  import { DEFAULT_API_MODEL, QWEN_NOTE, UPLOAD_NOTICE, refineChanges, refineChoice, type RefineChoice } from "../lib/refine";
  import { app, message } from "../lib/state.svelte";
  import type { AsrModel } from "../lib/types";
  import Choices from "./Choices.svelte";
  import KeyField from "./KeyField.svelte";
  import ModelDownload from "./ModelDownload.svelte";
  import Select from "./Select.svelte";

  const config = app.boot!.config;
  let choice = $state<RefineChoice>(refineChoice(config));
  let model = $state(config.refine_model);
  let apiModel = $state(config.refine_api_model ?? DEFAULT_API_MODEL);
  let qwenPython = $state(config.qwen_python ?? "");
  let keyStatus = $state(app.boot!.keys.asr);
  let key = $state("");
  let keyField = $state<KeyField>();
  let models = $state<AsrModel[]>([]);
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
    <Choices label="课后校正" bind:value={choice} options={[
      { value: "local", title: "本机 Qwen", text: "在本机把整节课重新转录一遍，终稿更准确，但下课后要多等几分钟。" },
      { value: "api", title: "云端 API", text: "把录音分段上传到转录服务重新转录，不占用本机显卡。" },
      { value: "off", title: "不校正", text: "下课后马上得到笔记，依据上课时的实时转录。" },
    ]} />
    {#if cloudLive && choice !== "off"}
      <span class="field-note">实时转录使用云端 API 时，下课后不进行校正。</span>
    {/if}
  </div>

  {#if choice === "api"}
    <div class="notice" role="note">
      <p class="upload">{UPLOAD_NOTICE}</p>
      <p>{QWEN_NOTE}。转录服务的地址和 key 与「转录」一节相同。</p>
    </div>
    <div class="grid">
      <label class="field">
        <span>校正模型</span>
        <input class="input mono" bind:value={apiModel} placeholder={DEFAULT_API_MODEL} spellcheck="false" />
      </label>
      <div class="field">
        <span id="refine-service">转录服务</span>
        <div class="input mono readonly" role="textbox" aria-readonly="true" aria-labelledby="refine-service">{config.asr_api_base}</div>
      </div>
    </div>
    <KeyField kind="asr" bind:status={keyStatus} bind:value={key} bind:this={keyField} />
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
  /* Monochrome like the rest of design D: a black rule and weight, not a coloured card. */
  .notice { border-left: 2px solid #111111; padding: 2px 0 2px 14px; display: flex; flex-direction: column; gap: 4px; }
  .notice p { margin: 0; font-size: 13px; line-height: 1.7; color: #5C5C5A; }
  .notice .upload { font-size: 15px; font-weight: 600; color: #111111; }
  /* Read-only: a dotted rule, so it does not read as a field to type in. */
  .readonly { display: block; line-height: 44px; border-bottom-style: dotted; color: #5C5C5A; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
