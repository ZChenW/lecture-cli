<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { CELLS, STILL_HINT, cells, watchLevel } from "../lib/mic";
  import type { MicLevel } from "../lib/types";

  // Plan GUI-4 Q1.5: a live level bar for the configured input. Cells in an SVG rather than <meter>
  // (its default look) or inline styles (the CSP). PLAN-GUI-5 R1: only the bar and the value; a line
  // of status text appears below only when something is wrong. passive: the start dialog, which
  // warns when the first 2 s hardly move.
  let { passive = false }: { passive?: boolean } = $props();
  let level = $state<MicLevel | null>(null);
  let peaks = $state<number[]>([]);
  let still = $state(false);
  let error = $state("");
  let stopped = $state("");
  let stream: { close(): void } | null = null;
  let shown = $derived(cells(level, peaks.length ? Math.max(...peaks) : null));

  function onLevel(next: MicLevel) {
    level = next;
    peaks = [...peaks, next.peak].slice(-15);  // The peak marker holds about 1.5 s.
  }

  function startLive() {
    stream?.close();
    error = stopped = "";
    stream = watchLevel({
      level: onLevel,
      still: (value) => (still = value),
      busy: () => (stopped = "正在录制，电平条已停止"),
      error: (message) => (error = message),
    });
  }

  /** Let go of the microphone, e.g. just before a lecture opens it. */
  export function stop() {
    stream?.close();
    stream = null;
    level = null;
  }

  /** Listen again, e.g. after the saved input device changed. */
  export function restart() {
    still = false;
    startLive();
  }

  onMount(startLive);
  onDestroy(() => stream?.close());
</script>

<div class="mic-level">
  <div class="meter" role="meter" aria-label="麦克风电平" aria-valuemin={-70} aria-valuemax={0}
    aria-valuenow={level ? Math.round(level.rms) : -70}
    aria-valuetext={level ? `约 ${Math.round(level.rms)} dBFS` : "暂无电平"}>
    <svg viewBox="0 0 {CELLS * 8 - 3} 12" preserveAspectRatio="none" aria-hidden="true">
      {#each { length: CELLS } as _, i (i)}
        <rect x={i * 8} y="0" width="5" height="12" rx="1" class:lit={i < shown.lit} class:peak={i === shown.peak} />
      {/each}
    </svg>
    <span class="db">{level ? `${Math.round(level.rms)} dB` : "— dB"}</span>
  </div>
  {#if error}
    <p class="note warn" role="status">{error}</p>
  {:else if stopped}
    <p class="note" role="status">{stopped}</p>
  {:else if passive && still}
    <p class="note warn" role="status">{STILL_HINT}</p>
  {/if}
</div>

<style>
  .mic-level { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .meter { display: flex; align-items: center; gap: 12px; min-width: 0; }
  svg { flex: 1; min-width: 0; height: 12px; }
  rect { fill: var(--line); }
  rect.lit { fill: var(--fg); }
  rect.peak { fill: var(--muted); }
  .db { flex: none; width: 5.5em; text-align: right; font-family: var(--mono); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .note { margin: 0; font-size: 13px; line-height: 1.6; color: var(--muted); }
  .warn { color: var(--warn); }
</style>
