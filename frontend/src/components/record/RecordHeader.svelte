<script lang="ts">
  import { navigate } from "../../lib/router";
  import type { Snapshot } from "../../lib/types";

  // ondiscard is set only while recording: discarding is refused once closing has begun.
  let { snapshot, ondiscard }: { snapshot: Snapshot | null; ondiscard?: () => void } = $props();
  let engine = $derived(snapshot ? [snapshot.asr.device_label, snapshot.asr.model].filter(Boolean).join(" · ") : "");
  let armed = $state(false);
  let disarm: ReturnType<typeof setTimeout> | undefined;

  // Plan GUI-3 items 1 and 6: a plain text button; the first press arms it ("确认放弃"), a second
  // press within 3 s discards.
  function discard() {
    clearTimeout(disarm);
    if (!armed) {
      armed = true;
      disarm = setTimeout(() => (armed = false), 3000);
      return;
    }
    armed = false;
    ondiscard?.();
  }

  $effect(() => {
    if (!ondiscard) {
      armed = false;
      clearTimeout(disarm);
    }
  });
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
    <button class="round" aria-label="设置" title="设置" onclick={() => navigate("settings")}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
        stroke-linecap="round" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></svg>
    </button>
    {#if ondiscard}
      <button type="button" class="discard has-tip" class:armed onclick={discard} aria-live="polite"
        aria-describedby="discard-tip">
        {armed ? "确认放弃" : "放弃这堂课"}
        <span class="tip" aria-hidden="true" id="discard-tip">录音和笔记都会删除，无法找回</span>
      </button>
    {/if}
  </div>
</header>

<style>
  .top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; position: relative; z-index: 3; }
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
  .round {
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
  .round:hover { color: #ECEAE4; border-color: #34363B; }
  /* Plan GUI-3 item 6: secondary text, far weaker than 下课; it wraps under the device label when narrow. */
  .discard {
    min-height: 44px;
    padding: 0 8px;
    border: 0;
    background: transparent;
    color: #A9ABB0; /* --muted, the secondary text colour of the dark screens */
    font-size: 14px;
    white-space: nowrap;
    cursor: pointer;
  }
  .discard:hover { color: #ECEAE4; text-decoration: underline; text-underline-offset: 4px; }
  .discard:focus-visible { outline-offset: 0; }
  .discard.armed { color: #FF6B5E; }
  /* The bar is at the top of the window: the tip opens below the button. */
  .discard .tip { top: calc(100% + 8px); bottom: auto; }
</style>
