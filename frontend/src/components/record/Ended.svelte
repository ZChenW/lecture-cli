<script lang="ts">
  import { api } from "../../lib/api";
  import { formatElapsed } from "../../lib/format";
  import { destinationLine, savedPath } from "../../lib/record";
  import { navigate } from "../../lib/router";
  import { app, message } from "../../lib/state.svelte";
  import type { RunRecord } from "../../lib/types";
  import StateLabel from "./StateLabel.svelte";

  let { record, elapsed, dateline }: { record: RunRecord; elapsed: number | null; dateline: string } = $props();
  let error = $state("");
  let log = $state<HTMLElement>();
  // The end of the log is what explains the failure.
  $effect(() => {
    if (log) log.scrollTop = log.scrollHeight;
  });
  // A discarded run never reaches this page (Record goes home), so it is never shown as failed.
  // empty (plan GUI-4 Q1.2): saved, but nothing was recognised, so there are no notes to read.
  type Kind = "done" | "empty" | "unsaved" | "failed";
  let kind = $derived<Kind>(record.status === "done" ? (record.flags?.empty ? "empty" : "done")
    : record.status === "unsaved" ? "unsaved" : "failed");
  const STATES: Record<Kind, string> = { done: "已保存", empty: "没有内容", unsaved: "尚未保存", failed: "异常结束" };
  const TITLES: Record<Kind, string> = {
    done: "笔记已保存", empty: "没有识别出内容", unsaved: "笔记尚未成功保存", failed: "课堂异常结束",
  };
  const CAUSES = ["麦克风没有收到声音", "选错了输入设备", "麦克风被静音"];

  function checkMic() {
    app.section = "mic";
    navigate("settings");
  }
  let file = $derived(record.output?.split("/").pop() ?? "");
  // Skipping refinement is the user's choice, not a problem: a plain line, not a warning (plan N2.5).
  let skipped = $derived(!!record.flags?.refinement_skipped);
  let warnings = $derived((record.warnings ?? []).filter((w) => !(skipped && w.startsWith("已跳过离线校正"))));
  let segments = $derived(savedPath(record.output, record.course));

  async function reveal() {
    error = "";
    try {
      await api.open(record.output ?? "", "reveal");
    } catch (e) {
      error = message(e);
    }
  }
</script>

