<script lang="ts">
  import { inRange, type CiteTarget, type TranscriptView } from "../../lib/note";

  // Each segment keeps its anchor id from the attachment (live-L12, refined-L12); a cited range is shaded.
  let { view, target }: { view: TranscriptView; target: CiteTarget | null } = $props();
  const NAMES: Record<string, string> = { live: "实时转录 · live", refined: "离线校正 · refined" };
</script>

{#if view.intro.length}<p class="intro">{view.intro.join(" ")}</p>{/if}
{#each view.groups as group (group.version)}
  <section>
    <h3>{NAMES[group.version] ?? group.version}</h3>
    <ol>
      {#each group.segments as segment (segment.id)}
        <li id="{segment.version}-L{segment.id}" class:hit={inRange(segment, target)} tabindex="-1">
          <span class="num"><span>L{segment.id}</span>{#if segment.time}{" "}<span class="time">{segment.time}</span>{/if}</span>
          <p>{segment.text}</p>
        </li>
      {/each}
    </ol>
  </section>
{/each}

<style>
  .intro { margin: 0 0 8px; font-size: 13px; line-height: 1.7; color: #5C5C5A; }
  h3 { margin: 24px 0 6px; font-family: var(--mono); font-size: 12px; font-weight: 400; letter-spacing: 0.08em; color: #5C5C5A; }
  ol { margin: 0; padding: 0; list-style: none; }
  li { display: flex; gap: 16px; padding: 14px 0; border-top: 1px solid #D9D9D6; }
  li.hit { margin: 0 -12px; padding: 14px 12px; background: #F0EFEB; }
  .num { flex: none; width: 52px; display: flex; flex-direction: column; font-family: var(--mono); font-size: 11px; line-height: 24px; color: #767674; }
  .time { line-height: 1.4; }
  p { margin: 0; min-width: 0; font-family: "Source Serif 4", var(--cjk-serif); font-size: 15px; line-height: 1.6; color: #5C5C5A; white-space: pre-line; overflow-wrap: anywhere; }
  .hit .num { color: #2A2A29; }
  .hit p { color: #111111; }
</style>
