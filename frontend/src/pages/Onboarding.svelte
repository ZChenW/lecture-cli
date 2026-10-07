<script lang="ts">
  import { onDestroy } from "svelte";
  import { api, subscribe, type Subscription } from "../lib/api";
  import { phaseLabel } from "../lib/format";
  import { navigate } from "../lib/router";
  import { app, message, refresh } from "../lib/state.svelte";
  import type { Check, Course, RunRecord, Snapshot } from "../lib/types";
  import AsrForm from "../components/AsrForm.svelte";
  import ChecksPanel from "../components/ChecksPanel.svelte";
  import CoursesDirForm from "../components/CoursesDirForm.svelte";
  import Icon from "../components/Icon.svelte";
  import MicForm from "../components/MicForm.svelte";
  import NotesForm from "../components/NotesForm.svelte";

  const steps = [
    { label: "课程目录", title: "笔记存放在哪里", lead: "选一个文件夹，用来存放所有课程的笔记。" },
    { label: "转录方式", title: "怎样把声音变成文字", lead: "本地转录不上传音频；没有 NVIDIA GPU 时，云端更快。" },
    { label: "笔记服务", title: "由哪个模型写笔记", lead: "转录文字会定期发给这个服务，生成结构化笔记。" },
    { label: "麦克风", title: "用哪个麦克风", lead: "一般保持系统默认即可。" },
    { label: "检查", title: "最后检查一遍", lead: "失败项下方有修复建议。也可以先试运行一次。" },
  ];
  const pad = (n: number) => String(n).padStart(2, "0");

  let step = $state(0);
  let ready = $state(false);
  let busy = $state(false);
  let form = $state<{ save(): Promise<boolean> }>();
  // Preselect from the GPU probe only while the transcription choice is still the default one.
  const config = app.boot!.config;
  const freshAsr = config.asr_backend === "local" && config.asr_model === "base.en";
  let gpu = $state<boolean | null>(null);
  api.checks().then((list) => {
    const probe = list.find((c) => c.id === "asr_device");
    gpu = probe ? probe.level === "ok" && probe.detail.startsWith("NVIDIA GPU") : false;
  }).catch(() => (gpu = false));

  let checks = $state<Check[] | null>(null);
  let panel = $state<ChecksPanel>();
  let override = $state(false);
  let failing = $derived(!checks || checks.some((c) => c.level === "fail"));

  let courses = $state<Course[]>([]);
  let demoCourse = $state("");
  let demo = $state<{ snapshot?: Snapshot; record?: RunRecord; error?: string; log?: string } | null>(null);
  let error = $state("");
  let stream: Subscription | null = null;
  onDestroy(() => stream?.close());

  $effect(() => {
    if (step === 4) api.courses().then((list) => {
      courses = list;
      demoCourse ||= list[0]?.name ?? "";
    }).catch(() => (courses = []));
  });

  async function next() {
    busy = true;
    try {
      error = "";
      if (step < 4 && form && !(await form.save())) return;
      await refresh();
      if (step < steps.length - 1) {
        step += 1;
        ready = step === 3;
      } else {
        navigate("home");
      }
    } catch (e) {
      error = message(e);
    } finally {
      busy = false;
    }
  }

  function back() {
    step -= 1;
    ready = false;
  }

  async function tryRun() {
    stream?.close();
    demo = {};
    try {
      const started = await api.startRun(demoCourse, { demo: true });
      demo.snapshot = started;
      const finish = (record: RunRecord) => {
        stream?.close();
        if (demo?.record) return;
        demo = { ...demo, record };
        panel?.run();
      };
      stream = subscribe({
        snapshot: (snapshot) => demo && (demo.snapshot = snapshot),
        finished: finish,
        // A demo can end before the stream connects; the registry then has the outcome.
        lost: () => api.runs().then((list) => {
          const record = list.find((r) => r.run_id === started.run_id);
          if (record && record.status !== "running") finish(record);
        }).catch(() => {}),
      });
    } catch (e) {
      demo = { error: message(e), log: (e as { log?: string }).log };
    }
  }
  let demoRunning = $derived(!!demo && !demo.record && !demo.error);
  let canFinish = $derived(step < 4 ? ready : !demoRunning && (!failing || override));
</script>

