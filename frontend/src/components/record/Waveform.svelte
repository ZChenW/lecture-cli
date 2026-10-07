<script lang="ts">
  import { waveBars } from "../../lib/record";

  let { samples, paused }: { samples: number[]; paused: boolean } = $props();
  let bars = $derived(waveBars(samples, paused));
</script>

<!-- Same geometry as the reference: 360×56 view box, 2.5 strokes, slots not yet sampled at 0.3 opacity. -->
<svg class="wave" class:paused viewBox="0 0 360 56" fill="none" stroke="#D4FF5C" stroke-width="2.5"
  stroke-linecap="round" aria-hidden="true">
  {#each bars as bar, i (i)}
    <path d={bar.d} class:quiet={!bar.sampled} />
  {/each}
</svg>

<style>
  .wave { width: 100%; max-width: 360px; height: 56px; }
  path { transition: d 120ms ease-out; }
  .quiet, .paused path { opacity: 0.3; }
</style>
