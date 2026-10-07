<script lang="ts">
  import { renderMarkdown } from "../../lib/markdown";
  import { updatedAgo } from "../../lib/record";
  import type { Snapshot } from "../../lib/types";

  let { notes, now }: { notes: Snapshot["notes"]; now: number } = $props();
  let html = $derived(notes.latest ? renderMarkdown(notes.latest) : "");
</script>

<aside class="card" aria-label="随堂笔记">
  <div class="head">
    <span class="label">随堂笔记</span>
    <span class="ago">{updatedAgo(notes.updated, now)}</span>
  </div>
  {#if notes.worker_alive === false}
    <p class="dead" role="alert">笔记进程已退出，结束时会保存待整理原文</p>
  {/if}
  {#if html}
    <!-- Sanitised by DOMPurify inside renderMarkdown. -->
    <div class="body">{@html html}</div>
  {:else}
    <p class="empty">第一批笔记将在约一分钟后出现</p>
  {/if}
</aside>

<style>
  .card {
    flex: 1 1 320px;
    max-width: 420px;
    align-self: center;
    box-sizing: border-box;
    border: 1px solid #23252A;
    background: rgba(255, 255, 255, 0.025);
    border-radius: 20px;
    padding: 26px 26px 22px;
    display: flex;
    flex-direction: column;
    gap: 18px;
    min-width: 0;
    /* Real notes outgrow the reference's three bullets: the card scrolls inside instead. */
    max-height: calc(100vh - 260px);
  }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .label { font-size: 12px; letter-spacing: 0.24em; color: #8E9096; }
  .ago { font-family: 'Geist Mono', monospace; font-size: 12px; color: #8E9096; }
  .dead { margin: 0; font-size: 13px; line-height: 1.6; color: #FFB454; }
  .empty { margin: 0; font-size: 15px; line-height: 1.65; color: #8E9096; }
  .body { display: flex; flex-direction: column; gap: 18px; min-height: 0; overflow-y: auto; }
  .body :global(:is(h1, h2, h3, h4, h5, h6)) { margin: 0; font-size: 22px; font-weight: 500; line-height: 1.35; }
  .body :global(ul), .body :global(ol) {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 12px;
    font-size: 15px;
    line-height: 1.65;
    color: #C9CACD;
  }
  .body :global(p) { margin: 0; font-size: 15px; line-height: 1.65; color: #C9CACD; }
  .body :global(.math-block) {
    font-family: 'Instrument Serif', serif;
    font-size: 28px;
    text-align: center;
    padding: 14px 0;
    border-top: 1px solid #23252A;
    border-bottom: 1px solid #23252A;
    color: #ECEAE4;
  }
  .body :global(.math-block math) { font-family: 'Instrument Serif', serif; }
  /* Single-letter identifiers would map to Mathematical Italic code points that few fonts carry. */
  .body :global(.math-block mi) { text-transform: none; }
  .body :global(.review) { display: flex; align-items: flex-start; gap: 10px; font-size: 13px; line-height: 1.6; color: #A9ABB0; }
  .body :global(.review)::before {
    content: "";
    box-sizing: content-box;  /* As the reference's span: 6px plus the border. */
    flex: none;
    margin-top: 7px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    border: 1px solid #D4FF5C;
  }
  .body :global(.citation) { font-family: 'Geist Mono', monospace; font-size: 12px; color: #8E9096; }
  .body :global(.math-error) { font-family: 'Geist Mono', monospace; font-size: 12px; }
  .body :global(.math-error-mark) { margin-left: 6px; font-size: 12px; color: #FFB454; }
</style>
