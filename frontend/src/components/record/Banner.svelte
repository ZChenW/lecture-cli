<script lang="ts">
  import { bannerItems } from "../../lib/format";
  import type { Snapshot } from "../../lib/types";

  let { asr }: { asr: Snapshot["asr"] } = $props();
  let collapsed = $state(false);
  // Dismissed notice texts; an error is never in here, so it can be folded away but not closed (plan 6.2).
  let dismissed = $state<string[]>([]);
  let items = $derived(bannerItems(asr).filter((item) => item.kind === "error" || !dismissed.includes(item.text)));
  let error = $derived(items.some((item) => item.kind === "error"));
</script>

{#if items.length}
  <div class="banner" class:error role={error ? "alert" : "status"}>
    {#if collapsed}
      <span class="summary">{error ? "转录出错" : `${items.length} 条提示`}</span>
    {:else}
      <ul>
        {#each items as item (item.text)}<li class={item.kind}>{item.text}</li>{/each}
      </ul>
    {/if}
    <div class="actions">
      <button class="act" aria-expanded={!collapsed} onclick={() => (collapsed = !collapsed)}>
        {collapsed ? "展开" : "收起"}
      </button>
      {#if !error}
        <button class="act" onclick={() => (dismissed = [...dismissed, ...items.map((item) => item.text)])}>关闭</button>
      {/if}
    </div>
  </div>
{/if}

<style>
  .banner {
    position: relative;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    padding: 4px 6px 4px 18px;
    border: 1px solid rgba(255, 180, 84, 0.35);
    border-radius: 14px;
    background: rgba(255, 180, 84, 0.06);
    color: #FFB454;
    font-size: 13px;
    line-height: 1.6;
  }
  .banner.error { border-color: rgba(255, 107, 94, 0.45); background: rgba(255, 107, 94, 0.07); color: #FF8A7E; }
  ul { margin: 0; padding: 8px 0; list-style: none; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  li.error { color: #FF8A7E; }
  .summary { padding-block: 10px; }
  .actions { display: flex; gap: 4px; flex: none; }
  .act {
    min-height: 44px;
    padding: 0 12px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: #ECEAE4;
    font-size: 13px;
    cursor: pointer;
  }
  .act:hover { background: rgba(255, 255, 255, 0.06); }
</style>
