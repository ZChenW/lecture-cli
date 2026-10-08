<script lang="ts">
  import { lyrics, tailText } from "../../lib/record";
  import type { Snapshot } from "../../lib/types";

  let { transcript }: { transcript: Snapshot["transcript"] } = $props();
  let view = $derived(lyrics(transcript));
  let lines = $state<HTMLElement>();
  let probe = $state<HTMLElement>();
  let small = $state(false);
  let shown = "";
  let currentText = $derived(tailText(view.current));

  // Plan N3.3: past two lines at 38px the current line drops to 30px. A hidden copy is always laid
  // out at 38px, so the decision never flips back and forth with the smaller size.
  $effect(() => {
    if (!probe) return;
    const element = probe;
    const measure = () => {
      const lineHeight = parseFloat(getComputedStyle(element).lineHeight) || 47.5;
      small = element.getBoundingClientRect().height > lineHeight * 2 + 1;
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  });

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
      <div class="current-wrap">
        <p class="current" class:small>{currentText}{#if view.live}<span class="cursor" aria-hidden="true"></span>{/if}</p>
        <p class="current probe" bind:this={probe} aria-hidden="true">{currentText}{#if view.live}<span class="cursor"></span>{/if}</p>
      </div>
    {:else}
      <p class="current waiting">等待语音…</p>
    {/if}
  </div>
  {#if view.marker}<span class="marker">{view.marker}</span>{/if}
</section>

<style>
  .live { flex: 2 1 420px; display: flex; flex-direction: column; justify-content: center; gap: 22px; min-width: 0; min-height: 0; }
  /* Section heading: 0.24em by the user-confirmed rule (form field labels use 0.08em, see base.css). */
  .label { font-size: 12px; letter-spacing: 0.24em; color: #8E9096; }
  /* The reference's lines sit directly in the column; this wrapper keeps the same 22px rhythm. */
  /* HANDOFF 3.3: confirmed lines show in full. When they outgrow the column, the newest stay in view
     and the oldest leave at the top (justify-content: flex-end overflows upward). */
  .lines { display: flex; flex-direction: column; justify-content: flex-end; gap: 22px; min-height: 0; overflow: hidden; }
  p { margin: 0; }
  .older { flex: none; font-size: 22px; line-height: 1.4; font-weight: 300; overflow-wrap: anywhere; }
  .size0 { color: #4A4C52; }
  .size1 { color: #5E6067; }
  .size2 { font-size: 24px; color: #7E8087; }
  .size3 { font-size: 26px; color: #A9ABB0; }
  .current { flex: none; font-size: 38px; line-height: 1.25; font-weight: 400; letter-spacing: -0.01em; color: #FFFFFF; text-wrap: pretty; }
  .current.small { font-size: 30px; }
  .current-wrap { position: relative; flex: none; }
  .probe { position: absolute; top: 0; left: 0; right: 0; visibility: hidden; pointer-events: none; }
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
  .small .cursor { height: 27px; vertical-align: -4px; }
  @keyframes blink { 50% { opacity: 0; } }
  .marker { font-family: 'Geist Mono', monospace; font-size: 12px; color: #8E9096; }
</style>
