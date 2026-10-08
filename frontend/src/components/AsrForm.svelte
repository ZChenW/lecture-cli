<script lang="ts">
  import { api } from "../lib/api";
  import type { ListOption } from "../lib/listbox";
  import { app, message } from "../lib/state.svelte";
  import type { AsrModel, Result } from "../lib/types";
  import Choices from "./Choices.svelte";
  import KeyField from "./KeyField.svelte";
  import ModelDownload from "./ModelDownload.svelte";
  import Select from "./Select.svelte";
  import ServiceTest from "./ServiceTest.svelte";

  // look: "tabs" in settings (design D), "rows" in the wizard (design E). gpu: the wizard's GPU
  // detection (null while unknown); preselect: let it choose the mode while the choice is still the
  // default one. Settings also offers the recognition device.
  let { ready = $bindable(false), gpu = undefined, preselect = false, settings = false, look = "tabs" }: {
    ready?: boolean; gpu?: boolean | null; preselect?: boolean; settings?: boolean; look?: "tabs" | "rows";
  } = $props();

  const boot = app.boot!;
  const config = boot.config;
  const presets = boot.presets.asr;
  let models = $state<AsrModel[]>([]);
  let qwenNames = $derived(models.filter((m) => m.family === "qwen").map((m) => m.name));
  let mode = $state<"whisper" | "qwen" | "api">(
    config.asr_backend === "api" ? "api" : config.asr_model.startsWith("qwen") ? "qwen" : "whisper");
  let touched = $state(false);
  let whisperModel = $state(config.asr_model.startsWith("qwen") ? "base.en" : config.asr_model);
  let qwenModel = $state(config.asr_model.startsWith("qwen") ? config.asr_model : "");
  let provider = $state(config.asr_provider in presets ? config.asr_provider : "custom");
  let apiBase = $state(config.asr_api_base);
  let apiModel = $state(config.asr_api_model);
  let language = $state(config.language);
  let device = $state(config.asr_device);
  let key = $state("");
  let keyStatus = $state(boot.keys.asr);
  let tested = $state<(Result & { signature: string }) | null>(null);
  let testing = $state(false);
  let keyField = $state<KeyField>();
  let tester = $state<ServiceTest>();
  let error = $state("");
  let uid = $derived(look);

  let signature = $derived(JSON.stringify([apiBase, apiModel, key, keyStatus.set]));
  let selected = $derived(models.find((m) => m.name === (mode === "qwen" ? qwenModel : whisperModel)));
  let qwenReady = $derived(models.some((m) => m.family === "qwen" && m.env_ready));

  $effect(() => {
    if (preselect && gpu !== undefined && gpu !== null && !touched) mode = gpu ? "whisper" : "api";
  });
  $effect(() => {
    ready = mode === "api" ? tested?.signature === signature && tested.level !== "fail"
      : !!selected && selected.cached && selected.env_ready;
  });
  $effect(() => { if (mode === "qwen" && language === "auto") language = "zh"; });

  async function loadModels() {
    try {
      models = await api.asrModels();
      if (!qwenModel && qwenNames.length) qwenModel = qwenNames.includes("qwen3-asr-1.7b") ? "qwen3-asr-1.7b" : qwenNames[0];
    } catch (e) {
      error = message(e);
    }
  }
  loadModels();

  function choosePreset() {
    const preset = presets[provider];
    if (provider !== "custom") {
      apiBase = preset.api_base;
      apiModel = preset.model;
    }
  }

  export async function save(): Promise<boolean> {
    error = "";
    try {
      if (mode === "api" && !(await keyField!.save())) return false;
      const extras = settings ? { language, asr_device: device } : { language };
      await api.saveConfig(mode === "api"
        ? { asr_backend: "api", asr_provider: provider, asr_api_base: apiBase.trim(), asr_api_model: apiModel.trim(), ...extras }
        : { asr_backend: "local", asr_model: mode === "qwen" ? qwenModel : whisperModel, ...extras });
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }

  const modes = $derived(look === "rows" ? [
    { value: "whisper", title: "本地 Whisper", text: "在本机转录，音频不离开电脑",
      aside: gpu == null ? "" : gpu ? "已检测到 NVIDIA GPU" : "未检测到 NVIDIA GPU" },
    { value: "qwen", title: "本地 Qwen", text: "中文课堂更准，需要另装运行环境", aside: models.length ? qwenReady ? "已安装" : "未安装" : "" },
    { value: "api", title: "云端 API", text: "没有显卡时用，音频会上传到转录服务", aside: keyStatus.set ? "已有 key" : "需要 key" },
  ] : [
    { value: "whisper", title: "本地 Whisper", text: "在本机转录，有 NVIDIA GPU 时更快。中文课堂建议改用本地 Qwen。" },
    { value: "qwen", title: "本地 Qwen", text: "中文课堂更准，需要另装运行环境。" },
    { value: "api", title: "云端 API", text: "没有显卡时用，音频会上传到 OpenAI 兼容的转录服务，需要 key。" },
  ]);
  const modelOptions = $derived<ListOption[]>((mode === "qwen" ? models.filter((m) => m.family === "qwen")
    : models.filter((m) => m.family === "whisper")).map((m) => ({ value: m.name, label: m.name })));
  const languages = $derived<ListOption[]>([
    { value: "en", label: "英语", code: "en" },
    { value: "zh", label: "中文", code: "zh" },
    { value: "auto", label: "自动识别", code: "auto", disabled: mode === "qwen" },
    ...(["en", "zh", "auto"].includes(language) ? [] : [{ value: language, label: language }]),
  ]);
  const devices: ListOption[] = [
    { value: "auto", label: "自动（优先 GPU）" }, { value: "cuda", label: "NVIDIA GPU" }, { value: "cpu", label: "CPU" },
  ];
</script>

<div class="form {look}">
  <div class="field">
    {#if look === "tabs"}<span class="label">方式</span>{/if}
    <Choices {look} label="转录方式" bind:value={mode} onchange={() => (touched = true)} options={modes} />
  </div>

  {#if mode === "api"}
    <div class="field api-field">
      <span class="label">服务</span>
      <Choices label="转录服务" bind:value={provider} onchange={choosePreset}
        options={Object.entries(presets).map(([id, preset]) => ({ value: id, title: preset.label.replace(/（.*）$/, "") }))} />
    </div>
    <div class="grid">
      <label class="field">
        <span>服务地址</span>
        <input class="input mono" bind:value={apiBase} placeholder="https://…/v1" spellcheck="false" />
      </label>
      <label class="field">
        <span>模型</span>
        <input class="input mono" bind:value={apiModel} spellcheck="false" />
      </label>
    </div>
    <KeyField kind="asr" bind:status={keyStatus} bind:value={key} bind:this={keyField} {testing} ontest={() => tester?.run()} />
    <ServiceTest bind:this={tester} kind="asr" {provider} {apiBase} model={apiModel} {key} {signature} bind:result={tested} bind:busy={testing} />
  {/if}

  <div class="grid">
    {#if mode !== "api"}
      <div class="field">
        <span id="{uid}-asr-model">语音模型</span>
        {#if modelOptions.length}
          <Select labelledby="{uid}-asr-model" mono options={modelOptions}
            bind:value={() => (mode === "qwen" ? qwenModel : whisperModel), (v) => (mode === "qwen" ? (qwenModel = v) : (whisperModel = v))} />
        {:else}
          <span class="input placeholder">正在读取…</span>
        {/if}
        {#if selected && !selected.env_ready}
          <span class="field-note warn-text">{mode === "qwen" ? "本地 Qwen 运行环境未安装，暂时不能使用" : "本地转录组件未安装，暂时不能使用"}</span>
        {:else if selected}
          <ModelDownload name={selected.name} cached={selected.cached} ondone={loadModels} />
        {/if}
      </div>
    {/if}
    <div class="field">
      <span id="{uid}-asr-lang">默认课堂语言</span>
      <Select labelledby="{uid}-asr-lang" options={languages} bind:value={language} />
      <span class="field-note">{look === "rows" ? "开始上课时可以单独更改" : "每门课可在开始上课时单独更改"}</span>
    </div>
    {#if settings && mode !== "api"}
      <div class="field">
        <span id="{uid}-asr-device">识别设备</span>
        <Select labelledby="{uid}-asr-device" options={devices} bind:value={device} />
      </div>
    {/if}
  </div>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .form { display: flex; flex-direction: column; gap: 28px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 28px 32px; }
  /* Wizard: rows, then the field grid 40px below (design E). */
  .rows { gap: 0; }
  .rows .grid { grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr)); gap: 24px 40px; padding-top: 40px; }
  .rows .api-field { padding-top: 40px; }
  .rows > :global(.key-field) { padding-top: 24px; }
  .placeholder { display: flex; align-items: center; color: var(--faint); }
  .warn-text.field-note { color: var(--warn); }
</style>
