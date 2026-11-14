"""LLM'ye sunulan araçların (tool) JSON şemaları. Sağlayıcıdan bağımsız biçim."""
from __future__ import annotations

NUM = {"type": "number"}
EDGES = {"type": "string", "enum": ["vertical", "top", "bottom", "all"],
         "description": "vertical: dış dikey köşeler, top: üst yüzey kenarları, bottom: alt yüzey kenarları, all: tümü"}

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "create_box",
        "description": "Yeni dikdörtgen prizma/plaka oluşturur ve aktif model yapar (önceki model kapanır). XY'de merkezli, alt yüzey Z=0.",
        "parameters": {"type": "object", "properties": {
            "length": {**NUM, "description": "X yönü uzunluk (mm)"},
            "width": {**NUM, "description": "Y yönü genişlik (mm)"},
            "height": {**NUM, "description": "Z yönü kalınlık (mm)"},
            "name": {"type": "string"}}, "required": ["length", "width", "height"]},
    },
    {
        "name": "create_cylinder",
        "description": "Yeni silindir (disk/mil) oluşturur ve aktif model yapar. Ekseni Z, alt yüzey Z=0.",
        "parameters": {"type": "object", "properties": {
            "radius": {**NUM, "description": "Yarıçap (mm). Kullanıcı çap verdiyse 2'ye böl."},
            "height": {**NUM, "description": "Yükseklik (mm)"},
            "name": {"type": "string"}}, "required": ["radius", "height"]},
    },
    {
        "name": "add_hole",
        "description": ("Deliği ekler. face_id verilmezse üst yüzeyden dikey delik açılır ve x,y parça koordinatıdır (parça merkezi 0,0). "
                        "face_id yatay bir yüzeyse x,y yine parça koordinatıdır; dikey (yan) bir yüzeyse x yüzeyin yatay ekseni, y dikey ekseni boyunca "
                        "yüzey merkezine göre ofsettir. depth verilmezse boydan boya (through) delik."),
        "parameters": {"type": "object", "properties": {
            "diameter": {**NUM, "description": "Delik ÇAPI (mm). Kullanıcı yarıçap verdiyse 2 ile çarp."},
            "x": NUM, "y": NUM,
            "depth": {**NUM, "description": "Kör delik derinliği (mm). Boydan boya için verme."},
            "face_id": {"type": "string", "description": "Deliğin açılacağı düzlemsel yüzey (ör. f5)"}}, "required": ["diameter"]},
    },
    {
        "name": "add_pocket",
        "description": "Dikdörtgen cep ekler (üst yüzeyden veya seçili düzlemsel yüzeyden içeri). x,y cep merkezidir (add_hole ile aynı koordinat kuralı).",
        "parameters": {"type": "object", "properties": {
            "length": {**NUM, "description": "X yönü (veya yüzeyin yatay ekseni) boyunca uzunluk"},
            "width": NUM, "depth": NUM, "x": NUM, "y": NUM,
            "corner_radius": {**NUM, "description": "İç köşe yarıçapı; CNC için en az takım yarıçapı (varsayılan freze Ø6 → 3 mm) önerilir"},
            "face_id": {"type": "string"}}, "required": ["length", "width", "depth"]},
    },
    {
        "name": "add_engraving_text",
        "description": ("Parça yüzeyine YAZI kazır (gravür, çizgi takip). x,y yazının merkezi (parça koordinatı); face_id verilmezse üst yüzey, "
                        "verilirse yukarı bakan yatay bir yüzey (ör. cep tabanı). Türkçe karakterler desteklenir. font: tek_cizgi (varsayılan; harflerin "
                        "orta çizgisi kazınır, gravür için standart) veya kontur (TrueType harf dış hatları; büyük yazılar için). Yazı yüzeye sığmazsa "
                        "veya bir deliğe taşarsa hata döner."),
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
            "height": {**NUM, "description": "Büyük harf yüksekliği (mm), varsayılan 6"},
            "x": NUM, "y": NUM,
            "depth": {**NUM, "description": "Kazıma derinliği (mm), varsayılan 0.3"},
            "width": {**NUM, "description": "Çizgi genişliği (mm); verilmezse listedeki V-uçla bu derinlikte oluşan genişlik"},
            "rotation": {**NUM, "description": "Derece, saat yönü tersi"},
            "font": {"type": "string", "enum": ["tek_cizgi", "kontur"]},
            "face_id": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "add_engraving_path",
        "description": ("Serbest çizgi/şekil kazır: takım verilen noktaları sırayla izler. paths: bir veya birden çok yol, her biri [[x,y],...] "
                        "(parça koordinatı, mm). closed=true şekli kapatır (son nokta ilkine bağlanır). Yay/daire için yeterli sıklıkta nokta ver "
                        "(ör. her 5-10 derecede bir). Logo (SVG) yalnızca arayüzdeki 'SVG logo' yüklemesiyle eklenir."),
        "parameters": {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "array", "items": {"type": "array", "items": NUM, "minItems": 2, "maxItems": 2}}},
            "closed": {"type": "boolean", "description": "true: her yol kapalı şekil (son nokta ilkine bağlanır). Karışık için ayrı çağrılar yap."},
            "depth": NUM, "width": NUM, "face_id": {"type": "string"}}, "required": ["paths"]},
    },
    {
        "name": "add_fillet",
        "description": "Kenarlara kavis (radyüs) uygular.",
        "parameters": {"type": "object", "properties": {"radius": NUM, "edges": EDGES}, "required": ["radius", "edges"]},
    },
    {
        "name": "add_chamfer",
        "description": "Kenarlara pah kırar.",
        "parameters": {"type": "object", "properties": {"size": NUM, "edges": EDGES}, "required": ["size", "edges"]},
    },
    {
        "name": "modify_base",
        "description": "Parametrik modelin ana gövde ölçülerini değiştirir (kutu: length/width/height, silindir: radius/height). Üst yüzeydeki özellikler yeni yüzeye taşınır.",
        "parameters": {"type": "object", "properties": {"length": NUM, "width": NUM, "height": NUM, "radius": NUM}},
    },
    {
        "name": "modify_hole",
        "description": ("Mevcut bir deliği düzenler (çap, konum, derinlik). hole_id get_holes / get_geometry_info sonucundan veya seçili yüzey bilgisinden alınır. "
                        "STEP'ten tanınan deliklerde (h1, h2...) delik geometriden kaldırılıp yeniden açılır."),
        "parameters": {"type": "object", "properties": {
            "hole_id": {"type": "string"},
            "new_diameter": {**NUM, "description": "Yeni ÇAP (mm). Kullanıcı yarıçap verdiyse 2 ile çarp."},
            "x": NUM, "y": NUM, "depth": NUM,
            "through": {"type": "boolean", "description": "true: boydan boya yap"}}, "required": ["hole_id"]},
    },
    {
        "name": "modify_feature",
        "description": ("Parametrik bir özelliğin parametrelerini değiştirir. pocket: length,width,depth,corner_radius,x,y; fillet: radius,edges; "
                        "chamfer: size,edges; hole: diameter,depth,x,y; engrave (yazı): text,height,font,x,y,rotation,depth,width; "
                        "engrave (SVG): size,x,y,rotation,depth,width; engrave (yol): x,y,rotation,depth,width (x,y yolun merkezini taşır)."),
        "parameters": {"type": "object", "properties": {
            "feature_id": {"type": "string"},
            "params": {"type": "object", "description": "Değiştirilecek parametreler, ör. {\"depth\": 5}"}}, "required": ["feature_id", "params"]},
    },
    {
        "name": "delete_feature",
        "description": "Bir özelliği (hole_1, pocket_1, fillet_1...) veya tanınan deliği (h1) siler.",
        "parameters": {"type": "object", "properties": {"feature_id": {"type": "string"}}, "required": ["feature_id"]},
    },
    {
        "name": "get_geometry_info",
        "description": "Aktif modelin özetini döndürür: ölçüler, hacim, özellik ağacı, tanınan delikler ve cepler.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_holes",
        "description": "Modeldeki tüm delikleri (kimlik, çap, derinlik, konum) listeler.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_face_info",
        "description": "Bir yüzeyin geometrik bilgisini ve ait olduğu özelliği (delik/cep) döndürür.",
        "parameters": {"type": "object", "properties": {"face_id": {"type": "string"}}, "required": ["face_id"]},
    },
    {
        "name": "undo",
        "description": "Son model değişikliğini geri alır.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "export_step",
        "description": "Aktif modeli STEP dosyası olarak dışa aktarır ve indirme bağlantısı verir.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "generate_gcode",
        "description": ("Aktif model için SEÇİLİ MAKİNE PROFİLİNE uygun 3 eksen freze G-code'u üretir (deterministik takım yolu + "
                        "profilin post-processor'ı: Fanuc / LinuxCNC / GRBL). Kurulum (malzeme, ham parça payları, iş sıfırı, G54–G59) ve operasyon "
                        "ayarları (takım, preset, rampa giriş, son paso, bırakılan pay, delme döngüsü) aktif ayarlardan gelir. Kesme verisi otomatik modda "
                        "takım presetinden / malzeme tablosundan hesaplanır; burada verilen devir/ilerleme yalnızca bu üretim için tüm freze operasyonlarına uygulanır. "
                        "Devir ve ilerleme makine sınırlarına göre otomatik düşürülür. Operasyonlar: face (ham parça üst payı varsa alın frezeleme), "
                        "drill (uygun matkapla G81/G83), helical (matkap yoksa freze ile helisel delik), pocket (cep boşaltma), engrave (kazıma), "
                        "contour (dış kontur kesimi). Planın her satırında kullanılan devir/ilerleme ve kaynağı döner. "
                        "Üretim sonrası otomatik doğrulama yapılır."),
        "parameters": {"type": "object", "properties": {
            "operations": {"type": "array", "items": {"type": "string", "enum": ["face", "drill", "helical", "pocket", "engrave", "contour"]}},
            "mill_tool_diameter": {**NUM, "description": "Parmak freze çapı (mm)"},
            "spindle_rpm": NUM, "feed_rate": {**NUM, "description": "mm/dk"}, "plunge_rate": NUM,
            "depth_per_pass": {**NUM, "description": "Kademe derinliği (mm)"},
            "material": {"type": "string", "description": "Malzeme kimliği (list_materials), yalnızca bu üretim için"}}},
    },
    {
        "name": "validate_gcode",
        "description": ("Mevcut (veya verilen kimlikteki) G-code'u seçili makine profiline göre deterministik kurallarla doğrular. "
                        "Seviye 1: sözdizimi ve kontrol ünitesinin desteklemediği kodlar; Seviye 2: makine koordinatında eksen kursu, "
                        "devir/ilerleme sınırları, takım değiştirici, G43; Seviye 3: malzeme içinde G0, iş mili kapalı kesme gibi basit güvenlik kuralları. "
                        "Her bulgu kod, seviye, satır ve yapılandırılmış data içerir."),
        "parameters": {"type": "object", "properties": {"gcode_id": {"type": "string"}}},
    },
    {
        "name": "set_tool_length",
        "description": ("Bir takımın boyunu (iş mili burnundan takım ucuna, mm) ayarlar ve mevcut G-code'u yeniden doğrular. "
                        "G43 H<no> aktifken bu boy makine Z konumuna eklenir. length=null boyu 'bilinmiyor' yapar."),
        "parameters": {"type": "object", "properties": {
            "tool_number": {"type": "integer", "description": "T numarası (H numarasıyla aynı)"},
            "length": {**NUM, "description": "mm; bilinmiyorsa null"}}, "required": ["tool_number", "length"]},
    },
    {
        "name": "list_machine_profiles",
        "description": "Tanımlı CNC makine profillerini (kontrol ünitesi, eksen kursları, devir/ilerleme sınırları, takım değiştirici, delme döngüsü desteği) ve aktif profili listeler.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "set_machine_profile",
        "description": ("Aktif makine profilini değiştirir ve mevcut G-code'u bu makineye göre yeniden doğrular. "
                        "work_offset verilirse iş sıfırının (G54) makine koordinatındaki yeri de değişir; verilmezse profilin varsayılanı kullanılır. "
                        "Üretilmiş bir program başka bir makine için yazılmışsa sonrasında generate_gcode ile yeniden üretmek gerekir."),
        "parameters": {"type": "object", "properties": {
            "profile_id": {"type": "string", "description": "Ör. fanuc_vmc, linuxcnc_mill, grbl_router"},
            "work_offset": {"type": "array", "items": NUM, "description": "[X, Y, Z] makine koordinatında G54 konumu (mm)"}},
            "required": ["profile_id"]},
    },
    # ---------------------------------------------------------------- takım kütüphanesi, kurulum, operasyonlar
    {
        "name": "list_materials",
        "description": "İş parçası malzeme kütüphanesini (ISO grubu, kesme hızı) ve aktif malzemeyi listeler.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "recommend_cutting",
        "description": ("Takım + malzeme için kesme verisi önerir: n = 1000·Vc/(π·D), F = n·z·fz; devir, ilerleme, dalma, kademe derinliği, yan paso "
                        "(matkapta gaga). Seçili makinenin devir/ilerleme sınırlarını uygular. Değerler genel başlangıç değerleridir; "
                        "cevapta formülü ve takım kataloğunun öncelikli olduğunu belirt. Sayıları bu sonuçtan al."),
        "parameters": {"type": "object", "properties": {
            "tool_number": {"type": "integer", "description": "Takım listesindeki T numarası (verilirse tip/çap/ağız oradan alınır)"},
            "tool_type": {"type": "string", "enum": ["endmill", "bullmill", "ballmill", "drill", "spot_drill", "chamfer", "vbit"]},
            "diameter": NUM, "flutes": {"type": "integer"},
            "tool_material": {"type": "string", "enum": ["carbide", "hss"]},
            "material": {"type": "string", "description": "Malzeme kimliği; verilmezse kurulumdaki malzeme"}}},
    },
    {
        "name": "upsert_tool",
        "description": ("Takım kütüphanesine takım ekler veya var olanı günceller (yalnızca verilen alanlar değişir). Geometri: diameter, flutes, "
                        "flute_length (ağız boyu), stickout, corner_radius, angle (V-uç/matkap), tool_material carbide|hss, length (takım boyu), "
                        "length_offset (H no), holder. presets: [{name, material, vc, fz, rpm, feed, plunge, stepdown, stepover_ratio}]."),
        "parameters": {"type": "object", "properties": {
            "number": {"type": "integer"},
            "type": {"type": "string", "enum": ["endmill", "bullmill", "ballmill", "drill", "spot_drill", "chamfer", "vbit"]},
            "diameter": NUM, "description": {"type": "string"}, "flutes": {"type": "integer"}, "flute_length": NUM, "stickout": NUM,
            "corner_radius": NUM, "angle": NUM, "tool_material": {"type": "string", "enum": ["carbide", "hss"]}, "length": NUM,
            "length_offset": {"type": "integer"}, "holder": {"type": "string"},
            "presets": {"type": "array", "items": {"type": "object"}}}, "required": ["number"]},
    },
    {
        "name": "delete_tool",
        "description": "Takımı kütüphaneden siler (onu kullanan operasyonlar otomatik seçime döner).",
        "parameters": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]},
    },
    {
        "name": "set_setup",
        "description": ("Kurulumu değiştirir: material (malzeme kimliği), origin_xy (iş sıfırı XY: center | front_left | front_right | back_left | "
                        "back_right; Z0 her zaman ham parça üst yüzeyi), wcs (54–59 → G54…G59), stock_side (ham parça yan payı mm), "
                        "stock_top (üst pay mm; >0 ise alın frezeleme eklenir), cutting_mode (auto: preset/malzeme tablosu, manual: elle değerler), "
                        "coolant (off | flood | mist | air). Sonra G-code'u yeniden üretmek gerekir."),
        "parameters": {"type": "object", "properties": {
            "material": {"type": "string"},
            "origin_xy": {"type": "string", "enum": ["center", "front_left", "front_right", "back_left", "back_right"]},
            "wcs": {"type": "integer", "minimum": 54, "maximum": 59}, "stock_side": NUM, "stock_top": NUM,
            "cutting_mode": {"type": "string", "enum": ["auto", "manual"]},
            "coolant": {"type": "string", "enum": ["off", "flood", "mist", "air"]}}},
    },
    {
        "name": "set_operation",
        "description": ("Bir operasyon türünün ayarını değiştirir (yalnızca verilen alanlar): enabled, tool (T no; null → otomatik), preset (takım preseti adı), "
                        "rpm/feed/plunge/stepdown/stepover_ratio (elle üzerine yazma), stock_xy/stock_z (bırakılacak pay), finish_pass + finish_allowance "
                        "(son paso), entry plunge|ramp + ramp_angle, cycle auto|G81|G83 + peck (delme). clear: varsayılana döndürülecek alan adları."),
        "parameters": {"type": "object", "properties": {
            "op": {"type": "string", "enum": ["face", "drill", "helical", "pocket", "engrave", "contour"]},
            "enabled": {"type": "boolean"}, "tool": {"type": "integer"}, "preset": {"type": "string"},
            "rpm": NUM, "feed": NUM, "plunge": NUM, "stepdown": NUM, "stepover_ratio": NUM,
            "stock_xy": NUM, "stock_z": NUM, "finish_pass": {"type": "boolean"}, "finish_allowance": NUM,
            "entry": {"type": "string", "enum": ["plunge", "ramp"]}, "ramp_angle": NUM,
            "cycle": {"type": "string", "enum": ["auto", "G81", "G83"]}, "peck": NUM,
            "clear": {"type": "array", "items": {"type": "string"}}}, "required": ["op"]},
    },
    # ---------------------------------------------------------------- atölye (prove-out desteği; hepsi salt okur)
    {
        "name": "get_line_state",
        "description": ("ATÖLYE: G-code'un bir satırı çalışmadan önceki ve sonraki makine durumunu deterministik olarak hesaplar: aktif takım, "
                        "modal kodlar, iş mili/soğutma, hareketin türü, iş ve makine koordinatında bitiş noktası, parçaya mesafe, malzemeye "
                        "girip girmediği, o satırdaki doğrulama bulguları ve uyarılar. 'N245 ne yapacak?', 'bu satırda ne olur?' sorularında kullan. "
                        "Cevabında sayıları bu sonuçtan al, kendin hesaplama."),
        "parameters": {"type": "object", "properties": {
            "line": {"type": "integer", "description": "1 tabanlı satır numarası (dosyadaki satır; N numarası değil)"},
            "gcode_id": {"type": "string"}}, "required": ["line"]},
    },
    {
        "name": "prove_out_checklist",
        "description": ("ATÖLYE: Programın makinede ilk denenmesi (prove-out) için kontrol listesi: hazırlık adımları, her takım değişimi, her takımın "
                        "parçaya ilk yaklaşması ve ilk kesişi, parçaya yakın hızlı hareketler ve doğrulayıcının uyarı verdiği satırlar; her biri için "
                        "önerilen override (rapid/feed) ile."),
        "parameters": {"type": "object", "properties": {"gcode_id": {"type": "string"}}},
    },
    {
        "name": "explain_alarm",
        "description": ("ATÖLYE: Makinenin verdiği alarm/hata kodunu aktif kontrol ünitesinin KAYNAKLI bilgi tabanından açıklar (GRBL resmî tablo, "
                        "Fanuc 0i bakım kılavuzu). line verilirse o satırın durumu ve ilgili doğrulama bulguları eklenir. found=false dönerse kodun "
                        "anlamını TAHMİN ETME; kılavuza bakılmasını söyle. Cevapta kaynağı belirt."),
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Ör. 'error:20', 'ALARM:2', 'PS0010', 'OT0501'"},
            "line": {"type": "integer"},
            "controller": {"type": "string", "enum": ["fanuc", "linuxcnc", "grbl"], "description": "Verilmezse aktif profilin kontrol ünitesi"}},
            "required": ["code"]},
    },
    {
        "name": "suggest_wear_offset",
        "description": ("ATÖLYE: Ölçülen parça sapmasından düzeltme önerir (makineye bir şey yazmaz). İç ölçüde (cep/kanal/frezelenmiş delik) "
                        "ve dış konturda yarıçap aşınması, derinlikte takım boyu aşınması hesaplar; program G41/G42 kullanmıyorsa CAM'de "
                        "etkin takım çapı düzeltmesi önerir. Matkapla delinmiş delikte ofset önermez."),
        "parameters": {"type": "object", "properties": {
            "feature": {"type": "string", "enum": ["pocket", "slot", "bore", "contour", "depth", "drilled_hole"]},
            "nominal": {**NUM, "description": "Resimdeki ölçü (mm)"},
            "measured": {**NUM, "description": "Ölçülen değer (mm)"},
            "tool_number": {"type": "integer", "description": "Verilmezse ayarlardaki freze"},
            "tolerance": {**NUM, "description": "± tolerans (mm), isteğe bağlı"}},
            "required": ["feature", "nominal", "measured"]},
    },
    {
        "name": "diff_programs",
        "description": ("ATÖLYE: Makinede değiştirilip geri yüklenen programı (edited_id) onaylı/üretilmiş sürümle (base_id) karşılaştırır; her değişikliği "
                        "riskine göre (ör. hızlı hareketin daha aşağı inmesi, G43/M3 silinmesi, ilerleme artışı) sınıflar. Verilmezse edited_id "
                        "aktif program, base_id ondan önceki en son üretilen programdır."),
        "parameters": {"type": "object", "properties": {"base_id": {"type": "string"}, "edited_id": {"type": "string"}}},
    },
]
