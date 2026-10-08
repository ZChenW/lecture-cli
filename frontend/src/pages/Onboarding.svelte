<script lang="ts">
  // design/E-wizard.reference.html: the step number and title on the left, the step's choices
  // on the right, skip links on both ends. Every step can be skipped (plan N2.4).
  import { onDestroy } from "svelte";
  import { api, subscribe, type Subscription } from "../lib/api";
  import type { FixTarget } from "../lib/checks";
  import { phaseLabel } from "../lib/format";
  import { navigate } from "../lib/router";
  import { app, message, refresh } from "../lib/state.svelte";
  import type { Check, Course, RunRecord, Snapshot } from "../lib/types";
  import AsrForm from "../components/AsrForm.svelte";
  import ChecksPanel from "../components/ChecksPanel.svelte";
  import CoursesDirForm from "../components/CoursesDirForm.svelte";
  import MicForm from "../components/MicForm.svelte";
  import NotesForm from "../components/NotesForm.svelte";
  import Select from "../components/Select.svelte";

  const steps = [
    { label: "课程目录", title: ["笔记存放", "在哪里"] },
    { label: "转录方式", title: ["怎样把声音", "变成文字"] },
    { label: "笔记服务", title: ["由哪个模型", "写笔记"] },
    { label: "麦克风", title: ["用哪个", "麦克风"] },
    { label: "检查", title: ["最后", "检查一遍"] },
  ];
  const pad = (n: number) => String(n).padStart(2, "0");
  const boot = app.boot!;

  let step = $state(0);
  let ready = $state(false);
  let busy = $state(false);
  let form = $state<{ save(): Promise<boolean> }>();
  // What each step ended as in this visit; a step already set up before shows as set.
  let outcome = $state<("set" | "skipped" | null)[]>([
    boot.missing.includes("courses_dir") ? null : "set", null,
    boot.missing.includes("notes_key") ? null : "set", null, null,
  ]);
  // Preselect from the GPU probe only while the transcription choice is still the default one.
  const freshAsr = boot.config.asr_backend === "local" && boot.config.asr_model === "base.en";
  let gpu = $state<boolean | null>(null);
  api.checks().then((list) => {
    const probe = list.find((c) => c.id === "asr_device");
    gpu = probe ? probe.level === "ok" && probe.detail.startsWith("NVIDIA GPU") : false;
  }).catch(() => (gpu = false));

  let checks = $state<Check[] | null>(null);
  let panel = $state<ChecksPanel>();
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

  function go(target: number) {
    step = target;
    ready = step === 3;
    error = "";
    document.querySelector<HTMLElement>(".wizard .scroll")?.scrollTo(0, 0);
  }

  /** Leaves the wizard for home, remembering that it is done so it does not open again. */
  async function enter() {
    busy = true;
    try {
      await api.saveConfig({ onboarded: true });
      await refresh();
      navigate("home");
    } catch (e) {
      error = message(e);
    } finally {
      busy = false;
    }
  }

  async function next() {
    if (step === steps.length - 1) return enter();
    busy = true;
    try {
      error = "";
      if (form && !(await form.save())) return;
      await refresh();
      outcome[step] = "set";
      go(step + 1);
    } catch (e) {
      error = message(e);
    } finally {
      busy = false;
    }
  }

  function skip() {
    outcome[step] = outcome[step] === "set" ? "set" : "skipped";
    if (step === steps.length - 1) enter();
    else go(step + 1);
  }

  // The wizard has no refine step; that one opens the settings section instead.
  const STEPS: Record<Exclude<FixTarget, "refine">, number> = { courses: 0, asr: 1, notes: 2, mic: 3 };
  function fix(target: FixTarget) {
    if (target === "refine") {
      app.section = "refine";
      navigate("settings");
    } else {
      go(STEPS[target]);
    }
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
  let canNext = $derived(step < 4 ? ready : !demoRunning && !failing);
</script>

<div class="wizard">
  <header class="top">
    <span class="brand">lecture</span>
    <button type="button" class="quiet" onclick={enter} disabled={busy}>全部跳过，直接进入</button>
  </header>

  <div class="scroll">
    <main class="body">
      <section class="intro" aria-label="第 {step + 1} 步，共 {steps.length} 步">
        <div class="number" aria-hidden="true">{pad(step + 1)}</div>
        <h1>{steps[step].title[0]}<br />{steps[step].title[1]}</h1>
        <ol>
          {#each steps as item, i (item.label)}
            <li class:current={i === step} aria-current={i === step ? "step" : undefined}>
              <span class="n">{pad(i + 1)}</span>{item.label}
              {#if i !== step && outcome[i]}<span class="state">{outcome[i] === "set" ? "已设置" : "稍后设置"}</span>{/if}
            </li>
          {/each}
        </ol>
      </section>

      <section class="step" aria-label={steps[step].label}>
        {#key step}
          {#if step === 0}
            <CoursesDirForm bind:ready bind:this={form} />
          {:else if step === 1}
            <AsrForm look="rows" bind:ready bind:this={form} {gpu} preselect={freshAsr} />
            {#if freshAsr && gpu === null}<p class="hint" role="status">正在检测 GPU…</p>{/if}
          {:else if step === 2}
            <NotesForm look="rows" bind:ready bind:this={form} />
          {:else if step === 3}
            <MicForm look="rows" bind:this={form} />
          {:else}
            <div class="stack">
              <ChecksPanel bind:results={checks} bind:this={panel} onfix={fix}
                fixText={(t) => t === "refine" ? "前往设置：课后校正" : `回到「${steps[STEPS[t]].label}」这一步`} />
              <section class="stack demo" aria-label="试运行">
                {#if courses.length}
                  <div class="demo-row">
                    {#if courses.length > 1}
                      <div class="field">
                        <span id="demo-course">写入课程</span>
                        <Select labelledby="demo-course" bind:value={demoCourse} disabled={demoRunning}
                          options={courses.map((c) => ({ value: c.name, label: c.name }))} />
                      </div>
                    {/if}
                    <!-- PLAN-GUI-5 R1: the explanation is the button's tooltip. -->
                    <button type="button" class="btn" onclick={tryRun} disabled={demoRunning}
                      title="用一段自造的文字走一遍完整流程，不录音；会消耗少量笔记服务 API 额度">试运行一次</button>
                  </div>
                {:else}
                  <p class="warn-text">试运行需要至少一门课程，请回到第一步新建。</p>
                {/if}
                {#if demo?.snapshot && !demo.record}
                  <p class="hint" role="status">{phaseLabel(demo.snapshot.phase)} · {demo.snapshot.notes.status}</p>
                {/if}
                {#if demo?.record}
                  {#if demo.record.status === "done"}
                    <p class="ok-text" role="status">试运行成功，演示笔记已保存到 {demo.record.output}</p>
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
                <p class="warn-text" title="可以先跳过这一步进入，之后在设置的「环境检查」里重新检查">还有未通过的检查</p>
              {/if}
            </div>
          {/if}
        {/key}
      </section>
    </main>
  </div>

  <footer class="bottom">
    <button type="button" class="quiet" onclick={skip} disabled={busy}>这一步稍后再设置</button>
    {#if error}<p class="error-text grow" role="alert">{error}</p>{/if}
    <div class="buttons">
      {#if step > 0}<button type="button" class="pill" onclick={() => go(step - 1)} disabled={busy}>上一步</button>{/if}
      <button type="button" class="pill primary" onclick={next} disabled={busy || !canNext}>
        {step === steps.length - 1 ? "完成" : "下一步"}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d={step === steps.length - 1 ? "M5 12.5l4.5 4.5L19 7.5" : "M5 12h14M13 6l6 6-6 6"} /></svg>
      </button>
    </div>
  </footer>
</div>

<style>
  /* Values from design/E-wizard.reference.html. The header and footer stay put and the middle
     scrolls, so "下一步" is always in reach. */
  .wizard {
    --mono: "Geist Mono", monospace;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    padding: 28px 44px 0;
    background: #0B0C0E;
    color: #ECEAE4;
    font-family: "Geist", "Noto Sans CJK SC", system-ui, sans-serif;
    font-size: 16px;
    line-height: normal;
  }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .brand { font-family: "Instrument Serif", serif; font-size: 26px; }
  .quiet {
    font-size: 14px;
    color: #A9ABB0;
    text-decoration: underline;
    text-underline-offset: 4px;
    min-height: 44px;
    display: flex;
    align-items: center;
    border: none;
    background: none;
    padding: 0;
    cursor: pointer;
  }
  .quiet:hover { color: #FFFFFF; }
  /* position: relative makes the middle the containing block of absolutely positioned descendants
     (the checks' .visually-hidden labels), so they scroll and clip with it instead of making the
     whole page scroll in a short window (PLAN-GUI-3 section 8: step 5 at 1366x700). */
  .scroll { position: relative; flex: 1; min-height: 0; overflow-y: auto; margin: 0 -44px; padding: 0 44px; }
  .body { display: flex; flex-wrap: wrap; gap: 40px 96px; align-items: flex-start; padding: 72px 0 40px; }
  .intro { flex: 1 1 320px; max-width: 440px; display: flex; flex-direction: column; gap: 36px; }
  .number { font-family: "Instrument Serif", serif; font-size: 168px; line-height: 0.8; letter-spacing: -0.03em; color: #ECEAE4; }
  h1 { margin: 0; font-family: "Noto Serif CJK SC", serif; font-weight: 400; font-size: 40px; line-height: 1.25; }
  ol { margin: 12px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; font-size: 14px; }
  li { display: flex; align-items: center; gap: 16px; min-height: 36px; color: #8E9096; }
  li.current { color: #ECEAE4; }
  .n { font-family: "Geist Mono", monospace; font-size: 12px; width: 20px; }
  .current .n { color: var(--accent); }
  .state { font-family: "Geist Mono", monospace; font-size: 12px; margin-left: auto; }
  .step { flex: 999 1 480px; min-width: 0; max-width: 720px; display: flex; flex-direction: column; gap: 16px; }
  .demo { padding-top: 16px; border-top: 1px solid var(--line); }
  .demo-row { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 24px; }
  .demo-row .field { flex: 1 1 240px; }
  .bottom {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    border-top: 1px solid #1D1F23;
    padding: 20px 0 28px;
  }
  .grow { flex: 1; }
  .buttons { display: flex; align-items: center; gap: 12px; }
  .pill {
    height: 52px;
    padding: 0 24px;
    border-radius: 999px;
    border: 1px solid #34363B;
    background: transparent;
    color: #ECEAE4;
    font-size: 15px;
    cursor: pointer;
  }
  .pill.primary {
    padding: 0 28px;
    border: none;
    background: var(--accent);
    color: #0B0C0E;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .pill:disabled { opacity: 0.45; cursor: not-allowed; }
  @media (max-width: 900px) {
    .wizard { padding: 20px 20px 0; }
    .scroll { margin: 0 -20px; padding: 0 20px; }
    .body { padding: 40px 0 32px; }
    .number { font-size: 120px; }
    h1 { font-size: 32px; }
  }
</style>
