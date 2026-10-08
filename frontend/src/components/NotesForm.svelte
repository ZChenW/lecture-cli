<script lang="ts">
  import { api } from "../lib/api";
  import { app, message } from "../lib/state.svelte";
  import type { Result } from "../lib/types";
  import Choices from "./Choices.svelte";
  import KeyField from "./KeyField.svelte";
  import ServiceTest from "./ServiceTest.svelte";

  // look: "tabs" in settings (design D), "rows" in the wizard (design E).
  let { ready = $bindable(false), settings = false, look = "tabs" }:
    { ready?: boolean; settings?: boolean; look?: "tabs" | "rows" } = $props();
  const boot = app.boot!;
  const config = boot.config;
  const presets = boot.presets.notes;
  let provider = $state(config.notes_provider in presets ? config.notes_provider : "custom");
  let apiBase = $state(config.notes_api_base);
  let model = $state(config.notes_model);
  let extraBody = $state(config.notes_extra_body);
  let interval = $state(config.interval);
  let key = $state("");
  let keyStatus = $state(boot.keys.notes);
  let tested = $state<(Result & { signature: string }) | null>(null);
  let testing = $state(false);
  let keyField = $state<KeyField>();
  let tester = $state<ServiceTest>();
  let error = $state("");

  let signature = $derived(JSON.stringify([apiBase, model, key, keyStatus.set]));
  $effect(() => { ready = tested?.signature === signature && tested.level !== "fail"; });

  function choosePreset() {
    const preset = presets[provider];
    // Provider-specific request fields (DeepSeek's "thinking") follow the preset.
    extraBody = preset.extra_body;
    if (provider !== "custom") {
      apiBase = preset.api_base;
      model = preset.model || model;
    }
  }

  export async function save(): Promise<boolean> {
    error = "";
    try {
      if (!(await keyField!.save())) return false;
      await api.saveConfig({ notes_provider: provider, notes_api_base: apiBase.trim(), notes_model: model.trim(),
        notes_extra_body: extraBody, ...(settings ? { interval } : {}) });
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }

  const options = Object.entries(presets).map(([id, preset]) => ({
    value: id,
    title: preset.label.replace(/（.*）$/, ""),
    text: id === "custom" ? "任何 OpenAI 兼容的服务" : preset.api_base,
  }));
</script>

<div class="form {look}">
  <div class="field">
    {#if look === "tabs"}<span class="label">服务</span>{/if}
    <Choices {look} compact label="笔记服务" bind:value={provider} onchange={choosePreset}
      options={look === "tabs" ? options.map(({ value, title }) => ({ value, title })) : options} />
  </div>
  <div class="grid">
    <label class="field">
      <span>服务地址</span>
      <input class="input mono" bind:value={apiBase} placeholder="https://…/v1" spellcheck="false" />
    </label>
    <label class="field">
      <span>模型</span>
      <input class="input mono" bind:value={model} spellcheck="false" />
    </label>
  </div>
  <div class="key">
    <KeyField kind="notes" bind:status={keyStatus} bind:value={key} bind:this={keyField} {testing} ontest={() => tester?.run()} />
    <ServiceTest bind:this={tester} kind="notes" {provider} {apiBase} {model} {key} {signature} bind:result={tested} bind:busy={testing} />
  </div>
  {#if settings}
    <label class="field narrow">
      <span>笔记检查间隔（秒）</span>
      <input class="input mono" type="number" min="1" max="3600" bind:value={interval} />
    </label>
  {/if}
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .form { display: flex; flex-direction: column; gap: 28px; }
  .rows { gap: 40px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 28px 32px; }
  .rows .grid { grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr)); gap: 24px 40px; }
  .key { display: flex; flex-direction: column; gap: 8px; }
  .narrow { max-width: 240px; }
</style>
