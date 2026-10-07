<script lang="ts">
  import { api } from "../lib/api";
  import { message } from "../lib/state.svelte";
  import type { KeyStatus } from "../lib/types";

  let { kind, status = $bindable(), value = $bindable("") }:
    { kind: "notes" | "asr"; status: KeyStatus; value?: string } = $props();
  let error = $state("");

  /** Stores a typed key; an empty field keeps the existing one. */
  export async function save(): Promise<boolean> {
    if (!value.trim()) return true;
    try {
      status = await api.saveKey(kind, value);
      value = "";
      error = "";
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }

  async function remove() {
    try {
      status = await api.deleteKey(kind);
      error = "";
    } catch (e) {
      error = message(e);
    }
  }
</script>

<div class="field">
  <span class="latin">API key</span>
  {#if status.source === "env"}
    <input class="input" type="password" disabled value="••••••••" aria-label="API key" />
    <p class="hint">来自环境变量，无法在此修改（末尾 {status.tail}）。</p>
  {:else}
    <input class="input" type="password" autocomplete="off" bind:value aria-label="API key"
      placeholder={status.set ? `已保存，末尾 ${status.tail}；输入新 key 可替换` : "粘贴 key"} />
    {#if status.source === "file"}
      <div class="row">
        <p class="hint">已保存在配置目录（权限 600）。</p>
        <button type="button" class="link-btn" onclick={remove}>删除已保存的 key</button>
      </div>
    {/if}
  {/if}
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>
