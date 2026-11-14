"""Makine profilleri (Machine Profile) ve iş bağlama düzeni (Setup).

Profil makineye aittir: kontrol ünitesi / diyalekt, desteklenen G/M kodları,
eksen kursları (MAKİNE koordinatında), hız ve devir sınırları, takım değiştirici.
Setup o anki bağlamaya aittir: iş sıfırının (G54) makine koordinatındaki yeri,
takım boyları.

Bir hareketin kurs sınırını aşıp aşmadığı şöyle hesaplanır:
    makine konumu = iş koordinatı + G54 ofseti (+ Z için takım boyu, G43 aktifse)
Böylece aynı program, parça tablada farklı bir yere bağlandığında farklı sonuç verebilir.

Profiller kodda değil `backend/profiles/*.json` dosyalarındadır; yeni makine eklemek
için yeni bir JSON dosyası yeterlidir.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "profiles"
DEFAULT_PROFILE_ID = "fanuc_vmc"

Controller = Literal["fanuc", "linuxcnc", "grbl"]


class AxisTravel(BaseModel):
    """Bir eksenin makine koordinatındaki kursu (mm)."""

    min: float
    max: float


class PostOptions(BaseModel):
    """Post işlemcinin makineye özel çıktı ayarları."""

    line_numbers: bool = False  # her blok N10, N20 … ile numaralansın mı
    line_increment: int = Field(default=10, ge=1, le=100)
    comments: bool = True  # yorum satırları yazılsın mı (bazı kontrol ünitelerinde bellek kazanmak için kapatılır)
    home_at_end: Literal["none", "G28", "G53"] = "none"  # program sonunda Z'yi referansa götür
    arc_format: Literal["ijk", "r"] = "ijk"  # yay merkezi I/J ile mi, yarıçap R ile mi


class MachineProfile(BaseModel):
    id: str
    name: str
    controller: Controller
    post: str = Field(description="Kullanılacak post-processor kimliği (fanuc | linuxcnc | grbl)")
    description: str = ""
    axes: int = 3
    travel: dict[str, AxisTravel]  # "X", "Y", "Z" → makine koordinatında kurs
    max_feed: float  # mm/dk, kesme ilerlemesi üst sınırı
    rapid_feed: float  # mm/dk, G0 hızı (süre tahmini için)
    max_spindle: float  # dev/dk
    min_spindle: float = 0.0  # dev/dk (router iş milleri belirli bir devrin altında dönmez)
    accel: dict[str, float] = Field(default_factory=dict)  # mm/s², ileride süre modeli için
    tool_changer: bool = True  # M6 ile otomatik takım değişimi var mı
    max_tools: int = 1
    tool_change_time_s: float = 0.0
    canned_cycles: bool = True  # G81/G83 gibi delme döngüleri
    requires_tool_length_comp: bool = False  # takım değişiminden sonra G43 H.. bekleniyor mu
    program_number: bool = False  # programın O numarasıyla başlaması gerekiyor mu (Fanuc)
    supported_g: list[float]
    supported_m: list[int]
    default_work_offset: tuple[float, float, float]  # G54'ün makine koordinatındaki varsayılan yeri
    notes: list[str] = Field(default_factory=list)
    # ---- makine bilgisi ve ek yetenekler (makine düzenleyicisi)
    manufacturer: str = ""
    machine_model: str = ""
    serial: str = ""
    spindle_power_kw: float | None = Field(default=None, gt=0)
    max_tool_diameter: float | None = Field(default=None, gt=0)
    max_tool_length: float | None = Field(default=None, gt=0)
    coolant_modes: dict[str, str] = Field(default_factory=lambda: {"flood": "M8"},
                                          description="soğutma türü → M kodu, ör. flood: M8, mist: M7")
    work_offsets: list[int] = Field(default_factory=lambda: [54, 55, 56, 57, 58, 59])
    post_options: PostOptions = Field(default_factory=PostOptions)
    builtin: bool = True  # hazır kütüphane (salt okunur) mı, kullanıcının makinesi mi

    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "controller": self.controller, "post": self.post}


class Setup(BaseModel):
    """Kurulum: ham parça, iş sıfırı ve parçanın tablada nereye bağlandığı.

    Takım boyları burada değil, her takımın kendi kaydındadır (config.Tool.length); böylece
    takım numarası değişse de boy takımla birlikte kalır.
    """

    work_offset: tuple[float, float, float] | None = Field(
        default=None, description="İş sıfırının makine koordinatındaki yeri (X, Y, Z). None → profilin varsayılanı.")
    wcs: int = Field(default=54, ge=54, le=59, description="İş koordinat sistemi: G54…G59")
    origin_xy: Literal["center", "front_left", "front_right", "back_left", "back_right"] = Field(
        default="center", description="İş sıfırının XY'deki yeri (ham parçaya göre). Z0 her zaman ham parçanın üst yüzeyidir.")
    material: str = Field(default="al6061", description="Ham parça malzemesi (materials.py kimliği)")
    stock_side: float = Field(default=0.0, ge=0, le=100, description="Ham parçanın parçadan her yanda taşması mm")
    stock_top: float = Field(default=0.0, ge=0, le=100, description="Ham parçanın üstte fazlası mm (alın frezelemeyle alınır)")

    def offset_for(self, profile: MachineProfile) -> tuple[float, float, float]:
        return tuple(self.work_offset) if self.work_offset is not None else tuple(profile.default_work_offset)  # type: ignore[return-value]


def custom_dir() -> Path:
    """Kullanıcının makineleri ("Makinelerim"). Hazır kütüphane (profiles/*.json) salt okunurdur."""
    import os

    d = Path(os.getenv("MACHINES_DIR") or PROFILE_DIR.parent / "data" / "machines")
    d.mkdir(parents=True, exist_ok=True)
    return d


@lru_cache(maxsize=1)
def _load_all() -> dict[str, MachineProfile]:
    out: dict[str, MachineProfile] = {}
    for path in sorted(PROFILE_DIR.glob("*.json")):
        p = MachineProfile.model_validate({**json.loads(path.read_text(encoding="utf-8")), "builtin": True})
        out[p.id] = p
    for path in sorted(custom_dir().glob("*.json")):
        try:
            p = MachineProfile.model_validate({**json.loads(path.read_text(encoding="utf-8")), "builtin": False})
        except Exception:  # bozuk kullanıcı dosyası tüm uygulamayı düşürmesin
            continue
        if p.id not in out:
            out[p.id] = p
    if DEFAULT_PROFILE_ID not in out:
        raise RuntimeError(f"Varsayılan makine profili bulunamadı: {PROFILE_DIR / (DEFAULT_PROFILE_ID + '.json')}")
    return out


def list_profiles() -> list[MachineProfile]:
    return list(_load_all().values())


def get_profile(profile_id: str | None = None) -> MachineProfile:
    profiles = _load_all()
    pid = profile_id or DEFAULT_PROFILE_ID
    if pid not in profiles:
        raise KeyError(f"Bilinmeyen makine profili: {pid}. Mevcut: {', '.join(profiles)}")
    return profiles[pid]


def g_supported(profile: MachineProfile, code: float) -> bool:
    return any(abs(code - s) < 1e-6 for s in profile.supported_g)


ID_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9_\-]{1,47}$")


def save_profile(data: dict) -> MachineProfile:
    """Kullanıcı makinesini kaydeder (yeni veya güncelleme). Hazır kütüphanedeki kimlikler değiştirilemez."""
    p = MachineProfile.model_validate({**data, "builtin": False})
    if not ID_RE.match(p.id):
        raise ValueError("Makine kimliği küçük harf, rakam, _ veya - içermeli (2–48 karakter).")
    existing = _load_all().get(p.id)
    if existing is not None and existing.builtin:
        raise ValueError(f"'{p.id}' hazır kütüphanede; kopyalayıp yeni bir kimlikle kaydedin.")
    for ax in ("X", "Y", "Z"):
        if ax not in p.travel or p.travel[ax].min >= p.travel[ax].max:
            raise ValueError(f"{ax} kursu geçersiz (min < max olmalı).")
    if p.min_spindle > p.max_spindle or p.max_feed <= 0 or p.rapid_feed <= 0:
        raise ValueError("Devir / ilerleme sınırları tutarsız.")
    if not p.work_offsets:
        raise ValueError("En az bir iş koordinat sistemi (G54…G59) seçilmeli.")
    # Soğutma M kodları ve iş koordinatları desteklenen kod listesinde olmalı (doğrulayıcı bunlara bakar)
    import re as _re

    ms = set(p.supported_m)
    for mode, code in p.coolant_modes.items():
        m = _re.fullmatch(r"M(\d+)", code.strip().upper())
        if not m:
            raise ValueError(f"Soğutma '{mode}' için M kodu geçersiz: {code!r} (ör. M8).")
        ms.add(int(m.group(1)))
    gs = set(p.supported_g) | {float(w) for w in p.work_offsets}
    if p.post_options.home_at_end == "G28":
        gs |= {28.0, 91.0}
    if p.post_options.home_at_end == "G53":
        gs.add(53.0)
    p = p.model_copy(update={"supported_m": sorted(ms), "supported_g": sorted(gs),
                             "coolant_modes": {k: v.strip().upper() for k, v in p.coolant_modes.items()}})
    path = custom_dir() / f"{p.id}.json"
    path.write_text(json.dumps(p.model_dump(exclude={"builtin"}), ensure_ascii=False, indent=2), encoding="utf-8")
    _load_all.cache_clear()
    return get_profile(p.id)


def delete_profile(profile_id: str) -> None:
    p = _load_all().get(profile_id)
    if p is None:
        raise KeyError(profile_id)
    if p.builtin:
        raise ValueError("Hazır kütüphanedeki makineler silinemez.")
    (custom_dir() / f"{profile_id}.json").unlink(missing_ok=True)
    _load_all.cache_clear()
