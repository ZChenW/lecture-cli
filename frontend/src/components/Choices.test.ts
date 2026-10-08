// @vitest-environment jsdom
// PLAN-GUI-5 R1: an option carries no "what this is" line. A recommendation is a short tag after the
// title, an explanation a tooltip; the only line under a choice is a notice such as the upload one.
import { flushSync, mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import Choices from "./Choices.svelte";

let target: HTMLElement;
let component: ReturnType<typeof mount> | null = null;
const OPTIONS = [
  { value: "whisper", title: "本地 Whisper", tip: "在本机转录" },
  { value: "qwen", title: "本地 Qwen", tag: "中文推荐", tip: "中文课堂更准" },
  { value: "api", title: "云端 API", text: "课堂音频会上传到转录服务" },
];

function render(look: "tabs" | "rows", value: string) {
  target = document.createElement("div");
  document.body.append(target);
  component = mount(Choices, { target, props: { label: "转录方式", look, value, options: OPTIONS } });
  flushSync();
}
const radios = () => [...target.querySelectorAll<HTMLButtonElement>('[role="radio"]')];

afterEach(() => {
  if (component) unmount(component);
  component = null;
  target.remove();
});

describe("Choices", () => {
  it("shows a tag after the title and the explanation only as a tooltip (tabs)", () => {
    render("tabs", "qwen");
    expect(radios().map((r) => r.textContent?.trim())).toEqual(["本地 Whisper", "本地 Qwen中文推荐", "云端 API"]);
    expect(radios()[0].title).toBe("在本机转录");
    expect(radios()[1].querySelector(".tag")?.textContent).toBe("中文推荐");
    expect(target.textContent).not.toContain("中文课堂更准");
    expect(target.querySelector(".about")).toBeNull();
  });

  it("shows the notice under the tabs only while that option is chosen", () => {
    render("tabs", "api");
    expect(target.querySelector(".about")?.textContent).toBe("课堂音频会上传到转录服务");
  });

  it("puts the tag beside the title and the notice under it (rows)", () => {
    render("rows", "whisper");
    const [, qwen, api] = radios();
    expect(qwen.querySelector(".title")?.textContent).toBe("本地 Qwen中文推荐");
    expect(qwen.querySelector(".text")).toBeNull();
    expect(api.querySelector(".text")?.textContent).toBe("课堂音频会上传到转录服务");
  });
});
