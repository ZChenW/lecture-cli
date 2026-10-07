<script lang="ts">
  import { lyrics, tailText } from "../../lib/record";
  import type { Snapshot } from "../../lib/types";

  let { transcript }: { transcript: Snapshot["transcript"] } = $props();
  let view = $derived(lyrics(transcript));
  let lines = $state<HTMLElement>();
  let shown = "";

  // A newly confirmed segment lifts the whole block: 300 ms shift and fade (plan 6.2).
  $effect(() => {
    const key = view.key;
    if (shown && key !== shown && lines && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      lines.animate([{ transform: "translateY(28px)", opacity: 0.4 }, { transform: "none", opacity: 1 }],
        { duration: 300, easing: "cubic-bezier(.2,.8,.2,1)" });
    }
    shown = key;
  });
</script>

<section class="live">
  <span class="label">实时转录</span>
  <div class="lines" bind:this={lines} aria-live="polite">
    {#each view.older as text, i (i)}
      <p class="older size{i + 4 - view.older.length}">{text}</p>
    {/each}
    {#if view.current}
      <p class="current">{tailText(view.current)}{#if view.live}<span class="cursor" aria-hidden="true"></span>{/if}</p>
    {:else}
      <p class="current waiting">等待语音…</p>
    {/if}
  </div>
  {#if view.marker}<span class="marker">{view.marker}</span>{/if}
</section>

<style>
  .live { flex: 2 1 420px; display: flex; flex-direction: column; justify-content: center; gap: 22px; min-width: 0; }
  .label { font-size: 12px; letter-spacing: 0.24em; color: #8E9096; }
  /* The reference's lines sit directly in the column; this wrapper keeps the same 22px rhythm. */
  .lines { display: flex; flex-direction: column; gap: 22px; }
  p { margin: 0; }
  .older {
    font-size: 22px;
    line-height: 1.4;
    font-weight: 300;
    /* Long segments stop at two lines so the column never outgrows the window. */
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
  }
  .size0 { color: #4A4C52; }
  .size1 { color: #5E6067; }
  .size2 { font-size: 24px; color: #7E8087; }
  .size3 { font-size: 26px; color: #A9ABB0; }
  .current { font-size: 38px; line-height: 1.25; font-weight: 400; letter-spacing: -0.01em; color: #FFFFFF; text-wrap: pretty; }
  .waiting { color: #8E9096; }
  .cursor {
    display: inline-block;
    width: 3px;
    height: 34px;
    margin-left: 8px;
    vertical-align: -5px;
    background: #D4FF5C;
    animation: blink 1s steps(1, end) infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }
  .marker { font-family: 'Geist Mono', monospace; font-size: 12px; color: #8E9096; }
</style>
