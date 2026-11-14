"""Presentation-only catalogs for built-in metadata and generated warnings.

Do not call these from the generator: generated text, plan and stored records
must remain byte/data identical regardless of request locale.
"""
import re
from .i18n import language

PROFILE_EN = {
    "fanuc_vmc": (
        "3-axis vertical machining center with a 20-tool automatic tool changer. Fanuc-style dialect: % and O number, M6 tool changes, G43 H tool length compensation, G81/G83 drilling cycles.",
        ["Example profile; obtain actual machine values from its parameters.", "Macro programming (G65/G66, #variables) is assumed disabled in this example profile."]),
    "grbl_router": (
        "Hobby/desktop router running GRBL 1.1. No drilling cycles (G81/G83) or M6 tool change; pause with M0 and change tools manually. G43.1 only, not G43 H. Short Z travel.",
        ["Example profile; obtain actual values from GRBL $ settings ($110-$132, $30).", "Machine coordinates use the common negative GRBL workspace (home at 0,0,0)."]),
    "linuxcnc_mill": (
        "Small 3-axis mill running LinuxCNC. Supports M6 tool changes (manual, length from the tool table), G43 H, drilling cycles and G64 P path blending. Spindle limited to 6000 rpm.",
        ["Example profile; obtain actual values from the machine .ini file.", "Tool changes are manual; estimated change time reflects this."]),
}
MATERIAL_EN = {
    "al6061": "Aluminum 6061-T6", "al7075": "Aluminum 7075-T6", "brass": "Brass (CuZn39Pb3)",
    "steel_mild": "Mild steel (S235 / 1018)", "steel_alloy": "Alloy steel (42CrMo4 / 4140, ~30 HRC)",
    "stainless": "Stainless steel (304)", "cast_iron": "Gray cast iron (GG25)", "pom": "POM (Delrin)",
    "acrylic": "Acrylic (PMMA)", "mdf": "MDF / wood",
}
GROUP_EN = {"P": "steel", "M": "stainless", "K": "cast iron", "N": "non-ferrous", "O": "plastic / wood"}
NOTE_EN = "General starting values (midpoints of handbook ranges); the tool manufacturer's catalog takes precedence."
WARNING_EN = {
    "Dış kontur boydan boya kesiliyor; tutma köprüsü (tab) yok, parçayı sabitlemeyi unutmayın.": "The outer contour cuts through the part; there are no holding tabs. Secure the part.",
    "Takım listesinde düz tabanlı parmak freze yok; frezeleme operasyonları atlandı.": "No flat end mill in the tool list; milling operations were skipped.",
    "Alt yüzeyde düzlemsel dış sınır bulunamadı; kontur işleme atlandı.": "No planar outer boundary on the bottom face; contour machining was skipped.",
    "İşlenecek operasyon bulunamadı.": "No machining operations found.",
    "Delme döngüleri G0/G1 hareketlerine açıldı (makinede G81/G83 yok).": "Drilling cycles were expanded into G0/G1 moves (the machine has no G81/G83).",
}
WARNING_PATTERNS = (
    (r"Hesaplanan devir (.*) dev/dk makine sınırını \((.*)\) aşıyor; devir sınıra, ilerleme aynı oranda düşürüldü\.", "Calculated speed {0} rpm exceeds the machine limit ({1}); speed was limited and feed reduced proportionally."),
    (r"Hesaplanan devir (.*) iş milinin alt sınırının \((.*)\) altında; devir yükseltildi, ilerleme oranlandı\.", "Calculated speed {0} is below the spindle minimum ({1}); speed was raised and feed scaled proportionally."),
    (r"İlerleme (.*) mm/dk makine sınırına \((.*)\) düşürüldü\.", "Feed {0} mm/min was reduced to the machine limit ({1})."),
    (r"G(.*) (.*) için tanımlı iş koordinatları arasında değil \((.*)\)\.", "G{0} is not among the work coordinates supported by {1} ({2})."),
    (r"(.*) '(.*)' soğutmayı desteklemiyor \((.*)\); soğutma kapatıldı\.", "{0} does not support coolant '{1}' ({2}); coolant was disabled."),
    (r"T(.*): '(.*)' adlı preset yok; malzemeye göre seçildi\.", "T{0}: preset '{1}' does not exist; selected settings for the material."),
    (r"(.*) T(.*): devir (.*) → (.*) dev/dk \(makine sınırı\); ilerlemeler aynı oranda \(×(.*)\) düşürüldü\.", "{0} T{1}: speed {2} → {3} rpm (machine limit); feeds reduced proportionally (×{4})."),
    (r"(.*) T(.*): devir (.*) → (.*) dev/dk \(iş milinin alt sınırı\)\.", "{0} T{1}: speed {2} → {3} rpm (spindle minimum)."),
    (r"(.*) T(.*): ilerleme (.*) → (.*) mm/dk \(makine ilerleme sınırı\)\.", "{0} T{1}: feed {2} → {3} mm/min (machine feed limit)."),
    (r"(.*) T(.*): dalma (.*) → (.*) mm/dk \(makine ilerleme sınırı\)\.", "{0} T{1}: plunge {2} → {3} mm/min (machine feed limit)."),
    (r"(.*): T(.*) takım listesinde yok; otomatik seçim kullanıldı\.", "{0}: T{1} is not in the tool list; automatic selection was used."),
    (r"(.*): T(.*) \((.*)\) bu operasyona uygun değil; otomatik seçim kullanıldı\.", "{0}: T{1} ({2}) is unsuitable for this operation; automatic selection was used."),
    (r"T(.*) Ø(.*) makinenin en büyük takım çapını \((.*)\) aşıyor\.", "T{0} Ø{1} exceeds the machine maximum tool diameter ({2})."),
    (r"(.*): derinlik (.*) mm, T(.*) ağız boyundan \((.*) mm\) fazla; gövde malzemeye sürtünür\. Daha uzun ağızlı takım seçin\.", "{0}: depth {1} mm exceeds T{2} flute length ({3} mm); the shank will rub the material. Choose a longer flute."),
    (r"Ham parçanın (.*) mm üst payı alın frezeleme ile kaldırılmıyor \(operasyon kapalı veya freze yok\); üst yüzeydeki işlemler bu payın altından başlar\.", "The {0} mm stock top allowance is not removed by facing (operation disabled or no mill); top-face operations begin beneath this allowance."),
    (r"(.*): eksen \+Z değil \(3 eksen tezgâhta üstten işlenemez\), atlandı\.", "{0}: axis is not +Z (cannot machine from above on a 3-axis machine); skipped."),
    (r"(.*) \(Ø(.*)\): Ø(.*) matkap yok ve helisel delik kapalı, atlandı\.", "{0} (Ø{1}): no Ø{2} drill and helical boring is disabled; skipped."),
    (r"(.*) \(Ø(.*)\): uygun matkap/freze yok, atlandı\. Takım listesine Ø(.*) matkap ekleyin\.", "{0} (Ø{1}): no suitable drill/mill; skipped. Add a Ø{2} drill to the tool list."),
    (r"(.*): (.*)° V-uç (.*) mm derinlikte (.*) mm genişlik keser \(modeldeki çizgi genişliği (.*) mm\)\. Derinliği veya genişliği uyumlu hâle getirin\.", "{0}: a {1}° V-bit cuts {3} mm wide at {2} mm depth (model line width {4} mm). Match the depth and width."),
    (r"(.*): takım listesinde V-uç yok; Ø(.*) freze ile kazınacak, çizgi (.*) mm yerine (.*) mm olur\. Takım listesine V-uç ekleyin\.", "{0}: no V-bit in the tool list; engraving with a Ø{1} mill makes the line {3} mm instead of {2} mm wide. Add a V-bit."),
    (r"(.*): kazıma için V-uç veya parmak freze yok, atlandı\.", "{0}: no V-bit or end mill for engraving; skipped."),
    (r"(.*): kör delik matkapla delinecek; taban (.*)° uç açısıyla konik kalır\.", "{0}: blind hole will be drilled; the bottom remains conical at the {1}° point angle."),
    (r"(.*): bırakılan pay ile delik takımdan dar kalıyor, atlandı\.", "{0}: with the allowance, the hole is narrower than the tool; skipped."),
    (r"(.*): cep Ø(.*) takım için çok dar, atlandı\.", "{0}: pocket too narrow for the Ø{1} tool; skipped."),
    (r"(.*): köşe yarıçapı \((.*)\) takım yarıçapından \((.*)\) küçük; köşelerde malzeme kalır\.", "{0}: corner radius ({1}) is smaller than tool radius ({2}); material remains in corners."),
)


