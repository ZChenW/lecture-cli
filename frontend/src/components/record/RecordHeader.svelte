<script lang="ts">
  import { navigate } from "../../lib/router";
  import type { Snapshot } from "../../lib/types";

  let { snapshot }: { snapshot: Snapshot | null } = $props();
  let engine = $derived(snapshot ? [snapshot.asr.device_label, snapshot.asr.model].filter(Boolean).join(" · ") : "");
</script>

<header class="top">
  <div class="brand">
    <span class="word">lecture</span>
    <span class="rule" aria-hidden="true"></span>
    <span class="course">{snapshot?.course ?? ""}</span>
  </div>
  <div class="pills">
    {#if engine}<span class="pill">{engine}</span>{/if}
    {#if snapshot}<span class="pill">{snapshot.input}</span>{/if}
    <button class="gear" aria-label="设置" onclick={() => navigate("settings")}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
        stroke-linecap="round" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></svg>
    </button>
  </div>
</header>

<style>
  .top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; position: relative; }
  .brand { display: flex; align-items: baseline; gap: 18px; }
  .word { font-family: 'Instrument Serif', serif; font-size: 26px; letter-spacing: 0.01em; }
  .rule { width: 1px; height: 14px; background: #34363B; align-self: center; }
  .course { font-family: 'Geist Mono', monospace; font-size: 13px; letter-spacing: 0.08em; color: #A9ABB0; }
  .pills { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  .pill {
    font-family: 'Geist Mono', monospace;
    font-size: 12px;
    color: #A9ABB0;
    border: 1px solid #26282C;
    border-radius: 999px;
    padding: 7px 14px;
  }
  .gear {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    border: 1px solid #26282C;
    background: transparent;
    color: #A9ABB0;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }
</style>
