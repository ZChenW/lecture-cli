<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { asrSummary } from "../lib/format";
  import { navigate } from "../lib/router";
  import { app, message } from "../lib/state.svelte";

  let { course, onclose }: { course: string; onclose: () => void } = $props();
  const boot = app.boot!;
  const cloud = boot.config.asr_backend === "api";
  let language = $state(boot.config.language);
  let refine = $state(boot.config.refine && !cloud);
  let busy = $state(false);
  let error = $state("");
  let log = $state("");
  let dialog: HTMLDialogElement;

  onMount(() => dialog.showModal());

  async function start(event: SubmitEvent) {
    event.preventDefault();
    busy = true;
    error = log = "";
    try {
      await api.startRun(course, cloud ? { language } : { language, refine });
      navigate("record");
    } catch (e) {
      error = message(e);
      log = (e as { log?: string }).log ?? "";
    } finally {
      busy = false;
    }
  }
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="start-title">
  <form class="stack" onsubmit={start}>
    <h2 id="start-title">开始上课</h2>
    <dl>
      <dt class="label">课程</dt><dd>{course}</dd>
      <dt class="label">转录</dt><dd class="mono">{asrSummary(boot.config, boot.presets.asr)}</dd>
    </dl>
    <label class="field">
      <span>课堂语言</span>
      <select class="input" bind:value={language}>
        <option value="en">英语 en</option>
        <option value="zh">中文 zh</option>
        {#if !boot.config.asr_model.startsWith("qwen") || cloud}<option value="auto">自动识别 auto</option>{/if}
        {#if !["en", "zh", "auto"].includes(language)}<option value={language}>{language}</option>{/if}
      </select>
    </label>
    <label class="check">
      <input type="checkbox" bind:checked={refine} disabled={cloud} />
      下课后用 Qwen 校正转录
    </label>
    {#if cloud}<p class="hint">使用云端转录时不进行课后校正。</p>{/if}
    {#if error}<p class="error-text" role="alert">{error}</p>{/if}
    {#if log}<pre class="log">{log}</pre>{/if}
    <div class="row actions">
      <button type="button" class="btn" onclick={() => dialog.close()}>取消</button>
      <button type="submit" class="btn primary" disabled={busy}>{busy ? "正在启动…" : "开始"}</button>
    </div>
  </form>
</dialog>

<style>
  dialog {
    width: min(460px, calc(100vw - 32px));
    padding: 32px;
    border: 1px solid var(--fg);
    border-radius: 4px;
    background: var(--bg);
    color: var(--fg);
  }
  dialog::backdrop { background: rgba(17, 17, 17, 0.35); }
  h2 { font-family: var(--display); font-weight: 600; font-size: 26px; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; align-items: baseline; }
  dd { margin: 0; }
  .actions { justify-content: flex-end; }
</style>