<div class="wizard">
  <div class="glow" aria-hidden="true"></div>
  <header class="top">
    <span class="brand">lecture</span>
    <span class="label mono latin" aria-label="第 {step + 1} 步，共 {steps.length} 步">{pad(step + 1)} / {pad(steps.length)}</span>
    <span class="label">{steps[step].label}</span>
  </header>

  <div class="scroll">
    <main class="body">
      <h1>{steps[step].title}</h1>
      <p class="lead">{steps[step].lead}</p>
      {#key step}
        {#if step === 0}
          <CoursesDirForm bind:ready bind:this={form} />
        {:else if step === 1}
          <AsrForm bind:ready bind:this={form} gpu={freshAsr ? gpu : undefined} />
          {#if freshAsr && gpu === null}<p class="hint" role="status">正在检测 GPU…</p>{/if}
        {:else if step === 2}
          <NotesForm bind:ready bind:this={form} />
        {:else if step === 3}
          <MicForm bind:this={form} />
        {:else}
          <div class="stack">
            <ChecksPanel bind:results={checks} bind:this={panel} />
            <section class="stack demo" aria-label="试运行">
              <p class="hint">试运行用一段自造的文字走一遍完整流程，不录音；会消耗少量笔记服务 API 额度。</p>
              {#if courses.length}
                <div class="row">
                  {#if courses.length > 1}
                    <label class="field">
                      <span>写入课程</span>
                      <select class="input" bind:value={demoCourse} disabled={demoRunning}>
                        {#each courses as course}<option value={course.name}>{course.name}</option>{/each}
                      </select>
                    </label>
                  {/if}
                  <button type="button" class="btn" onclick={tryRun} disabled={demoRunning}>试运行一次</button>
                </div>
              {:else}
                <p class="warn-text">试运行需要至少一门课程，请回到第一步新建。</p>
              {/if}
              {#if demo?.snapshot && !demo.record}
                <p class="hint" role="status">{phaseLabel(demo.snapshot.phase)} · {demo.snapshot.notes.status}</p>
              {/if}
              {#if demo?.record}
                {#if demo.record.status === "done"}
                  <p class="ok-text" role="status"><Icon name="ok" size={16} /> 试运行成功，演示笔记已保存到 {demo.record.output}</p>
                {:else}
                  <p class="error-text" role="alert">试运行没有成功（{demo.record.status}）。</p>
                  {#if demo.record.log}<p class="hint">日志：<span class="mono">{demo.record.log}</span></p>{/if}
                {/if}
                {#each demo.record.warnings ?? [] as warning}<p class="warn-text">{warning}</p>{/each}
              {/if}
              {#if demo?.error}
                <p class="error-text" role="alert">{demo.error}</p>
                {#if demo.log}<pre class="log">{demo.log}</pre>{/if}
              {/if}
            </section>
            {#if checks && failing}
              <label class="check">
                <input type="checkbox" bind:checked={override} />
                仍然继续（之后可在设置里重新检查）
              </label>
            {/if}
          </div>
        {/if}
      {/key}
    </main>
  </div>

  <footer class="bottom">
    {#if error}<p class="error-text grow" role="alert">{error}</p>{/if}
    {#if step > 0}
      <button type="button" class="btn large" onclick={back} disabled={busy}><Icon name="back" />上一步</button>
    {/if}
    <button type="button" class="btn large primary" onclick={next} disabled={busy || !canFinish}>
      {step === steps.length - 1 ? "完成" : "下一步"}<Icon name={step === steps.length - 1 ? "ok" : "next"} />
    </button>
  </footer>
</div>

<style>
  /* Header and navigation stay put; only the step's content scrolls. */
  .wizard {
    position: relative;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  /* The page's single gradient sits behind the step title. */
  .glow {
    position: absolute;
    left: calc(50% - 690px);
    top: -330px;
    width: 900px;
    height: 900px;
    background: radial-gradient(closest-side, rgba(212, 255, 92, 0.09), rgba(212, 255, 92, 0));
    pointer-events: none;
  }
  .top, .scroll, .bottom { position: relative; }
  .top { display: flex; align-items: baseline; gap: 24px; padding: 28px 44px 0; }
  .brand { font-family: var(--display); font-size: 28px; line-height: 1; margin-right: auto; }
  .scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 0 44px; }
  .body {
    max-width: 680px;
    margin: 0 auto;
    padding: 48px 0 40px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  h1 { font-family: var(--display); font-weight: 400; font-size: 52px; line-height: 1.05; letter-spacing: -0.01em; }
  .lead { color: var(--muted); margin-bottom: 16px; }
  .demo { padding-top: 16px; border-top: 1px solid var(--line); }
  .bottom {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 12px;
    padding: 20px 44px 32px;
    border-top: 1px solid var(--line);
  }
  .grow { margin-right: auto; }
  @media (max-width: 900px) {
    .top { padding: 20px 20px 0; }
    .scroll { padding: 0 20px; }
    .bottom { padding: 16px 20px 24px; }
    .glow { left: -330px; }
    h1 { font-size: 40px; }
  }
</style>
