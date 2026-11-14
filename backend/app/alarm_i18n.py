"""Alarm presentation uses the existing English source, never guesses meanings."""
from .i18n import language

ACTIONS_EN = {
    "Alarmı sıfırla ($X veya homing), sonra programı yeniden başlat.": "Clear the alarm ($X or homing), then restart the program.",
    "Satırdaki G kodunu GRBL'nin desteklediği eşdeğerle değiştir (GRBL'de delme döngüsü ve M6 yoktur).": "Replace the G-code with a GRBL-supported equivalent (GRBL has no drilling cycles or M6).",
    "Programı GRBL profiliyle yeniden üret.": "Regenerate the program using the GRBL profile.",
    "İlk G1/G2/G3 satırından önce F değeri ver.": "Set F before the first G1/G2/G3 line.",
    "Yayın başlangıç, bitiş ve merkez değerlerini kontrol et.": "Check the arc start, end and center values.",
    "Limit anahtarını serbest bırak, makineyi yeniden homing yap.": "Release the limit switch and re-home the machine.",
    "Programın kursu aşan satırını bul (E040).": "Find the program line that exceeds travel (E040).",
    "Eksen kursu aşımı: iş sıfırını (G54) veya parçanın tabladaki yerini kontrol et; doğrulama raporundaki E040'a bak.": "Axis overtravel: check the work offset (G54) or part location on the table; see E040 in the validation report.",
    "$X ile alarmı kaldır.": "Clear the alarm with $X.",
    "Satırdaki sayıların basamak sayısını ve ondalık biçimini kontrol et.": "Check digit counts and decimal formatting on the line.",
    "Satırı G1 X10 F500 biçiminde yaz.": "Write the line in the form G1 X10 F500.",
    "Değeri eksik kelimeyi bul ve tamamla.": "Find the word with a missing value and complete it.",
    "Negatif olmaması gereken değeri (ör. F, S, T) düzelt.": "Correct the value that must not be negative (for example F, S, T).",
    "Örn. T, M, H, D, O adresleri tam sayı olmalı.": "For example, T, M, H, D and O addresses must be integers.",
    "Satırdaki harfleri kontrol et; makinenin tanımadığı harf var mı bak.": "Check the address letters for any unsupported by the machine.",
    "G kodunu makinenin desteklediği eşdeğerle değiştir veya programı bu makinenin profiliyle yeniden üret.": "Replace the G-code with a supported equivalent or regenerate with this machine's profile.",
    "İlk kesme hareketinden önce geçerli bir F değeri ver.": "Set a valid F before the first cutting move.",
    "Hareketi eksenlere bölerek ayrı satırlarda yaz.": "Split the axis moves onto separate lines.",
    "I/J/K veya R değerlerini ve yayın bitiş noktasını kontrol et.": "Check I/J/K or R and the arc endpoint.",
    "Yaydan önce doğru düzlemi seç (freze için genelde G17).": "Select the correct plane before the arc (usually G17 for milling).",
    "G2/G3 satırına R veya I/J ekle.": "Add R or I/J to the G2/G3 line.",
    "Ofset tablosundaki değeri kontrol et.": "Check the value in the offset table.",
    "H/D numarasının ofset tablosunda tanımlı olduğunu kontrol et.": "Check that the H/D number exists in the offset table.",
    "Programı değiştir: giriş/çıkış hareketlerini ve takım yarıçapını kontrol et.": "Revise the program: check entry/exit moves and cutter radius.",
    "Takım bu köşeye/kanala sığmıyor olabilir; daha küçük takım veya farklı yol kullan.": "The tool may not fit this corner/slot; use a smaller tool or a different path.",
    "Gereksiz programları sil veya programı satır satır besle (DNC).": "Delete unused programs or stream the program line by line (DNC).",
    "Program/blok numarasını kontrol et.": "Check the program/block number.",
    "O numarasını değiştir veya eski programı sil.": "Change the O number or delete the old program.",
    "İş sıfırını (G54), takım boyunu ve parçanın tabladaki yerini kontrol et; doğrulama raporundaki E040'a bak.": "Check the work offset (G54), tool length and part location; see E040 in the validation report.",
    "Ekseni elle limitten çıkar, sebebi bul; programın kursu aştığı satırı kontrol et.": "Move the axis off the limit manually, investigate the cause and check the program's overtravel line.",
    "Program kaynaklı değildir; bakım ekibine haber ver.": "This is not caused by the program; contact maintenance.",
}
NOTES_EN = {
    "grbl": "English descriptions are verbatim from the official table; Turkish meanings are translations.",
    "fanuc": "English descriptions are verbatim from the manual; Turkish meanings and actions are system summaries. Text can vary by series/software version; verify against the machine's own manual.",
    "linuxcnc": "LinuxCNC errors use text rather than numbers; they have not yet been added to this knowledge base.",
}
SOURCE_EN = {
    "grbl": "GRBL v1.1 official error and alarm code tables (doc/csv)",
    "fanuc": "FANUC Series 0i-D Maintenance Manual B-64305EN/03, Appendix A Alarm List",
}


def present_alarm(result: dict) -> dict:
    if language() != "en":
        return result
    out = dict(result)
    ctrl = result["controller"]
    out["kb_note"] = NOTES_EN.get(ctrl, "No alarm knowledge base for this controller.")
    if result.get("source") and ctrl in SOURCE_EN:
        out["source"] = {**result["source"], "title": SOURCE_EN[ctrl]}
    if result.get("found"):
        alarm = result["alarm"]
        # Keep legacy API keys; only their presentation values follow locale.
        out["alarm"] = {**alarm, "meaning_tr": alarm["description_en"],
                        "actions": [ACTIONS_EN.get(a, a) for a in alarm.get("actions", [])]}
    else:
        out["note"] = f"Code '{result['query']}' is not in the {ctrl} alarm knowledge base. Do not guess its meaning; consult the controller manual."
    return out
