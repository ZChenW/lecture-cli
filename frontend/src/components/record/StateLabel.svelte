<script lang="ts">
  let { text, live }: { text: string; live: boolean } = $props();
</script>

<div class="state">
  <span class="dot" class:live></span>
  <span class="text">{text}</span>
</div>

<style>
  .state { display: flex; align-items: center; gap: 10px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #8E9096; }
  .live { background: #D4FF5C; box-shadow: 0 0 0 5px rgba(212, 255, 92, 0.14); animation: breathe 2s ease-in-out infinite; }
  /* The reference's 5px ring at rest; it widens and fades once per two seconds (plan 6.2). */
  @keyframes breathe {
    50% { box-shadow: 0 0 0 8px rgba(212, 255, 92, 0.04); }
  }
  /* Section heading: 0.24em by the user-confirmed rule (form field labels use 0.08em, see base.css). */
  .text { font-size: 13px; letter-spacing: 0.24em; color: #A9ABB0; }
  @media (prefers-reduced-motion: reduce) { .live { animation: none; } }
</style>
