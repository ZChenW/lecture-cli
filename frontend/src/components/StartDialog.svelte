<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { api } from "../lib/api";
  import { MISSING, TARGET_LABELS } from "../lib/checks";
  import { navigate } from "../lib/router";
  import {
    asrRequest, cloudLine, defaultChoice, effectiveChoice, qwenModel, qwenOption, qwenState,
    refineDefault, whisperModel, whisperOption, type LiveChoice,
  } from "../lib/start";
  import { app, message } from "../lib/state.svelte";
  import type { Missing, QwenLive } from "../lib/types";
  import MicLevel from "./MicLevel.svelte";
  import Segments from "./Segments.svelte";

  // Plan GUI-3 item 3: design/F-dialog.reference.html, left half. Three rows of underlined segments.
  // missing: what the lecture cannot start without; the dialog explains it and links to settings.
  let { course, missing = [], onclose, onfix }: {
    course: string; missing?: Missing[]; onclose: () => void; onfix: (section: string) => void;
  } = $props();
  const boot = app.boot!;
  const cloud = boot.config.asr_backend === "api";
  const remembered = untrack(() => boot.config.course_settings?.[course]);
  let language = $state(remembered?.language ?? boot.config.language);
  let refine = $state(refineDefault(boot.config, remembered?.refine));
  // Whether live Qwen is ready here; asked once for a local lecture (the GPU probe takes seconds the first time).
  let live = $state<QwenLive | null>(null);
  let liveFailed = $state(false);
  let picked = $state<LiveChoice | null>(null);
  const whisper = whisperModel(boot.config, remembered?.asr_model);
  let qwen = $derived(qwenState(live, liveFailed));
  let qwenSeg = $derived(qwenOption(language, qwen, live));
  let whisperSeg = whisperOption(whisper);
  let choice = $derived(effectiveChoice(picked, defaultChoice(boot.config, remembered?.asr_model, language, qwen, qwenSeg, whisperSeg),
                                        qwenSeg, whisperSeg));
  let busy = $state(false);
  let error = $state("");
  let log = $state("");
  let dialog: HTMLDialogElement;
  let meter = $state<ReturnType<typeof MicLevel>>();
  let blocked = $derived(missing.length > 0);
  // Qwen streaming needs a named language; with no Whisper model to fall back on, 自动 cannot be offered.
  const languages = $derived([
    { value: "en", title: "英语" },
    { value: "zh", title: "中文" },
    { value: "auto", title: "自动", disabled: !cloud && !whisper, hint: !cloud && !whisper ? "Qwen 实时转录需要指定英语或中文" : undefined },
    ...(["en", "zh", "auto"].includes(language) ? [] : [{ value: language, title: language }]),
  ]);

  onMount(() => {
    dialog.showModal();
    // Start on the language row's selected option, not on an unselected one that comes first.
    dialog.querySelector<HTMLElement>('[role="radio"][tabindex="0"]')?.focus();
    if (!cloud) api.qwenLive().then((result) => (live = result), () => (liveFailed = true));
  });

  function fix(section: string) {
    dialog.close();
    onfix(section);
  }

  async function start(event: SubmitEvent) {
    event.preventDefault();
    if (blocked) return;
    busy = true;
    error = log = "";
    meter?.stop();  // The lecture opens the microphone itself; let go of it first.
    try {
      await api.startRun(course, cloud ? { language } : {
        language, refine: refine === "on",
        ...asrRequest(boot.config, choice, { whisper, qwen: qwenModel(boot.config, live) }, qwen, language, qwenSeg, whisperSeg),
      });
      navigate("record");
    } catch (e) {
      error = message(e);
      log = (e as { log?: string }).log ?? "";
      meter?.restart();
    } finally {
      busy = false;
    }
  }
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="start-title" aria-describedby={blocked ? "start-missing" : undefined}>
  <form onsubmit={start}>
    <h2 id="start-title">开始上课</h2>
    <div class="course">{course}</div>
    {#if blocked}
      <div class="missing" id="start-missing" role="alert">
        <p>还不能开始，缺少：</p>
        <ul>
          {#each missing as item (item)}
            <li>
              <span>{MISSING[item].label}</span>
              <a href="#/settings" onclick={(event) => { event.preventDefault(); fix(MISSING[item].section); }}>
                去设置「{TARGET_LABELS[MISSING[item].section]}」</a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
    <!-- Plan GUI-4 Q1.5: whether the microphone hears anything, before the lecture starts. -->
    <div class="line first mic">
      <span id="start-mic" class="name">麦克风</span>
      <div class="cell meter-cell" role="group" aria-labelledby="start-mic"><MicLevel bind:this={meter} /></div>
    </div>
    <div class="line">
      <span id="start-language" class="name">语言</span>
      <Segments labelledby="start-language" options={languages} bind:value={language} />
    </div>
    <div class="line" class:last={cloud}>
      <span id="start-live" class="name">实时转录</span>
      {#if cloud}
        <span class="cloud" aria-labelledby="start-live">{cloudLine(boot.config)}</span>
      {:else}
        <Segments labelledby="start-live" value={choice} onchange={(next) => (picked = next as LiveChoice)} options={[
          { value: "whisper", title: "Whisper", aside: whisperSeg.aside, disabled: !whisperSeg.enabled, hint: whisperSeg.hint },
          { value: "qwen", title: "Qwen", aside: qwenSeg.aside, disabled: !qwenSeg.enabled, hint: qwenSeg.hint },
        ]} />
      {/if}
    </div>
    {#if !cloud}
      <!-- Cloud transcription is never refined afterwards, so the row is not offered. -->
      <div class="line last">
        <span id="start-refine" class="name">下课后</span>
        <!-- PLAN-GUI-5 R1: the upload notice stays in settings, where the cloud is chosen. -->
        <Segments labelledby="start-refine" bind:value={refine} options={[
          { value: "on", title: "校正一遍" }, { value: "off", title: "不校正" },
        ]} />
      </div>
    {/if}
    {#if error}<p class="error-text" role="alert">{error}</p>{/if}
    {#if log}<pre class="log">{log}</pre>{/if}
    <div class="actions">
      <button type="button" class="btn" onclick={() => dialog.close()}>取消</button>
      <button type="submit" class="btn primary" disabled={busy || blocked}>{busy ? "正在启动…" : "开始"}</button>
    </div>
  </form>
</dialog>

<style>
  /* Values from design/F-dialog.reference.html (left section). */
  dialog {
    width: min(520px, calc(100vw - 32px));
    box-sizing: border-box;
    padding: 32px 36px 28px;
    border: 1px solid #111111;
    background: #FBFBFA;
    color: #111111;
    font-size: 16px;
    line-height: normal;
  }
  dialog[open] { display: flex; flex-direction: column; }
  dialog::backdrop { background: rgba(17, 17, 17, 0.35); }
  form { display: flex; flex-direction: column; }
  h2 { margin: 0 0 6px; font-family: "Noto Serif SC", var(--cjk-serif); font-weight: 600; font-size: 26px; }
  .course { padding-bottom: 22px; font-family: var(--mono); font-size: 14px; color: #5C5C5A; overflow-wrap: anywhere; }
  /* content-box as in the reference: 64px plus the rule. */
  .line { box-sizing: content-box; display: grid; grid-template-columns: 96px minmax(0, 1fr); align-items: center; min-height: 64px; border-top: 1px solid #D9D9D6; }
  .line.first { border-top-color: #111111; }
  .line.last { border-bottom: 1px solid #D9D9D6; }
  /* Form labels keep the app's 0.08em tracking (user-confirmed rule; the reference has none). */
  .name { font-size: 14px; letter-spacing: 0.08em; color: #5C5C5A; }
  .cloud { padding: 0 14px; font-family: var(--mono); font-size: 14px; color: #111111; overflow-wrap: anywhere; }
  .cell { display: flex; flex-direction: column; min-width: 0; }
  .meter-cell { padding: 14px 14px 12px; }
  .missing { margin-bottom: 16px; padding: 14px 0; border-top: 1px solid var(--line); font-size: 14px; }
  .missing p { color: var(--warn); }
  .missing ul { margin: 6px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; }
  .missing li { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 4px 16px; min-height: 44px; }
  .missing a { display: inline-flex; align-items: center; min-height: 44px; color: var(--fg); text-underline-offset: 4px; }
  .error-text, .log { margin-top: 16px; }
  .actions { display: flex; justify-content: flex-end; gap: 12px; padding-top: 28px; }
  .actions .btn { height: 48px; padding: 0 22px; border: 1px solid #111111; font-size: 15px; color: #111111; }
  .actions .btn.primary { padding: 0 28px; background: #111111; color: #FBFBFA; font-weight: 600; }
  .actions .btn.primary:disabled { background: transparent; color: #111111; }
</style>
