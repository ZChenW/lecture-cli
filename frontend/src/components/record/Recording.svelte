<script lang="ts">
  import { formatElapsed, weakLabel } from "../../lib/format";
  import { BACKLOG_WARN_SECONDS, closingHint, destinationLine, gainState, micPercent, recordingState } from "../../lib/record";
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
  let gain = $derived(gainState(snapshot.asr));
  let weak = $derived(snapshot.asr.weak_input ?? "");
  let estimate = $derived(closingHint(snapshot.closing_estimate_seconds));
</script>

<main class="cols">
  <section class="left">
    <StateLabel text={recordingState(snapshot)} live={!snapshot.paused} />
    <div class="timer" class:long={snapshot.elapsed_seconds >= 3600} data-timer>{formatElapsed(snapshot.elapsed_seconds)}</div>
    <Waveform {samples} paused={snapshot.paused} />
    <div class="meta">
      <span>{dateline}</span>
      <span>{destinationLine(snapshot.course, false)}</span>
    </div>
  </section>
  <Transcript transcript={snapshot.transcript} elapsed={snapshot.elapsed_seconds} paused={snapshot.paused} />
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
      <span class="value" class:warn={weak}>{micPercent(snapshot.asr.level)}</span>
      <!-- Plan N3.4: weak input turns the cell to the warning colour; the full text is the tooltip. -->
      {#if weak}<span class="sub warn" title={weak}>{weakLabel(weak)}<span class="visually-hidden">：{weak}</span></span>
      <!-- The automatic volume notice, formerly a banner (plan N2.5); its full text is the tooltip. -->
      {:else if gain}<span class="sub" title={gain.detail}>{gain.cell}<span class="visually-hidden">：{gain.detail}</span></span>{/if}
    </div>
  </div>
  <div class="controls">
    <button class="pause" aria-disabled={busy} onclick={onpause} aria-describedby="pause-tip">
      {#if snapshot.paused}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 5l12 7-12 7z" /></svg>
        继续
      {:else}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" aria-hidden="true"><path d="M8 5v14M16 5v14" /></svg>
        课间暂停
      {/if}
      <kbd>P</kbd>
      <span class="tip" id="pause-tip" role="tooltip">暂停期间不录音、不计时</span>
    </button>
    <div class="stop-wrap">
      {#if armed && estimate}<p class="estimate" role="status">{estimate}</p>{/if}
      <button class="stop" class:armed aria-disabled={busy} onclick={onstop} aria-live="polite">
        {armed ? "再按一次确认下课" : "下课，生成笔记"}
        <kbd>Q</kbd>
      </button>
    </div>
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
  .sub { font-size: 12px; color: #8E9096; }
  .sub.warn { color: #FFB454; }
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
  .pause { position: relative; padding: 0 26px; border: 1px solid #34363B; background: transparent; color: #ECEAE4; }
  /* Tooltip above the button on hover or keyboard focus. */
  .tip {
    position: absolute;
    bottom: calc(100% + 10px);
    left: 50%;
    transform: translateX(-50%);
    padding: 8px 12px;
    background: #15171A;
    border: 1px solid #34363B;
    font-size: 13px;
    font-weight: 400;
    color: #ECEAE4;
    white-space: nowrap;
    pointer-events: none;
    opacity: 0;
    transition: opacity 120ms ease 300ms;
  }
  .pause:hover .tip, .pause:focus-visible .tip { opacity: 1; }
  .stop-wrap { position: relative; }
  /* Above the armed button, right-aligned with it. */
  .estimate {
    position: absolute;
    right: 0;
    bottom: calc(100% + 10px);
    margin: 0;
    font-size: 13px;
    color: #A9ABB0;
    white-space: nowrap;
  }
  .stop { padding: 0 28px; border: none; background: #D4FF5C; color: #0B0C0E; font-weight: 600; }
  /* aria-disabled, not disabled: a disabled button drops keyboard focus to the page mid-request. */
  button[aria-disabled="true"] { cursor: progress; }
  kbd { font-family: 'Geist Mono', monospace; font-size: 12px; border-radius: 5px; padding: 2px 7px; }
  .pause kbd { color: #8E9096; border: 1px solid #34363B; }
  .stop kbd { font-weight: 500; border: 1px solid rgba(11, 12, 14, 0.35); }
  /* PLAN-GUI-3 section 8: short windows (a 1366x768 laptop) shrink the timer and the vertical
     whitespace before anything else; the transcript and the bottom bar are never hidden. */
  @media (max-height: 720px) {
    .left { gap: 20px; }
    .timer { font-size: 152px; }
    .timer.long { font-size: 112px; }
    .bar { padding-top: 18px; }
  }
</style>
