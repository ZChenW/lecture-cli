<script lang="ts">
  import { api } from "../lib/api";
  import type { ListOption } from "../lib/listbox";
  import { app, message } from "../lib/state.svelte";
  import type { Devices } from "../lib/types";
  import Choices from "./Choices.svelte";
  import MicLevel from "./MicLevel.svelte";
  import Select from "./Select.svelte";

  let { look = "tabs" }: { look?: "tabs" | "rows" } = $props();
  const config = app.boot!.config;
  let devices = $state<Devices | null>(null);
  let unavailable = $state("");
  let error = $state("");
  // Names survive reboots better than PortAudio indices; an index from lecture setup is kept as is.
  let choice = $state(config.device === null ? "" : String(config.device));
  // The level bar listens to the saved device (plan GUI-4 Q1.5); a new choice applies once saved.
  let saved = $state(config.device === null ? "" : String(config.device));
  let meter = $state<ReturnType<typeof MicLevel>>();
  let autoGain = $state(config.auto_gain ? "on" : "off");
  let options = $derived<ListOption[]>([
    { value: "", label: "系统默认" },
    ...(devices?.devices ?? []).map((d) => ({ value: d.name, label: d.default ? `${d.name}（当前默认）` : d.name })),
    ...(choice && devices && !devices.devices.some((d) => d.name === choice) ? [{ value: choice, label: choice }] : []),
  ]);

  api.devices().then((result) => {
    devices = result;
    const byIndex = result.devices.find((d) => String(d.index) === choice);
    if (byIndex) choice = saved = byIndex.name;
  }).catch((e) => (unavailable = message(e)));

  export async function save(): Promise<boolean> {
    try {
      await api.saveConfig({ device: choice === "" ? null : choice, auto_gain: autoGain === "on" });
      error = "";
      if (saved !== choice) {
        saved = choice;
        meter?.restart();
      }
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }
</script>

<div class="form">
  <div class="field">
    <!-- PLAN-GUI-5 R1: the backend's notes on the default input are a tooltip, not a line. -->
    <span id="mic-device-{look}" title={devices ? `${devices.notes.default} ${devices.notes.pipewire}` : undefined}>输入设备</span>
    <Select labelledby="mic-device-{look}" {options} bind:value={choice} />
    {#if unavailable}
      <span class="field-note warn-text">{unavailable}；仍可使用系统默认麦克风。</span>
    {/if}
  </div>
  <div class="field">
    <span id="mic-level-{look}">电平</span>
    <div aria-labelledby="mic-level-{look}" role="group"
      title={saved !== choice ? "电平条显示的是已保存的输入设备，保存后改为新选的设备" : undefined}>
      <MicLevel bind:this={meter} />
    </div>
  </div>
  <div class="field">
    <span class="label">自动调节音量</span>
    <Choices label="自动调节音量" bind:value={autoGain} options={[
      { value: "on", title: "开", tip: "只对系统默认输入生效" },
      { value: "off", title: "关", tip: "不改动系统音量" },
    ]} />
  </div>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .form { display: flex; flex-direction: column; gap: 28px; }
  .warn-text.field-note { color: var(--warn); }
</style>
