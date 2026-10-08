<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { api } from "../lib/api";
  import { MISSING, TARGET_LABELS } from "../lib/checks";
  import { asrSummary } from "../lib/format";
  import type { ListOption } from "../lib/listbox";
  import { navigate } from "../lib/router";
  import { app, message } from "../lib/state.svelte";
  import type { Missing } from "../lib/types";
  import Choices from "./Choices.svelte";
  import Select from "./Select.svelte";

  // missing: what the lecture cannot start without; the dialog explains it and links to settings.
  let { course, missing = [], onclose, onfix }: {
    course: string; missing?: Missing[]; onclose: () => void; onfix: (section: string) => void;
  } = $props();
  const boot = app.boot!;
  const cloud = boot.config.asr_backend === "api";
  const qwen = !cloud && boot.config.asr_model.startsWith("qwen");
  // The language last used for this course, else the default (remembered by the backend on start).
  const remembered = untrack(() => boot.config.course_settings?.[course]?.language);
  let language = $state(remembered ?? boot.config.language);
  let refine = $state(boot.config.refine && !cloud ? "on" : "off");
  let busy = $state(false);
  let error = $state("");
  let log = $state("");
  let dialog: HTMLDialogElement;
  let blocked = $derived(missing.length > 0);
  const languages = $derived<ListOption[]>([
    { value: "en", label: "英语", code: "en" },
    { value: "zh", label: "中文", code: "zh" },
    { value: "auto", label: "自动识别", code: "auto", disabled: qwen },
    ...(["en", "zh", "auto"].includes(language) ? [] : [{ value: language, label: language }]),
  ]);

  onMount(() => dialog.showModal());

  function fix(section: string) {
    dialog.close();
    onfix(section);
  }

  async function start(event: SubmitEvent) {
    event.preventDefault();
    if (blocked) return;
    busy = true;
    error = log = "";
    try {
      await api.startRun(course, cloud ? { language } : { language, refine: refine === "on" });
      navigate("record");
    } catch (e) {
      error = message(e);
      log = (e as { log?: string }).log ?? "";
    } finally {
      busy = false;
    }
  }
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="start-title" aria-describedby={blocked ? "start-missing" : undefined}>
  <form class="stack" onsubmit={start}>
    <h2 id="start-title">开始上课</h2>
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
    <dl>
      <dt class="label">课程</dt><dd>{course}</dd>
      <dt class="label">转录</dt><dd class="mono">{asrSummary(boot.config, boot.presets.asr)}</dd>
    </dl>
    <div class="field">
      <span id="start-language">课堂语言</span>
      <Select labelledby="start-language" options={languages} bind:value={language} />
      <span class="field-note">{remembered ? "上次这门课用的语言" : "默认课堂语言，可在设置中更改"}</span>
    </div>
    {#if !cloud}
      <!-- Cloud transcription is never refined afterwards, so the option is not offered. -->
      <div class="field">
        <span class="label">下课后</span>
        <Choices label="下课后" bind:value={refine} options={[
          { value: "on", title: "用 Qwen 校正转录" }, { value: "off", title: "直接用实时转录" },
        ]} />
      </div>
    {/if}
    {#if error}<p class="error-text" role="alert">{error}</p>{/if}
    {#if log}<pre class="log">{log}</pre>{/if}
    <div class="row actions">
      <button type="button" class="btn" onclick={() => dialog.close()}>取消</button>
      <button type="submit" class="btn primary" disabled={busy || blocked}>{busy ? "正在启动…" : "开始"}</button>
    </div>
  </form>
</dialog>

<style>
  dialog {
    width: min(480px, calc(100vw - 32px));
    padding: 32px;
    border: 1px solid var(--fg);
    background: var(--bg);
    color: var(--fg);
    /* The language list may extend past the dialog's edge. */
    overflow: visible;
  }
  dialog::backdrop { background: rgba(17, 17, 17, 0.35); }
  form { gap: 20px; }
  h2 { font-family: "Noto Serif SC", var(--cjk-serif); font-weight: 600; font-size: 22px; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; align-items: baseline; }
  dt { font-size: 13px; letter-spacing: 0.08em; color: var(--label); }
  dd { margin: 0; }
  .missing { padding: 14px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); font-size: 14px; }
  .missing p { color: var(--warn); }
  .missing ul { margin: 6px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; }
  .missing li { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 4px 16px; min-height: 44px; }
  .missing a { display: inline-flex; align-items: center; min-height: 44px; color: var(--fg); text-underline-offset: 4px; }
  .actions { justify-content: flex-end; }
</style>
