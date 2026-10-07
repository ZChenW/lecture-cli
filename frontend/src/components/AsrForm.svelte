<script lang="ts">
  import { api } from "../lib/api";
  import { app, message } from "../lib/state.svelte";
  import type { AsrModel, Result } from "../lib/types";
  import KeyField from "./KeyField.svelte";
  import ModelDownload from "./ModelDownload.svelte";
  import ServiceTest from "./ServiceTest.svelte";

  // gpu: the wizard's GPU detection (null while unknown); settings shows language and device too.
  let { ready = $bindable(false), gpu = undefined, settings = false }:
    { ready?: boolean; gpu?: boolean | null; settings?: boolean } = $props();

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
  let keyField = $state<KeyField>();
  let error = $state("");

  let signature = $derived(JSON.stringify([apiBase, apiModel, key, keyStatus.set]));
  let selected = $derived(models.find((m) => m.name === (mode === "qwen" ? qwenModel : whisperModel)));
  let suggestion = $derived(gpu === undefined || gpu === null ? "" :
    gpu ? "检测到 NVIDIA GPU，已预选本地转录。" : "未检测到 NVIDIA GPU，已预选云端 API。");

  $effect(() => {
    if (gpu !== undefined && gpu !== null && !touched) mode = gpu ? "whisper" : "api";
  });
  $effect(() => {
    ready = mode === "api" ? tested?.signature === signature && tested.level !== "fail"
      : !!selected && selected.cached && selected.env_ready;
  });

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
      const extras = settings ? { language, asr_device: device } : {};
      await api.saveConfig(mode === "api"
        ? { asr_backend: "api", asr_provider: provider, asr_api_base: apiBase.trim(), asr_api_model: apiModel.trim(), ...extras }
        : { asr_backend: "local", asr_model: mode === "qwen" ? qwenModel : whisperModel, ...extras });
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }
</script>

<div class="stack">
  <div class="cards" role="radiogroup" aria-label="转录方式">
    {#each [["whisper", "本地 Whisper", "在本机转录，音频不离开电脑；有 NVIDIA GPU 时更快。"],
            ["qwen", "本地 Qwen", "本机 Qwen3-ASR，需要单独安装的 Qwen 运行环境。"],
            ["api", "云端 API", "把音频发送到 OpenAI 兼容的转录服务，需要 key。"]] as [value, title, text]}
      <label class="card" class:selected={mode === value}>
        <input type="radio" name="asr-mode" {value} bind:group={mode} onchange={() => (touched = true)} />
        <strong>{title}</strong>
        <span class="hint">{text}</span>
      </label>
    {/each}
  </div>
  {#if suggestion && !touched}<p class="hint">{suggestion}</p>{/if}

  {#if mode === "whisper"}
    <label class="field">
      <span>语音模型</span>
      <select class="input" bind:value={whisperModel}>
        {#each models.filter((m) => m.family === "whisper") as model}
          <option value={model.name}>{model.name}{model.cached ? "" : "（未下载）"}</option>
        {/each}
      </select>
    </label>
    {#if selected && !selected.env_ready}
      <p class="error-text">本地转录依赖未安装，请在项目目录运行 ./install.sh。</p>
    {:else if selected}
      <ModelDownload name={selected.name} cached={selected.cached} ondone={loadModels} />
    {/if}
  {:else if mode === "qwen"}
    {#if qwenNames.length && !models.some((m) => m.family === "qwen" && m.env_ready)}
      <p class="warn-text">需要 Qwen 运行环境：在项目目录运行 <code class="mono">./install-qwen.sh</code>，完成后回到此页。</p>
    {/if}
    <label class="field">
      <span>语音模型</span>
      <select class="input" bind:value={qwenModel}>
        {#each qwenNames as name}<option value={name}>{name}</option>{/each}
      </select>
    </label>
    {#if selected?.env_ready}
      <ModelDownload name={selected.name} cached={selected.cached} ondone={loadModels} />
    {/if}
  {:else}
    <label class="field">
      <span>服务</span>
      <select class="input" bind:value={provider} onchange={choosePreset}>
        {#each Object.entries(presets) as [id, preset]}<option value={id}>{preset.label}</option>{/each}
      </select>
    </label>
    <label class="field">
      <span>服务地址</span>
      <input class="input mono" bind:value={apiBase} placeholder="https://…/v1" spellcheck="false" />
    </label>
    <label class="field">
      <span>模型</span>
      <input class="input mono" bind:value={apiModel} spellcheck="false" />
    </label>
    <KeyField kind="asr" bind:status={keyStatus} bind:value={key} bind:this={keyField} />
    <ServiceTest kind="asr" {provider} {apiBase} model={apiModel} {key} {signature} bind:result={tested} />
  {/if}

  {#if settings}
    <div class="row">
      <label class="field">
        <span>课堂语言</span>
        <select class="input" bind:value={language}>
          <option value="en">英语 en</option>
          <option value="zh">中文 zh</option>
          <option value="auto" disabled={mode === "qwen"}>自动识别 auto</option>
          {#if !["en", "zh", "auto"].includes(language)}<option value={language}>{language}</option>{/if}
        </select>
      </label>
      {#if mode !== "api"}
        <label class="field">
          <span>识别设备</span>
          <select class="input" bind:value={device}>
            <option value="auto">自动（优先 GPU）</option>
            <option value="cuda">NVIDIA GPU</option>
            <option value="cpu">CPU</option>
          </select>
        </label>
      {/if}
    </div>
  {/if}
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
  .card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-height: 44px;
    padding: 18px 20px;
    border: 1px solid var(--card-line);
    border-radius: 16px;
    background: var(--card);
    cursor: pointer;
  }
  .card.selected { border-color: var(--fg); }
  .card input { position: absolute; opacity: 0; pointer-events: none; }
  .card:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
  .row .field { flex: 1 1 200px; }
</style>
