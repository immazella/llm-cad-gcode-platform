"""Takım yolu ara temsili (IR).

CAM katmanı (generator.py) makineden bağımsız bu hareket listesini üretir; hangi
kontrol ünitesine hangi G-code yazılacağına post-processor (posts/) karar verir.
FreeCAD CAM'deki "Path komutları" ve LinuxCNC'deki "canonical commands" ile aynı fikir.

Koordinatlar iş koordinatındadır (mm, parça üst yüzey merkezi = X0 Y0 Z0).
None olan eksen "değişmiyor" anlamına gelir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from .config import Tool


def fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass
class Comment:
    text: str


@dataclass
class Rapid:
    x: float | None = None
    y: float | None = None
    z: float | None = None


@dataclass
class Linear:
    x: float | None = None
    y: float | None = None
    z: float | None = None
    f: float | None = None


@dataclass
class Arc:
    """G17 düzleminde yay (z verilirse helis). i, j: başlangıca göre merkez ofseti."""

    x: float
    y: float
    z: float | None
    i: float
    j: float
    cw: bool
    f: float


@dataclass
class DrillCycle:
    """Tek bir delik için delme döngüsü.

    r_plane: döngünün hızlı inip ilerlemeye geçtiği düzlem; clearance_z: döngü sonunda
    dönülen yükseklik (G98 davranışı); peck: gagalama derinliği (None → düz delme).
    """

    x: float
    y: float
    z_bottom: float
    r_plane: float
    clearance_z: float
    feed: float
    peck: float | None = None


@dataclass
class ToolChange:
    tool: Tool
    first: bool = False  # programdaki ilk takım mı (elle değişimde önemli)


@dataclass
class Spindle:
    on: bool
    rpm: float | None = None
    cw: bool = True


@dataclass
class Coolant:
    on: bool
    mode: str = "flood"  # flood | mist | air
    code: str = "M8"  # makine profilinin bu mod için M kodu


Op = Union[Comment, Rapid, Linear, Arc, DrillCycle, ToolChange, Spindle, Coolant]


@dataclass
class Program:
    name: str
    header: list[str] = field(default_factory=list)  # program başı yorumları
    ops: list[Op] = field(default_factory=list)
    safe_z: float = 5.0
    wcs: int = 54  # iş koordinat sistemi G54..G59
