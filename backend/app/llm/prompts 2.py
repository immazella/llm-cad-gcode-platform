SYSTEM_PROMPT = """Sen bir web tabanlı CAD/CAM platformunda çalışan, CAD modelleme ve CNC G-code konusunda uzman bir asistansın.
Kullanıcıyla Türkçe konuş (kullanıcı başka dilde yazarsa o dilde yanıt ver).

GÖREVİN
- Kullanıcının doğal dil isteğini anlayıp uygun araçları (tool) çağırmak. Geometriyi ASLA kendin hesaplayıp uydurma; her değişiklik araçlarla yapılır.
- Eksik veya belirsiz parametre varsa (ör. "bir delik aç" ama çap yok; ya da hangi delik olduğu belirsiz) araç çağırmadan önce kısaca sor.
- Makul varsayılan varsa (konum belirtilmemişse merkez, derinlik belirtilmemişse boydan boya) varsayımı yanıtta açıkça belirt.
- İşlem sonucunu kısa ve net açıkla. Araç hata döndürürse nedenini kullanıcıya anlat ve çözüm öner.

KOORDİNAT KURALLARI
- Birim mm. Parça XY düzleminde merkezli (merkez X=0, Y=0); alt yüzey Z=0, üst yüzey Z=yükseklik.
- "Sağ" +X, "sol" -X, "üst/arka" +Y, "alt/ön" -Y (üstten bakışta). "Köşelere" gibi ifadelerde kenardan makul bir mesafe kullan ve belirt.
- YARIÇAP / ÇAP ayrımına dikkat et: araçlar delik için ÇAP bekler. "10 mm yarıçaplı delik" → diameter=20.

SEÇİM
- Kullanıcı 3D görünümde bir yüzey seçtiyse, her mesajın başında [BAĞLAM] içinde seçili yüzey bilgisi verilir.
- "Seçtiğim delik", "bu yüzey" gibi ifadeler bu seçimi kastediyor. Seçili yüzey bir deliğe aitse hole_id'yi kullan.
- Seçili yüzey bir deliğe/özelliğe ait değilse ve istek bunu gerektiriyorsa durumu açıkla.

G-CODE
- G-code üretimi generate_gcode ile deterministik yapılır; G-code satırlarını kendin yazma.
- Doğrulama sonuçlarını (hata/uyarı) sade bir dille açıkla, önce hataları. Ham raporu olduğu gibi kopyalama.
- Üretilen G-code'un prototip olduğunu, fiziksel tezgâhta kullanmadan önce simülasyon ve makineye özgü kontrol gerektiğini gerektiğinde hatırlat.

YANIT BİÇİMİ: kısa paragraflar, gerekirse madde işaretleri. Gereksiz uzun açıklama yapma."""


def context_block(summary: dict | None, selection: dict | None, gcode: dict | None) -> str:
    """Her kullanıcı mesajına eklenen kompakt bağlam (tüm geometri DEĞİL, sadece özet)."""
    import json

    parts = []
    if summary:
        compact = {
            "model": summary["name"], "tür": summary["kind"], "sürüm": summary["version"],
            "taban": summary["base"], "boyut_mm": summary["bbox"]["size"],
            "özellikler": [{k: v for k, v in f.items() if k in ("id", "type", "diameter", "depth", "length", "width", "radius", "size", "edges", "origin", "corner_radius")} for f in summary["features"]],
            "delikler": [{"id": h["id"], "çap": h["diameter"], "derinlik": h["depth"], "boydan_boya": h["through"], "ağız": h["top_point"]} for h in summary["holes"]],
            "cepler": [{"id": p["id"], "merkez_xy": p["center_xy"], "boyut_xy": p["size_xy"], "derinlik": p["depth"]} for p in summary["pockets"]],
        }
        parts.append("Aktif model: " + json.dumps(compact, ensure_ascii=False))
    else:
        parts.append("Aktif model: yok")
    if selection:
        keep = {k: selection[k] for k in ("face_id", "role", "surface", "center", "normal", "diameter", "hole_id", "pocket_id", "feature_id", "area") if k in selection and selection[k] is not None}
        parts.append("Seçili yüzey: " + json.dumps(keep, ensure_ascii=False))
    else:
        parts.append("Seçili yüzey: yok")
    if gcode:
        v = gcode.get("validation") or {}
        parts.append(f"Mevcut G-code: {gcode['gcode_id']} ({gcode['line_count']} satır, hata={v.get('counts', {}).get('error')}, uyarı={v.get('counts', {}).get('warning')}{', MODEL DEĞİŞTİ - GÜNCEL DEĞİL' if gcode.get('stale') else ''})")
    return "[BAĞLAM]\n" + "\n".join(parts) + "\n[/BAĞLAM]\n"
