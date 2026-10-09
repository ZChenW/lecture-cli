<script lang="ts">
  // N2.2 "打开方式": which program shows a note's folder, opens a terminal there, or edits the
  // Markdown. Only names from the backend's fixed tables can be chosen; a custom command lives in
  // config.json and is shown, never edited, here.
  import { api } from "../lib/api";
  import type { ListOption } from "../lib/listbox";
  import { message } from "../lib/state.svelte";
  import type { OpenerKind, Openers } from "../lib/types";
  import Select from "./Select.svelte";

  const KINDS: OpenerKind[] = ["file_manager", "terminal", "editor"];
  const NOTES: Record<OpenerKind, string> = {
    file_manager: "「在文件管理器中显示」用它打开笔记所在文件夹",
    terminal: "「在终端中打开」在笔记所在文件夹启动它",
    editor: "「用编辑器打开」用它打开笔记的 Markdown 文件",
  };
  let info = $state<Openers | null>(null);
  let choice = $state<Record<OpenerKind, string>>({ file_manager: "", terminal: "", editor: "" });
  let tries = $state<Partial<Record<OpenerKind, { ok: boolean; text: string }>>>({});
  let busy = $state<OpenerKind | null>(null);
  let error = $state("");

  async function load() {
    try {
      info = await api.openers();
      for (const kind of KINDS) choice[kind] = info[kind].selected ?? "";
    } catch (e) {
      error = message(e);
    }
  }
  load();

  function options(kind: OpenerKind): ListOption[] {
    const item = info![kind];
    if (item.custom) return [{ value: choice[kind], label: "config.json 中的自定义命令" }];
    const auto = item.available[0];
    return [
      { value: "", label: auto ? `自动（当前：${auto}）` : "自动（未找到可用程序）" },
      ...item.programs.filter((p) => item.available.includes(p) || p === choice[kind])
        .map((p) => ({ value: p, label: item.available.includes(p) ? p : `${p}（未安装）` })),
    ];
  }

  async function test(kind: OpenerKind) {
    busy = kind;
    try {
      const result = await api.tryOpener(kind, choice[kind] || null);
      tries[kind] = { ok: true, text: `已启动 ${result.program ?? result.argv0}，请看看是否打开了窗口` };
    } catch (e) {
      tries[kind] = { ok: false, text: message(e) };
    } finally {
      busy = null;
    }
  }

  export async function save(): Promise<boolean> {
    if (!info) return true;
    try {
      const changes: Record<string, string | null> = {};
      for (const kind of KINDS) if (!info[kind].custom) changes[kind] = choice[kind] || null;
      await api.saveConfig(changes);
      error = "";
      await load();
      return true;
    } catch (e) {
      error = message(e);
      return false;
    }
  }
</script>

<div class="form">
  {#if info}
    {#each KINDS as kind (kind)}
      <div class="field">
        <span id="opener-{kind}" title={info[kind].custom ? "在 config.json 中设置；要改用列表中的程序，请删除该项" : NOTES[kind]}>{info[kind].label}</span>
        <div class="line">
          <Select labelledby="opener-{kind}" mono={info[kind].custom ? false : choice[kind] !== ""} options={options(kind)}
            bind:value={choice[kind]} disabled={info[kind].custom} onchange={() => delete tries[kind]} />
          <button type="button" class="text-action" onclick={() => test(kind)} disabled={busy === kind}
            title="用当前选中的程序打开课程目录（编辑器打开一个示例文件），不会保存选择">
            {busy === kind ? "正在启动…" : "试一下"}
          </button>
        </div>
        {#if tries[kind]}
          <span class="field-note" class:error-text={!tries[kind]!.ok} role="status">{tries[kind]!.text}</span>
        {/if}
      </div>
    {/each}
  {:else if !error}
    <p class="field-note" role="status">正在查找已安装的程序…</p>
  {/if}
  {#if error}<p class="error-text" role="alert">{error}</p>{/if}
</div>

<style>
  .form { display: flex; flex-direction: column; gap: 28px; }
  .line { display: flex; align-items: flex-end; gap: 12px; }
  .line > :global(.dropdown) { flex: 1; }
  .error-text.field-note { color: var(--error); }
</style>
