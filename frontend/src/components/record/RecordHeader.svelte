<script lang="ts">
  import { tick } from "svelte";
  import { navigate } from "../../lib/router";
  import type { Snapshot } from "../../lib/types";

  // ondiscard is set only while recording: discarding is refused once closing has begun.
  let { snapshot, ondiscard }: { snapshot: Snapshot | null; ondiscard?: () => void } = $props();
  let engine = $derived(snapshot ? [snapshot.asr.device_label, snapshot.asr.model].filter(Boolean).join(" · ") : "");
  let open = $state(false);
  let armed = $state(false);
  let disarm: ReturnType<typeof setTimeout> | undefined;
  let menuButton = $state<HTMLButtonElement>();
  let item = $state<HTMLButtonElement>();
  let root = $state<HTMLElement>();

  async function toggle() {
    open = !open;
    armed = false;
    clearTimeout(disarm);
    if (open) {
      await tick();
      item?.focus();
    }
  }

  function close(refocus = true) {
    open = false;
    armed = false;
    clearTimeout(disarm);
    if (refocus) menuButton?.focus();
  }

  // Plan N2.5: the first press only arms the item; a second press within 3 s discards.
  function discard() {
    clearTimeout(disarm);
    if (!armed) {
      armed = true;
      disarm = setTimeout(() => (armed = false), 3000);
      return;
    }
    close();
    ondiscard?.();
  }

  function onkey(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") {
      close(false);
    }
  }

  $effect(() => { if (!ondiscard && open) close(false); });
</script>

<svelte:document onpointerdown={(event) => open && root && !root.contains(event.target as Node) && close(false)} />

<header class="top">
  <div class="brand">
    <span class="word">lecture</span>
    <span class="rule" aria-hidden="true"></span>
    <span class="course">{snapshot?.course ?? ""}</span>
  </div>
  <div class="pills">
    {#if engine}<span class="pill">{engine}</span>{/if}
    {#if snapshot}<span class="pill">{snapshot.input}</span>{/if}
    <button class="round" aria-label="设置" title="设置" onclick={() => navigate("settings")}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
        stroke-linecap="round" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></svg>
    </button>
    {#if ondiscard}
      <div class="more" bind:this={root}>
        <button class="round" bind:this={menuButton} aria-label="更多操作" title="更多操作" aria-haspopup="menu"
          aria-expanded={open} aria-controls={open ? "record-menu" : undefined} onclick={toggle}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
            stroke-linecap="round" aria-hidden="true"><path d="M5 12h.01M12 12h.01M19 12h.01" /></svg>
        </button>
        {#if open}
          <div class="menu" id="record-menu" role="menu" aria-label="更多操作" tabindex="-1" onkeydown={onkey}>
            <button role="menuitem" class="item" class:armed bind:this={item} onclick={discard} aria-live="polite">
              {armed ? "再按一次，录音和笔记都会删除" : "放弃这堂课"}
            </button>
          </div>
        {/if}
      </div>
    {/if}
  </div>
</header>

<style>
  .top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; position: relative; z-index: 3; }
  .brand { display: flex; align-items: baseline; gap: 18px; }
  .word { font-family: 'Instrument Serif', serif; font-size: 26px; letter-spacing: 0.01em; }
  .rule { width: 1px; height: 14px; background: #34363B; align-self: center; }
  .course { font-family: 'Geist Mono', monospace; font-size: 13px; letter-spacing: 0.08em; color: #A9ABB0; }
  .pills { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  .pill {
    font-family: 'Geist Mono', monospace;
    font-size: 12px;
    color: #A9ABB0;
    border: 1px solid #26282C;
    border-radius: 999px;
    padding: 7px 14px;
  }
  .round {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    border: 1px solid #26282C;
    background: transparent;
    color: #A9ABB0;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }
  .round:hover, .round[aria-expanded="true"] { color: #ECEAE4; border-color: #34363B; }
  .more { position: relative; }
  /* HANDOFF 3.2: the warning banner's surface and 10px radius, with room around the item. The banner
     is rgba(255,255,255,0.04) over #0B0C0E; a menu covers text, so it uses that colour opaque. */
  .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 6px);
    min-width: 280px;
    padding: 8px;
    border-radius: 10px;
    background: #151618;
    border: 1px solid #26282C;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
    outline: none;
  }
  .item {
    width: 100%;
    min-height: 44px;
    padding: 0 16px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: #ECEAE4;
    font-size: 15px;
    text-align: left;
    white-space: nowrap;
    cursor: pointer;
  }
  .item:hover, .item:focus-visible { background: #23252A; outline: none; }
  .item.armed { color: #FF6B5E; }
</style>
