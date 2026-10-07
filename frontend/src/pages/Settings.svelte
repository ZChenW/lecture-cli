<script lang="ts">
  import AsrForm from "../components/AsrForm.svelte";
  import ChecksPanel from "../components/ChecksPanel.svelte";
  import CoursesDirForm from "../components/CoursesDirForm.svelte";
  import Icon from "../components/Icon.svelte";
  import MicForm from "../components/MicForm.svelte";
  import NotesForm from "../components/NotesForm.svelte";
  import RefineForm from "../components/RefineForm.svelte";
  import SettingsSection from "../components/SettingsSection.svelte";
  import TopBar from "../components/TopBar.svelte";
  import { onMount } from "svelte";
  import { TARGET_LABELS, type FixTarget } from "../lib/checks";
  import { app } from "../lib/state.svelte";

  type Form = { save(): Promise<boolean> };
  let courses = $state<Form>();
  let asr = $state<Form>();
  let refine = $state<Form>();
  let notes = $state<Form>();
  let mic = $state<Form>();
  const unfinished = !app.boot?.configured;

  function show(target: FixTarget) {
    const heading = document.getElementById(`s-${target}`);
    heading?.scrollIntoView({ block: "start" });
    heading?.focus({ preventScroll: true });
  }
  onMount(() => {
    if (app.section) show(app.section as FixTarget);
    app.section = null;
  });
</script>

<TopBar>
  {#if app.recording}
    <a class="btn" href="#/record"><Icon name="back" />返回录制</a>
  {:else}
    <a class="btn" href="#/"><Icon name="back" />返回首页</a>
  {/if}
</TopBar>

<main class="settings">
  <h1>设置</h1>
  {#if unfinished}
    <p class="warn-text">设置尚未完成。也可以回到 <a href="#/onboarding">首次启动向导</a> 逐步完成。</p>
  {/if}
  <SettingsSection id="s-courses" title="课程目录" onsave={() => courses!.save()}>
    <CoursesDirForm bind:this={courses} checkButton={false} />
  </SettingsSection>
  <SettingsSection id="s-asr" title="转录" onsave={() => asr!.save()}>
    <AsrForm bind:this={asr} settings />
  </SettingsSection>
  <SettingsSection id="s-refine" title="课后校正" onsave={() => refine!.save()}>
    <RefineForm bind:this={refine} />
  </SettingsSection>
  <SettingsSection id="s-notes" title="笔记服务" onsave={() => notes!.save()}>
    <NotesForm bind:this={notes} settings />
  </SettingsSection>
  <SettingsSection id="s-mic" title="麦克风" onsave={() => mic!.save()}>
    <MicForm bind:this={mic} />
  </SettingsSection>
  <SettingsSection id="s-checks" title="环境检查">
    <p class="hint">检查本地依赖、模型、麦克风和两个服务的连接；会向服务发送一次很小的测试请求。</p>
    <ChecksPanel auto={false} onfix={show} fixText={(t) => `前往「${TARGET_LABELS[t]}」设置`} />
  </SettingsSection>
</main>

<style>
  .settings { max-width: 44em; margin: 0 auto; padding: 48px 44px 96px; }
  h1 { font-family: var(--display); font-weight: 600; font-size: 48px; line-height: 1.1; color: var(--fg); }
  @media (max-width: 900px) { .settings { padding: 32px 20px 64px; } }
</style>
