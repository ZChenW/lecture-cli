<script lang="ts">
  import type { Snippet } from "svelte";
  import { refresh } from "../lib/state.svelte";

  // One row of design/D-settings.reference.html: heading and description on the left, the form
  // on the right. onsave is absent for sections without settings of their own (环境检查).
  let { id, title, about, onsave, gap = 28, children }: {
    id: string; title: string; about: string; onsave?: () => Promise<boolean>; gap?: number; children: Snippet;
  } = $props();
  let busy = $state(false);
  let status = $state<"clean" | "dirty" | "saved" | "failed">("clean");

  async function save() {
    busy = true;
    try {
      const ok = await onsave!();
      status = ok ? "saved" : "failed";
      if (ok) await refresh();
    } finally {
      busy = false;
    }
  }

  // Text fields send input, the custom dropdown and choices send a bubbling change event.
  // Controls that act at once (creating a course) carry data-own-save.
  function touched(event: Event) {
    if (onsave && !(event.target as Element).closest?.("[data-own-save]")) status = "dirty";
  }
</script>

<section {id} aria-labelledby="{id}-title">
  <div class="about">
    <h2 id="{id}-title" tabindex="-1">{title}</h2>
    <p>{about}</p>
  </div>
  <div class="body" style:gap="{gap}px" oninput={touched} onchange={touched}>
    {@render children()}
    {#if onsave}
      <div class="save">
        <button type="button" class="btn primary" onclick={save} disabled={busy}>{busy ? "正在保存…" : "保存这一节"}</button>
        {#if status === "dirty"}<span class="status">有未保存的改动</span>
        {:else if status === "saved"}<span class="status" role="status">已保存</span>{/if}
      </div>
    {/if}
  </div>
</section>

<style>
  section {
    display: flex;
    flex-wrap: wrap;
    gap: 20px 48px;
    padding: 44px 0 48px;
    border-bottom: 1px solid #111111;
    scroll-margin-top: 0;
  }
  section:last-child { border-bottom: none; }
  .about { flex: 1 1 200px; max-width: 240px; display: flex; flex-direction: column; gap: 10px; }
  h2 { margin: 0; font-family: "Noto Serif CJK SC", serif; font-weight: 600; font-size: 22px; color: #111111; }
  h2:focus { outline: none; }
  .about p { margin: 0; font-size: 13px; line-height: 1.7; color: #5C5C5A; }
  .body { flex: 999 1 380px; min-width: 0; display: flex; flex-direction: column; }
  .save { display: flex; align-items: center; gap: 16px; }
  .save .btn { height: 44px; padding: 0 22px; font-size: 14px; }
  .status { font-size: 13px; color: #5C5C5A; }
</style>
