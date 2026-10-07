<script lang="ts">
  import type { TocEntry } from "../../lib/markdown";

  // The note's h2/h3 outline; the section being read is marked (one of the two accent uses, plan 6.3).
  let { entries, current, onjump }: { entries: TocEntry[]; current: string | null; onjump: (id: string) => void } = $props();
</script>

<nav class="contents" aria-label="目录">
  <span class="label">目录</span>
  <ol>
    {#each entries as entry (entry.id)}
      <li class="level-{entry.level}">
        <button type="button" aria-current={entry.id === current ? "location" : undefined} onclick={() => onjump(entry.id)}>
          {entry.text}
        </button>
      </li>
    {/each}
  </ol>
</nav>

<style>
  .contents { font-family: var(--ui); }
  .label { display: block; margin-bottom: 6px; font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; color: #767674; }
  ol { margin: 0; padding: 0; list-style: none; }
  button {
    display: flex;
    align-items: center;
    width: 100%;
    min-height: 44px;
    padding: 4px 0;
    border: 0;
    background: none;
    text-align: left;
    font-size: 13px;
    line-height: 1.5;
    color: #5C5C5A;
    cursor: pointer;
  }
  .level-3 button { padding-left: 14px; color: #767674; }
  button:hover { color: #111111; }
  button[aria-current="location"] { color: #C42710; }
</style>
