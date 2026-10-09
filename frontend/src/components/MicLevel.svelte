<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { CELLS, LOW_DBFS, OVERLOAD_LEVELS, cells, overloaded, watchLevel } from "../lib/mic";
  import type { MicLevel } from "../lib/types";

  // A live level bar for the configured input. Cells in an SVG rather than <meter> (its default
  // look) or inline styles (the CSP). PLAN-GUI-5 R1: only the bar and the value; a line of status
  // text appears below only when something is wrong. R2.3: the server judges the first 2 s (the
  // same rule wherever the bar is: settings, the wizard and the start dialog); the bar only looks,
  // it never changes the volume. Clipping in the last second turns the right end to the warning
  // colour and the value to "过载".
  const OVERLOAD_CELLS = 3;
  let level = $state<MicLevel | null>(null);
  let recent = $state<MicLevel[]>([]);
  let verdict = $state("");
  let error = $state("");
  let stopped = $state("");
  let stream: { close(): void } | null = null;
  let shown = $derived(cells(level, recent.length ? Math.max(...recent.slice(-15).map((item) => item.peak)) : null));
  let overload = $derived(overloaded(recent));

  function onLevel(next: MicLevel) {
    level = next;
    recent = [...recent, next].slice(-Math.max(15, OVERLOAD_LEVELS));  // The peak marker holds about 1.5 s.
  }

  function startLive() {
    stream?.close();
    error = stopped = verdict = "";
    recent = [];
    stream = watchLevel({
      level: onLevel,
      verdict: (value) => (verdict = value.text),
      busy: () => (stopped = "正在录制，电平条已停止"),
      error: (message) => (error = message),
    });
  }

  /** Let go of the microphone, e.g. just before a lecture opens it. */
  export function stop() {
    stream?.close();
    stream = null;
    level = null;
    recent = [];
  }

  /** Listen again, e.g. after the saved input device changed. */
  export function restart() {
    startLive();
  }

  onMount(startLive);
  onDestroy(() => stream?.close());
</script>

<div class="mic-level">
  <div class="meter" role="meter" aria-label="麦克风电平" aria-valuemin={LOW_DBFS} aria-valuemax={0}
    aria-valuenow={level ? Math.max(LOW_DBFS, Math.round(level.rms)) : LOW_DBFS}
    aria-valuetext={overload ? "过载" : level ? `约 ${Math.round(level.rms)} dBFS` : "暂无电平"}>
    <svg viewBox="0 0 {CELLS * 8 - 3} 12" preserveAspectRatio="none" aria-hidden="true">
      {#each { length: CELLS } as _, i (i)}
        <rect x={i * 8} y="0" width="5" height="12" rx="1" class:lit={i < shown.lit} class:peak={i === shown.peak}
          class:over={overload && i >= CELLS - OVERLOAD_CELLS} />
      {/each}
    </svg>
    <span class="db" class:warn={overload}>{overload ? "过载" : level ? `${Math.round(level.rms)} dB` : "— dB"}</span>
  </div>
  {#if error}
    <p class="note warn" role="status">{error}</p>
  {:else if stopped}
    <p class="note" role="status">{stopped}</p>
  {:else if verdict}
    <p class="note warn" role="status">{verdict}</p>
  {/if}
</div>

<style>
  .mic-level { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .meter { display: flex; align-items: center; gap: 12px; min-width: 0; }
  svg { flex: 1; min-width: 0; height: 12px; }
  rect { fill: var(--line); }
  rect.lit { fill: var(--fg); }
  rect.peak { fill: var(--muted); }
  rect.over { fill: var(--warn); }
  .db { flex: none; width: 5.5em; text-align: right; font-family: var(--mono); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .db.warn { color: var(--warn); }
  .note { margin: 0; font-size: 13px; line-height: 1.6; color: var(--muted); }
  .warn { color: var(--warn); }
</style>
