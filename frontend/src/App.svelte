<script lang="ts">
  import { onMount } from "svelte";
  import { ApiError } from "./lib/api";
  import { parseRoute, startRoute, type Route } from "./lib/router";
  import { app, message, refresh } from "./lib/state.svelte";
  import Home from "./pages/Home.svelte";
  import NotePlaceholder from "./pages/NotePlaceholder.svelte";
  import Onboarding from "./pages/Onboarding.svelte";
  import Record from "./pages/Record.svelte";
  import Settings from "./pages/Settings.svelte";

  let route = $state<Route>(parseRoute(location.hash));
  let failure = $state<{ title: string; text: string } | null>(null);
  // Pages mount only after the start route is chosen, so none loads data it is about to leave.
  let started = $state(false);
  let night = $derived(route.name === "onboarding" || route.name === "record");

  onMount(() => {
    const follow = () => (route = parseRoute(location.hash));
    window.addEventListener("hashchange", follow);
    refresh().then((boot) => {
      const target = startRoute(boot, location.hash);
      if (target !== location.hash) history.replaceState(null, "", target);
      follow();
      started = true;
    }).catch((e) => {
      failure = e instanceof ApiError && e.status === 401
        ? { title: "界面会话已失效", text: "请关闭此窗口，在终端重新运行 lecture gui。" }
        : { title: "无法载入", text: message(e) };
    });
    return () => window.removeEventListener("hashchange", follow);
  });
</script>

<div class="app" class:night class:paper={!night}>
  {#if failure}
    <main class="failure" role="alert">
      <h1>{failure.title}</h1>
      <p>{failure.text}</p>
    </main>
  {:else if started && app.boot}
    {#key route.name + route.params.join("/")}
      {#if route.name === "onboarding"}
        <Onboarding />
      {:else if route.name === "record"}
        <Record />
      {:else if route.name === "settings"}
        <Settings />
      {:else if route.name === "notes"}
        <NotePlaceholder course={route.params[0]} file={route.params[1]} />
      {:else}
        <Home />
      {/if}
    {/key}
  {/if}
</div>

<style>
  .failure { max-width: 44em; margin: 0 auto; padding: 96px 44px; display: flex; flex-direction: column; gap: 16px; }
  h1 { font-family: var(--display); font-weight: 600; font-size: 36px; }
</style>
