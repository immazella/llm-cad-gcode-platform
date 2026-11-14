"""Request-local language and explicit presentation helpers.

Canonical CAD/CAM records stay language independent. Render only newly produced
human-facing fields at API/tool boundaries; never walk arbitrary user dictionaries.
ContextVars propagate through Starlette/AnyIO's threadpool and are reset per request.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_locale: ContextVar[str] = ContextVar("response_language", default="tr")


def normalize_language(value: str | None) -> str:
    """Negotiate supported language ranges; unknown/malformed input falls back to tr."""
    candidates = []
    for position, part in enumerate((value or "").split(",")):
        pieces = part.strip().lower().split(";")
        language = pieces[0].split("-")[0]
        quality = 1.0
        try:
            for param in pieces[1:]:
                if param.strip().startswith("q="):
                    quality = float(param.strip()[2:])
        except ValueError:
            continue
        if language in ("en", "tr") and 0 < quality <= 1:
            candidates.append((quality, -position, language))
    return max(candidates)[2] if candidates else "tr"


def language() -> str:
    return _locale.get()


@contextmanager
def locale_scope(value: str | None):
    token = _locale.set(normalize_language(value))
    try:
        yield
    finally:
        _locale.reset(token)


def tr(turkish: str, english: str) -> str:
    return english if language() == "en" else turkish


# Only known application error messages are mapped, never arbitrary response text.
ERRORS = {
    "G-code bulunamadı.": "G-code not found.",
    "Aktif G-code yok. Önce G-code üretin veya yükleyin.": "No active G-code. Generate or upload G-code first.",
    "Sohbet bulunamadı.": "Conversation not found.",
    "Audit kaydı bulunamadı.": "Audit entry not found.",
    "Henüz aktif bir model yok. Önce bir model oluşturun veya STEP dosyası yükleyin.": "No active model. Create a model or upload a STEP file first.",
    "Doğrulanacak G-code yok. Önce G-code üretin veya yükleyin.": "No G-code to validate. Generate or upload G-code first.",
    "Yalnızca STEP (.step, .stp) dosyaları destekleniyor.": "Only STEP (.step, .stp) files are supported.",
    "SVG dosyası çok büyük (en fazla 2 MB).": "SVG file is too large (maximum 2 MB).",
    "Dosya çok büyük (en fazla 5 MB).": "File is too large (maximum 5 MB).",
    "Model sürümü değişti; seçim geçersiz.": "Model version changed; the selection is no longer valid.",
    "Model sürümü değişti; çizimi tekrar yapın.": "Model version changed; draw the region again.",
    "Tanımlı bir LLM API anahtarı yok; backend/.env dosyasına anahtar ekleyip backend'i yeniden başlatın.": "No LLM API key configured; add a key to backend/.env and restart the backend.",
    "Takım boyu negatif olamaz.": "Tool length cannot be negative.",
    "Son takım silinemez.": "The last tool cannot be deleted.",
    "closed listesi yol sayısıyla aynı uzunlukta olmalı.": "The closed list must have the same length as the paths list.",
    "tool_number veya diameter verin.": "Provide tool_number or diameter.",
    "work_offset üç değer olmalı: [X, Y, Z] (makine koordinatında).": "work_offset must contain three values: [X, Y, Z] (machine coordinates).",
    "Karşılaştırılacak program yok.": "No program to compare.",
    "Karşılaştırılacak üretilmiş (onaylı) bir program yok; base_id verin.": "No generated (approved) program to compare; provide base_id.",
}


ERRORS.update({
    "Kutu ölçüleri pozitif olmalı.": "Box dimensions must be positive.",
    "Silindir yarıçapı ve yüksekliği pozitif olmalı.": "Cylinder radius and height must be positive.",
    "Delik çapı pozitif olmalı.": "Hole diameter must be positive.",
    "Cep ölçüleri pozitif olmalı.": "Pocket dimensions must be positive.",
    "Köşe yarıçapı cebin kısa kenarının yarısından küçük olmalı.": "Corner radius must be less than half the shorter pocket side.",
    "edges parametresi vertical | top | bottom | all olmalı.": "edges must be vertical | top | bottom | all.",
    "Seçilen kenar grubu boş.": "The selected edge group is empty.",
    "Geri alınacak işlem yok.": "No operation to undo.",
    "Delik parçanın sınırlarını aşıyor veya başka bir özellikle kesişiyor; tam bir delik oluşmadı. Konumu/çapı kontrol edin.": "The hole exceeds the part boundary or intersects another feature; a complete hole was not created. Check its position/diameter.",
    "Verilen konum seçili yüzeyin sınırları dışında kalıyor.": "The specified position lies outside the selected face.",
    "Her yol en az iki nokta içermeli: [[x, y], [x, y], ...].": "Each path must contain at least two points: [[x, y], [x, y], ...].",
    "Kazıma yalnızca yukarı bakan yatay yüzeylere yapılabilir (üst yüzey veya cep tabanı).": "Engraving requires an upward-facing horizontal surface (top face or pocket floor).",
    "Kazıma yalnızca yukarı bakan yatay yüzeylere yapılabilir.": "Engraving requires an upward-facing horizontal surface.",
    "Kazınacak yazı boş.": "Engraving text is empty.",
    "Yazı yüksekliği pozitif olmalı.": "Text height must be positive.",
    "Kazınacak yol yok.": "No path to engrave.",
    "Kazıma çizgi genişliği pozitif olmalı.": "Engraving line width must be positive.",
    "Kazıma derinliği pozitif olmalı.": "Engraving depth must be positive.",
    "SVG'de kazınacak çizgi/şekil bulunamadı (yazı öğeleri desteklenmez; yazıyı yola çevirin).": "No engravable paths/shapes found in SVG (text elements are unsupported; convert text to paths).",
    "SVG geometrisinin boyutu sıfır.": "SVG geometry has zero size.",
    "Yüklenen STEP modelinin ana gövde ölçüleri değiştirilemez (yalnızca özellik eklenebilir/düzenlenebilir).": "The base dimensions of an imported STEP model cannot be changed (only features can be added/edited).",
    "Yan yüzeydeki deliğin konumu bu sürümde değiştirilemez; silip yeniden ekleyin.": "This version cannot reposition a side-face hole; delete and recreate it.",
    "Yalnızca dikey deliklerin konumu değiştirilebilir.": "Only vertical holes can be repositioned.",
    "Delik geometriden kaldırılamadı (defeaturing başarısız).": "The hole could not be removed from the geometry (defeaturing failed).",
    "Delik kaldırma geçersiz geometri üretti.": "Hole removal produced invalid geometry.",
    "Yalnızca üst yüzeydeki özelliklerin konumu değiştirilebilir.": "Only top-face features can be repositioned.",
    "STEP dosyasında katı geometri bulunamadı.": "No solid geometry found in the STEP file.",
    "STEP dosyasında katı (solid) yok; yalnızca yüzey/kabuk modelleri desteklenmiyor.": "The STEP file contains no solid; surface/shell-only models are unsupported.",
    "Bölge hesaplanamadı (kesişim işlemi başarısız).": "Could not compute the region (intersection failed).",
    "anchor_point gerekli.": "anchor_point is required.",
    "Çizimin başlangıç noktası seçilen yüzeyin üzerinde değil.": "The drawing start point is not on the selected face.",
    "Daire için center ve radius gerekli.": "A circle requires center and radius.",
    "Daire çok küçük (yarıçap < 0.05 mm).": "Circle too small (radius < 0.05 mm).",
    "Bölgenin üzerinde durduğu yüzey artık yok (model değişti).": "The region's supporting face no longer exists (model changed).",
    "Çizilen şekil çok küçük.": "The drawn shape is too small.",
    "Çizilen şekilden yüzey oluşturulamadı; daha sade bir şekil çizin.": "Could not create a face from the drawing; draw a simpler shape.",
    "Çizilen şekil kendini kesiyor veya çok küçük; daha sade bir şekil çizin.": "The shape self-intersects or is too small; draw a simpler shape.",
    "Şekil görüş açısına göre çok geniş.": "The shape is too wide for the viewing angle.",
    "Çizilen şekilden seçim hacmi oluşturulamadı; daha sade bir şekil çizin.": "Could not create a selection volume from the drawing; draw a simpler shape.",
    "Çizilen şekil kendini kesiyor; daha sade bir şekil çizin.": "The shape self-intersects; draw a simpler shape.",
    "Çizilen şekil seçilen yüzeyle kesişmiyor (yüzeyin görünen kısmında bir alan çizin).": "The shape does not intersect the selected face (draw on its visible portion).",
    "Makine kimliği küçük harf, rakam, _ veya - içermeli (2–48 karakter).": "Machine ID must contain lowercase letters, digits, _ or - (2–48 characters).",
    "Devir / ilerleme sınırları tutarsız.": "Spindle / feed limits are inconsistent.",
    "En az bir iş koordinat sistemi (G54…G59) seçilmeli.": "Select at least one work coordinate system (G54…G59).",
    "Hazır kütüphanedeki makineler silinemez.": "Built-in machines cannot be deleted.",
})

# Anchored application-owned templates. Captured identifiers/names are inserted
# verbatim and are never run through the catalog themselves.
ERROR_PATTERNS = (
    (r"Satır (.*) yok; program (.*) satır\.", "Line {0} does not exist; the program has {1} lines."),
    (r"'(.*)' kimlikli G-code yok\. Mevcut: (.*)", "G-code '{0}' not found. Available: {1}"),
    (r"Bilinmeyen araç: (.*)", "Unknown tool: {0}"),
    (r"Makine bulunamadı: (.*)", "Machine not found: {0}"),
    (r"Bilinmeyen makine profili: (.*)\. Mevcut: (.*)", "Unknown machine profile: {0}. Available: {1}"),
    (r"T(\d+) takım listesinde yok\. Mevcut: (.*)", "T{0} is not in the tool list. Available: {1}"),
    (r"T(\d+) takım listesinde yok\.", "T{0} is not in the tool list."),
    (r"Bilinmeyen malzeme '(.*)'\. Kullanılabilir: (.*)", "Unknown material '{0}'. Available: {1}"),
    (r"Bilinmeyen operasyon '(.*)'\. Kullanılabilir: (.*)", "Unknown operation '{0}'. Available: {1}"),
    (r"Geçersiz operasyon\(lar\): (.*)\. Kullanılabilir: (.*)", "Invalid operation(s): {0}. Available: {1}"),
    (r"G(.*) bu makinede tanımlı değil: (.*)", "G{0} is not supported by this machine: {1}"),
    (r"Geçersiz parametre: (.*)", "Invalid parameter: {0}"),
    (r"Geçersiz takım: (.*)", "Invalid tool: {0}"),
    (r"Geçersiz kurulum değeri: (.*)", "Invalid setup value: {0}"),
    (r"Geçersiz operasyon ayarı: (.*)", "Invalid operation setting: {0}"),
    (r"İşlem başarısız: (.*)", "Operation failed: {0}"),
    (r"'(.*)' kimlikli yüzey bulunamadı \(model sürümü değişmiş olabilir\)\.", "Face '{0}' not found (the model version may have changed)."),
    (r"'(.*)' kimlikli delik yok\. Mevcut delikler: (.*)", "Hole '{0}' not found. Available holes: {1}"),
    (r"'(.*)' kimlikli parametrik özellik yok\.", "Parametric feature '{0}' not found."),
    (r"'(.*)' kimlikli bölge yok\. Mevcut bölgeler: (.*)", "Region '{0}' not found. Available regions: {1}"),
    (r"(.*) bölgesi model değiştiği için geçersiz oldu ve kaldırıldı\.", "Region {0} became invalid after the model changed and was removed."),
    (r"'(.*)' özelliği uygulanamadı: (.*)", "Could not apply feature '{0}': {1}"),
    (r"'(.*)' özelliği geçersiz bir geometri üretti\.", "Feature '{0}' produced invalid geometry."),
    (r"Bilinmeyen taban tipi: (.*)", "Unknown base type: {0}"),
    (r"Bilinmeyen özellik tipi: (.*)", "Unknown feature type: {0}"),
    (r"Bilinmeyen kazıma kaynağı: (.*)", "Unknown engraving source: {0}"),
    (r"STEP dosyası okunamadı: (.*)", "Could not read STEP file: {0}"),
    (r"SVG okunamadı: (.*)", "Could not read SVG: {0}"),
    (r"Kontur font ile yazı oluşturulamadı: (.*)", "Could not create text with the contour font: {0}"),
    (r"Bu işlem için düzlemsel bir yüzey seçilmeli \(seçilen yüzey: (.*)\)\.", "Select a planar face for this operation (selected surface: {0})."),
    (r"\((.*), (.*)\) noktası parçanın üst yüzeyinin dışında kalıyor\.", "Point ({0}, {1}) is outside the part's top face."),
    (r"Şekil 3–(\d+) nokta içermeli\.", "The shape must contain 3–{0} points."),
    (r"Eğri yüzeyde seçim için 3–(\d+) ışın gerekli\.", "Selection on a curved face requires 3–{0} rays."),
    (r"'(.*)' hazır kütüphanede; kopyalayıp yeni bir kimlikle kaydedin\.", "'{0}' is built in; copy it and save with a new ID."),
    (r"(.*) kursu geçersiz \(min < max olmalı\)\.", "Invalid {0} travel (min must be less than max)."),
    (r"Soğutma '(.*)' için M kodu geçersiz: (.*) \(ör\. M8\)\.", "Invalid M code for coolant '{0}': {1} (for example M8)."),
)


def schema_errors(errors: list[dict]) -> list[dict]:
    """Preserve FastAPI error structure and inputs; translate only msg by error type."""
    if language() == "en":
        return errors
    messages = {
        "missing": "Bu alan zorunludur",
        "literal_error": "Değer şunlardan biri olmalıdır: {expected}",
        "string_type": "Metin girilmelidir", "bool_parsing": "Geçerli bir mantıksal değer girilmelidir",
        "int_parsing": "Geçerli bir tam sayı girilmelidir", "int_type": "Tam sayı girilmelidir",
        "float_parsing": "Geçerli bir sayı girilmelidir", "float_type": "Sayı girilmelidir",
        "greater_than": "Değer {gt} sayısından büyük olmalıdır",
        "greater_than_equal": "Değer en az {ge} olmalıdır",
        "less_than": "Değer {lt} sayısından küçük olmalıdır",
        "less_than_equal": "Değer en fazla {le} olmalıdır",
        "string_too_short": "Metin en az {min_length} karakter içermelidir",
        "string_too_long": "Metin en fazla {max_length} karakter içermelidir",
        "list_type": "Liste girilmelidir", "dict_type": "Nesne girilmelidir",
        "json_invalid": "Geçersiz JSON", "model_attributes_type": "Alanları içeren bir nesne girilmelidir",
    }
    result = []
    for error in errors:
        entry = dict(error)
        template = messages.get(entry["type"])
        if template:
            entry["msg"] = template.format(**entry.get("ctx", {}))
        result.append(entry)
    return result


def error_message(message: str) -> str:
    if language() != "en":
        return message
    if message in ERRORS:
        return ERRORS[message]
    import re
    for pattern, template in ERROR_PATTERNS:
        match = re.fullmatch(pattern, message, flags=re.DOTALL)
        if match:
            return template.format(*match.groups())
    # Third-party technical diagnostics are kept verbatim, not mistranslated.
    return message
