<script lang="ts" module>
  let next = 0;
</script>

<script lang="ts">
  // N2.1: the app's only dropdown. No native <select> popup anywhere (plan section 1, rule 2).
  // Trigger: an underlined button; list: role=listbox, focus moves into it while open.
  import { tick } from "svelte";
  import { matchIndex, moveIndex, opensUpward, type ListOption } from "../lib/listbox";

  let { value = $bindable(), options, label, labelledby, mono = false, disabled = false, onchange }: {
    value: string; options: ListOption[]; label?: string; labelledby?: string; mono?: boolean; disabled?: boolean;
    onchange?: (value: string) => void;
  } = $props();

  const id = `dd-${++next}`;
  let open = $state(false);
  let up = $state(false);
  let active = $state(-1);
  let root = $state<HTMLElement>();
  let trigger = $state<HTMLButtonElement>();
  let list = $state<HTMLElement>();
  let typed = "";
  let typedAt = 0;

  let current = $derived(options.find((option) => option.value === value));
  let names = $derived([labelledby, `${id}-value`].filter(Boolean).join(" "));

  async function show(at: number | null = null) {
    if (disabled || open) return;
    active = at ?? Math.max(0, options.findIndex((option) => option.value === value));
    if (options[active]?.disabled) active = moveIndex(options, active, "ArrowDown") ?? -1;
    open = true;
    await tick();
    if (!list || !trigger) return;
    up = opensUpward(trigger.getBoundingClientRect(), list.offsetHeight, window.innerHeight);
    list.focus({ preventScroll: true });
    reveal();
  }

  function hide(refocus = true) {
    open = false;
    typed = "";
    if (refocus) trigger?.focus({ preventScroll: true });
  }

  function choose(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    const changed = option.value !== value;
    value = option.value;
    hide();
    if (changed) {
      onchange?.(option.value);
      // Lets a settings section notice an unsaved change, as it does for text fields.
      root?.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function reveal() {
    list?.querySelector<HTMLElement>(`#${id}-${active}`)?.scrollIntoView({ block: "nearest" });
  }

  function typeahead(key: string): boolean {
    if (key.length !== 1 || key === " " && !typed) return false;
    const now = Date.now();
    typed = now - typedAt > 600 ? key : typed + key;
    typedAt = now;
    const found = matchIndex(options, active, typed);
    if (found != null) {
      active = found;
      reveal();
    }
    return true;
  }

  function onTriggerKey(event: KeyboardEvent) {
    if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      show(event.key === "ArrowUp" ? moveIndex(options, -1, "End") : null);
    } else if (!event.ctrlKey && !event.metaKey && !event.altKey && event.key.length === 1) {
      // Typing on the closed trigger opens the list at the first match.
      event.preventDefault();
      show().then(() => typeahead(event.key));
    }
  }

  function onListKey(event: KeyboardEvent) {
    const moved = moveIndex(options, active, event.key);
    if (moved != null) {
      active = moved;
      reveal();
    } else if (event.key === "Enter" || event.key === " " && !typed) {
      choose(active);
    } else if (event.key === "Escape") {
      hide();
    } else if (event.key === "Tab") {
      hide();  // Focus is back on the trigger, so Tab continues from there.
      return;
    } else if (event.ctrlKey || event.metaKey || event.altKey || !typeahead(event.key)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
  }

  function outside(event: PointerEvent) {
    if (open && root && !root.contains(event.target as Node)) hide(false);
  }
</script>

<svelte:document onpointerdown={outside} />

<div class="dropdown" class:open class:up bind:this={root}>
  <button type="button" class="trigger" class:mono bind:this={trigger} {disabled}
    aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? `${id}-list` : undefined}
    aria-label={labelledby ? undefined : label} aria-labelledby={labelledby ? names : undefined}
    onclick={() => (open ? hide() : show())} onkeydown={onTriggerKey}>
    <span class="value" id="{id}-value">{current?.label ?? value}</span>
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d={open ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6"} /></svg>
  </button>
  {#if open}
    <ul class="list" role="listbox" id="{id}-list" tabindex="-1" bind:this={list}
      aria-label={labelledby ? undefined : label} aria-labelledby={labelledby}
      aria-activedescendant={active >= 0 ? `${id}-${active}` : undefined} onkeydown={onListKey}>
      {#each options as option, i (option.value)}
        <!-- Keys are handled by the listbox (aria-activedescendant); options only take the mouse. -->
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <li role="option" id="{id}-{i}" aria-selected={option.value === value} aria-disabled={option.disabled || undefined}
          class:active={i === active} class:selected={option.value === value}
          onpointermove={() => !option.disabled && (active = i)} onclick={() => choose(i)}>
          <span class="text">{option.label}{#if option.code}{" "}<span class="code">{option.code}</span>{/if}</span>
          {#if option.value === value}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  /* Values from design/D-settings.reference.html ("默认课堂语言", drawn open). */
  .dropdown { position: relative; min-width: 0; }
  .trigger {
    width: 100%;
    height: 44px;
    box-sizing: border-box;
    padding: 0 2px;
    border: none;
    border-bottom: 1px solid var(--field-line);
    border-radius: 0;
    background: transparent;
    color: var(--fg);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 15px;
    text-align: left;
    cursor: pointer;
  }
  .trigger.mono { font-family: var(--mono); }
  .value { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .trigger svg { flex: none; }
  /* Rule 3: the underline thickens to 2px instead of a ring, as when the list is open. */
  .open .trigger, .trigger:focus-visible { border-bottom-width: 2px; outline: none; }
  .trigger:disabled { opacity: 0.55; cursor: not-allowed; }
  .list {
    position: absolute;
    left: 0;
    right: 0;
    top: calc(100% + 6px);
    z-index: 20;
    margin: 0;
    padding: 6px 0;
    list-style: none;
    max-height: 320px;
    overflow-y: auto;
    box-sizing: border-box;
    background: var(--dd-bg);
    border: 1px solid var(--dd-border);
    box-shadow: var(--dd-shadow);
    display: flex;
    flex-direction: column;
    outline: none;
  }
  .up .list { top: auto; bottom: calc(100% + 6px); }
  li {
    flex: none;
    min-height: 44px;
    padding: 0 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 15px;
    color: var(--fg);
    cursor: pointer;
  }
  li.selected { font-weight: 600; }
  li.active { background: var(--dd-hover); }
  li[aria-disabled="true"] { color: var(--faint); cursor: not-allowed; }
  .code { font-family: var(--mono); font-size: 12px; font-weight: 400; color: var(--muted); }
  li svg { flex: none; }
</style>
