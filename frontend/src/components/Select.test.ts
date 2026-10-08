// @vitest-environment jsdom
// N2.1: the custom dropdown's keyboard model, focus return and outside click, mounted for real.
import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Select from "./Select.svelte";

const options = [
  { value: "en", label: "英语", code: "en" },
  { value: "zh", label: "中文", code: "zh" },
  { value: "auto", label: "自动识别", code: "auto" },
];

let target: HTMLElement;
let component: ReturnType<typeof mount>;
let changes: string[];

function press(element: Element, key: string) {
  element.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  flushSync();
}
// show() awaits Svelte's tick before measuring and focusing the list.
const settle = () => new Promise((resolve) => setTimeout(resolve, 0)).then(() => flushSync());
const trigger = () => target.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!;
const list = () => target.querySelector<HTMLElement>('[role="listbox"]');
const active = () => {
  const id = list()?.getAttribute("aria-activedescendant");
  return id ? target.querySelector(`#${id}`)?.textContent?.trim() : null;
};

beforeEach(() => {
  changes = [];
  // jsdom has no layout: give every element a box near the top of an 820px window.
  Element.prototype.scrollIntoView = vi.fn();
  target = document.createElement("div");
  document.body.append(target);
  component = mount(Select, { target, props: { value: "en", options, label: "默认课堂语言", onchange: (v: string) => changes.push(v) } });
  flushSync();
});

afterEach(() => {
  unmount(component);
  target.remove();
  vi.restoreAllMocks();
});

describe("Select", () => {
  it("is a listbox button with the current value and no popup until opened", () => {
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    expect(trigger().textContent).toContain("英语");
    expect(list()).toBeNull();
    expect(target.querySelector("select")).toBeNull();
  });

  it("opens with Enter, Space or ArrowDown on the selected option and moves focus into the list", async () => {
    for (const key of ["Enter", " ", "ArrowDown"]) {
      trigger().focus();
      press(trigger(), key);
      await settle();
      expect(trigger().getAttribute("aria-expanded")).toBe("true");
      expect(document.activeElement).toBe(list());
      expect(active()).toBe("英语 en");
      expect(target.querySelector('[aria-selected="true"]')?.textContent).toContain("英语");
      press(list()!, "Escape");
    }
  });

  it("moves with the arrows, Home and End, and selects with Enter", async () => {
    press(trigger(), "Enter");
    await settle();
    press(list()!, "ArrowDown");
    expect(active()).toBe("中文 zh");
    press(list()!, "End");
    expect(active()).toBe("自动识别 auto");
    press(list()!, "ArrowDown");
    expect(active()).toBe("自动识别 auto");
    press(list()!, "Home");
    expect(active()).toBe("英语 en");
    press(list()!, "ArrowDown");
    press(list()!, "Enter");
    expect(list()).toBeNull();
    expect(trigger().textContent).toContain("中文");
    expect(changes).toEqual(["zh"]);
    expect(document.activeElement).toBe(trigger());
  });

  it("jumps by typing a label or code", async () => {
    press(trigger(), "Enter");
    await settle();
    press(list()!, "z");
    expect(active()).toBe("中文 zh");
    press(list()!, "自");
    expect(active()).toBe("中文 zh");  // "z自" matches nothing: the highlight stays.
  });

  it("closes with Escape without changing the value and returns focus to the trigger", async () => {
    trigger().focus();
    press(trigger(), "ArrowDown");
    await settle();
    press(list()!, "ArrowDown");
    press(list()!, "Escape");
    expect(list()).toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger());
    expect(trigger().textContent).toContain("英语");
    expect(changes).toEqual([]);
  });

  it("closes on a click outside, and selects on a click on an option", async () => {
    trigger().click();
    flushSync();
    await settle();
    document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    flushSync();
    expect(list()).toBeNull();
    trigger().click();
    flushSync();
    await settle();
    target.querySelectorAll<HTMLElement>('[role="option"]')[2].click();
    flushSync();
    expect(list()).toBeNull();
    expect(changes).toEqual(["auto"]);
  });

  it("opens upward when the trigger is near the bottom of the window", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
      { top: 740, bottom: 784, left: 0, right: 200, width: 200, height: 44, x: 0, y: 740, toJSON: () => ({}) });
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(150);
    Object.defineProperty(window, "innerHeight", { value: 820, configurable: true });
    press(trigger(), "Enter");
    await settle();
    flushSync();
    expect(target.querySelector(".dropdown")!.classList.contains("up")).toBe(true);
  });
});
