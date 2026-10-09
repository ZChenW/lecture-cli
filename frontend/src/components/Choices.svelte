<script lang="ts">
  // Plan section 1, rule 1: no rounded cards with radio dots. The light UI uses underlined
  // segments (design/D-settings.reference.html), the dark UI full rows separated by thin lines
  // (design/E-wizard.reference.html). Both are a radiogroup with roving focus and arrow keys.
  // PLAN-GUI-5 R1: text is only a notice given where the choice is made (e.g. the upload notice);
  // tag is a two-or-three-character recommendation after the title ("中文推荐", as in the start
  // dialog); tip is a hover explanation that takes no room.
  interface Option { value: string; title: string; text?: string; aside?: string; tag?: string; tip?: string }
  let { label, options, value = $bindable(), look = "tabs", compact = false, onchange }: {
    label: string; options: Option[]; value: string; look?: "tabs" | "rows"; compact?: boolean;
    onchange?: (value: string) => void;
  } = $props();
  let group = $state<HTMLElement>();
  let current = $derived(options.find((option) => option.value === value));

  function choose(next: string) {
    if (next === value) return;
    value = next;
    onchange?.(next);
    group?.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function onkey(event: KeyboardEvent) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    const index = options.findIndex((option) => option.value === value);
    let target: number | null = null;
    if (step) target = (index + step + options.length) % options.length;
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = options.length - 1;
    if (target == null) return;
    event.preventDefault();
    choose(options[target].value);
    group?.querySelectorAll<HTMLElement>('[role="radio"]')[target]?.focus();
  }
</script>

<div class="choices {look}" class:compact role="radiogroup" aria-label={label} bind:this={group} tabindex="-1" onkeydown={onkey}>
  {#each options as option (option.value)}
    {@const selected = option.value === value}
    <button type="button" role="radio" aria-checked={selected} tabindex={selected || (!current && option === options[0]) ? 0 : -1}
      class:selected title={option.tip} onclick={() => choose(option.value)}>
      {#if look === "rows"}
        <span class="dot" aria-hidden="true"></span>
        <span class="body">
          <span class="title">{option.title}{#if option.tag}<span class="tag">{option.tag}</span>{/if}</span>
          {#if option.text}<span class="text">{option.text}</span>{/if}
        </span>
        {#if option.aside}<span class="aside">{option.aside}</span>{/if}
      {:else}
        {option.title}{#if option.tag}<span class="tag">{option.tag}</span>{/if}
      {/if}
    </button>
  {/each}
</div>
{#if look === "tabs" && current?.text}<p class="about">{current.text}</p>{/if}

<style>
  .choices { display: flex; outline: none; }
  button { font: inherit; cursor: pointer; text-align: left; background: transparent; }

  /* Light: underlined segments. */
  .tabs { flex-wrap: wrap; border-bottom: 1px solid var(--tab-line); }
  .tabs button {
    height: 48px;
    padding: 0 20px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    color: var(--muted);
    font-size: 16px;
  }
  .tabs button:first-child { padding-left: 2px; }
  .tabs button.selected { border-bottom-color: var(--fg); color: var(--fg); font-weight: 600; }
  /* The start dialog's segment aside: 12px, regular weight, muted. */
  .tag { margin-left: 8px; font-size: 12px; font-weight: 400; color: var(--muted); }
  .tabs button:hover:not(.selected) { color: var(--fg); }
  .tabs button:focus-visible { outline-offset: -2px; }
  .about { margin: 6px 0 0; font-size: 13px; line-height: 1.7; color: var(--label); }

  /* Dark: full rows, the selected one marked by the accent dot and a brighter rule. */
  .rows { flex-direction: column; }
  .rows button {
    display: flex;
    align-items: center;
    gap: 24px;
    min-height: 104px;
    padding: 0;
    border: none;
    border-top: 1px solid #23252A;
    color: #A9ABB0;
  }
  .rows button:last-child { border-bottom: 1px solid #23252A; }
  .rows button.selected { border-top-color: #34363B; color: #FFFFFF; }
  .dot { flex: none; width: 10px; height: 10px; box-sizing: border-box; border-radius: 50%; border: 1px solid #5E6067; }
  .selected .dot { border: none; background: var(--accent); }
  .body { flex: 1; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .title { font-size: 26px; font-weight: 300; line-height: normal; }
  .rows .tag { margin-left: 12px; color: #8E9096; vertical-align: middle; }
  .rows .selected .tag { color: #A9ABB0; }
  .selected .title { font-weight: 400; }
  .text { font-size: 14px; line-height: normal; color: #8E9096; }
  .selected .text { color: #A9ABB0; }
  .aside { flex: none; font-family: "Geist Mono", monospace; font-size: 12px; color: #8E9096; }
  .selected .aside { color: var(--accent); }
  .rows button:hover:not(.selected) .title { color: #ECEAE4; }
  .rows button:focus-visible { outline-offset: -2px; }
  .rows.compact button { min-height: 64px; }
  .rows.compact .title { font-size: 20px; }
</style>
