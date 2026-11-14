"""Post-processor temel sınıfı ve modal G-code yazıcı.

Bir post-processor, takım yolu IR'sini (ir.py) belirli bir kontrol ünitesinin
G-code diyalektine çevirir. Ortak davranış burada; diyalekt farkları alt sınıflarda.
"""
from __future__ import annotations

import math
import re

from .. import ir
from ..ir import fmt
from ..profiles import MachineProfile

COMMENT_MAP = str.maketrans({"ç": "c", "Ç": "C", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I", "ö": "o", "Ö": "O",
                             "ş": "s", "Ş": "S", "ü": "u", "Ü": "U", "Ø": "D", "°": "deg", "â": "a", "—": "-", "–": "-"})


class GWriter:
    """Modal durumu izleyerek gereksiz tekrarları atlayan basit G-code yazıcı."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.pos: dict[str, float | None] = {"X": None, "Y": None, "Z": None}
        self.feed: float | None = None
        self.motion: str | None = None

    def raw(self, s: str) -> None:
        self.lines.append(s)

    def comment(self, s: str) -> None:
        # Kontrol üniteleri için: iç içe parantez yok, yalnızca ASCII
        self.lines.append(f"({clean_comment(s)})")

    def move(self, code: str, x=None, y=None, z=None, f=None, extra: str = "", prefix: str = "") -> None:
        words = []
        if code != self.motion:
            words.append(code)
        if prefix:
            words.append(prefix)
        for ax, v in (("X", x), ("Y", y), ("Z", z)):
            if v is not None and (self.pos[ax] is None or abs(self.pos[ax] - v) > 5e-4 or extra or prefix):
                words.append(f"{ax}{fmt(v)}")
                self.pos[ax] = v
        if extra:
            words.append(extra)
        if f is not None and f != self.feed:
            words.append(f"F{fmt(f)}")
            self.feed = f
        if not words or (len(words) == 1 and words[0] == code and not extra):
            return
        self.motion = code
        self.lines.append(" ".join(words))

    def forget_position(self) -> None:
        self.pos = {"X": None, "Y": None, "Z": None}
        self.motion = None


def clean_comment(s: str) -> str:
    s = s.translate(COMMENT_MAP).replace("(", "[").replace(")", "]")
    return s.encode("ascii", "ignore").decode()


class PostProcessor:
    id = "base"
    name = "Genel"
    percent_delimiters = True
    header_codes = "G21 G90 G17 G94 G40 G49 G80"

    def __init__(self, profile: MachineProfile, program_number: int = 1000) -> None:
        self.profile = profile
        self.program_number = program_number
        self.notes: list[str] = []  # post sırasında yapılan dönüşümler (kullanıcıya gösterilir)

    # ------------------------------------------------------------------ ana akış
    def run(self, prog: ir.Program) -> str:
        self.w = GWriter()
        self.prog = prog
        self.current_tool: ir.Tool | None = None
        self.start(prog)
        for op in prog.ops:
            getattr(self, f"op_{type(op).__name__}")(op)
        self.end(prog)
        return "\n".join(self.finalize(self.w.lines)) + "\n"

    def finalize(self, lines: list[str]) -> list[str]:
        """Makine profilinin post seçenekleri: yorumları kaldır, satır numarası ekle."""
        po = self.profile.post_options
        if not po.comments:
            out = []
            for ln in lines:
                t = re.sub(r"\s*\([^)]*\)", "", ln).strip()
                if t:
                    out.append(t)
            lines = out
        if po.line_numbers:
            n, inc, out = po.line_increment, po.line_increment, []
            for ln in lines:
                if ln == "%" or ln.startswith("O") or ln.startswith("("):
                    out.append(ln)
                else:
                    out.append(f"N{n} {ln}")
                    n += inc
            lines = out
        return lines

    def start(self, prog: ir.Program) -> None:
        if self.percent_delimiters:
            self.w.raw("%")
        self.program_header(prog)
        for c in prog.header:
            self.w.comment(c)
        self.w.comment(f"Post: {self.name} | Makine profili: {self.profile.name}")
        self.w.raw(self.header_codes)
        self.w.raw(f"G{prog.wcs}")

    def program_header(self, prog: ir.Program) -> None:
        pass

    def go_home(self) -> None:
        """Program sonu park hareketi (post seçeneği)."""
        h = self.profile.post_options.home_at_end
        if h == "G28":
            self.w.raw("G91 G28 Z0")
            self.w.raw("G28 X0 Y0")
            self.w.raw("G90")
        elif h == "G53":
            self.w.raw("G53 G0 Z0")

    def end(self, prog: ir.Program) -> None:
        self.go_home()
        self.w.raw("M30")
        if self.percent_delimiters:
            self.w.raw("%")

    # ------------------------------------------------------------------ operasyonlar
    def op_Comment(self, op: ir.Comment) -> None:
        self.w.comment(op.text)

    def op_Rapid(self, op: ir.Rapid) -> None:
        self.w.move("G0", op.x, op.y, op.z)

    def op_Linear(self, op: ir.Linear) -> None:
        self.w.move("G1", op.x, op.y, op.z, op.f)

    def op_Arc(self, op: ir.Arc) -> None:
        if self.profile.post_options.arc_format == "r":
            r = math.hypot(op.i, op.j)
            self.w.move("G2" if op.cw else "G3", op.x, op.y, op.z, op.f, extra=f"R{fmt(r)}")
        else:
            self.w.move("G2" if op.cw else "G3", op.x, op.y, op.z, op.f, extra=f"I{fmt(op.i)} J{fmt(op.j)}")

    def op_Spindle(self, op: ir.Spindle) -> None:
        if op.on:
            self.w.raw(f"S{fmt(op.rpm or 0)} {'M3' if op.cw else 'M4'}")
        else:
            self.w.raw("M5")

    def op_Coolant(self, op: ir.Coolant) -> None:
        self.w.raw(op.code if op.on else "M9")

    def op_ToolChange(self, op: ir.ToolChange) -> None:
        raise NotImplementedError

    def op_DrillCycle(self, op: ir.DrillCycle) -> None:
        if self.profile.canned_cycles:
            self.canned_drill(op)
        else:
            self.expanded_drill(op)

    # ------------------------------------------------------------------ delme
    def canned_drill(self, op: ir.DrillCycle) -> None:
        w = self.w
        w.move("G0", z=op.clearance_z)
        w.move("G0", op.x, op.y)
        if op.peck:
            w.raw(f"G98 G83 X{fmt(op.x)} Y{fmt(op.y)} Z{fmt(op.z_bottom)} R{fmt(op.r_plane)} Q{fmt(op.peck)} F{fmt(op.feed)}")
        else:
            w.raw(f"G98 G81 X{fmt(op.x)} Y{fmt(op.y)} Z{fmt(op.z_bottom)} R{fmt(op.r_plane)} F{fmt(op.feed)}")
        w.raw("G80")
        w.motion = None
        w.feed = op.feed
        w.pos.update({"X": op.x, "Y": op.y, "Z": op.clearance_z})

    def expanded_drill(self, op: ir.DrillCycle) -> None:
        """Delme döngüsü olmayan kontrol üniteleri için döngüyü G0/G1 hareketlerine açar."""
        w = self.w
        kind = "gagalamalı" if op.peck else "düz"
        w.comment(f"{kind} delme dongusu G0/G1 hareketlerine acildi")
        w.move("G0", z=op.clearance_z)
        w.move("G0", op.x, op.y)
        w.move("G0", z=op.r_plane)
        if op.peck:
            depth = op.r_plane
            n = max(1, math.ceil((op.r_plane - op.z_bottom) / op.peck - 1e-9))
            for k in range(1, n + 1):
                target = max(op.r_plane - k * op.peck, op.z_bottom)
                if depth < op.r_plane:
                    w.move("G0", z=depth + 0.5)  # bir önceki derinliğin hemen üstüne hızlı dön
                w.move("G1", z=target, f=op.feed)
                w.move("G0", z=op.r_plane)  # talaş boşaltma
                depth = target
        else:
            w.move("G1", z=op.z_bottom, f=op.feed)
        w.move("G0", z=op.clearance_z)
        if "Delme döngüleri G0/G1 hareketlerine açıldı (makinede G81/G83 yok)." not in self.notes:
            self.notes.append("Delme döngüleri G0/G1 hareketlerine açıldı (makinede G81/G83 yok).")
