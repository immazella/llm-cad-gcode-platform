"""FreyaTTS (https://github.com/freyavoiceai/FreyaTTS) için ince bir sarmalayıcı.

FreyaTTS, 183M parametreli, yalnızca Türkçe destekleyen bir TTS modelidir.
Çıktı 48 kHz float32 dalga formudur. Konuşmacı (ses) seçimi `seed` ile yapılır;
varsayılan seed kanonik "Leyla" sesini verir.

Kurulum (bir kez):
    git clone --depth 1 https://github.com/freyavoiceai/FreyaTTS.git third_party/FreyaTTS
    .venv/bin/pip install -r third_party/FreyaTTS/requirements.txt

Kullanım:
    python freya.py "Merhaba, size nasıl yardımcı olabilirim?" -o merhaba.wav
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

_REPO_DIR = Path(__file__).resolve().parent / "third_party" / "FreyaTTS"
if _REPO_DIR.is_dir() and str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

from freyatts import FreyaTTS  # noqa: E402
from freyatts.model import LEYLA_SEED  # noqa: E402

DEFAULT_MODEL = "freyavoice/freya-tts"
SAMPLE_RATE = 48000


def pick_device(preferred: str | None = None) -> str:
    """cuda > mps > cpu sırasıyla kullanılabilir cihazı seçer."""
    import torch

    if preferred:
        return preferred
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class FreyaSpeaker:
    """FreyaTTS modelini bir kez yükleyip tekrar tekrar ses üretmek için."""

    def __init__(self, model: str = DEFAULT_MODEL, device: str | None = None):
        self.device = pick_device(device)
        try:
            self.tts = FreyaTTS.from_pretrained(model, device=self.device)
        except Exception:
            if self.device == "mps":
                # Bazı operasyonlar MPS'te desteklenmeyebilir; CPU'ya düş.
                self.device = "cpu"
                self.tts = FreyaTTS.from_pretrained(model, device=self.device)
            else:
                raise

    def synthesize(self, text: str, steps: int = 32, seed: int | None = LEYLA_SEED) -> np.ndarray:
        """Türkçe metni seslendirir; 48 kHz float32 numpy dizisi döner."""
        return self.tts.synthesize(text, steps=steps, seed=LEYLA_SEED if seed is None else seed)

    def save(self, text: str, path: str | Path, steps: int = 32, seed: int | None = LEYLA_SEED) -> Path:
        """Metni seslendirip wav dosyası olarak kaydeder."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        wav = self.synthesize(text, steps=steps, seed=seed)
        self.tts.save_wav(wav, str(path))
        return path


def main() -> None:
    parser = argparse.ArgumentParser(description="FreyaTTS ile Türkçe metin seslendirme")
    parser.add_argument("text", nargs="?", default="Merhaba, size nasıl yardımcı olabilirim?")
    parser.add_argument("-o", "--out", default="output.wav", help="çıktı wav yolu (48 kHz)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="HF repo id veya yerel klasör")
    parser.add_argument("--device", default=None, help="cuda / mps / cpu (varsayılan: otomatik)")
    parser.add_argument("--steps", type=int, default=32, help="ODE adım sayısı")
    parser.add_argument("--seed", type=int, default=LEYLA_SEED, help="konuşmacı seed'i (varsayılan: Leyla)")
    args = parser.parse_args()

    speaker = FreyaSpeaker(args.model, args.device)
    t0 = time.perf_counter()
    out = speaker.save(args.text, args.out, steps=args.steps, seed=args.seed)
    elapsed = time.perf_counter() - t0

    import soundfile as sf

    duration = sf.info(str(out)).duration
    print(f"[{speaker.device}] {out} yazıldı: {duration:.2f} sn ses, {elapsed:.2f} sn sürdü")


if __name__ == "__main__":
    main()
