import { describe, expect, test } from "vitest";
import { catalogs, createLocalePreference, describeSelection, translate } from "../src/i18n";

describe("local catalogs", () => {
  test("have matching keys and placeholders, with Turkish fallback", () => {
    expect(Object.keys(catalogs.en).sort()).toEqual(Object.keys(catalogs.tr).sort());
    for (const key of Object.keys(catalogs.tr) as (keyof typeof catalogs.tr)[]) {
      expect(catalogs.en[key]).not.toBe("");
      expect(catalogs.en[key].match(/\{\w+\}/g)?.sort()).toEqual(catalogs.tr[key].match(/\{\w+\}/g)?.sort());
    }
    expect(translate("tr", "refresh")).toBe("Yenile");
    expect(translate("en", "refresh")).toBe("Refresh");
    expect(translate("en", "modelErrorDetail", { detail: "$& {face} özel" })).toBe("Could not load model: $& {face} özel");
  });
  test("localizes new selection notices but preserves technical and user values", () => {
    const sel = { face_id: "face_özel", role: "user role {version}" };
    expect(describeSelection("en", sel, 7)).toBe('The user selected face face_özel in the 3D viewer: user role {version} (model version 7). If they say "the face/hole I selected", this is what they mean; verify with get_selection if needed.');
    expect(describeSelection("tr", sel, 7)).toContain("Kullanıcı 3B görüntüleyicide face_özel");
    expect(describeSelection("en", null, 7)).toBe("The user cleared the selection in the viewer.");
    expect(describeSelection("tr", null, 7)).toBe("Kullanıcı görüntüleyicide seçimi kaldırdı.");
  });
});

describe("sandbox-safe language preference", () => {
  test("defaults invalid or absent preferences to Turkish and restores saved English", () => {
    let stored: string | null = null;
    const storage = { getItem: () => stored, setItem: (_key: string, value: string) => { stored = value; } };
    expect(createLocalePreference(() => storage).read()).toBe("tr");
    stored = "de";
    expect(createLocalePreference(() => storage).read()).toBe("tr");
    const preference = createLocalePreference(() => storage);
    preference.write("en");
    expect(createLocalePreference(() => storage).read()).toBe("en");
    preference.write("tr");
    expect(createLocalePreference(() => storage).read()).toBe("tr");
  });
  test.each(["access", "read", "write"])("retains an in-memory choice when storage %s throws", (failure) => {
    const preference = createLocalePreference(() => {
      if (failure === "access") throw new Error("SecurityError");
      return {
        getItem: () => { if (failure === "read") throw new Error("SecurityError"); return null; },
        setItem: () => { throw new Error("QuotaExceededError"); },
      };
    });
    expect(preference.read()).toBe("tr");
    expect(() => preference.write("en")).not.toThrow();
    expect(preference.read()).toBe("en");
    preference.write("tr");
    expect(preference.read()).toBe("tr");
  });
});
