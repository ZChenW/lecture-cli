<script lang="ts">
  import type { Snippet } from "svelte";
  // The only heavy rule in 方案 B sits under this bar.
  let { children }: { children?: Snippet } = $props();
</script>

<header class="topbar">
  <a class="brand" href="#/">Lecture</a>
  <div class="actions">{@render children?.()}</div>
</header>

<style>
  .topbar {
    display: flex;
    align-items: center;
    gap: 16px;
    min-height: 72px;
    padding: 12px 44px;
    border-bottom: 1px solid var(--fg);
  }
  .brand {
    font-family: var(--display);
    font-style: italic;
    font-size: 26px;
    text-decoration: none;
    color: var(--fg);
    min-height: 44px;
    display: inline-flex;
    align-items: center;
  }
  .brand:hover { color: var(--fg); }
  .actions { margin-left: auto; display: flex; align-items: center; gap: 12px; }
  @media (max-width: 900px) { .topbar { padding: 12px 20px; } }
</style>
