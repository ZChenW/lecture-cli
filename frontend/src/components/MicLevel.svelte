<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { ADVICE, CELLS, HINT, STILL_HINT, cells, resultText, watchLevel } from "../lib/mic";
  import type { MicLevel, MicTestResult } from "../lib/types";

  // Plan GUI-4 Q1.5: a live level bar for the configured input. Cells in an SVG rather than <meter>
  // (its default look) or inline styles (the CSP). passive: the start dialog's hint, which turns
  // into a warning when the first 2 s hardly move. test: the 5-second test button.
  let { passive = false, test = false }: { passive?: boolean; test?: boolean } = $props();
  let level = $state<MicLevel | null>(null);
  let peaks = $state<number[]>([]);
  let still = $state(false);
  let error = $state("");
  let stopped = $state("");
  let testing = $state(false);
  let heard = $state(0);
  let result = $state<MicTestResult | null>(null);
  let stream: { close(): void } | null = null;
  let shown = $derived(cells(level, peaks.length ? Math.max(...peaks) : null));
  let remaining = $derived(Math.max(1, 5 - Math.floor(heard / 10)));

  function onLevel(next: MicLevel) {
    level = next;
    peaks = [...peaks, next.peak].slice(-15);  // The peak marker holds about 1.5 s.
    if (testing) heard += 1;
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

  function runTest() {
    stream?.close();
    testing = true;
    heard = 0;
    result = null;
    error = stopped = "";
    stream = watchLevel({
      level: onLevel,
      result: (value) => (result = value),
      busy: () => (stopped = "正在录制，测试已停止"),
      error: (message) => (error = message),
      end: () => {
        testing = false;
        if (!error && !stopped) startLive();
      },
    }, { test: true });
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
    result = null;
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
  {:else if passive}
    <p class="note" class:warn={still} role="status">{still ? STILL_HINT : HINT}</p>
  {/if}
  {#if test}
    <div class="test">
      <button type="button" class="btn" onclick={runTest} disabled={testing}>{testing ? "正在测试…" : "测试"}</button>
      <p class="note" role="status">
        {#if testing}请正常说几句话，还剩 {remaining} 秒
        {:else if result}<span class:warn={!result.passed} class:ok={result.passed}>{resultText(result)}</span>
        {:else}测试 5 秒：期间说几句话，看电平条是否跟着动{/if}
      </p>
    </div>
    {#if result && !result.passed && !testing}
      <ul class="advice">
        {#each ADVICE as line (line)}<li>{line}</li>{/each}
      </ul>
    {/if}
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
  .ok { color: var(--ok); }
  .test { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; }
  .advice { margin: 0; padding-left: 1.2em; display: flex; flex-direction: column; gap: 4px; font-size: 13px; line-height: 1.6; color: var(--text); }
</style>
