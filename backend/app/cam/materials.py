"""Malzeme kütüphanesi ve kesme verisi hesabı (feeds & speeds).

Değerler, takım üreticisi kataloglarında ve tezgâh el kitaplarında yaygın verilen aralıkların
ORTASINDAN alınmış GENEL BAŞLANGIÇ değerleridir; gerçek takımın kataloğu her zaman önceliklidir.
Hesap:
    n  [dev/dk] = 1000 · Vc / (π · D)          Vc: kesme hızı [m/dk], D: takım çapı [mm]
    F  [mm/dk]  = n · z · fz                   z: ağız sayısı, fz: diş başına ilerleme [mm/diş]
    Matkap: fz = fn / 2 (fn: devir başına ilerleme, iki ağız)
Diş başına ilerleme çapla orantılı alınır (fz = k · D, alt/üst sınırla), kademe derinliği ve yan paso
çapın oranıdır. Makinenin devir/ilerleme sınırına çarpan değerler aynı talaş kalınlığını koruyacak
şekilde birlikte düşürülür.
"""
from __future__ import annotations

import math
from typing import Any

# vc: m/dk {takım malzemesi: {freze, matkap}} · fz_k: fz = k·D (freze), fn = k·D (matkap)
# ap: kademe derinliği / D (kaba), ae: yan paso / D, plunge: dalma ilerlemesi / F
MATERIALS: dict[str, dict[str, Any]] = {
    "al6061": {"name": "Alüminyum 6061-T6", "group": "N", "vc": {"carbide": (300, 120), "hss": (90, 60)},
               "fz_k": (0.010, 0.020), "ap": 0.5, "ae": 0.40, "plunge": 0.35},
    "al7075": {"name": "Alüminyum 7075-T6", "group": "N", "vc": {"carbide": (250, 100), "hss": (75, 50)},
               "fz_k": (0.009, 0.018), "ap": 0.45, "ae": 0.40, "plunge": 0.33},
    "brass": {"name": "Pirinç (CuZn39Pb3)", "group": "N", "vc": {"carbide": (200, 90), "hss": (60, 45)},
              "fz_k": (0.008, 0.015), "ap": 0.4, "ae": 0.40, "plunge": 0.33},
    "steel_mild": {"name": "Yapı çeliği (S235 / 1018)", "group": "P", "vc": {"carbide": (140, 80), "hss": (30, 25)},
                   "fz_k": (0.006, 0.012), "ap": 0.25, "ae": 0.30, "plunge": 0.25},
    "steel_alloy": {"name": "Alaşımlı çelik (42CrMo4 / 4140, ~30 HRC)", "group": "P", "vc": {"carbide": (100, 60), "hss": (20, 15)},
                    "fz_k": (0.005, 0.010), "ap": 0.2, "ae": 0.25, "plunge": 0.25},
    "stainless": {"name": "Paslanmaz çelik (304)", "group": "M", "vc": {"carbide": (80, 45), "hss": (15, 12)},
                  "fz_k": (0.005, 0.008), "ap": 0.2, "ae": 0.25, "plunge": 0.25},
    "cast_iron": {"name": "Gri dökme demir (GG25)", "group": "K", "vc": {"carbide": (120, 70), "hss": (25, 20)},
                  "fz_k": (0.007, 0.015), "ap": 0.3, "ae": 0.35, "plunge": 0.3},
    "pom": {"name": "POM (Delrin)", "group": "O", "vc": {"carbide": (300, 100), "hss": (150, 60)},
            "fz_k": (0.015, 0.030), "ap": 0.6, "ae": 0.45, "plunge": 0.4},
    "acrylic": {"name": "Akrilik (PMMA)", "group": "O", "vc": {"carbide": (250, 80), "hss": (120, 50)},
                "fz_k": (0.012, 0.025), "ap": 0.5, "ae": 0.40, "plunge": 0.35},
    "mdf": {"name": "MDF / ahşap", "group": "O", "vc": {"carbide": (400, 120), "hss": (200, 60)},
            "fz_k": (0.020, 0.030), "ap": 1.0, "ae": 0.45, "plunge": 0.4},
}
DEFAULT_MATERIAL = "al6061"
GROUP_TR = {"P": "çelik", "M": "paslanmaz", "K": "dökme demir", "N": "demir dışı", "O": "plastik / ahşap"}
NOTE = "Genel başlangıç değerleridir (el kitabı aralıklarının ortası); takım üreticisinin kataloğu önceliklidir."


