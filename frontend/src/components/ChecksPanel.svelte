<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { message } from "../lib/state.svelte";
  import { fixFor, type FixTarget } from "../lib/checks";
  import type { Check } from "../lib/types";
  import Icon from "./Icon.svelte";

  // fixText names the destination: a wizard step or a settings section.
  let { results = $bindable(null), auto = true, onfix, fixText }: {
    results?: Check[] | null; auto?: boolean; onfix: (target: FixTarget) => void; fixText: (target: FixTarget) => string;
  } = $props();
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
    <button type="button" class="btn" onclick={run} disabled={busy}
      title="检查本地依赖、模型、麦克风和两个服务的连接；会向服务发送一次很小的测试请求"><Icon name="refresh" />{busy ? "正在检查…" : results ? "重新检查" : "开始检查"}</button>
  </div>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
  {#if results}
    <ul class="checks">
      {#each results as item (item.id)}
        {@const fix = fixFor(item)}
        <li class={item.level}>
          <span class="mark"><Icon name={item.level} size={16} /></span>
          <div>
            <p>{item.label}{item.detail ? `：${item.detail}` : ""}</p>
            {#if fix && "target" in fix}
              <button type="button" class="link-btn fix" onclick={() => onfix(fix.target)}>
                {fixText(fix.target)}<Icon name="next" size={14} />
              </button>
            {:else if fix}
              <p class="hint">{fix.note}</p>
            {/if}
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
  .fix { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--fg); }
  .ok .mark { color: var(--ok); }
  .warn .mark { color: var(--warn); }
  .fail .mark { color: var(--error); }
</style>
