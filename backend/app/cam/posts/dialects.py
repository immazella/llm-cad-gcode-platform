"""Kontrol ünitesine özgü post-processor'lar."""
from __future__ import annotations

from .. import ir
from ..ir import fmt
from .base import PostProcessor, clean_comment


class FanucPost(PostProcessor):
    """Fanuc 0i tarzı: % ve O numarası, M6 ile takım değişimi, G43 H ile takım boyu, G81/G83."""

    id = "fanuc"
    name = "Fanuc 0i tarzı"
    header_codes = "G21 G90 G17 G94 G40 G49 G80"

    def program_header(self, prog: ir.Program) -> None:
        self.w.raw(f"O{self.program_number:04d} ({clean_comment(prog.name)[:24].upper()})")

    def op_ToolChange(self, op: ir.ToolChange) -> None:
        t = op.tool
        self.w.comment(f"T{t.number}: {t.label()} D={fmt(t.diameter)}")
        self.w.raw(f"T{t.number} M6")
        self.w.forget_position()
        self.pending_h = t.h  # takım boyu ofset numarası (varsayılan = takım numarası)
        self.current_tool = t

    def op_Rapid(self, op: ir.Rapid) -> None:
        h = getattr(self, "pending_h", None)
        if h is not None and op.z is not None:
            # takım boyu telafisi ilk Z hareketiyle birlikte devreye alınır
            self.w.move("G0", op.x, op.y, op.z, prefix=f"G43 H{h}")
            self.pending_h = None
            return
        super().op_Rapid(op)


class LinuxCncPost(FanucPost):
    """LinuxCNC: O numarası yok, G64 P ile yol harmanlama, takım boyu takım tablosundan (G43 H)."""

    id = "linuxcnc"
    name = "LinuxCNC"
    header_codes = "G17 G21 G40 G49 G80 G90 G94 G64 P0.01"

    def program_header(self, prog: ir.Program) -> None:
        pass


class GrblPost(PostProcessor):
    """GRBL 1.1: delme döngüsü ve M6 yok. Döngüler açılır, takım değişimi M0 ile elle yapılır."""

    id = "grbl"
    name = "GRBL 1.1"
    percent_delimiters = False
    header_codes = "G21 G90 G17 G94 G40 G49"

    def op_ToolChange(self, op: ir.ToolChange) -> None:
        t = op.tool
        desc = f"T{t.number} {t.label()} D={fmt(t.diameter)}"
        if op.first:
            self.w.comment(f"Baslangic takimi: {desc} - programdan once takili ve Z sifiri alinmis olmali")
        else:
            # IR'de takım değişiminden önce iş mili zaten durduruldu (M5)
            self.w.raw(f"M0 ({clean_comment('TAKIM DEGISTIR: ' + desc + ' - Z sifirini yeniden alin')})")
            note = "Takım değişimleri M0 (duraklat) ile elle yapılır; her değişimde Z sıfırı yeniden alınmalı (makinede M6 yok)."
            if note not in self.notes:
                self.notes.append(note)
        self.w.forget_position()
        self.current_tool = t

    def end(self, prog: ir.Program) -> None:
        self.go_home()
        self.w.raw("M30")
