<script lang="ts">
  import type { Snippet } from "svelte";
  import { refresh } from "../lib/state.svelte";

  // onsave is absent for sections without settings of their own (the environment check).
  let { id, title, onsave, children }:
    { id: string; title: string; onsave?: () => Promise<boolean>; children: Snippet } = $props();
  let busy = $state(false);
  let saved = $state(false);

  async function save() {
    busy = true;
    saved = false;
    try {
      saved = await onsave!();
      if (saved) await refresh();
    } finally {
      busy = false;
    }
  }
</script>

<section aria-labelledby={id}>
  <h2 {id} tabindex="-1">{title}</h2>
  {@render children()}
  {#if onsave}
    <div class="row save">
      <button type="button" class="btn primary" onclick={save} disabled={busy}>{busy ? "正在保存…" : "保存"}</button>
      {#if saved}<span class="ok-text" role="status">已保存</span>{/if}
    </div>
  {/if}
</section>

<style>
  section { padding-top: 56px; display: flex; flex-direction: column; gap: 20px; }
  h2 { font-family: var(--display); font-weight: 600; font-size: 26px; color: var(--fg); }
  .save { padding-top: 4px; }
</style>
