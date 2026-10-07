<script lang="ts">
  import { api } from "../lib/api";
  import { app, message } from "../lib/state.svelte";
  import type { Devices } from "../lib/types";

  const config = app.boot!.config;
  let devices = $state<Devices | null>(null);
  let unavailable = $state("");
  let error = $state("");
  // Names survive reboots better than PortAudio indices; an index from lecture setup is kept as is.
  let choice = $state(config.device === null ? "" : String(config.device));
  let autoGain = $state(config.auto_gain);

  api.devices().then((result) => {
    devices = result;
    const byIndex = result.devices.find((d) => String(d.index) === choice);
    if (byIndex) choice = byIndex.name;
  }).catch((e) => (unavailable = message(e)));

  export async function save(): Promise<boolean> {
    try {
      await api.saveConfig({ device: choice === "" ? null : choice, auto_gain: autoGain });
      error = "";
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }
</script>

<div class="stack">
  <label class="field">
    <span>麦克风</span>
    <select class="input" bind:value={choice}>
      <option value="">系统默认</option>
      {#each devices?.devices ?? [] as device}
        <option value={device.name}>{device.name}{device.default ? "（当前默认）" : ""}</option>
      {/each}
      {#if choice && devices && !devices.devices.some((d) => d.name === choice)}<option value={choice}>{choice}</option>{/if}
    </select>
  </label>
  {#if devices}
    <p class="hint">{devices.notes.default} {devices.notes.pipewire}</p>
  {:else if unavailable}
    <p class="warn-text">{unavailable}；仍可使用系统默认麦克风。</p>
  {/if}
  <label class="check">
    <input type="checkbox" bind:checked={autoGain} />
    自动增益
  </label>
  <p class="hint">检测到削波时自动调低 PipeWire 默认麦克风音量，避免爆音；只对系统默认源生效。</p>
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>
