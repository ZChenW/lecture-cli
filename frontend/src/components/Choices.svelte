<script lang="ts">
  // Radio cards: the transcription methods and the service presets share this look.
  interface Option { value: string; title: string; text?: string }
  let { name, label, options, value = $bindable(), compact = false, onchange }: {
    name: string; label: string; options: Option[]; value: string; compact?: boolean; onchange?: (value: string) => void;
  } = $props();

  function choose(next: string) {
    value = next;
    onchange?.(next);
  }
</script>

<div class="choices" class:compact role="radiogroup" aria-label={label}>
  {#each options as option (option.value)}
    <label class="choice" class:selected={value === option.value}>
      <input type="radio" class="visually-hidden" {name} value={option.value} checked={value === option.value}
        onchange={() => choose(option.value)} />
      <span class="head"><span class="dot" aria-hidden="true"></span><strong>{option.title}</strong></span>
      {#if option.text}<span class="hint">{option.text}</span>{/if}
    </label>
  {/each}
</div>

<style>
  .choices { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
  .compact { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
  .choice {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-height: 44px;
    padding: 18px 20px;
    border: 1px solid var(--card-line);
    border-radius: 16px;
    background: var(--card);
    cursor: pointer;
  }
  .compact .choice { justify-content: center; padding: 10px 16px; border-radius: 12px; }
  .choice:hover { border-color: var(--control-line); }
  .choice.selected { border-color: var(--select); box-shadow: inset 0 0 0 1px var(--select); }
  .choice:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
  .head { display: flex; align-items: center; gap: 10px; }
  .dot {
    flex: none;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 1.5px solid var(--control-line);
  }
  .selected .dot { border-color: var(--select); background: var(--select); box-shadow: inset 0 0 0 2px var(--bg); }
  strong { font-weight: 500; color: var(--fg); }
</style>
