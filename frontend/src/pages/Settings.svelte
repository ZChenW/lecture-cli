<script lang="ts">
  // design/D-settings.reference.html: a heading bar, a section list on the left, and one row per
  // section, each saved on its own.
  import AsrForm from "../components/AsrForm.svelte";
  import ChecksPanel from "../components/ChecksPanel.svelte";
  import CoursesDirForm from "../components/CoursesDirForm.svelte";
  import MicForm from "../components/MicForm.svelte";
  import NotesForm from "../components/NotesForm.svelte";
  import OpenersForm from "../components/OpenersForm.svelte";
  import RefineForm from "../components/RefineForm.svelte";
  import SettingsSection from "../components/SettingsSection.svelte";
  import { onMount } from "svelte";
  import { SECTIONS, type Section } from "../lib/checks";
  import { app } from "../lib/state.svelte";

  type Form = { save(): Promise<boolean> };
  let courses = $state<Form>();
  let asr = $state<Form>();
  let refine = $state<Form>();
  let notes = $state<Form>();
  let mic = $state<Form>();
  let open = $state<Form>();
  let current = $state<Section>("courses");

  function show(target: Section) {
    current = target;
    const section = document.getElementById(target);
    section?.scrollIntoView({ block: "start" });
    document.getElementById(`${target}-title`)?.focus({ preventScroll: true });
  }

  onMount(() => {
    if (app.section) show(app.section as Section);
    app.section = null;
    // The list marks the section nearest the top of the window.
    const spy = () => {
      const passed = SECTIONS.filter(({ id }) => (document.getElementById(id)?.getBoundingClientRect().top ?? 1e9) <= 120);
      const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
      current = atEnd ? SECTIONS.at(-1)!.id : passed.at(-1)?.id ?? SECTIONS[0].id;
    };
    window.addEventListener("scroll", spy, { passive: true });
    return () => window.removeEventListener("scroll", spy);
  });
</script>

<div class="page">
  <header>
    <div class="title">
      <a class="back" href={app.recording ? "#/record" : "#/"} aria-label={app.recording ? "返回录制" : "返回首页"}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
          stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5M11 6l-6 6 6 6" /></svg>
      </a>
      <h1>设置</h1>
    </div>
  </header>

  <div class="columns">
    <nav aria-label="设置分节">
      {#each SECTIONS as section (section.id)}
        <a href="#/settings" aria-current={current === section.id ? "location" : undefined}
          onclick={(event) => { event.preventDefault(); show(section.id); }}>
          {#if current === section.id}<span class="dot" aria-hidden="true"></span>{/if}{section.label}
        </a>
      {/each}
    </nav>

    <main>
      {#if app.boot && !app.boot.configured}
        <p class="unfinished">还差几项设置，补齐后才能开始上课。也可以回到 <a href="#/onboarding">首次设置</a> 逐步完成。</p>
      {/if}
      <SettingsSection id="courses" title="课程目录" gap={18}
        onsave={() => courses!.save()}>
        <CoursesDirForm bind:this={courses} />
      </SettingsSection>
      <SettingsSection id="asr" title="转录" onsave={() => asr!.save()}>
        <AsrForm bind:this={asr} settings />
      </SettingsSection>
      <SettingsSection id="refine" title="课后校正"
        onsave={() => refine!.save()}>
        <RefineForm bind:this={refine} />
      </SettingsSection>
      <SettingsSection id="notes" title="笔记服务" onsave={() => notes!.save()}>
        <NotesForm bind:this={notes} settings />
      </SettingsSection>
      <SettingsSection id="mic" title="麦克风" onsave={() => mic!.save()}>
        <MicForm bind:this={mic} />
      </SettingsSection>
      <SettingsSection id="open" title="打开方式" onsave={() => open!.save()}>
        <OpenersForm bind:this={open} />
      </SettingsSection>
      <SettingsSection id="checks" title="环境检查">
        <ChecksPanel auto={false} onfix={show} fixText={(t) => `前往「${SECTIONS.find((s) => s.id === t)?.label}」`} />
      </SettingsSection>
    </main>
  </div>
</div>

<style>
  /* Root of design/D-settings.reference.html: 16px, normal line height. Its Google Fonts names
     "Noto Sans SC" / "Noto Serif SC" are the installed Noto CJK fonts here. */
  .page {
    --mono: "DM Mono", monospace;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: #FBFBFA;
    color: #111111;
    font-family: "Geist", "Noto Sans CJK SC", system-ui, sans-serif;
    font-size: 16px;
    line-height: normal;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 18px 48px;
    border-bottom: 1px solid #111111;
  }
  .title { display: flex; align-items: center; gap: 20px; }
  .back { display: flex; align-items: center; justify-content: center; width: 44px; height: 44px; margin-left: -12px; color: #111111; }
  h1 { font-family: "Noto Serif CJK SC", serif; font-weight: 600; font-size: 20px; }
  .columns { flex: 1; display: flex; flex-wrap: wrap; }
  nav {
    flex: 1 1 200px;
    max-width: 248px;
    padding: 44px 0 40px 48px;
    display: flex;
    flex-direction: column;
    font-size: 14px;
    align-self: flex-start;
    position: sticky;
    top: 0;
  }
  nav a { text-decoration: none; min-height: 40px; display: flex; align-items: center; gap: 10px; color: #5C5C5A; }
  nav a[aria-current] { font-weight: 600; color: #111111; }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: #111111; }
  main { flex: 999 1 560px; min-width: 0; padding: 0 48px 64px 32px; max-width: 1040px; display: flex; flex-direction: column; }
  .unfinished { margin-top: 28px; font-size: 13px; color: var(--warn); }
  /* Narrow windows: the list becomes a wrapping row above the sections. */
  @media (max-width: 900px) {
    header { padding: 12px 20px; }
    nav { position: static; max-width: none; flex-basis: 100%; flex-direction: row; flex-wrap: wrap; column-gap: 20px; padding: 16px 20px 0; }
    nav a { min-height: 44px; }
    main { padding: 0 20px 48px; }
  }
</style>
