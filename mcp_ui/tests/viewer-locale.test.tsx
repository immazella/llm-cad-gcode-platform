import { act } from "react";
import { JSDOM } from "jsdom";
import { expect, test, vi } from "vitest";

const host = vi.hoisted(() => ({ app: null as any, connect: vi.fn(), call: vi.fn(), context: vi.fn(), mounts: 0 }));
vi.mock("@modelcontextprotocol/ext-apps/app-with-deps", () => ({
  App: class {
    constructor(public info: unknown) { host.app = this; }
    connect = host.connect.mockResolvedValue(undefined);
    callServerTool = host.call;
    updateModelContext = host.context.mockResolvedValue(undefined);
    getHostContext() { return { availableDisplayModes: ["inline", "fullscreen"] }; }
    requestDisplayMode = vi.fn(async ({ mode }) => ({ mode }));
  },
}));
vi.mock("../src/Viewer", async () => {
  const { useEffect } = await import("react");
  return {
    DARK: {}, LIGHT: {},
    default: ({ onSelect, selected }: any) => {
      useEffect(() => { host.mounts++; }, []);
      return <button data-testid="face" data-selected={selected} onClick={() => onSelect(selected ? null : "face_42")}>face_42</button>;
    },
  };
});

async function changeLanguage(value: string) {
  const select = document.querySelector("select")!;
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function button(text: string) {
  return [...document.querySelectorAll("button")].find((b) => b.textContent?.includes(text))!;
}

test("switches live labels, notices and errors without reconnecting or changing the model/selection IDs", async () => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  document.body.innerHTML = '<div id="root"></div>';
  // Use browser Storage, not Node 25's unrelated experimental global storage.
  vi.stubGlobal("localStorage", new JSDOM("", { url: "https://viewer.test" }).window.localStorage);
  localStorage.clear();
  await act(async () => { await import("../src/main"); });
  const language = document.querySelector("select");
  expect(language, "viewer needs a keyboard-accessible language selector").not.toBeNull();
  expect(language!.getAttribute("aria-label") || document.querySelector(`label[for="${language!.id}"]`)?.textContent).toBe("Dil");
  expect([...language!.options].map((o) => [o.value, o.text])).toEqual([["tr", "Türkçe"], ["en", "English"]]);
  expect(document.documentElement.lang).toBe("tr");
  expect(document.body.textContent).toContain("Model yükleniyor…");
  expect(document.title).toBe("3B model görüntüleyici");
  await changeLanguage("en");
  expect(document.documentElement.lang).toBe("en");
  expect(document.title).toBe("3D model viewer");
  expect(document.body.textContent).toContain("Loading model…");
  expect(language!.getAttribute("aria-label") || document.querySelector(`label[for="${language!.id}"]`)?.textContent).toBe("Language");
  expect(localStorage.getItem("busproje.viewer.language")).toBe("en");
  expect(button("Refresh").title).toBe("Reload model");
  expect(button("Fullscreen").title).toBe("Fullscreen");

  const mesh = { model_id: "model_özel", version: 7, faces: [], edges: "", summary: { name: "Kullanıcının Türkçe modeli", version: 7, features: [], bbox: { size: [1, 2, 3], min: [0, 0, 0], max: [1, 2, 3] } } };
  await act(async () => host.app.ontoolresult({ _meta: { "busproje/mesh": mesh, "busproje/selection": null } }));
  expect(document.body.textContent).toContain(mesh.summary.name);
  expect(document.body.textContent).toContain("version 7");
  expect(document.body.textContent).toContain("engraving / feature");
  expect(document.body.textContent).toContain("Rotate: drag");
  host.call.mockResolvedValue({ content: [{ type: "text", text: JSON.stringify({ selection: { face_id: "face_42", role: "custom Türkçe role" } }) }] });
  await act(async () => (document.querySelector('[data-testid="face"]') as HTMLButtonElement).click());
  expect(host.call).toHaveBeenLastCalledWith({ name: "set_selection", arguments: { face_id: "face_42", model_version: 7 } });
  expect(host.context.mock.lastCall![0].content[0].text).toContain("The user selected");
  expect(host.context.mock.lastCall![0].content[0].text).toContain("face_42");
  expect(host.context.mock.lastCall![0].content[0].text).toContain("custom Türkçe role");
  expect(host.context.mock.lastCall![0].content[0].text).toContain("get_selection");
  expect(document.body.textContent).toContain('"the face I selected"');
  await changeLanguage("tr");
  expect(document.body.textContent).toContain("custom Türkçe role");
  expect(document.querySelector('[data-testid="face"]')!.getAttribute("data-selected")).toBe("face_42");
  expect(host.mounts).toBe(1);
  expect(host.connect).toHaveBeenCalledTimes(1);
  host.call.mockResolvedValue({ content: [{ type: "text", text: '{"selection":null}' }] });
  await act(async () => (document.querySelector('[data-testid="face"]') as HTMLButtonElement).click());
  expect(host.context.mock.lastCall![0].content[0].text).toBe("Kullanıcı görüntüleyicide seçimi kaldırdı.");
  host.call.mockResolvedValue({});
  await act(async () => button("Yenile").click());
  expect(host.call).toHaveBeenLastCalledWith({ name: "show_model_3d", arguments: {} });
  expect(document.body.textContent).toContain("Sunucu mesh verisi göndermedi.");
  await changeLanguage("en");
  expect(document.body.textContent).toContain("The server did not send mesh data.");
  host.call.mockRejectedValue(new Error("raw diagnostics / işlem"));
  await act(async () => button("Refresh").click());
  expect(document.body.textContent).toContain("Could not load model: raw diagnostics / işlem");
  await act(async () => host.app.ontoolresult({ isError: true }));
  expect(document.body.textContent).toContain("The tool returned an error.");
  await act(async () => host.app.ontoolresult({ isError: true, content: [{ type: "text", text: "External user text / özel" }] }));
  expect(document.body.textContent).toContain("External user text / özel");
  await act(async () => button("Fullscreen").click());
  expect(button("Exit fullscreen").title).toBe("Exit fullscreen");
  expect(document.querySelector(".shell.full")).not.toBeNull();

  // A pending tool response must use the locale chosen before it completes.
  let finishSelection!: (value: unknown) => void;
  host.call.mockImplementationOnce(() => new Promise((resolve) => { finishSelection = resolve; }));
  await act(async () => (document.querySelector('[data-testid="face"]') as HTMLButtonElement).click());
  await changeLanguage("tr");
  await act(async () => finishSelection({ content: [{ type: "text", text: '{"selection":{"face_id":"face_42"}}' }] }));
  expect(host.context.mock.lastCall![0].content[0].text).toContain("Kullanıcı 3B görüntüleyicide face_42");
  expect(document.body.textContent).toContain("seçildi");
  expect(button("Küçült").title).toBe("Küçült");

  // Privacy settings may change even after initial startup; switching still works.
  Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new DOMException("Blocked", "SecurityError"); } });
  await changeLanguage("en");
  expect(document.documentElement.lang).toBe("en");
  expect(document.body.textContent).toContain("selected");
  expect(document.querySelector('[data-testid="face"]')!.getAttribute("data-selected")).toBe("face_42");
  host.call.mockRejectedValue(new Error("offline diagnostic"));
  await act(async () => (document.querySelector('[data-testid="face"]') as HTMLButtonElement).click());
  expect(document.body.textContent).toContain("Could not send selection: offline diagnostic");
  expect(document.querySelector('[data-testid="face"]')!.getAttribute("data-selected")).toBe("face_42");
  expect(host.connect).toHaveBeenCalledTimes(1);
  expect(host.mounts).toBe(1);
});
