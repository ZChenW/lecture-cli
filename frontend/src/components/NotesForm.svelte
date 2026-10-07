<script lang="ts">
  import { api } from "../lib/api";
  import { app, message } from "../lib/state.svelte";
  import type { Result } from "../lib/types";
  import KeyField from "./KeyField.svelte";
  import ServiceTest from "./ServiceTest.svelte";

  let { ready = $bindable(false), settings = false }: { ready?: boolean; settings?: boolean } = $props();
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
  let keyField = $state<KeyField>();
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
</script>

<div class="stack">
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
    <input class="input mono" bind:value={model} spellcheck="false" />
  </label>
  <KeyField kind="notes" bind:status={keyStatus} bind:value={key} bind:this={keyField} />
  <ServiceTest kind="notes" {provider} {apiBase} {model} {key} {signature} bind:result={tested} />
  <p class="hint">需要 OpenAI 兼容的 chat/completions 接口，支持 max_tokens。</p>
  {#if settings}
    <label class="field narrow">
      <span>笔记检查间隔（秒）</span>
      <input class="input mono" type="number" min="1" max="3600" bind:value={interval} />
    </label>
  {/if}
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .narrow { max-width: 240px; }
</style>
