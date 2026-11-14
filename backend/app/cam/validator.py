"""Deterministik, makine profiline duyarlı G-code doğrulayıcı.

Kontroller üç seviyede gruplanır (proje tanımındaki seviyelerle aynı numaralar):
  Seviye 1 — Sözdizimi ve komut: ayrıştırma, modal çakışmalar, eksik parametreler,
             kontrol ünitesinin desteklemediği kodlar.
  Seviye 2 — Makine uyumluluğu: eksen kursları (makine koordinatında), ilerleme ve
             devir sınırları, takım değiştirici / takım boyu telafisi, diyalekt kuralları.
  Seviye 3 — Hareket güvenliği (basit): malzeme içinde G0, iş mili kapalı kesme,
             güvensiz takım değişimi. Stok her zaman tam dolu varsayılır.

Her bulgu kayıt defterindeki (RULES) bir kurala bağlıdır ve yapılandırılmış `data`
alanı taşır. LLM bu raporu yorumlayıp kullanıcıya açıklar; geçti/kaldı kararını
yalnızca bu modül verir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .interpreter import CANNED, MODAL_GROUPS, SIMULATED_G, SIMULATED_M, interpret, toolpath_json
from .profiles import MachineProfile, Setup, g_supported, get_profile

STOCK_TOP_Z = 0.0  # iş sıfırı parça üst yüzeyi


@dataclass(frozen=True)
class Rule:
    code: str
    level: int
    severity: str  # error | warning | info
    title: str


_RULE_LIST = [
    # Seviye 1 — sözdizimi ve komut
    Rule("E001", 1, "error", "Sözdizimi hatası"),
    Rule("E002", 1, "error", "Kontrol ünitesinin desteklemediği kod"),
    Rule("E003", 1, "error", "Aynı modal gruptan birden fazla kod"),
    Rule("E004", 1, "error", "Tekrarlanan adres harfi"),
    Rule("W005", 1, "warning", "Bir satırda birden fazla M komutu"),
    Rule("E010", 1, "error", "İlerleme hızı (F) eksik veya geçersiz"),
    Rule("E013", 1, "error", "S değeri olmadan iş mili başlatma"),
    Rule("E014", 1, "error", "T numarası olmadan takım değişimi"),
    Rule("E020", 1, "error", "Aktif hareket kodu olmadan eksen koordinatı"),
    Rule("E021", 1, "error", "Delme döngüsünde Z veya R eksik"),
    Rule("E022", 1, "error", "R düzlemi delik dibinin altında"),
    Rule("E023", 1, "error", "Gagalamalı delmede Q eksik"),
    Rule("E050", 1, "error", "Yay merkezi/yarıçapı eksik"),
    Rule("E051", 1, "error", "Yay yarıçapı geçersiz"),
    Rule("W052", 1, "warning", "G18/G19 yayı geometrik olarak kontrol edilmedi"),
    Rule("E053", 1, "error", "Yay geometrisi tutarsız"),
    Rule("W040", 1, "warning", "Program sonundan sonra komut"),
    Rule("W060", 1, "warning", "Birim komutu (G20/G21) yok"),
    Rule("W061", 1, "warning", "Mutlak/artımsal mod (G90/G91) yok"),
    Rule("W062", 1, "warning", "Program sonu (M30/M2) yok"),
    Rule("I063", 1, "info", "Programda hareket yok"),
    Rule("I070", 1, "info", "Komut simülasyonda modellenmiyor"),
    # Seviye 2 — makine uyumluluğu
    Rule("W011", 2, "warning", "İlerleme hızı makine sınırını aşıyor"),
    Rule("E012", 2, "error", "İş mili devri makine sınırları dışında"),
    Rule("E015", 2, "error", "Makinede otomatik takım değiştirici yok"),
    Rule("E016", 2, "error", "Takım numarası magazin kapasitesini aşıyor"),
    Rule("W017", 2, "warning", "Takım boyu telafisi (G43) etkin değil"),
    Rule("E018", 2, "error", "Makinede delme döngüsü yok"),
    Rule("W019", 2, "warning", "Program numarası (O) yok"),
    Rule("I071", 2, "info", "Takım boyu bilinmiyor"),
    Rule("E040", 2, "error", "Eksen kursu aşımı (makine koordinatı)"),
    # Seviye 3 — hareket güvenliği (basit)
    Rule("E030", 3, "error", "İş mili kapalıyken kesme"),
    Rule("E031", 3, "error", "Malzeme içinde yatay hızlı hareket"),
    Rule("E032", 3, "error", "Hızlı hareketle malzemeye dalma"),
    Rule("W032", 3, "warning", "Düşük Z'de takım değişimi"),
    Rule("W033", 3, "warning", "İş mili açıkken takım değişimi"),
    Rule("I041", 3, "info", "Program sonunda iş mili açık"),
    Rule("W064", 3, "warning", "Parçanın çok altına kesme (tabla riski)"),
]
RULES: dict[str, Rule] = {r.code: r for r in _RULE_LIST}

LEVEL_NAMES = {1: "Sözdizimi ve komut", 2: "Makine uyumluluğu", 3: "Hareket güvenliği (basit)"}

NOT_CHECKED = [
    "Takım tutucusu ve bağlama aparatı (mengene/fikstür) çarpışması",
    "Talaş kaldırma: stok her zaman tam dolu bir blok varsayılır (önceden boşaltılmış bölgeler bilinmez)",
    "Seviye 4 — işlenmiş geometrinin CAD modeliyle karşılaştırılması",
    "Kontrol ünitesinin ivmelenme ve önden okuma (look-ahead) davranışı",
    "Makro/değişkenli programlar (#değişken, O-word, alt programlar)",
    "Takım yarıçap telafisi (G41/G42) ile oluşan gerçek yol",
]

# Belirli kodlar için diyalekte özgü öneriler
CODE_HINTS: dict[tuple[str, float], str] = {
    ("M", 6): "Bu makinede otomatik takım değiştirici yok. M5 + M0 ile duraklatıp takımı elle değiştirin ve Z sıfırını yeniden alın.",
    ("G", 43): "Bu kontrol ünitesinde G43 H yok. Takım boyunu her takımda Z sıfırını yeniden alarak ayarlayın (GRBL: G43.1 dinamik telafi).",
    ("G", 81): "Delme döngüsü yok. Döngüyü G0/G1 hareketlerine açın (bu makinenin post-processor'ı bunu otomatik yapar).",
    ("G", 83): "Gagalamalı delme döngüsü yok. Gagalamayı G1 dalma + G0 geri çıkma hareketleriyle yazın.",
    ("G", 65): "Makro çağrısı (G65) bu profilde kapalı.",
}


def _fmt_code(letter: str, v: float) -> str:
    return f"{letter}{int(v)}" if float(v).is_integer() else f"{letter}{v:g}"


class Report:
    def __init__(self, lines):
        self.issues: list[dict[str, Any]] = []
        self.lines = lines

    def add(self, code: str, line: int | None, message: str, suggestion: str = "", data: dict | None = None,
            severity: str | None = None) -> None:
        rule = RULES[code]
        text = self.lines[line - 1].raw.strip() if line else ""
        self.issues.append({
            "code": code, "level": rule.level, "severity": severity or rule.severity, "rule": rule.title,
            "line": line, "text": text, "message": message, "suggestion": suggestion, "data": data or {},
        })


def validate(text: str, profile: MachineProfile | None = None, setup: Setup | None = None,
             stock_height: float | None = None, tool_lengths: dict[int, float] | None = None) -> dict[str, Any]:
    """tool_lengths: H numarası → takım boyu (mm). G43 H aktifken Z'nin makine konumuna eklenir."""
    profile = profile or get_profile()
    setup = setup or Setup()
    tool_lengths = tool_lengths or {}
    offset = setup.offset_for(profile)
    res = interpret(text, profile.rapid_feed)
    rep = Report(res["lines"])
    travel = {ax: (profile.travel[ax].min, profile.travel[ax].max) for ax in "XYZ"}

    # ---- Seviye 1: sözdizimi
    for pl in res["lines"]:
        for e in pl.errors:
            rep.add("E001", pl.number, f"Sözdizimi hatası: {e}", "Satırı adres harfi + sayı biçiminde yazın (ör. G1 X10 F500).")

    # ---- Seviye 2: program numarası (Fanuc)
    if profile.program_number:
        first_code = next((pl for pl in res["lines"] if pl.code), None)
        has_o = any(l == "O" for pl in res["lines"][:5] for l, _ in pl.words)
        if first_code is not None and not has_o:
            rep.add("W019", None, f"{profile.name}: program bir O numarasıyla başlamıyor.",
                    "Programın başına (% satırından sonra) O1000 gibi bir program numarası ekleyin.",
                    {"controller": profile.controller})

    feed_warned = False
    spindle_speed_seen = False
    tool_changes = 0
    tools_used: set[int] = set()
    first_motion_line = None
    end_line = None
    lowest_z = None
    unmodeled_reported: set[str] = set()
    tlo_warned_after_change = False
    pending_tlo_check = False  # son takım değişiminden sonra G43 bekleniyor mu
    unknown_length_h: dict[int, int] = {}  # boyu bilinmeyen H → ilk kullanıldığı satır
    travel_viol: dict[tuple[str, str], dict[str, Any]] = {}  # (eksen, alt/üst) → ilk satır, en uç değer, satırlar

    for ev in res["events"]:
        ln = ev["line"]
        if ev["type"] == "line":
            g, m, vals = ev["g"], ev["m"], ev["vals"]
            if ev["ended"]:
                rep.add("W040", ln, "Program sonu (M30/M2) sonrasında komut var; bu satırlar çalıştırılmaz.", "M30'u programın son satırına taşıyın.")
            for code in g:
                if not g_supported(profile, code):
                    rule = "E018" if code in CANNED and not profile.canned_cycles else "E002"
                    hint = CODE_HINTS.get(("G", code), "Bu kodu kontrol ünitesinin desteklediği eşdeğeriyle değiştirin.")
                    rep.add(rule, ln, f"{profile.name} bu G kodunu desteklemiyor: {_fmt_code('G', code)}", hint,
                            {"code": _fmt_code("G", code), "controller": profile.controller})
                elif code not in SIMULATED_G and f"G{code}" not in unmodeled_reported:
                    unmodeled_reported.add(f"G{code}")
                    rep.add("I070", ln, f"{_fmt_code('G', code)} makinede geçerli ama bu prototipin simülasyonunda modellenmiyor; sonraki hareketlerin konumu belirsiz olabilir.",
                            data={"code": _fmt_code("G", code)})
            for code in m:
                if code == 6 and not profile.tool_changer:
                    rep.add("E015", ln, f"{profile.name}: otomatik takım değiştirici yok, M6 çalıştırılamaz.", CODE_HINTS[("M", 6)],
                            {"code": "M6", "controller": profile.controller})
                elif not (float(code).is_integer() and int(code) in profile.supported_m):
                    rep.add("E002", ln, f"{profile.name} bu M kodunu desteklemiyor: {_fmt_code('M', code)}",
                            CODE_HINTS.get(("M", code), ""), {"code": _fmt_code("M", code), "controller": profile.controller})
                elif code not in SIMULATED_M and f"M{code}" not in unmodeled_reported:
                    unmodeled_reported.add(f"M{code}")
                    rep.add("I070", ln, f"{_fmt_code('M', code)} makinede geçerli ama simülasyonda modellenmiyor.", data={"code": _fmt_code("M", code)})
            for gname, members in MODAL_GROUPS.items():
                hits = [c for c in g if c in members]
                if len(hits) > 1:
                    rep.add("E003", ln, f"Aynı satırda aynı modal gruptan ({gname}) birden fazla kod: " + ", ".join(_fmt_code("G", h) for h in hits),
                            data={"group": gname, "codes": [_fmt_code("G", h) for h in hits]})
            if ev["dup"]:
                rep.add("E004", ln, "Aynı adres harfi bir satırda birden fazla kez kullanılmış: " + ", ".join(ev["dup"]), data={"letters": ev["dup"]})
            if len([c for c in m if c in (3, 4, 5, 6, 30, 2)]) > 1:
                rep.add("W005", ln, "Bir satırda birden fazla M komutu var; bazı kontrol üniteleri yalnızca birini işler.")
            if "F" in vals and vals["F"] <= 0:
                rep.add("E010", ln, "İlerleme hızı (F) sıfır veya negatif.", data={"F": vals["F"]})
            if "F" in vals and vals["F"] > profile.max_feed:
                rep.add("W011", ln, f"İlerleme hızı F{vals['F']:g}, {profile.name} sınırını ({profile.max_feed:g} mm/dk) aşıyor; kontrol ünitesi hızı sınıra düşürür ve süre uzar.",
                        f"F değerini en fazla {profile.max_feed:g} yapın.", {"F": vals["F"], "max_feed": profile.max_feed})
            if "S" in vals:
                spindle_speed_seen = True
                s_val = vals["S"]
                if s_val <= 0:
                    rep.add("E012", ln, "İş mili hızı (S) sıfır veya negatif.", data={"S": s_val})
                elif s_val > profile.max_spindle:
                    rep.add("E012", ln, f"İş mili hızı S{s_val:g}, {profile.name} sınırını ({profile.max_spindle:g} dev/dk) aşıyor.",
                            f"S değerini en fazla {profile.max_spindle:g} yapın ve ilerlemeyi (F) aynı oranda düşürün.",
                            {"S": s_val, "max_spindle": profile.max_spindle})
                elif s_val < profile.min_spindle:
                    rep.add("E012", ln, f"İş mili hızı S{s_val:g}, {profile.name} alt sınırının ({profile.min_spindle:g} dev/dk) altında.",
                            data={"S": s_val, "min_spindle": profile.min_spindle})
            if 3 in m or 4 in m:
                if not spindle_speed_seen:
                    rep.add("E013", ln, "İş mili S değeri tanımlanmadan çalıştırılıyor (M3/M4).", "M3'ten önce veya aynı satırda S değeri verin.")
            if "T" in vals:
                t = int(vals["T"])
                tools_used.add(t)
                if profile.tool_changer and t > profile.max_tools:
                    rep.add("E016", ln, f"T{t}: {profile.name} magazininde {profile.max_tools} takım yeri var.",
                            data={"tool": t, "max_tools": profile.max_tools})
            if 0 in m and not profile.tool_changer:
                tool_changes += 1  # takım değiştiricisi olmayan makinede M0 = elle takım değişimi için duruş
            if 6 in m:
                tool_changes += 1
                if "T" not in vals and ev["state"]["tool"] is None and profile.tool_changer:
                    rep.add("E014", ln, "M6 takım değişimi için T numarası verilmemiş.")
                z = ev["state"]["pos"][2]
                if z is not None and z < STOCK_TOP_Z + 1:
                    rep.add("W032", ln, f"Takım değişimi düşük Z konumunda (Z{z:g}) yapılıyor.", "Takım değişiminden önce güvenli yüksekliğe çıkın.", {"Z": z})
                if ev["state"]["spindle_on"]:
                    rep.add("W033", ln, "İş mili açıkken takım değişimi.", "Önce M5 ile iş milini durdurun.")
                if profile.requires_tool_length_comp:
                    pending_tlo_check = True
                    tlo_warned_after_change = False
            if 43 in g:
                pending_tlo_check = False
            if (2 in m or 30 in m) and end_line is None:
                end_line = ln
                if ev["state"]["spindle_on"] and 5 not in m:
                    rep.add("I041", ln, "Program sonunda iş mili açık; M30 durdurur ama öncesinde M5 kullanmak iyi uygulamadır.")
        elif ev["type"] == "axis_without_motion":
            rep.add("E020", ln, "Eksen koordinatı var ama aktif hareket komutu (G0/G1/G2/G3) yok.", "Satırın başına uygun G kodunu ekleyin.")
        elif ev["type"] == "canned":
            if not ev["has_z"] or not ev["has_r"]:
                rep.add("E021", ln, f"G{ev['cycle']} delme döngüsünde Z veya R eksik.")
            elif ev["r"] < ev["z"]:
                rep.add("E022", ln, "Delme döngüsünde R düzlemi delik dibinin (Z) altında.", data={"R": ev["r"], "Z": ev["z"]})
            elif ev["r"] < STOCK_TOP_Z - 1e-6:
                rep.add("E032", ln, f"Delme döngüsü R düzlemine (R{ev['r']:g}) hızlı hareketle iniyor; R stok üst yüzeyinin altında.",
                        "Delik bir cebin tabanındaysa ve cep henüz işlenmediyse R'yi üst yüzeyin üstüne alın ya da cebi önce işleyin.",
                        {"R": ev["r"], "stock_top": STOCK_TOP_Z})
            if ev["cycle"] in (73, 83) and not ev["has_q"]:
                rep.add("E023", ln, f"G{ev['cycle']} gagalamalı delmede Q (gagalama derinliği) eksik.")
            if not ev["spindle_on"]:
                rep.add("E030", ln, "İş mili kapalıyken delme döngüsü çalıştırılıyor.", "Önce S... M3 ile iş milini başlatın.")
            if not ev["feed"]:
                rep.add("E010", ln, "Delme döngüsü için ilerleme hızı (F) tanımlı değil.")
        elif ev["type"] == "move":
            first_motion_line = first_motion_line or ln
            start, end, kind = ev["start"], ev["end"], ev["kind"]
            # Seviye 2: eksen kursu, makine koordinatında
            h = ev.get("tlo")
            tl = tool_lengths.get(h, 0.0) if h is not None else 0.0
            if h is not None and h not in tool_lengths:
                unknown_length_h.setdefault(h, ln)
            for pt in ev.get("extremes", []):
                for i, ax in enumerate("XYZ"):
                    v = pt[i]
                    if v is None:
                        continue
                    mv = v + offset[i] + (tl if ax == "Z" else 0.0)
                    lo, hi = travel[ax]
                    side = "min" if mv < lo - 1e-6 else "max" if mv > hi + 1e-6 else None
                    if side is None:
                        continue
                    t = travel_viol.setdefault((ax, side), {"first": ln, "lines": set(), "work": v, "machine": mv, "tl": tl})
                    t["lines"].add(ln)
                    if (side == "min" and mv < t["machine"]) or (side == "max" and mv > t["machine"]):
                        t["work"], t["machine"], t["tl"] = v, mv, tl
            zs = [p[2] for p in ev.get("extremes", []) if p[2] is not None]
            if zs:
                lowest_z = min(zs + ([lowest_z] if lowest_z is not None else []))
            if kind in ("feed", "arc", "drill"):
                if ev["feed"] is None and not feed_warned:
                    rep.add("E010", ln, "Kesme hareketi için ilerleme hızı (F) tanımlanmamış.", "İlk G1/G2/G3 satırına F değeri ekleyin.")
                    feed_warned = True
                below = any(z < STOCK_TOP_Z - 1e-6 for z in zs)
                if below and not ev["spindle_on"]:
                    rep.add("E030", ln, "İş mili kapalıyken malzemenin içinde kesme hareketi.", "Kesmeden önce S... M3 ile iş milini başlatın.")
                if below and pending_tlo_check and not tlo_warned_after_change and ev.get("tlo") is None:
                    tlo_warned_after_change = True
                    rep.add("W017", ln, f"{profile.name} takım değişiminden sonra G43 H.. bekliyor; takım boyu telafisi olmadan kesme yapılıyor.",
                            "Takım değişiminden sonra 'G43 H<takım no>' satırı ekleyin.", {"controller": profile.controller})
            if kind == "rapid" and end[2] is not None:
                xy_move = any(end[i] is not None and (start[i] is None or abs(start[i] - end[i]) > 1e-6) for i in (0, 1))
                zmin = min(z for z in (start[2], end[2]) if z is not None)
                if xy_move and zmin < STOCK_TOP_Z - 1e-6:
                    rep.add("E031", ln, f"Malzeme içinde (Z{zmin:g}) yatay hızlı hareket (G0): çarpışma riski.", "Önce Z'yi güvenli yüksekliğe kaldırın veya G1 kullanın.", {"Z": zmin})
                elif not xy_move and end[2] < STOCK_TOP_Z - 1e-6 and (start[2] is None or end[2] < start[2]):
                    rep.add("E032", ln, f"Hızlı hareketle (G0) malzemeye dalma (Z{end[2]:g}).", "Malzemeye G1 ve dalma ilerlemesiyle girin.", {"Z": end[2]})
            arc = ev.get("arc")
            if arc:
                err = arc.get("error")
                if err == "missing_center":
                    rep.add("E050", ln, "Yay komutunda merkez (I/J) veya yarıçap (R) eksik.")
                elif err == "radius_too_small":
                    rep.add("E051", ln, f"Yay yarıçapı R{arc['r']:g} başlangıç-bitiş mesafesi ({arc['chord']:.3f}) için çok küçük.", data={"R": arc["r"], "chord": round(arc["chord"], 4)})
                elif err == "zero_chord":
                    rep.add("E051", ln, "R biçimli yayda başlangıç ve bitiş aynı nokta; tam daire için I/J kullanın.")
                elif err == "plane":
                    rep.add("W052", ln, "G18/G19 düzleminde yay: bu prototipte geometrik kontrolü yapılmıyor.")
                elif arc.get("mode") == "IJ" and abs(arc["r_start"] - arc["r_end"]) > 0.01:
                    rep.add("E053", ln, f"Yay geometrisi tutarsız: başlangıç yarıçapı {arc['r_start']:.3f}, bitiş yarıçapı {arc['r_end']:.3f}.",
                            data={"r_start": round(arc["r_start"], 4), "r_end": round(arc["r_end"], 4)})

    # Seviye 2: kurs aşımları eksen/yön başına tek bulgu olarak (her satır ayrı hata olmasın)
    for (ax, side), t in sorted(travel_viol.items(), key=lambda kv: kv[1]["first"]):
        i = "XYZ".index(ax)
        lo, hi = travel[ax]
        n = len(t["lines"])
        limit = lo if side == "min" else hi
        rep.add("E040", t["first"],
                f"{ax} ekseni kursun {'alt' if side == 'min' else 'üst'} sınırını aşıyor: en uç nokta iş koord. {ax}{t['work']:.3f} → makine {ax}{t['machine']:.3f} "
                f"(kurs {lo:g} … {hi:g}, G{setup.wcs} {ax}={offset[i]:g}"
                f"{f', takım boyu {t['tl']:g}' if ax == 'Z' and t['tl'] else ''}). Etkilenen satır sayısı: {n}.",
                f"İş sıfırını (G{setup.wcs}) tabla üzerinde başka bir yere alın, parçayı yeniden bağlayın veya programı bu makinenin kursuna göre düzenleyin.",
                {"axis": ax, "side": side, "work": round(t["work"], 4), "machine": round(t["machine"], 4), "travel": [lo, hi],
                 "exceeds_by": round(abs(t["machine"] - limit), 4), "work_offset": offset[i],
                 "tool_length": t["tl"] if ax == "Z" else 0.0, "frame": "machine",
                 "line_count": n, "lines": sorted(t["lines"])[:20]})

    for h, first in sorted(unknown_length_h.items(), key=lambda kv: kv[1]):
        rep.add("I071", first,
                f"H{h} (T{h}) için takım boyu girilmemiş; Z kursu kontrolü takım boyu 0 varsayılarak yapıldı. "
                "Gerçek makine Z'si takım boyu kadar yüksektir: Z'nin üst sınırını aşan hareketler gözden kaçabilir.",
                "İşleme ayarları → Takımlar bölümünden bu takımın boyunu girin.", {"H": h, "assumed_length": 0.0})

    st = res["final_state"]
    if not st.seen_units:
        rep.add("W060", None, "Birim komutu (G21 mm / G20 inç) yok; makinenin varsayılan birimi kullanılır.", "Programın başına G21 ekleyin.")
    if not st.seen_distance:
        rep.add("W061", None, "Mutlak/artımsal mod (G90/G91) belirtilmemiş.", "Programın başına G90 ekleyin.")
    if end_line is None:
        rep.add("W062", None, "Program sonu komutu (M30 veya M2) yok.", "Programın sonuna M30 ekleyin.")
    if first_motion_line is None:
        rep.add("I063", None, "Programda hareket komutu yok.")
    if stock_height is not None and lowest_z is not None and lowest_z < -(stock_height + 5.0):
        rep.add("W064", None, f"En derin nokta Z{lowest_z:.3f}, parça kalınlığının ({stock_height:g} mm) 5 mm'den fazla altında: tabla/bağlama aparatına kesme riski.",
                data={"lowest_z": round(lowest_z, 4), "stock_height": stock_height})

    tp = toolpath_json(text, profile.rapid_feed)
    tc_time = tool_changes * profile.tool_change_time_s
    counts = {s: sum(1 for i in rep.issues if i["severity"] == s) for s in ("error", "warning", "info")}
    levels = {
        str(lv): {"name": name, "checked": True,
                  "counts": {s: sum(1 for i in rep.issues if i["level"] == lv and i["severity"] == s) for s in ("error", "warning", "info")}}
        for lv, name in LEVEL_NAMES.items()
    }
    rep.issues.sort(key=lambda i: ({"error": 0, "warning": 1, "info": 2}[i["severity"]], i["line"] or 0))
    stats = {**tp["stats"], "line_count": len(res["lines"]), "tool_changes": tool_changes, "tools": sorted(tools_used),
             "motion_time_s": tp["stats"]["estimated_time_s"], "tool_change_time_s": round(tc_time, 1),
             "estimated_time_s": round(tp["stats"]["estimated_time_s"] + tc_time, 1)}
    return {
        "valid": counts["error"] == 0,
        "counts": counts,
        "levels": levels,
        "issues": rep.issues[:200],
        "truncated": len(rep.issues) > 200,
        "profile": profile.summary(),
        "setup": {"work_offset": list(offset), "tool_lengths": {str(k): v for k, v in sorted(tool_lengths.items())}},
        "stats": stats,
        "not_checked": NOT_CHECKED,
        "disclaimer": ("Bu rapor yalnızca yukarıdaki kontroller kapsamında sonuç verir; 'kontrol edilmeyenler' listesindeki riskler değerlendirilmemiştir. "
                       "Simülasyondan geçmek, programın fiziksel tezgâhta güvenle çalışacağının garantisi değildir."),
    }


def rule_catalog() -> list[dict[str, Any]]:
    """Arayüz ve LLM için kural listesi."""
    return [{"code": r.code, "level": r.level, "severity": r.severity, "title": r.title} for r in _RULE_LIST]
