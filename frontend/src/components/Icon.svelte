<script lang="ts">
  // Inline stroke icons, 1.5px lines; decorative unless the parent labels them.
  let { name, size = 18 }: { name: keyof typeof paths; size?: number } = $props();
  const paths = {
    settings: "M4 7h10M18 7h2M4 17h2M10 17h10M16 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4M8 15a2 2 0 1 0 0 4 2 2 0 0 0 0-4",
    back: "M19 12H5M11 6l-6 6 6 6",
    next: "M5 12h14M13 6l6 6-6 6",
    plus: "M12 5v14M5 12h14",
    ok: "M5 12.5l4.5 4.5L19 7",
    warn: "M12 4l9 16H3zM12 10v4M12 17v.01",
    fail: "M6 6l12 12M18 6L6 18",
    download: "M12 4v11M7 10l5 5 5-5M5 20h14",
    refresh: "M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4",
  };
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>
