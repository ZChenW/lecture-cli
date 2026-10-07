<script lang="ts">
  import { onDestroy } from "svelte";
  import { subscribe } from "../lib/api";
  import { formatElapsed, phaseLabel } from "../lib/format";
  import { app } from "../lib/state.svelte";
  import type { RunRecord, Snapshot } from "../lib/types";

  // Minimal stand-in until the full recording screen exists: it follows the run and links home when done.
  let snapshot = $state<Snapshot | null>(app.boot?.active_run ?? null);
  let record = $state<RunRecord | null>(null);
  const stream = subscribe({ snapshot: (s) => (snapshot = s), finished: (r) => (record = r) });
  onDestroy(() => stream.close());
</script>

<main class="record">
  <p class="label mono">{snapshot?.course ?? ""}</p>
  {#if record}
    <h1>课堂已结束</h1>
    <p class="muted">{record.status === "done" ? `笔记已保存到 ${record.output}` : `状态：${record.status}`}</p>
  {:else if snapshot}
    <h1 class="mono">{formatElapsed(snapshot.elapsed_seconds)}</h1>
    <p class="muted">{phaseLabel(snapshot.phase)}{snapshot.paused ? " · 已暂停" : ""}</p>
  {:else}
    <h1>没有进行中的课堂</h1>
  {/if}
  <a class="btn" href="#/">返回首页</a>
</main>

<style>
  .record { min-height: 100vh; padding: 28px 44px 32px; display: flex; flex-direction: column; gap: 16px; align-items: flex-start; }
  h1 { font-family: var(--display); font-weight: 400; font-size: 64px; line-height: 1; }
  .muted { color: var(--muted); }
</style>