<main class="cols">
  <section class="left">
    <StateLabel text={STATES[kind]} live={false} />
    {#if elapsed != null}<div class="timer" data-timer>{formatElapsed(elapsed)}</div>{/if}
    <div class="meta">
      <span>{dateline}</span>
      {#if record.course && kind !== "empty"}<span>{destinationLine(record.course, kind === "done")}</span>{/if}
    </div>
  </section>
  <section class="center">
    <span class="label">{kind === "done" || kind === "empty" ? "课堂结束" : kind === "unsaved" ? "需要处理" : "出错了"}</span>
    <h1>{TITLES[kind]}</h1>
    {#if kind === "done"}
      <!-- Whole segments: a line may break at a separator, never inside the file name. -->
      <p class="segments" title={record.output}>
        {#each segments as segment, i (i)}
          <span class="segment" class:course={i === 0 && segments.length > 1}><span class="name">{segment}</span>{#if i < segments.length - 1}<span class="sep" aria-hidden="true">/</span>{/if}</span>
        {/each}
      </p>
    {:else if kind === "empty"}
      <div class="callout empty" role="status">
        <span class="dot" aria-hidden="true"></span>
        <div>
          <p>这次录制没有识别出任何内容，所以没有生成笔记。可能的原因：</p>
          <ul class="causes">{#each CAUSES as cause (cause)}<li>{cause}</li>{/each}</ul>
          <p class="sub">可以在终端运行 <span class="mono cmd">lecture doctor --mic-test</span> 检查麦克风。</p>
        </div>
      </div>
    {:else if kind === "unsaved"}
      <p class="callout" role="alert">
        <span class="dot" aria-hidden="true"></span>
        <span>笔记尚未成功保存，暂存于 <span class="mono">{record.workspace_kept ?? "临时目录"}</span>；恢复目标目录可写后重新打开 Lecture 会自动恢复</span>
      </p>
    {:else}
      <p class="path">退出码 {record.exit_code ?? "未知"}</p>
      {#if record.log_tail}
        <pre class="log" bind:this={log} aria-label="控制器日志末尾 20 行">{record.log_tail}</pre>
      {:else}
        <p class="path">没有可显示的日志{record.log ? `：${record.log}` : ""}</p>
      {/if}
    {/if}
    {#if record.stages?.length}
      <ul class="stages" aria-label="各阶段耗时">
        {#each record.stages as stage (stage.name)}
          <li><span>{stage.name}</span><span class="time">{formatElapsed(stage.seconds)}</span></li>
        {/each}
      </ul>
    {/if}
    {#if skipped}<p class="skipped">已跳过校正，笔记依据实时转录</p>{/if}
    {#if warnings.length}
      <ul class="warnings">
        {#each warnings as warning (warning)}<li>{warning}</li>{/each}
      </ul>
    {/if}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
  </section>
  <div class="spacer" aria-hidden="true"></div>
</main>

<footer class="bar">
  <button class="ghost" onclick={() => navigate("home")}>返回首页</button>
  {#if kind === "done" && record.output}
    <div class="controls">
      <button class="ghost" onclick={reveal}>在文件管理器中显示</button>
      <button class="primary" onclick={() => navigate("notes", record.course ?? "", file)}>阅读笔记</button>
    </div>
  {:else if kind === "empty"}
    <div class="controls">
      <button class="primary" onclick={checkMic}>检查麦克风</button>
    </div>
  {/if}
</footer>

<style>
  .cols { flex: 1; display: flex; flex-wrap: wrap; gap: 48px; align-items: stretch; position: relative; min-height: 0; }
  .left { flex: 1 1 340px; display: flex; flex-direction: column; justify-content: flex-start; gap: 28px; min-width: 0; padding-top: 24px; }
  .timer { font-family: 'Instrument Serif', serif; font-size: 72px; line-height: 0.86; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
  .meta { display: flex; flex-direction: column; gap: 4px; }
  .meta span { font-size: 13px; color: #8E9096; }
  .center { flex: 2 1 420px; display: flex; flex-direction: column; justify-content: center; gap: 22px; min-width: 0; }
  .spacer { flex: 1 1 320px; max-width: 420px; }
  /* Section heading: 0.24em by the user-confirmed rule (form field labels use 0.08em, see base.css). */
  .label { font-size: 12px; letter-spacing: 0.24em; color: #8E9096; }
  h1 { margin: 0; font-size: 38px; line-height: 1.25; font-weight: 400; letter-spacing: -0.01em; color: #FFFFFF; }
  p { margin: 0; }
  .path { font-family: 'Geist Mono', monospace; font-size: 13px; line-height: 1.6; color: #A9ABB0; overflow-wrap: anywhere; }
  .segments { display: flex; flex-wrap: wrap; column-gap: 0.6ch; font-family: 'Geist Mono', monospace; font-size: 13px; line-height: 1.6; color: #A9ABB0; }
  .segment { display: inline-flex; gap: 0.6ch; min-width: 0; max-width: 100%; white-space: nowrap; }
  /* A course name longer than the line gives way first, with an ellipsis; the title has the full path. */
  .segment .name { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .sep { flex: none; color: #8E9096; }
  .mono { font-family: 'Geist Mono', monospace; font-size: 13px; overflow-wrap: anywhere; }
  /* Plan GUI-3 item 2: no left bars anywhere; the warning banner's surface and dot (plan N2) instead. */
  .callout {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 18px 20px;
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.04);
    color: #ECEAE4;
    font-size: 15px;
    line-height: 1.65;
  }
  .callout.empty > div { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .causes { display: flex; flex-direction: column; gap: 2px; padding-left: 1.2em; list-style: disc; }
  .callout .sub { font-size: 13px; color: #A9ABB0; }
  .callout .cmd { white-space: nowrap; }
  .callout .dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: #FFB454; transform: translateY(-2px); }
  .log {
    margin: 0;
    max-height: 384px;  /* Twenty unwrapped lines. */
    overflow: auto;
    padding: 16px 18px;
    border: 1px solid #23252A;
    background: rgba(255, 255, 255, 0.025);
    font-family: 'Geist Mono', monospace;
    font-size: 12px;
    line-height: 1.6;
    color: #A9ABB0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  ul { margin: 0; padding: 0; list-style: none; }
  .stages { display: flex; flex-direction: column; max-width: 520px; }
  .stages li {
    display: flex;
    justify-content: space-between;
    gap: 20px;
    padding: 12px 0;
    border-top: 1px solid #1D1F23;
    font-size: 15px;
    line-height: 1.65;
    color: #C9CACD;
  }
  .stages li:last-child { border-bottom: 1px solid #1D1F23; }
  .time { font-family: 'Geist Mono', monospace; font-size: 12px; color: #8E9096; }
  .skipped { font-size: 15px; line-height: 1.65; color: #ECEAE4; }
  .warnings { display: flex; flex-direction: column; gap: 8px; font-size: 13px; line-height: 1.6; color: #FFB454; }
  .error { font-size: 13px; color: #FF6B5E; }
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
  .controls { display: flex; align-items: center; gap: 12px; }
  .ghost, .primary { height: 56px; border-radius: 999px; font-size: 15px; cursor: pointer; }
  .ghost { padding: 0 26px; border: 1px solid #34363B; background: transparent; color: #ECEAE4; }
  .primary { padding: 0 28px; border: none; background: #D4FF5C; color: #0B0C0E; font-weight: 600; }
</style>
