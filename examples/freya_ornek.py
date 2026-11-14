"""FreyaTTS örneği: birkaç cümleyi iki farklı sesle seslendirir.

Çalıştırma (proje kökünden):
    .venv/bin/python examples/freya_ornek.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from freya import FreyaSpeaker, LEYLA_SEED  # noqa: E402

OUT_DIR = ROOT / "examples" / "freya_ciktilar"

CUMLELER = {
    "selamlama": "Merhaba, size nasıl yardımcı olabilirim?",
    "cnc": "Freze işlemi 14:45 itibarıyla başladı. Kesme derinliği 3 milimetre, ilerleme hızı 1200.",
    "bilgi": "Parça başarıyla üretildi. Lütfen tezgahı kontrol edip bir sonraki işleme geçin.",
}


def main():
    speaker = FreyaSpeaker()  # cihazı otomatik seçer (cuda / mps / cpu)
    print(f"Cihaz: {speaker.device}")

    for ad, metin in CUMLELER.items():
        yol = speaker.save(metin, OUT_DIR / f"{ad}_leyla.wav", seed=LEYLA_SEED)
        print("yazıldı:", yol.relative_to(ROOT))

    # Farklı bir seed = farklı bir konuşmacı
    yol = speaker.save(CUMLELER["selamlama"], OUT_DIR / "selamlama_seed42.wav", seed=42)
    print("yazıldı:", yol.relative_to(ROOT))


if __name__ == "__main__":
    main()
