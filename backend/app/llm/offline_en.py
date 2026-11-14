"""Deliberately limited English offline grammar, using existing workspace tools.

No translation of arbitrary input: quoted text, names and tool arguments are never
rewritten. Unsupported sentences return help instead of guessing machining actions.
"""
import re
from ..i18n import error_message
from ..validation_i18n import present_report
from ..presentation_i18n import present_plan_feature, profile_name

HELP_EN = (
    "Offline mode uses a limited command grammar, not a language model. "
    "Send one command at a time. Supported examples:\n"
    "• create box 60x40x10\n"
    "• create cylinder diameter 50 height 8\n"
    "• add hole diameter 6 depth 5 x=20 y=-15\n"
    "• add pocket 20x15x4 x=0 y=0\n"
    "• generate G-code / validate G-code / export STEP / undo\n"
    "• list machine profiles / use profile grbl_router\n"
    "Advanced editing, engraving, workshop queries and free-form English require an online LLM "
    "or the corresponding interface controls. Switch to Turkish for the legacy Turkish offline grammar."
)
NUM = r"(\d+(?:[.,]\d+)?)"
DIMS = NUM + r"\s*[x×*]\s*" + NUM + r"\s*[x×*]\s*" + NUM


def _number(value):
    return float(value.replace(",", "."))


def _failure(result):
    if result.get("ok") is False:
        return "⚠️ " + error_message(result.get("error", "Operation failed."))
    return None


def run_english(message, call):
    s = message.strip().rstrip(".!?").strip().lower()
    # Explicit full matches avoid treating an unsupported edit as a new box.
    m = re.fullmatch(r"(?:create\s+)?(?:box|plate)\s+" + DIMS + r"(?:\s*mm)?", s)
    if m is None:
        m = re.fullmatch(DIMS + r"(?:\s*mm)?\s+(?:plaka oluştur|box|plate)", s)
    if m:
        length, width, height = map(_number, m.groups())
        r = call("create_box", {"length": length, "width": width, "height": height})
        return _failure(r) or f"Created a {length:g} x {width:g} x {height:g} mm plate."
    m = re.fullmatch(r"(?:create\s+)?cylinder\s+diameter\s+" + NUM + r"\s+height\s+" + NUM + r"(?:\s*mm)?", s)
    if m:
        diameter, height = map(_number, m.groups())
        r = call("create_cylinder", {"radius": diameter / 2, "height": height})
        return _failure(r) or f"Created a Ø{diameter:g} x {height:g} mm cylinder."
    m = re.fullmatch(r"(?:add\s+)?hole\s+diameter\s+" + NUM + r"(?:\s+depth\s+" + NUM + r")?(?:\s+x=(-?\d+(?:[.,]\d+)?)\s+y=(-?\d+(?:[.,]\d+)?))?", s)
    if m:
        diameter, depth, x, y = m.groups()
        args = {"diameter": _number(diameter), "x": _number(x or "0"), "y": _number(y or "0")}
        if depth is not None:
            args["depth"] = _number(depth)
        r = call("add_hole", args)
        return _failure(r) or f"Created hole {r['feature_id']}: diameter {args['diameter']:g} mm at ({args['x']:g}, {args['y']:g})."
    m = re.fullmatch(r"(?:add\s+)?pocket\s+" + DIMS + r"(?:\s+x=(-?\d+(?:[.,]\d+)?)\s+y=(-?\d+(?:[.,]\d+)?))?", s)
    if m:
        length, width, depth, x, y = m.groups()
        args = {"length": _number(length), "width": _number(width), "depth": _number(depth), "x": _number(x or "0"), "y": _number(y or "0")}
        r = call("add_pocket", args)
        return _failure(r) or f"Created pocket {r['feature_id']}: {args['length']:g} x {args['width']:g} mm, depth {args['depth']:g} mm."
    if s in ("validate g-code", "validate gcode", "g-code'u doğrula", "g-code doğrula"):
        r = call("validate_gcode", {})
        if _failure(r):
            return _failure(r)
        r = present_report(r)
        counts = r["counts"]
        lines = [f"Validation ({profile_name(r['profile'])}): {counts['error']} errors, {counts['warning']} warnings, {counts['info']} informational findings."]
        lines += [f"• Line {i['line'] or '-'}: {i['message']} {i['suggestion']}" for i in r["issues"][:8]]
        lines.append(r["disclaimer"])
        return "\n".join(lines)
    if s in ("generate g-code", "generate gcode", "g-code üret"):
        r = call("generate_gcode", {})
        if _failure(r):
            return _failure(r)
        v = r["validation_summary"]
        plan = ", ".join(f"{p['op']}({present_plan_feature(p['feature'])}, T{p['tool']})" for p in r["plan"])
        out = (f"Generated G-code — {profile_name(r['profile'])}, post: {r['post']} ({r['line_count']} lines, "
               f"estimated time {v['estimated_time_s']:.0f} s). Plan: {plan}. "
               f"Validation: {v['counts']['error']} errors, {v['counts']['warning']} warnings.")
        if r["warnings"]:
            out += "\nGeneration has additional warnings; review the program and validation report before machining."
        return out
    if s in ("undo", "geri al"):
        r = call("undo", {})
        return _failure(r) or f"Undone: {r.get('undone')}"
    if s in ("export step", "step export"):
        r = call("export_step", {})
        return _failure(r) or "STEP file ready; use the Download STEP button."
    if s == "list machine profiles":
        r = call("list_machine_profiles", {})
        if _failure(r):
            return _failure(r)
        return "Machine profiles:\n" + "\n".join(f"• {p['id']}: {profile_name(p)}" + (" (active)" if p['id'] == r['active'] else "") for p in r["profiles"])
    m = re.fullmatch(r"use profile ([a-z0-9_-]+)", s)
    if m:
        r = call("set_machine_profile", {"profile_id": m[1]})
        if _failure(r):
            return _failure(r)
        out = f"Machine profile: {profile_name(r['profile'])}."
        if r.get("revalidation"):
            c = r["revalidation"]["counts"]
            out += f" Revalidated: {c['error']} errors, {c['warning']} warnings."
        if r.get("note"):
            out += " Regenerate G-code for the selected machine before use."
        return out
    return HELP_EN
