<script lang="ts">
  import { api } from "../lib/api";
  import { message } from "../lib/state.svelte";
  import type { Result } from "../lib/types";

  // The button sits in the key row; this shows the outcome below it.
  // signature identifies the values that were tested; a change makes the result stale.
  let { kind, provider, apiBase, model, key, signature, result = $bindable(null), busy = $bindable(false) }: {
    kind: "notes" | "asr"; provider: string; apiBase: string; model: string; key: string; signature: string;
    result?: (Result & { signature: string }) | null; busy?: boolean;
  } = $props();

  export async function run() {
    busy = true;
    try {
      const body = { provider, api_base: apiBase, model, ...(key.trim() ? { key: key.trim() } : {}) };
      result = { ...(await api.testService(kind, body)), signature };
    } catch (e) {
      result = { ok: false, level: "fail", message: message(e), signature };
    } finally {
      busy = false;
    }
  }
</script>

{#if busy}
  <p class="hint" role="status">正在测试连接…</p>
{:else if result && result.signature === signature}
  <p class={result.level === "fail" ? "error-text" : result.level === "warn" ? "warn-text" : "ok-text"} role="status">
    {result.message}
  </p>
{/if}
