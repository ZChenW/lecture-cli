<script lang="ts">
  // Plan GUI-3 item 3: the start dialog's underlined segments (design/F-dialog.reference.html).
  // A radiogroup with roving focus: arrow keys, Home and End move and select, skipping options that
  // cannot be chosen; those stay visible, greyed, with their reason as small text and a tooltip.
  interface Segment { value: string; title: string; aside?: string; disabled?: boolean; hint?: string }
  let { labelledby, options, value = $bindable(), onchange }: {
    labelledby: string; options: Segment[]; value: string; onchange?: (value: string) => void;
  } = $props();
  let group = $state<HTMLElement>();
  let enabled = $derived(options.filter((option) => !option.disabled));
  let current = $derived(enabled.find((option) => option.value === value));

  function choose(next: Segment) {
    if (next.disabled || next.value === value) return;
    value = next.value;
    onchange?.(next.value);
  }

  function onkey(event: KeyboardEvent) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    const index = enabled.findIndex((option) => option.value === value);
    let target: Segment | undefined;
    if (step) target = enabled[(index + step + enabled.length) % enabled.length];
    else if (event.key === "Home") target = enabled[0];
    else if (event.key === "End") target = enabled.at(-1);
    if (!target) return;
    event.preventDefault();
    choose(target);
    group?.querySelector<HTMLElement>(`[data-value="${CSS.escape(target.value)}"]`)?.focus();
  }
</script>

<!-- Not focusable itself: the one Tab stop is the selected option (roving tabindex). -->
<div class="segments" role="radiogroup" aria-labelledby={labelledby} bind:this={group}>
  {#each options as option (option.value)}
    {@const selected = option.value === value && !option.disabled}
    <button type="button" role="radio" aria-checked={selected} aria-disabled={option.disabled ? "true" : undefined}
      data-value={option.value} title={option.hint} class:selected class:disabled={option.disabled}
      tabindex={selected || (!current && option === enabled[0]) ? 0 : -1} onclick={() => choose(option)} onkeydown={onkey}>
      {option.title}
      {#if option.aside}<span class="aside">{option.aside}</span>{/if}
      {#if option.disabled && option.hint}<span class="visually-hidden">：{option.hint}</span>{/if}
    </button>
  {/each}
</div>

<style>
  .segments { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; min-width: 0; }
  button {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 44px;
    padding: 0 14px;
    border: none;
    border-bottom: 2px solid transparent;
    background: transparent;
    color: #5C5C5A;
    font: inherit;
    font-size: 15px;
    cursor: pointer;
    white-space: nowrap;
  }
  button.selected { border-bottom-color: #111111; color: #111111; font-weight: 600; }
  button:hover:not(.selected):not(.disabled) { color: #111111; }
  button:focus-visible { outline-offset: -2px; }
  .aside { font-size: 12px; font-weight: 400; color: #5C5C5A; }
  /* Not choosable here: greyed, with the reason beside it (#767674 keeps 4.5:1 on the paper). */
  button.disabled { color: #A3A3A0; cursor: not-allowed; }
  button.disabled .aside { color: #767674; }
</style>