PROFILE_NAME_EN = {
    "fanuc_vmc": "Vertical machining center — Fanuc 0i-MF style (example)",
    "grbl_router": "Desktop CNC router — GRBL 1.1 (example)",
    "linuxcnc_mill": "Small mill — LinuxCNC (example)",
}
PLAN_FEATURE_EN = {"dış kontur": "outer contour"}


def present_plan_feature(feature: str) -> str:
    """Generator-owned plan labels only; hole/pocket IDs pass through unchanged."""
    if language() != "en":
        return feature
    if feature in PLAN_FEATURE_EN:
        return PLAN_FEATURE_EN[feature]
    match = re.fullmatch(r"üst yüzey \((.*) mm\)", feature)
    return f"top face ({match[1]} mm)" if match else feature


def profile_name(profile: dict) -> str:
    if language() == "en" and profile.get("id") in PROFILE_NAME_EN and profile.get("name", "").endswith("(örnek)"):
        return PROFILE_NAME_EN[profile["id"]]
    return profile.get("name", "")


def present_profile(profile: dict) -> dict:
    if language() != "en" or not profile.get("builtin") or profile["id"] not in PROFILE_EN:
        return profile
    description, notes = PROFILE_EN[profile["id"]]
    return {**profile, "description": description, "notes": list(notes)}


def present_warning(text: str) -> str:
    if language() != "en":
        return text
    if text in WARNING_EN:
        return WARNING_EN[text]
    for pattern, template in WARNING_PATTERNS:
        match = re.fullmatch(pattern, text)
        if match:
            # Preserve captured user tool labels, IDs and profile names verbatim.
            return template.format(*match.groups())
    return text


def present_materials(result: dict) -> dict:
    if language() != "en":
        return result
    rows = [{**m, "name": MATERIAL_EN.get(m["id"], m["name"]), "group_tr": GROUP_EN.get(m["group"], m["group_tr"])} for m in result["materials"]]
    return {**result, "materials": rows, "note": NOTE_EN}


def present_cutting(result: dict) -> dict:
    if language() != "en":
        return result
    return {**result, "material_name": MATERIAL_EN.get(result["material"], result["material_name"]),
            "notes": [present_warning(n) for n in result["notes"]], "source": NOTE_EN}
