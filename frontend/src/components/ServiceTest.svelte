<script lang="ts">
  import { api } from "../lib/api";
  import { message } from "../lib/state.svelte";
  import type { Result } from "../lib/types";

  // signature identifies the values that were tested; a change makes the result stale.
  let { kind, provider, apiBase, model, key, signature, result = $bindable(null) }: {
    kind: "notes" | "asr"; provider: string; apiBase: string; model: string; key: string; signature: string;
    result?: (Result & { signature: string }) | null;
  } = $props();
  let busy = $state(false);

  async function run() {
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

<div class="row">
  <button type="button" class="btn" onclick={run} disabled={busy}>{busy ? "正在测试…" : "测试连接"}</button>
  {#if result && result.signature === signature}
    <p class={result.level === "fail" ? "error-text" : result.level === "warn" ? "warn-text" : "ok-text"} role="status">
      {result.message}
    </p>
  {/if}
</div>
