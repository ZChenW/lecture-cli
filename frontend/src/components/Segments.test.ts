// @vitest-environment jsdom
// Plan GUI-3 item 3: the start dialog's segments, mounted for real: arrows select and skip options
// that cannot be chosen, which stay visible with their reason.
import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import Segments from "./Segments.svelte";

let target: HTMLElement;
let component: ReturnType<typeof mount>;
let changes: string[];
const radios = () => [...target.querySelectorAll<HTMLButtonElement>('[role="radio"]')];
const checked = () => radios().find((radio) => radio.getAttribute("aria-checked") === "true")?.dataset.value;

function press(key: string) {
  (document.activeElement ?? target).dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  flushSync();
}

beforeEach(() => {
  changes = [];
  target = document.createElement("div");
  document.body.append(target);
  component = mount(Segments, { target, props: {
    labelledby: "x", value: "en", onchange: (v: string) => changes.push(v),
    options: [{ value: "en", title: "英语" }, { value: "zh", title: "中文" },
              { value: "qwen", title: "Qwen", aside: "未安装", disabled: true, hint: "运行 ./install.sh --with-qwen" },
              { value: "auto", title: "自动" }],
  } });
  flushSync();
});

afterEach(() => {
  unmount(component);
  target.remove();
});

describe("Segments", () => {
  it("is one tab stop on the selected option", () => {
    expect(radios().map((radio) => radio.tabIndex)).toEqual([0, -1, -1, -1]);
    const group = target.querySelector('[role="radiogroup"]')!;
    expect(group.getAttribute("aria-labelledby")).toBe("x");
    // The group itself is no focus target: a dialog opening on it would need an extra Tab.
    expect(group.hasAttribute("tabindex")).toBe(false);
  });

  it("moves and selects with the arrow keys, skipping what cannot be chosen", () => {
    radios()[0].focus();
    press("ArrowRight");
    expect(checked()).toBe("zh");
    press("ArrowRight");
    expect(checked()).toBe("auto");
    expect(document.activeElement).toBe(radios()[3]);
    press("ArrowRight");
    expect(checked()).toBe("en");
    press("ArrowLeft");
    expect(checked()).toBe("auto");
    press("Home");
    expect(checked()).toBe("en");
    press("End");
    expect(checked()).toBe("auto");
    expect(changes).toEqual(["zh", "auto", "en", "auto", "en", "auto"]);
  });

  it("keeps a disabled option visible with its reason, and ignores clicks on it", () => {
    const qwen = radios()[2];
    expect(qwen.getAttribute("aria-disabled")).toBe("true");
    expect(qwen.title).toBe("运行 ./install.sh --with-qwen");
    expect(qwen.textContent).toContain("未安装");
    qwen.click();
    flushSync();
    expect(checked()).toBe("en");
    expect(changes).toEqual([]);
  });
});
