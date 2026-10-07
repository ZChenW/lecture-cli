<script lang="ts">
  import { formatElapsed } from "../../lib/format";
  import { BACKLOG_WARN_SECONDS, destinationLine, micPercent } from "../../lib/record";
  import type { Snapshot } from "../../lib/types";
  import NotesCard from "./NotesCard.svelte";
  import StateLabel from "./StateLabel.svelte";
  import Transcript from "./Transcript.svelte";
  import Waveform from "./Waveform.svelte";

  let { snapshot, samples, now, armed, busy, onpause, onstop, dateline }: {
    snapshot: Snapshot; samples: number[]; now: number; armed: boolean; busy: boolean; dateline: string;
    onpause: () => void; onstop: () => void;
  } = $props();
  let backlog = $derived(snapshot.asr.backlog_seconds);
  let slow = $derived(backlog > BACKLOG_WARN_SECONDS);
</script>

<main class="cols">
  <section class="left">
    <StateLabel text={snapshot.paused ? "已暂停" : "录制中"} live={!snapshot.paused} />
    <div class="timer" class:long={snapshot.elapsed_seconds >= 3600} data-timer>{formatElapsed(snapshot.elapsed_seconds)}</div>
    <Waveform {samples} paused={snapshot.paused} />
    <div class="meta">
      <span>{dateline}</span>
      <span>{destinationLine(snapshot.course, false)}</span>
    </div>
  </section>
  <Transcript transcript={snapshot.transcript} />
  <NotesCard notes={snapshot.notes} {now} />
</main>

<footer class="bar">
  <div class="stats">
    <div class="stat">
      <span class="key">转录积压</span>
      <span class="value" class:warn={slow}>{backlog.toFixed(1)} s</span>
      {#if slow}<span class="slow">转录慢于实时，下课后收尾会更久</span>{/if}
    </div>
    <div class="stat">
      <span class="key">待整理内容</span>
      <span class="value">{snapshot.notes.unprocessed_segments} 片段</span>
    </div>
    <div class="stat">
      <span class="key">麦克风音量</span>
      <span class="value">{micPercent(snapshot.asr.level)}</span>
    </div>
  </div>
  <div class="controls">
    <button class="pause" aria-disabled={busy} onclick={onpause}>
      {#if snapshot.paused}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 5l12 7-12 7z" /></svg>
        继续
      {:else}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" aria-hidden="true"><path d="M8 5v14M16 5v14" /></svg>
        暂停
      {/if}
      <kbd>P</kbd>
    </button>
    <button class="stop" class:armed aria-disabled={busy} onclick={onstop} aria-live="polite">
      {armed ? "再按一次确认下课" : "下课，生成笔记"}
      <kbd>Q</kbd>
    </button>
  </div>
</footer>

<style>
  .cols { flex: 1; display: flex; flex-wrap: wrap; gap: 48px; align-items: stretch; position: relative; min-height: 0; }
  .left { flex: 1 1 340px; display: flex; flex-direction: column; justify-content: center; gap: 28px; min-width: 0; }
  .timer { font-family: 'Instrument Serif', serif; font-size: 184px; line-height: 0.86; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
  .timer.long { font-size: 132px; }
  .meta { display: flex; flex-direction: column; gap: 4px; }
  .meta span { font-size: 13px; color: #8E9096; }
  .bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    position: relative;
    border-top: 1px solid #1D1F23;
    padding-top: 22px;
  }
  .stats { display: flex; flex-wrap: wrap; gap: 44px; }
  .stat { display: flex; flex-direction: column; gap: 6px; }
  .key { font-size: 12px; color: #8E9096; }
  .value { font-family: 'Geist Mono', monospace; font-size: 18px; }
  .value.warn { color: #FFB454; }
  .slow { font-size: 12px; color: #FFB454; }
  .controls { display: flex; align-items: center; gap: 12px; }
  .pause, .stop {
    height: 56px;
    border-radius: 999px;
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 15px;
    cursor: pointer;
  }
  .pause { padding: 0 26px; border: 1px solid #34363B; background: transparent; color: #ECEAE4; }
  .stop { padding: 0 28px; border: none; background: #D4FF5C; color: #0B0C0E; font-weight: 600; }
  /* aria-disabled, not disabled: a disabled button drops keyboard focus to the page mid-request. */
  button[aria-disabled="true"] { cursor: progress; }
  kbd { font-family: 'Geist Mono', monospace; font-size: 12px; border-radius: 5px; padding: 2px 7px; }
  .pause kbd { color: #8E9096; border: 1px solid #34363B; }
  .stop kbd { font-weight: 500; border: 1px solid rgba(11, 12, 14, 0.35); }
</style>
