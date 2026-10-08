<script lang="ts">
  // N2.3 key status row (design/D-settings.reference.html, "笔记服务"). A set key shows a tick,
  // "•••• •••• <last four>" and where it comes from; the key itself never reaches the page.
  import { api } from "../lib/api";
  import { message } from "../lib/state.svelte";
  import type { KeyStatus } from "../lib/types";

  let { kind, status = $bindable(), value = $bindable(""), ontest, testing = false }: {
    kind: "notes" | "asr"; status: KeyStatus; value?: string; ontest?: () => void; testing?: boolean;
  } = $props();
  let error = $state("");
  let busy = $state(false);
  let replacing = $state(false);
  let editing = $derived(!status.set || replacing);
  let inputId = $derived(`key-${kind}`);

  /** Stores a typed key; an empty field keeps the existing one. */
  export async function save(): Promise<boolean> {
    if (!value.trim()) return true;
    try {
      status = await api.saveKey(kind, value);
      value = "";
      error = "";
      replacing = false;
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }

  async function act(run: () => Promise<KeyStatus>) {
    busy = true;
    error = "";
    try {
      status = await run();
    } catch (e) {
      error = message(e);
    } finally {
      busy = false;
    }
  }
  // Only the backend reads the environment value and writes it to the key file.
  const persist = () => act(() => api.persistKey(kind));
  const remove = () => act(() => api.deleteKey(kind));
</script>

<div class="field key-field">
  {#if editing}
    <label class="latin" for={inputId}>API key</label>
    <div class="row-line editing">
      <input id={inputId} class="input" type="password" autocomplete="off" spellcheck="false" bind:value
        title="key 只保存在本机配置目录（权限 600），不会显示在界面上"
        placeholder={replacing ? "粘贴新 key，保存这一节后替换" : "粘贴 key"} />
      <div class="actions">
        {#if ontest}<button type="button" class="text-action" onclick={ontest} disabled={testing}>{testing ? "正在测试…" : "测试连接"}</button>{/if}
        {#if replacing}<button type="button" class="text-action" onclick={() => { replacing = false; value = ""; }}>取消</button>{/if}
      </div>
    </div>
  {:else}
    <span class="latin" id="{inputId}-label">API key</span>
    <div class="row-line" role="group" aria-labelledby="{inputId}-label">
      <div class="state">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round" aria-label="已设置" role="img"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
        <span class="dots">•••• •••• {status.tail}</span>
        <!-- PLAN-GUI-5 R1: status only; the reasons behind 保存到本机 are its tooltip. -->
        <span class="source">{status.source === "env" ? `来自环境变量 ${status.variable ?? ""}${status.stored ? " · 已保存到本机" : ""}` : "已保存到本机"}</span>
      </div>
      <div class="actions">
        {#if ontest}<button type="button" class="text-action" onclick={ontest} disabled={testing}>{testing ? "正在测试…" : "测试连接"}</button>{/if}
        {#if status.source === "env" && !status.stored}
          <button type="button" class="text-action" onclick={persist} disabled={busy}
            title="从应用菜单启动时读不到终端里的环境变量；保存到本机后两种启动方式都可用">保存到本机</button>
        {:else if status.source === "file"}
          <button type="button" class="text-action" onclick={() => (replacing = true)}>更换</button>
          <button type="button" class="text-action" onclick={remove} disabled={busy}>删除</button>
        {/if}
      </div>
    </div>
  {/if}
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .row-line {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 44px;
    border-bottom: 1px solid var(--line);
  }
  .row-line.editing { flex-wrap: nowrap; border-bottom: none; }
  .row-line.editing .input { flex: 1; }
  .state { display: flex; align-items: center; gap: 14px; min-width: 0; color: var(--fg); }
  .state svg { flex: none; }
  .dots { font-family: var(--mono); font-size: 15px; }
  .source { font-size: 13px; color: var(--label); }
  .actions { display: flex; gap: 4px; }
</style>