def material(mid: str | None) -> dict[str, Any]:
    return MATERIALS.get(mid or DEFAULT_MATERIAL) or MATERIALS[DEFAULT_MATERIAL]


def list_materials() -> list[dict[str, Any]]:
    return [{"id": k, "name": v["name"], "group": v["group"], "group_tr": GROUP_TR[v["group"]],
             "vc_carbide_mill": v["vc"]["carbide"][0], "vc_hss_drill": v["vc"]["hss"][1]} for k, v in MATERIALS.items()]


def _r(v: float, d: int = 0) -> float:
    return round(v, d) if d else float(round(v))


def recommend(tool_type: str, diameter: float, flutes: int, tool_material: str, material_id: str | None,
              max_rpm: float | None = None, min_rpm: float | None = None, max_feed: float | None = None) -> dict[str, Any]:
    """Takım + malzeme için devir, ilerleme, dalma, kademe derinliği, yan paso önerisi."""
    m = material(material_id)
    is_drill = tool_type in ("drill", "spot_drill")
    tm = tool_material if tool_material in ("carbide", "hss") else "carbide"
    vc = m["vc"][tm][1 if is_drill else 0]
    D = max(diameter, 0.1)
    z = 2 if is_drill else max(1, flutes)
    if is_drill:
        fn = min(max(m["fz_k"][1] * D, 0.02), 0.5)
        fz = fn / 2
    else:
        fz = min(max(m["fz_k"][0] * D, 0.005), 0.25)
        if tool_type in ("vbit", "chamfer"):  # uç noktası çapı küçük: yarı ilerleme
            fz *= 0.5
        if tool_type == "ballmill":
            fz *= 0.8
    rpm = 1000 * vc / (math.pi * D)
    notes: list[str] = []
    feed = rpm * z * fz
    if max_rpm and rpm > max_rpm:
        notes.append(f"Hesaplanan devir {rpm:.0f} dev/dk makine sınırını ({max_rpm:g}) aşıyor; devir sınıra, ilerleme aynı oranda düşürüldü.")
        feed *= max_rpm / rpm
        rpm = max_rpm
    if min_rpm and 0 < rpm < min_rpm:
        notes.append(f"Hesaplanan devir {rpm:.0f} iş milinin alt sınırının ({min_rpm:g}) altında; devir yükseltildi, ilerleme oranlandı.")
        feed *= min_rpm / rpm
        rpm = min_rpm
    if max_feed and feed > max_feed:
        notes.append(f"İlerleme {feed:.0f} mm/dk makine sınırına ({max_feed:g}) düşürüldü.")
        feed = max_feed
    out = {
        "material": material_id or DEFAULT_MATERIAL, "material_name": m["name"], "tool_material": tm,
        "vc": vc, "fz": round(fz, 4), "flutes": z,
        "rpm": _r(rpm), "feed": _r(feed), "plunge": _r(feed * m["plunge"]),
        "stepdown": round(min(m["ap"] * D, 2.5 * D), 2), "stepover_ratio": m["ae"],
        "formula": f"n = 1000·{vc:g}/(π·{D:g}) ; F = n·{z}·{fz:.4f}",
        "notes": notes, "source": NOTE,
    }
    if is_drill:
        out["peck"] = round(max(D * (1.0 if m["group"] in ("N", "O") else 0.5), 0.5), 2)
        out["stepdown"] = None
        out["stepover_ratio"] = None
    return out
