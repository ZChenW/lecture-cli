<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { message } from "../lib/state.svelte";
  import type { Check } from "../lib/types";
  import Icon from "./Icon.svelte";

  let { results = $bindable(null), auto = true }: { results?: Check[] | null; auto?: boolean } = $props();
  let busy = $state(false);
  let error = $state("");

  export async function run() {
    busy = true;
    error = "";
    try {
      results = await api.checks();
    } catch (e) {
      error = message(e);
    } finally {
      busy = false;
    }
  }
  onMount(() => { if (auto) run(); });
</script>

<div class="stack">
  <div class="row">
    <button type="button" class="btn" onclick={run} disabled={busy}><Icon name="refresh" />{busy ? "正在检查…" : results ? "重新检查" : "开始检查"}</button>
    {#if busy}<span class="hint" role="status">检查可能需要几秒。</span>{/if}
  </div>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
  {#if results}
    <ul class="checks">
      {#each results as item (item.id)}
        <li class={item.level}>
          <span class="mark"><Icon name={item.level} size={16} /></span>
          <div>
            <p>{item.label}{item.detail ? `：${item.detail}` : ""}</p>
            {#if item.hint}<p class="hint">建议：{item.hint}</p>{/if}
          </div>
          <span class="visually-hidden">{item.level === "ok" ? "通过" : item.level === "warn" ? "警告" : "失败"}</span>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .checks li { display: flex; gap: 12px; padding: 12px 0; border-top: 1px solid var(--line); }
  .mark { flex: none; padding-top: 3px; }
  .ok .mark { color: var(--ok); }
  .warn .mark { color: var(--warn); }
  .fail .mark { color: var(--error); }
</style>
