<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";
  import Banner from "../components/record/Banner.svelte";
  import Closing from "../components/record/Closing.svelte";
  import Ended from "../components/record/Ended.svelte";
  import RecordHeader from "../components/record/RecordHeader.svelte";
  import Recording from "../components/record/Recording.svelte";
  import { api, subscribe, type Subscription } from "../lib/api";
  import { formatNoteDate } from "../lib/format";
  import { CLOSING_PHASES, isTyping, pushLevel } from "../lib/record";
  import { app, message } from "../lib/state.svelte";
  import type { RunRecord, Snapshot } from "../lib/types";

  const LEAVE = "录制会在后台继续，重新打开 Lecture 即可回到这里";
  const WEEKDAYS = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];

  let snapshot = $state<Snapshot | null>(null);
  let record = $state<RunRecord | null>(null);
  let missing = $state(false);
  let samples = $state<number[]>([]);
  let drainStart = $state<number | null>(null);
  let armed = $state(false);
  let busy = $state(false);
  let error = $state("");
  let now = $state(Date.now());
  let stream: Subscription | null = null;
  let disarm: ReturnType<typeof setTimeout> | undefined;
  let clock: ReturnType<typeof setInterval> | undefined;

  let closing = $derived(!!snapshot && CLOSING_PHASES.includes(snapshot.phase));
  let recording = $derived(!!snapshot && !closing && !record);
  let dateline = $derived(dateOf(snapshot?.started ?? record?.started ?? null));

  function dateOf(started: string | null): string {
    if (!started) return "";
    const day = new Date(started);
    return Number.isNaN(day.getTime()) ? formatNoteDate(started) : `${formatNoteDate(started)} · ${WEEKDAYS[day.getDay()]}`;
  }

  /** The one large motion: the timer shrinks into the closing layout's corner (plan 6.2). */
  async function shrinkTimer(apply: () => void) {
    const before = document.querySelector<HTMLElement>("[data-timer]")?.getBoundingClientRect();
    apply();
    await tick();
    const timer = document.querySelector<HTMLElement>("[data-timer]");
    if (!before || !timer || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const after = timer.getBoundingClientRect();
    const scale = before.height / after.height;
    timer.animate([
      { transform: `translate(${before.left - after.left}px, ${before.top - after.top}px) scale(${scale})`, transformOrigin: "left top" },
      { transform: "none", transformOrigin: "left top" },
    ], { duration: 500, easing: "cubic-bezier(.2,.8,.2,1)" });
  }

  function accept(next: Snapshot) {
    // One bar per new reading; a repeated snapshot (the stream's first one, a control reply) adds none.
    const fresh = !snapshot || next.asr.level !== snapshot.asr.level || next.elapsed_seconds !== snapshot.elapsed_seconds;
    if (fresh) samples = pushLevel(samples, next.paused ? 0 : next.asr.level);
    if (next.phase === "draining" && drainStart == null) drainStart = next.phase_since;
    const entering = !!snapshot && !CLOSING_PHASES.includes(snapshot.phase) && CLOSING_PHASES.includes(next.phase);
    if (entering) shrinkTimer(() => (snapshot = next));
    else snapshot = next;
  }

  // A dropped stream may mean the run ended in between; the registry list says how.
  async function recheck() {
    if (!snapshot?.run_id) return;
    try {
      const found = (await api.runs()).find((run) => run.run_id === snapshot?.run_id);
      if (found && found.status !== "running") finish(found);
    } catch {
      // The stream's own retry keeps going.
    }
  }

  function finish(done: RunRecord) {
    record = done;
    stream?.close();
  }

  async function act(action: "pause" | "resume" | "stop" | "skip-refine") {
    busy = true;
    error = "";
    try {
      accept(await api.control(action));
    } catch (e) {
      error = message(e);
    } finally {
      busy = false;
    }
  }

  const togglePause = () => snapshot && act(snapshot.paused ? "resume" : "pause");

  // Stopping takes a second press within three seconds; no dialog (plan M6).
  function stop() {
    clearTimeout(disarm);
    if (!armed) {
      armed = true;
      disarm = setTimeout(() => (armed = false), 3000);
      return;
    }
    armed = false;
    act("stop");
  }

  function onkey(event: KeyboardEvent) {
    if (!recording || busy || event.repeat || event.ctrlKey || event.metaKey || event.altKey || isTyping(event.target)) return;
    const key = event.key.toLowerCase();
    if (key === "p") togglePause();
    else if (key === "q") stop();
    else return;
    event.preventDefault();
  }

  function onleave(event: BeforeUnloadEvent) {
    if (!recording) return;
    event.preventDefault();
    event.returnValue = LEAVE;
    return LEAVE;
  }

  onMount(async () => {
    clock = setInterval(() => (now = Date.now()), 15000);
    try {
      const first = app.boot?.active_run ?? (await api.activeRun());
      if (!first) {
        missing = true;
        return;
      }
      accept(first);
      if (first.phase === "draining") drainStart = first.phase_since;
      stream = subscribe({ snapshot: accept, finished: finish, lost: recheck });
    } catch (e) {
      error = message(e);
    }
  });

  $effect(() => {
    app.recording = !!snapshot && !record;
    // A finished run must not reopen this page from the stale bootstrap.
    if (record && app.boot) app.boot.active_run = null;
  });

  onDestroy(() => {
    stream?.close();
    clearTimeout(disarm);
    clearInterval(clock);
  });
</script>

<svelte:window onkeydown={onkey} onbeforeunload={onleave} />

<div class="record">
  <div class="glow" aria-hidden="true"></div>
  <RecordHeader {snapshot} />
  {#if snapshot && !record}<Banner asr={snapshot.asr} />{/if}
  {#if error}<p class="failure" role="alert">{error}</p>{/if}
  {#if record}
    <Ended {record} elapsed={snapshot?.elapsed_seconds ?? null} {dateline} />
  {:else if snapshot && closing}
    <Closing {snapshot} {drainStart} {dateline} {busy} onskip={() => act("skip-refine")} />
  {:else if snapshot}
    <Recording {snapshot} {samples} {now} {armed} {busy} {dateline} onpause={togglePause} onstop={stop} />
  {:else if missing}
    <main class="missing">
      <h1>没有进行中的课堂</h1>
      <a class="home" href="#/">返回首页</a>
    </main>
  {/if}
</div>

<style>
  /* Root of the reference: 16px and normal line height, which the night theme otherwise overrides. */
  .record {
    min-height: 100vh;
    box-sizing: border-box;
    background: #0B0C0E;
    color: #ECEAE4;
    font-family: 'Geist', 'Noto Sans SC', 'Noto Sans CJK SC', 'Source Han Sans SC', system-ui, sans-serif;
    font-size: 16px;
    line-height: normal;
    letter-spacing: normal;
    display: flex;
    flex-direction: column;
    gap: 20px;
    padding: 28px 44px 32px;
    position: relative;
    overflow: hidden;
  }
  /* The reference keeps the browser defaults: content-box everywhere except buttons (and the
     root and card, which set border-box themselves), so its sizes mean the same thing here. */
  :global(.record :where(:not(button)), .record :where(:not(button))::before, .record :where(:not(button))::after) {
    box-sizing: content-box;
  }
  .glow {
    position: absolute;
    left: -220px;
    top: 60px;
    width: 900px;
    height: 900px;
    border-radius: 50%;
    background: radial-gradient(closest-side, rgba(212, 255, 92, 0.09), rgba(212, 255, 92, 0));
    pointer-events: none;
  }
  .failure { position: relative; margin: 0; font-size: 13px; color: #FF6B5E; }
  .missing { flex: 1; position: relative; display: flex; flex-direction: column; justify-content: center; gap: 22px; }
  h1 { margin: 0; font-size: 38px; line-height: 1.25; font-weight: 400; letter-spacing: -0.01em; color: #FFFFFF; }
  .home {
    align-self: flex-start;
    display: inline-flex;
    align-items: center;
    height: 56px;
    padding: 0 26px;
    border-radius: 999px;
    border: 1px solid #34363B;
    color: #ECEAE4;
    font-size: 15px;
    text-decoration: none;
  }
</style>
