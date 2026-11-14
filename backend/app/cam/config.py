"""Takım, ham malzeme ve işleme parametreleri.

Makineye ait bilgiler (kurslar, hız sınırları, diyalekt) artık `profiles.py` ve
`backend/profiles/*.json` dosyalarındadır; burada yalnızca seçili profilin kimliği
ve iş bağlama düzeni (Setup) tutulur.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .profiles import DEFAULT_PROFILE_ID, MachineProfile, Setup, get_profile


ToolType = Literal["endmill", "bullmill", "ballmill", "drill", "spot_drill", "chamfer", "vbit"]
MILL_TYPES = ("endmill", "bullmill")  # düz tabanlı: cep, kontur, alın frezeleme, helisel delik
TOOL_TYPE_TR = {"endmill": "parmak freze", "bullmill": "köşe radyüslü freze", "ballmill": "küresel freze", "drill": "matkap",
                "spot_drill": "punta matkabı", "chamfer": "pah kırıcı", "vbit": "V-uç"}
OpType = Literal["face", "drill", "helical", "pocket", "engrave", "contour"]
OP_TYPES: tuple[str, ...] = ("face", "drill", "helical", "pocket", "engrave", "contour")


class CuttingPreset(BaseModel):
    """Takımın kesme verisi seti (ör. "Alüminyum kaba"). Doğrudan devir/ilerleme ya da Vc/fz verilebilir."""

    name: str
    material: str | None = Field(default=None, description="Malzeme kimliği (materials.py); None → tüm malzemeler")
    vc: float | None = Field(default=None, gt=0, description="Kesme hızı m/dk")
    fz: float | None = Field(default=None, gt=0, description="Diş başına ilerleme mm")
    rpm: float | None = Field(default=None, gt=0)
    feed: float | None = Field(default=None, gt=0)
    plunge: float | None = Field(default=None, gt=0)
    stepdown: float | None = Field(default=None, gt=0)
    stepover_ratio: float | None = Field(default=None, gt=0, le=1)


class Tool(BaseModel):
    number: int
    type: ToolType
    diameter: float  # V-uçta gövde (en büyük kesme) çapı
    description: str = ""
    angle: float | None = Field(default=None, gt=0, lt=180, description="V-uç / pah / matkap uç açısı (derece)")
    # Takım boyu (mm): iş mili burun çizgisinden takım ucuna. G43 H<no> aktifken makine Z'sine eklenir.
    # None → bilinmiyor; doğrulayıcı Z kursunu 0 boyla kontrol eder ve bunu raporda belirtir.
    length: float | None = Field(default=None, ge=0)
    flutes: int = Field(default=2, ge=1, le=12)
    flute_length: float | None = Field(default=None, gt=0, description="Ağız (kesici) boyu mm")
    stickout: float | None = Field(default=None, gt=0, description="Tutucudan taşma boyu mm")
    shank_diameter: float | None = Field(default=None, gt=0)
    corner_radius: float | None = Field(default=None, ge=0)
    tool_material: Literal["carbide", "hss"] = "carbide"
    coating: str = ""
    length_offset: int | None = Field(default=None, ge=0, description="H numarası; None → takım numarası")
    diameter_offset: int | None = Field(default=None, ge=0, description="D numarası; None → takım numarası")
    holder: str = Field(default="", description="Tutucu, ör. BT40 ER32")
    vendor: str = ""
    product_id: str = ""
    presets: list[CuttingPreset] = Field(default_factory=list)

    @property
    def h(self) -> int:
        return self.length_offset if self.length_offset is not None else self.number

    @property
    def is_mill(self) -> bool:
        return self.type in MILL_TYPES

    def label(self) -> str:
        if self.description:
            return self.description
        return f"Ø{self.diameter:g} {TOOL_TYPE_TR.get(self.type, self.type)}"


def default_tools() -> list[Tool]:
    tools = [Tool(number=1, type="endmill", diameter=6, flutes=3, flute_length=18, stickout=30, tool_material="carbide",
                  description="Ø6 parmak freze, 3 ağız karbür",
                  presets=[CuttingPreset(name="Alüminyum kaba", material="al6061", vc=300, fz=0.05, stepdown=3, stepover_ratio=0.4),
                           CuttingPreset(name="Alüminyum finiş", material="al6061", vc=350, fz=0.03, stepdown=6, stepover_ratio=0.1)])]
    for i, d in enumerate([3, 4, 5, 6, 6.8, 8, 8.5, 10, 12], start=2):
        tools.append(Tool(number=i, type="drill", diameter=d, angle=118, tool_material="hss", flute_length=round(d * 7, 1),
                          description=f"Ø{d:g} HSS matkap"))
    tools.append(Tool(number=len(tools) + 1, type="vbit", diameter=6, angle=60, flutes=1, tool_material="carbide",
                      description="60° V-uç gravür"))
    return tools


class Cutting(BaseModel):
    """Genel ayarlar ve "elle" kesme modu değerleri (otomatik modda yalnızca yedek)."""

    material: str = "Alüminyum 6061"  # eski alan; malzeme artık setup.material
    spindle_rpm: float = 10000
    feed_rate: float = 600  # frezeleme ilerlemesi mm/dk
    plunge_rate: float = 150  # dalma ilerlemesi mm/dk
    drill_rpm: float = 3000
    drill_feed: float = 120
    depth_per_pass: float = 1.5  # kademe derinliği
    stepover_ratio: float = 0.45  # takım çapına oranla yan paso
    peck_depth: float = 2.0  # G83 gagalama derinliği
    safe_z: float = 5.0  # güvenli yükseklik (Z0 = ham parça üst yüzeyi)
    retract_z: float = 2.0  # delme döngüsü R düzlemi (delik ağzına göre)
    breakthrough: float = 0.5  # boydan boya işlemede alt yüzeyin altına taşma
    coolant: Literal["off", "flood", "mist", "air"] = "flood"

    @field_validator("coolant", mode="before")
    @classmethod
    def _coolant_bool(cls, v: Any) -> Any:  # eski oturumlarda True/False
        if isinstance(v, bool):
            return "flood" if v else "off"
        return v


class OpSettings(BaseModel):
    """Operasyon şablonu: bu türdeki her operasyon için takım, kesme verisi ve strateji."""

    enabled: bool = True
    tool: int | None = Field(default=None, description="None → otomatik seçim")
    preset: str | None = Field(default=None, description="Takım presetinin adı; None → malzemeye uyan ilk preset")
    rpm: float | None = Field(default=None, gt=0)
    feed: float | None = Field(default=None, gt=0)
    plunge: float | None = Field(default=None, gt=0)
    stepdown: float | None = Field(default=None, gt=0)
    stepover_ratio: float | None = Field(default=None, gt=0, le=1)
    stock_xy: float = Field(default=0.0, ge=0, description="Bırakılacak yan pay mm")
    stock_z: float = Field(default=0.0, ge=0, description="Bırakılacak taban payı mm")
    finish_pass: bool = False
    finish_allowance: float = Field(default=0.2, gt=0)
    entry: Literal["plunge", "ramp"] = "plunge"
    ramp_angle: float = Field(default=3.0, gt=0, le=45)
    cycle: Literal["auto", "G81", "G83"] = "auto"
    peck: float | None = Field(default=None, gt=0)


def default_op_settings() -> dict[str, OpSettings]:
    return {k: OpSettings(entry="ramp" if k in ("pocket",) else "plunge") for k in OP_TYPES}


class MachiningConfig(BaseModel):
    profile_id: str = DEFAULT_PROFILE_ID
    setup: Setup = Field(default_factory=Setup)
    tools: list[Tool] = Field(default_factory=default_tools)
    cutting: Cutting = Field(default_factory=Cutting)
    cutting_mode: Literal["auto", "manual"] = Field(
        default="auto", description="auto: takım presetleri + malzeme tablosu; manual: 'cutting' değerleri")
    operations: list[OpType] = Field(default_factory=lambda: ["face", "drill", "pocket", "engrave", "contour"])
    op_settings: dict[str, OpSettings] = Field(default_factory=default_op_settings)
    mill_tool: int = 1  # cep / kontur / helisel delik için varsayılan parmak freze numarası

    @field_validator("profile_id", mode="after")
    @classmethod
    def _known_profile(cls, v: str) -> str:
        # Kullanıcı makinesi silinmiş / başka bilgisayardan gelmiş oturum: varsayılan makineye dön (uygulama açılmaya devam etsin)
        try:
            get_profile(v)
            return v
        except KeyError:
            return DEFAULT_PROFILE_ID

    @field_validator("op_settings", mode="after")
    @classmethod
    def _all_ops(cls, v: dict[str, OpSettings]) -> dict[str, OpSettings]:
        for k in OP_TYPES:
            v.setdefault(k, OpSettings(entry="ramp" if k == "pocket" else "plunge"))
        return {k: v[k] for k in OP_TYPES}

    @property
    def profile(self) -> MachineProfile:
        try:
            return get_profile(self.profile_id)
        except KeyError:  # makine dosyası çalışırken silindi
            self.profile_id = DEFAULT_PROFILE_ID
            return get_profile(self.profile_id)

    def tool_lengths(self) -> dict[int, float]:
        """H numarası → takım boyu."""
        return {t.h: t.length for t in self.tools if t.length is not None}

    def tool(self, number: int) -> Tool | None:
        return next((t for t in self.tools if t.number == number), None)
