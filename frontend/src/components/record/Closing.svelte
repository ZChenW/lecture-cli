<script lang="ts">
  import { formatElapsed } from "../../lib/format";
  import { closingStages, destinationLine, stageTime } from "../../lib/record";
  import type { Snapshot } from "../../lib/types";
  import StateLabel from "./StateLabel.svelte";

  let { snapshot, drainStart, dateline, busy, onskip }: {
    snapshot: Snapshot; drainStart: number | null; dateline: string; busy: boolean; onskip: () => void;
  } = $props();
  let stages = $derived(closingStages(snapshot, drainStart));
</script>

<main class="cols">
  <section class="left">
    <StateLabel text="已下课" live={false} />
    <div class="timer" data-timer>{formatElapsed(snapshot.elapsed_seconds)}</div>
    <div class="meta">
      <span>{dateline}</span>
      <span>{destinationLine(snapshot.course, false)}</span>
    </div>
  </section>
  <section class="center">
    <span class="label">正在整理笔记</span>
    <ol class="stages">
      {#each stages as stage (stage.key)}
        <li class={stage.state} aria-current={stage.state === "current" ? "step" : undefined}>
          <span class="mark" aria-hidden="true">
            {#if stage.state === "done"}
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#D4FF5C" stroke-width="1.5"
                stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
            {:else}
              <span class="dot"></span>
            {/if}
          </span>
          <div class="body">
            <span class="name">{stage.label}</span>
            {#if stage.state === "current" && stage.status}
              <span class="status">{stage.status}</span>
            {/if}
            {#if stage.state === "current" && stage.progress}
              <progress max={stage.progress.total} value={stage.progress.done}></progress>
            {/if}
          </div>
          <span class="time">{stage.state === "done" ? stageTime(stage.seconds) : ""}</span>
        </li>
      {/each}
    </ol>
  </section>
  <!-- Keeps the reference's 1 : 2 : 1 columns so the list stays centred. -->
  <div class="spacer" aria-hidden="true"></div>
</main>

<footer class="bar">
  <span class="hint">收尾在后台进行，关闭窗口不会中断</span>
  {#if snapshot.can_skip}
    <button class="skip" aria-disabled={busy} onclick={() => busy || onskip()}>跳过校正，用实时转录生成笔记</button>
  {/if}
</footer>

<style>
  .cols { flex: 1; display: flex; flex-wrap: wrap; gap: 48px; align-items: stretch; position: relative; min-height: 0; }
  .left { flex: 1 1 340px; display: flex; flex-direction: column; justify-content: flex-start; gap: 28px; min-width: 0; padding-top: 24px; }
  /* The recording timer shrinks into this spot (a 500 ms FLIP run by the page). */
  .timer { font-family: 'Instrument Serif', serif; font-size: 72px; line-height: 0.86; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; transform-origin: left top; }
  .meta { display: flex; flex-direction: column; gap: 4px; }
  .meta span { font-size: 13px; color: #8E9096; }
  .center { flex: 2 1 420px; display: flex; flex-direction: column; justify-content: center; gap: 22px; min-width: 0; }
  .spacer { flex: 1 1 320px; max-width: 420px; }
  .label { font-size: 12px; letter-spacing: 0.24em; color: #8E9096; }
  .stages { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; }
  li {
    display: grid;
    grid-template-columns: 18px 1fr auto;
    align-items: baseline;
    gap: 20px;
    padding: 20px 0;
    border-top: 1px solid #1D1F23;
  }
  li:last-child { border-bottom: 1px solid #1D1F23; }
  .mark { display: flex; align-items: center; justify-content: center; align-self: center; }
  .dot { width: 9px; height: 9px; border-radius: 50%; box-sizing: border-box; border: 1px solid #34363B; }
  .current .dot { border: 0; background: #D4FF5C; box-shadow: 0 0 0 5px rgba(212, 255, 92, 0.14); animation: breathe 2s ease-in-out infinite; }
  @keyframes breathe { 50% { box-shadow: 0 0 0 8px rgba(212, 255, 92, 0.04); } }
  .body { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .name { font-size: 22px; line-height: 1.4; font-weight: 300; }
  .done .name { color: #A9ABB0; }
  .pending .name { color: #5E6067; }
  .current .name { font-size: 26px; font-weight: 400; color: #FFFFFF; }
  .status { font-size: 13px; line-height: 1.6; color: #A9ABB0; overflow-wrap: anywhere; }
  progress { appearance: none; width: 100%; max-width: 420px; height: 2px; border: 0; background: #23252A; }
  progress::-webkit-progress-bar { background: #23252A; }
  progress::-webkit-progress-value { background: #D4FF5C; transition: width 300ms cubic-bezier(.2, .8, .2, 1); }
  progress::-moz-progress-bar { background: #D4FF5C; }
  .time { font-family: 'Geist Mono', monospace; font-size: 12px; color: #8E9096; }
  .bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    position: relative;
    border-top: 1px solid #1D1F23;
    padding-top: 22px;
    /* Same height with or without the skip button, so the rule does not jump between stages. */
    min-height: 56px;
  }
  .hint { font-size: 12px; color: #8E9096; }
  .skip {
    height: 56px;
    padding: 0 26px;
    border-radius: 999px;
    border: 1px solid #34363B;
    background: transparent;
    color: #ECEAE4;
    font-size: 15px;
    cursor: pointer;
  }
  .skip[aria-disabled="true"] { cursor: progress; }
  @media (prefers-reduced-motion: reduce) { .current .dot { animation: none; } }
</style>
