"""API anahtarı olmadan çalışan basit, kural tabanlı Türkçe komut yorumlayıcı.

Gerçek LLM'nin yerini tutmaz; amacı platformun geri kalanını (CAD, CAM, arayüz)
anahtarsız da gösterebilmek ve test edebilmektir. Aynı araçları çağırır.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable

N = r"(\d+(?:[.,]\d+)?)"
DIM3 = re.compile(N + r"\s*(?:mm)?\s*[x×\*]\s*" + N + r"\s*(?:mm)?\s*[x×\*]\s*" + N)


def num(s: str) -> float:
    return float(s.replace(",", "."))


def find(pattern: str, s: str) -> float | None:
    m = re.search(pattern, s)
    return num(m.group(1)) if m else None


def diameter_in(s: str) -> float | None:
    r = find(N + r"\s*(?:mm)?\s*(?:'?l[iı]k\s*)?yar[iı]çap", s) or find(r"yar[iı]çap\w*\s*(?:=|:)?\s*" + N, s)
    if r is not None:
        return 2 * r
    return find(N + r"\s*(?:mm)?\s*(?<!yarı)(?<!yari)çap", s) or find(r"(?:(?<!yarı)(?<!yari)çap\w*|ø)\s*(?:=|:)?\s*" + N, s)


def xy_in(s: str) -> tuple[float, float]:
    x = find(r"\bx\s*[=:]?\s*(-?\d+(?:[.,]\d+)?)", s)
    y = find(r"\by\s*[=:]?\s*(-?\d+(?:[.,]\d+)?)", s)
    return (x or 0.0, y or 0.0)


def edges_in(s: str, default: str) -> str:
    if "üst" in s:
        return "top"
    if "alt" in s:
        return "bottom"
    if "tüm" in s or "bütün" in s:
        return "all"
    if "dikey" in s or "köşe" in s:
        return "vertical"
    return default


EDGE_TR = {"vertical": "dış dikey", "top": "üst", "bottom": "alt", "all": "tüm"}

HELP = ("Çevrimdışı moddayım (LLM API anahtarı tanımlı değil), bu yüzden yalnızca basit kalıpları anlayabiliyorum. Örnekler:\n"
        "• 60x60x10 mm plaka oluştur. Merkezine 10 mm yarıçaplı delik aç.\n"
        "• Silindir oluştur, çap 50, yükseklik 8\n"
        "• x=20 y=-15 noktasına 6 mm çaplı 5 mm derinliğinde delik aç\n"
        "• 20x15x4 cep aç, köşe yarıçapı 3\n"
        "• Seçtiğim deliğin yarıçapını 15 mm yap\n"
        "• Dikey kenarlara 3 mm kavis ver / üst kenarlara 1 mm pah kır\n"
        "• G-code üret / G-code'u doğrula / STEP olarak dışa aktar / geri al\n"
        "• Makine profillerini listele / GRBL makinesine geç / Fanuc için G-code üret\n"
        "• T1 takım boyu 65 mm\n"
        "• \"İTÜ\" yazısını 8 mm yüksekliğinde kazı / kenardan 4 mm içeride çerçeve kazı\n"
        "• x=-20 y=0 x=0 y=10 x=20 y=0 noktalarından çizgi kazı\n"
        "Gerçek doğal dil anlayışı için backend/.env dosyasına bir API anahtarı ekleyin.")


QUOTED = re.compile(r"[\"“”«»](.+?)[\"“”«»]|(?<![\w])['‘’](.+?)['‘’](?![\w])")
XY_PAIR = re.compile(r"x\s*[=:]?\s*(-?\d+(?:[.,]\d+)?)\s*,?\s*y\s*[=:]?\s*(-?\d+(?:[.,]\d+)?)")


class OfflineInterpreter:
    MULTI_REF = re.compile(r"seçt|seçili|bunlar|hepsi|tümü|bu (?:bölge|delik|yüzey|alan)")

    def run(self, message: str, selection: dict | list | None, call: Callable[[str, dict], dict], ws: Any) -> str:
        from ..i18n import language
        if language() == "en":
            from .offline_en import run_english
            return run_english(message, call)
        sels = [s for s in (selection if isinstance(selection, list) else [selection]) if s]
        sentences = [s.strip() for s in re.split(r"(?<!\d)[.!?\n]+|\.(?=\s|$)(?!\s*sat[ıi]r)", message) if s.strip()]
        replies: list[str] = []
        for s in sentences:
            self._orig = s  # büyük/küçük harf korunmuş hâli (kazınacak yazı için)
            low = s.lower()
            if len(sels) > 1 and self.MULTI_REF.search(low):
                # Çoklu seçim: cümleyi seçili her eleman için ayrı uygula
                done: set[str] = set()
                for sel in sels:
                    key = sel.get("region_id") or sel.get("hole_id") or sel.get("face_id")
                    if key in done:
                        continue  # aynı deliğin iki yarım yüzeyi
                    done.add(key)
                    if sel.get("kind") == "region" and ws.model is not None:
                        try:  # önceki işlem modeli değiştirmiş olabilir: bölgeyi güncel modelde yeniden çöz
                            sel = ws.model.region_info(sel["region_id"], mesh=False)
                        except Exception as exc:
                            replies.append(f"⚠️ {exc}")
                            continue
                    r = self._sentence(low, sel, call, ws)
                    if r:
                        replies.append(r)
                continue
            r = self._sentence(low, sels[-1] if sels else None, call, ws)
            if r:
                replies.append(r)
        return "\n".join(replies) if replies else HELP

    def _result(self, res: dict, ok_msg: str) -> str:
        if res.get("ok") is False:
            return "⚠️ " + res.get("error", "İşlem başarısız.")
        return ok_msg

    PROFILE_WORDS = (("grbl", "grbl_router"), ("router", "grbl_router"), ("linuxcnc", "linuxcnc_mill"),
                     ("fanuc", "fanuc_vmc"), ("işleme merkezi", "fanuc_vmc"), ("vmc", "fanuc_vmc"))

    MATERIAL_WORDS = (("7075", "al7075"), ("6061", "al6061"), ("alüminyum", "al6061"), ("aluminyum", "al6061"), ("pirinç", "brass"),
                      ("paslanmaz", "stainless"), ("304", "stainless"), ("4140", "steel_alloy"), ("42crmo4", "steel_alloy"),
                      ("dökme demir", "cast_iron"), ("döküm", "cast_iron"), ("çelik", "steel_mild"), ("delrin", "pom"), ("pom", "pom"),
                      ("akrilik", "acrylic"), ("pleksi", "acrylic"), ("mdf", "mdf"), ("ahşap", "mdf"))

    def _material_in(self, s: str) -> str | None:
        return next((mid for word, mid in self.MATERIAL_WORDS if re.search(r"(?<!\w)" + re.escape(word), s)), None)

    def _profile_in(self, s: str) -> str | None:
        return next((pid for word, pid in self.PROFILE_WORDS if word in s), None)

    def _sentence(self, s: str, sel: dict | None, call, ws) -> str | None:
        if re.search(r"profil|makine", s) and re.search(r"listele|neler|hangi|göster", s):
            r = call("list_machine_profiles", {})
            rows = [f"• {p['id']}: {p['name']}{' (aktif)' if p['id'] == r['active'] else ''}" for p in r["profiles"]]
            return "Tanımlı makine profilleri:\n" + "\n".join(rows)
        mat = self._material_in(s)
        if mat and re.search(r"malzeme|parça|iş parçası|kullan|seç|işle|ayarla|olarak", s) and not re.search(r"devir|ilerleme|kesme veri", s):
            r = call("set_setup", {"material": mat})
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            from ..cam.materials import material
            return f"Malzeme {material(mat)['name']} olarak ayarlandı. " + (r.get("note") or "Kesme verisi bu malzemeye göre hesaplanacak.")
        if re.search(r"devir|ilerleme|kesme veri|feeds|vc\b|fz\b", s) and re.search(r"öner|hesapla|ne olmalı|kaç|ver", s):
            tn = re.search(r"\bt\s*(\d+)\b", s)
            d = find(r"(?:ø|çap\w*)\s*" + N, s) or find(N + r"\s*(?:mm)?\s*(?:çap|freze|matkap)", s)
            args: dict[str, Any] = {}
            if tn:
                args["tool_number"] = int(tn.group(1))
            elif d:
                args.update(diameter=d, tool_type="drill" if "matkap" in s else "endmill")
            else:
                args["tool_number"] = ws.config.mill_tool
            if mat:
                args["material"] = mat
            r = call("recommend_cutting", args)
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            msg = (f"{r['material_name']} için öneri: {r['rpm']:g} dev/dk, ilerleme {r['feed']:g} mm/dk"
                   + (f", dalma {r['plunge']:g} mm/dk, kademe {r['stepdown']:g} mm, yan paso %{r['stepover_ratio'] * 100:.0f}" if r.get("stepdown") else "")
                   + (f", gaga {r['peck']:g} mm" if r.get("peck") else "") + f".\nHesap: {r['formula']} (Vc {r['vc']:g} m/dk).")
            if r["notes"]:
                msg += "\n" + " ".join(r["notes"])
            return msg + "\n" + r["source"]
        tl = re.search(r"\bt\s*(\d+)\b[^.]*?boy\w*\s*(?:=|:)?\s*" + N, s)
        if tl:
            r = call("set_tool_length", {"tool_number": int(tl.group(1)), "length": num(tl.group(2))})
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            msg = f"T{tl.group(1)} takım boyu {num(tl.group(2)):g} mm olarak kaydedildi."
            if r.get("revalidation"):
                c = r["revalidation"]["counts"]
                msg += f" Program yeniden doğrulandı: {c['error']} hata, {c['warning']} uyarı."
            return msg + (" " + r["note"] if r.get("note") else "")
        pid = self._profile_in(s)
        wants_gcode = bool(re.search(r"g[\s-]?kod|gcode|g-code", s))
        if pid and (wants_gcode or re.search(r"profil|makine|tezgah|tezgâh|geç|seç|kullan", s)):
            r = call("set_machine_profile", {"profile_id": pid})
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            msg = f"Makine profili: {r['profile']['name']}."
            if r.get("revalidation"):
                c = r["revalidation"]["counts"]
                msg += f" Mevcut program bu makineye göre yeniden doğrulandı: {c['error']} hata, {c['warning']} uyarı."
            if r.get("note") and not wants_gcode:
                msg += " " + r["note"]
            if not wants_gcode:
                return msg
            rest = self._core(s, sel, call, ws)
            return msg + ("\n" + rest if rest else "")
        return self._core(s, sel, call, ws)

    def _engrave(self, s: str, sel: dict | None, call, ws) -> str | None:
        """Kazıma kalıpları: tırnak içindeki yazı, çerçeve, x/y noktalarından çizgi."""
        depth = find(r"derinli\w*\s*" + N, s) or find(N + r"\s*(?:mm)?\s*derin", s)
        face = sel["face_id"] if sel and sel.get("surface") == "plane" and sel.get("normal") == [0, 0, 1] and re.search(r"seçt|seçili|bu yüzey|bölge|bu alan", s) else None
        common: dict[str, Any] = {k: v for k, v in (("depth", depth), ("face_id", face)) if v is not None}
        q = QUOTED.search(getattr(self, "_orig", ""))
        if q:
            text = q.group(1) or q.group(2)
            rest = s.replace(text.lower(), " ")
            h = find(N + r"\s*(?:mm)?\s*(?:yüksekli|boy|harf)", rest) or find(r"(?:yükseklik|boy)\w*\s*" + N, rest)
            rot = find(N + r"\s*(?:°|derece)", rest)
            x, y = xy_in(rest)
            pl = sel.get("placement") if sel and sel.get("kind") == "region" else None
            if pl and face and not re.search(r"\b[xy]\s*[=:]?\s*-?\d", rest):
                x, y = pl["x"], pl["y"]  # seçili bölgenin ortasına
            args = {"text": text, "x": x, "y": y, **common}
            if h:
                args["height"] = h
            if rot:
                args["rotation"] = rot
            if "kontur" in rest:
                args["font"] = "kontur"
            r = call("add_engraving_text", args)
            return self._result(r, f"'{text}' yazısı kazındı ({r.get('feature_id')}, {r.get('stroke_count')} vuruş, derinlik {r.get('depth')} mm, "
                                   f"çizgi {r.get('width')} mm).")
        if "çerçeve" in s and ws.model is not None:
            margin = find(N + r"\s*(?:mm)?\s*içer", s) or 3.0
            bb = ws.model.summary()["bbox"]
            if ws.model.base.get("type") == "cylinder":
                rr = min(bb["size"][0], bb["size"][1]) / 2 - margin
                pts = [[round(rr * math.cos(math.radians(a)), 4), round(rr * math.sin(math.radians(a)), 4)] for a in range(0, 360, 5)]
            else:
                x0, y0 = bb["min"][0] + margin, bb["min"][1] + margin
                x1, y1 = bb["max"][0] - margin, bb["max"][1] - margin
                pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            r = call("add_engraving_path", {"paths": [pts], "closed": True, **common})
            return self._result(r, f"Kenardan {margin:g} mm içeride çerçeve kazındı ({r.get('feature_id')}).")
        pairs = XY_PAIR.findall(s)
        if len(pairs) >= 2 and re.search(r"çizgi|yol|kaz", s):
            pts = [[num(a), num(b)] for a, b in pairs]
            closed = bool(re.search(r"kapalı|üçgen|çokgen|kapat", s))
            r = call("add_engraving_path", {"paths": [pts], "closed": closed, **common})
            return self._result(r, f"{len(pts)} noktalı {'kapalı şekil' if closed else 'çizgi'} kazındı ({r.get('feature_id')}).")
        return None

    def _region(self, s: str, sel: dict, call) -> str | None:
        """Seçili bölge (⌘/Ctrl ile çizilen alan) için: cep, delik, çevre kazıma."""
        pl, rid = sel.get("placement"), sel["region_id"]
        wants = "cep" in s or "delik" in s or "deliğ" in s or re.search(r"kaz[ıi]", s)
        if not pl:
            return f"{rid} eğri bir yüzeyde; bu sürümde cep/delik/kazıma yalnızca düzlemsel yüzeylerdeki bölgelere yapılabilir." if wants else None
        depth = find(r"derinli\w*\s*" + N, s) or find(N + r"\s*(?:mm)?\s*(?:derin|cep|kaz)", s)
        if "cep" in s and not DIM3.search(s):
            if depth is None:
                return f"Cebin derinliğini belirtir misiniz? (ör. seçtiğim bölgeye 3 mm cep aç)"
            L, W = pl["length"], pl["width"]
            rc = find(r"köşe\s*yar[iı]çap\w*\s*" + N, s)
            if rc is None and sel["shape"] == "circle":
                rc = round(min(L, W) * 0.49, 3)  # daireye yakın cep
            args = {"length": L, "width": W, "depth": depth, "x": pl["x"], "y": pl["y"], "face_id": pl["face_id"], "corner_radius": rc or 0}
            r = call("add_pocket", args)
            note = "" if sel["shape"] in ("rect", "circle") and not sel.get("clipped") else " Cep dikdörtgen olduğu için bölgeyi çevreleyen dikdörtgen kullanıldı."
            return self._result(r, f"{rid} bölgesine {L:g}×{W:g} mm, {depth:g} mm derinliğinde cep açıldı ({r.get('feature_id')}).{note}")
        if ("delik" in s or "deliğ" in s) and sel.get("circle") and not re.search(r"\bsil|kaldır", s):
            c = sel["circle"]
            d = diameter_in(s) or c["diameter"]
            args = {"diameter": d, "x": c["x"], "y": c["y"], "face_id": pl["face_id"]}
            if depth:
                args["depth"] = depth
            r = call("add_hole", args)
            return self._result(r, f"{rid} bölgesinin merkezine Ø{d:g} mm delik açıldı ({r.get('feature_id')}).")
        if re.search(r"kaz[ıi]", s) and re.search(r"çevre|çerçeve|kenar|sınır|kontur", s) and sel.get("outline_xy"):
            args = {"paths": [sel["outline_xy"]], "closed": [True]}
            if depth:
                args["depth"] = depth
            r = call("add_engraving_path", args)
            return self._result(r, f"{rid} bölgesinin çevresi kazındı ({r.get('feature_id')}).")
        return None

    FEATURE_WORDS = (("derinli", "depth"), ("kanal", "slot"), ("cep", "pocket"), ("kontur", "contour"), ("dış", "contour"),
                     ("matkap", "drilled_hole"), ("delik", "bore"))

    def _shop(self, s: str, call, ws) -> str | None:
        """Atölye kalıpları: satır sorgusu, alarm, prove-out listesi, ölçü düzeltme, program farkı."""
        alarm = re.search(r"\b(error\s*:?\s*\d+|alarm\s*:?\s*\d+|ps\s*0*\d+|ot\s*0*\d+|sv\s*0*\d+|sp\s*0*\d+)\b", s)
        if alarm and re.search(r"alarm|hata|error|verdi|çıktı|ne demek", s):
            orig = re.search(re.escape(alarm.group(1)), getattr(self, "_orig", ""), re.I)
            code = (orig.group(0) if orig else alarm.group(1)).replace(" ", "")
            ln = find(r"(?:satır|n)\s*(\d+)", s.replace(alarm.group(0), " "))
            args: dict[str, Any] = {"code": code}
            if ln:
                args["line"] = int(ln)
            r = call("explain_alarm", args)
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            if not r["found"]:
                other = "".join(f"\nBu kod {o['controller']} tablosunda var: {o['code']} — {o['title']}." for o in r.get("found_in_other", []))
                return f"⚠️ {r['note']}{other}"
            a = r["alarm"]
            src = (r.get("source") or {}).get("title", "")
            out = [f"**{a['code']} — {a['title']}**", a.get("meaning_tr") or a["description_en"]]
            out += [f"• {x}" for x in a.get("actions", [])]
            if r.get("matching_issues"):
                out.append("Doğrulayıcı bu satırda da şunu bulmuştu: " + "; ".join(i["message"] for i in r["matching_issues"]))
            if r.get("line_state"):
                out.append(r["line_state"]["summary"])
            out.append(f"Kaynak: {src}")
            return "\n".join(out)
        if re.search(r"kontrol listesi|prove[\s-]?out|ilk deneme|ilk çalıştırma", s):
            r = call("prove_out_checklist", {})
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            out = ["**Hazırlık**"] + [f"• {p}" for p in r["preparation"]] + ["**Dikkat edilecek satırlar**"]
            out += [f"• Satır {i['line']}: {i['message']} → {i['action']}" for i in r["items"] if i["severity"] != "info"][:20]
            return "\n".join(out)
        m = re.search(r"(?:\bn\s*|satır\s*|\b)(\d+)\s*(?:\.|'?(?:inci|ıncı|nci|ncı|üncü|uncu))?\s*(?:satır\w*)?\s*(?:ne yap|ne olur|ne olacak|açıkla|neden)", s)
        if m and re.search(r"satır|\bn\s*\d", s):
            n = int(m.group(1))
            from ..cam.shop import find_n_line
            rec = ws.gcodes.get(ws.current_gcode or "")
            line = find_n_line(rec["text"], n) if rec and re.search(r"\bn\s*\d", s) else None
            r = call("get_line_state", {"line": line or n})
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            return r["summary"]
        if re.search(r"çıktı|ölçtüm|ölçüldü|ölçtük", s) and re.search(r"olmal|olması gerek|nominal|resim", s):
            nums = [num(x) for x in re.findall(N, s)]
            feat = next((f for w, f in self.FEATURE_WORDS if w in s), None)
            if feat and len(nums) >= 2:
                r = call("suggest_wear_offset", {"feature": feat, "nominal": nums[0], "measured": nums[1]})
                if r.get("ok") is False:
                    return "⚠️ " + r["error"]
                return f"Sapma {r['error_mm']:+.3f} mm. {r['message']}" + (f"\n⚠️ {r['warning']}" if r.get("warning") else "")
        if re.search(r"fark|karşılaştır", s) and re.search(r"program|g[\s-]?kod|makinede", s):
            r = call("diff_programs", {})
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            c = r["counts"]
            out = [f"{r['change_count']} değişiklik: {c['error']} kritik, {c['warning']} dikkat, {c['info']} bilgi."]
            for ch in [x for x in r["changes"] if x["severity"] != "info"][:10]:
                out.append(f"• Satır {ch['edited_line'] or ch['base_line']}: " + "; ".join(ch["risks"]))
            out.append(r["note"])
            return "\n".join(out)
        return None

    def _core(self, s: str, sel: dict | None, call, ws) -> str | None:
        if "geri al" in s:
            r = call("undo", {})
            return self._result(r, f"Geri alındı: {r.get('undone')}")
        r = self._shop(s, call, ws)
        if r:
            return r
        if sel and sel.get("kind") == "region":
            r = self._region(s, sel, call)
            if r:
                return r
        if re.search(r"kaz[ıi]|gravür", s) or (re.search(r"\byaz\b", s) and QUOTED.search(getattr(self, "_orig", ""))):
            r = self._engrave(s, sel, call, ws)
            if r:
                return r
        if re.search(r"g[\s-]?kod|gcode|g-code", s):
            if re.search(r"doğrula|kontrol|denetle", s):
                r = call("validate_gcode", {})
                if r.get("ok") is False:
                    return "⚠️ " + r["error"]
                c = r["counts"]
                lines = [f"Doğrulama ({r['profile']['name']}): {c['error']} hata, {c['warning']} uyarı, {c['info']} bilgi."]
                lines += [f"• Satır {i['line'] or '-'}: {i['message']}" for i in r["issues"][:8]]
                return "\n".join(lines)
            ops = [o for o, k in (("drill", r"del(me|ik)"), ("pocket", r"cep"), ("contour", r"kontur")) if re.search(k, s)]
            r = call("generate_gcode", {"operations": ops} if ops else {})
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            v = r["validation_summary"]
            plan = ", ".join(f"{p['op']}({p['feature']}, T{p['tool']})" for p in r["plan"])
            msg = f"G-code üretildi — {r['profile']['name']}, post: {r['post']} ({r['line_count']} satır, tahmini süre {v['estimated_time_s']:.0f} sn). Plan: {plan}. Doğrulama: {v['counts']['error']} hata, {v['counts']['warning']} uyarı."
            if r["warnings"]:
                msg += "\nNotlar:\n" + "\n".join("• " + w for w in r["warnings"])
            return msg
        if "step" in s and re.search(r"indir|dışa|aktar|kaydet|export", s):
            return self._result(call("export_step", {}), "STEP dosyası hazır; sağ üstteki 'STEP indir' düğmesini kullanın.")
        if re.search(r"bilgi|özet|listele", s):
            r = call("get_geometry_info", {})
            if r.get("ok") is False:
                return "⚠️ " + r["error"]
            holes = ", ".join(f"{h['id']} Ø{h['diameter']:g}" for h in r["holes"]) or "yok"
            return f"{r['name']}: {' x '.join(f'{v:g}' for v in r['bbox']['size'])} mm, hacim {r['volume_mm3']:.0f} mm³. Delikler: {holes}. Özellik sayısı: {len(r['features'])}."

        hole_ref = re.search(r"\b(h\d+|hole_\d+)\b", s)
        sel_hole = sel.get("hole_id") if sel else None

        if "delik" in s or "deliğ" in s:
            if re.search(r"\bsil|kaldır", s):
                hid = hole_ref.group(1) if hole_ref else sel_hole
                if not hid:
                    return "Hangi deliği sileyim? Bir delik yüzeyi seçin veya kimliğini (ör. hole_1) yazın."
                return self._result(call("delete_feature", {"feature_id": hid}), f"{hid} silindi.")
            if re.search(r"\byap\b|çıkar|değiştir|büyüt|küçült|olsun|getir|ayarla", s) and (sel_hole or hole_ref or "deliğin" in s):
                hid = hole_ref.group(1) if hole_ref else sel_hole
                if not hid and ws.model and len(ws.model.holes) == 1:
                    hid = ws.model.holes[0]["id"]
                if not hid and ws.model and ws.model.holes and "merkez" in s:
                    hid = min(ws.model.holes, key=lambda h: h["top_point"][0] ** 2 + h["top_point"][1] ** 2)["id"]
                if not hid:
                    return "Hangi deliği değiştireyim? 3D görünümde deliğin iç yüzeyine tıklayarak seçin veya kimliğini yazın."
                nums = re.findall(N, s.replace(hid, ""))
                if not nums:
                    return "Yeni ölçüyü belirtir misiniz?"
                v = num(nums[-1])
                d = 2 * v if re.search(r"yar[iı]çap", s) else v
                return self._result(call("modify_hole", {"hole_id": hid, "new_diameter": d}), f"{hid} deliğinin çapı {d:g} mm yapıldı.")
            d = diameter_in(s)
            if d is None:
                return "Deliğin çapını veya yarıçapını belirtir misiniz?"
            depth = find(r"derinli\w*\s*" + N, s) or find(N + r"\s*(?:mm)?\s*derin", s)
            x, y = xy_in(s)
            args: dict[str, Any] = {"diameter": d, "x": x, "y": y}
            if depth:
                args["depth"] = depth
            if sel and sel.get("surface") == "plane" and re.search(r"seçt|seçili|bu yüzey", s):
                args["face_id"] = sel["face_id"]
            r = call("add_hole", args)
            kind = f"{depth:g} mm derinliğinde kör" if depth else "boydan boya"
            return self._result(r, f"({x:g}, {y:g}) konumuna Ø{d:g} mm {kind} delik açıldı ({r.get('feature_id')}).")

        m = DIM3.search(s)
        if m and "cep" in s:
            L, W, D = (num(g) for g in m.groups())
            x, y = xy_in(s.replace(m.group(0), ""))
            rc = find(r"köşe\s*yar[iı]çap\w*\s*" + N, s) or 0
            args = {"length": L, "width": W, "depth": D, "x": x, "y": y, "corner_radius": rc}
            if sel and sel.get("surface") == "plane" and re.search(r"seçt|seçili|bu yüzey", s):
                args["face_id"] = sel["face_id"]
            r = call("add_pocket", args)
            return self._result(r, f"{L:g}x{W:g} mm, {D:g} mm derinliğinde cep açıldı ({r.get('feature_id')}).")
        if m:
            L, W, H = (num(g) for g in m.groups())
            r = call("create_box", {"length": L, "width": W, "height": H})
            return self._result(r, f"{L:g} x {W:g} x {H:g} mm plaka oluşturuldu.")
        if re.search(r"silindir|disk|\bmil\b", s):
            d = diameter_in(s)
            h = find(r"(?:yükseklik|boy|kalınlık)\w*\s*" + N, s) or find(N + r"\s*(?:mm)?\s*(?:yüksek|boy|kalın)", s)
            if d is None or h is None:
                return "Silindir için çap (veya yarıçap) ve yükseklik gerekli."
            return self._result(call("create_cylinder", {"radius": d / 2, "height": h}), f"Ø{d:g} x {h:g} mm silindir oluşturuldu.")
        if re.search(r"kavis|radyüs|fillet|yuvarla", s):
            v = find(N, s)
            if v is None:
                return "Kavis yarıçapını belirtir misiniz?"
            e = edges_in(s, "vertical")
            return self._result(call("add_fillet", {"radius": v, "edges": e}), f"{EDGE_TR[e]} kenarlara {v:g} mm kavis verildi.")
        if "pah" in s:
            v = find(N, s)
            if v is None:
                return "Pah ölçüsünü belirtir misiniz?"
            e = edges_in(s, "top")
            return self._result(call("add_chamfer", {"size": v, "edges": e}), f"{EDGE_TR[e]} kenarlara {v:g} mm pah kırıldı.")
        for word, key in (("kalınl", "height"), ("yüksekl", "height"), ("uzunl", "length"), ("genişl", "width")):
            if word in s:
                v = find(N, s.split(word, 1)[1]) or find(N, s)
                if v is not None:
                    return self._result(call("modify_base", {key: v}), f"{key} {v:g} mm yapıldı.")
        return None
