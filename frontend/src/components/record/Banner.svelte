<script lang="ts">
  import { api } from "../../lib/api";
  import { bannerItems, weakLabel } from "../../lib/format";
  import type { Snapshot } from "../../lib/types";

  // Plan N2.5: banners only for warnings and errors. No outline; a dot carries the level and the
  // text stays in the main colour. Warnings close with the icon button; errors cannot be closed.
  let { asr }: { asr: Snapshot["asr"] } = $props();
  let dismissed = $state<string[]>([]);
  // The weak-input text that was closed: the start-of-recording notice (plan GUI-3 item 5) and the
  // weak-input one share the field, so a different text shows again.
  let weakClosed = $state<string | null>(null);
  // Plan GUI-4 Q2.3: the volume change that was closed, by its id: a later change shows again.
  let gainClosed = $state<number | null>(null);
  let items = $derived(bannerItems(asr).filter((item) => item.kind === "error" ||
    (item.kind === "weak" ? item.text !== weakClosed
      : item.kind === "gain_change" ? item.id !== gainClosed : !dismissed.includes(item.text))));
  // Plan N3.4: a closed weak-input banner comes back if the input recovers and later turns weak again.
  $effect(() => {
    if (!asr.weak_input) weakClosed = null;
  });

  function close(item: { kind: string; text: string; id?: number }) {
    // PLAN-GUI-5 R2.6: once closed, the "可能只是噪声" notice stays off for the whole recording; the
    // capture process keeps that, so it holds across page changes. A failed request only means it may return.
    if (item.kind === "weak" && weakLabel(item.text) === "可能只是噪声") api.control("dismiss-still").catch(() => {});
    if (item.kind === "weak") weakClosed = item.text;
    else if (item.kind === "gain_change") gainClosed = item.id ?? null;
    else dismissed = [...dismissed, item.text];
  }
</script>

{#if items.length}
  <div class="banners">
    {#each items as item (item.text)}
      <div class="banner" class:error={item.kind === "error"} role={item.kind === "error" ? "alert" : "status"}>
        <span class="dot" aria-hidden="true"></span>
        <span class="visually-hidden">{item.kind === "error" ? "错误：" : "提示："}</span>
        <p>{item.text}</p>
        {#if item.kind !== "error"}
          <button class="close" aria-label="关闭这条提示" title="关闭" onclick={() => close(item)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
              stroke-linecap="round" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg>
          </button>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .banners { position: relative; display: flex; flex-direction: column; gap: 8px; }
  .banner {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 44px;
    padding: 0 4px 0 16px;
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.04);
    color: #ECEAE4;
    font-size: 13px;
    line-height: 1.6;
  }
  .banner.error { padding-right: 16px; }
  .dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: #FFB454; }
  .error .dot { background: #FF6B5E; }
  p { flex: 1; margin: 0; padding: 11px 0; min-width: 0; overflow-wrap: anywhere; }
  .close {
    flex: none;
    width: 44px;
    height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: #A9ABB0;
    cursor: pointer;
  }
  .close:hover { color: #ECEAE4; background: rgba(255, 255, 255, 0.06); }
</style>
