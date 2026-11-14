"""English presentation of canonical validation reports, keyed by stable rule IDs.

The validator and persisted reports remain unchanged. Only presentation fields on
copies are replaced; raw source lines, data, codes, numbers and profile names stay intact.
"""
from copy import deepcopy
from .i18n import language

# title, explanation, corrective guidance. Variant-specific numeric context follows below.
RULE_EN = {
    "E001": ("Syntax error", "The line contains invalid G-code syntax.", "Use an address letter followed by a number, for example G1 X10 F500."),
    "E002": ("Unsupported controller code", "The selected controller does not support this code.", "Replace it with a code supported by this controller."),
    "E003": ("Conflicting modal codes", "More than one code from the same modal group occurs on this line.", "Keep only one code from each modal group."),
    "E004": ("Repeated address letter", "An address letter occurs more than once on this line.", "Use each address letter only once per line."),
    "W005": ("Multiple M commands on one line", "Some controllers execute only one M command per line.", "Put M commands on separate lines."),
    "E010": ("Missing or invalid feed rate (F)", "A cutting or drilling move has no feed rate, or F is zero or negative.", "Set a positive F value before the cutting move."),
    "E013": ("Spindle start without S", "M3/M4 starts the spindle before a speed S has been set.", "Set S before M3 or on the same line."),
    "E014": ("Tool change without T", "M6 has no tool number T.", "Specify the tool number before or with M6."),
    "E020": ("Axis coordinates without motion mode", "Axis coordinates are present without an active G0/G1/G2/G3 motion command.", "Add the appropriate motion G-code."),
    "E021": ("Drilling cycle missing Z or R", "The drilling cycle is missing its Z depth or R retract plane.", "Specify both Z and R."),
    "E022": ("R plane below hole bottom", "The drilling cycle R plane is below the hole bottom Z.", "Place R above the hole bottom and clear of the stock."),
    "E023": ("Peck drilling missing Q", "The peck drilling cycle has no peck depth Q.", "Specify a positive Q peck depth."),
    "E050": ("Missing arc center or radius", "The arc has neither a center (I/J) nor a radius (R).", "Specify I/J or R."),
    "E051": ("Invalid arc radius", "The R-format arc radius is too small for the chord, or its endpoints coincide.", "Correct the radius; use I/J for a full circle."),
    "W052": ("G18/G19 arc geometry not checked", "This prototype does not geometrically validate arcs in the G18/G19 plane.", "Verify the arc in a controller-compatible simulator."),
    "E053": ("Inconsistent arc geometry", "The start and end radii of the I/J arc differ by more than 0.01 mm.", "Correct the endpoint or center coordinates."),
    "W040": ("Commands after program end", "Commands after M30/M2 will not be executed.", "Move M30 to the end of the program."),
    "W060": ("Missing unit command (G20/G21)", "No G21 (mm) or G20 (inch) command is present; the machine default applies.", "Declare the intended units at the start of the program."),
    "W061": ("Missing distance mode (G90/G91)", "Absolute or incremental distance mode has not been specified.", "Declare the intended G90/G91 mode at the start."),
    "W062": ("Missing program end (M30/M2)", "There is no program-end command M30 or M2.", "Add M30 to the end of the program."),
    "I063": ("No motion in program", "The program contains no motion commands.", ""),
    "I070": ("Command not simulated", "This code is accepted by the machine but not modeled by this prototype; subsequent positions may be uncertain.", "Verify its behavior on a controller-compatible simulator."),
    "W011": ("Feed exceeds machine limit", "The feed rate exceeds the machine limit; the controller clamps it and machining takes longer.", "Reduce F to the machine limit."),
    "E012": ("Spindle speed outside machine limits", "The spindle speed S is nonpositive or outside the selected machine limits.", "Use a supported S value and adjust feed accordingly."),
    "E015": ("No automatic tool changer", "This machine has no automatic tool changer; M6 cannot be executed.", "Stop with M5 + M0, change the tool manually and reset Z zero."),
    "E016": ("Tool number exceeds magazine capacity", "The requested tool exceeds the machine magazine capacity.", "Choose a tool within the supported magazine range."),
    "W017": ("Tool length compensation (G43) inactive", "Cutting after a tool change without the G43 H compensation expected by this machine.", "Add G43 H<tool number> after the tool change."),
    "E018": ("Drilling cycles not supported", "This machine does not support the requested canned drilling cycle.", "Expand the cycle into G0/G1 moves; the machine post-processor does this automatically."),
    "W019": ("Missing program number (O)", "The program does not start with an O program number.", "Add a program number such as O1000 after the % line."),
    "I071": ("Unknown tool length", "No tool length was entered; Z travel was checked assuming zero length. Actual machine Z is higher by the tool length, so upper-limit violations may be missed.", "Enter the tool length in machining settings → Tools."),
    "E040": ("Axis travel exceeded (machine coordinates)", "An axis exceeds its machine-coordinate travel limit.", "Relocate the work offset, refixture the part, or adapt the program to the machine travel."),
    "E030": ("Cutting with spindle stopped", "A cutting move inside the stock or a drilling cycle runs while the spindle is stopped.", "Start the spindle with S... M3 before cutting."),
    "E031": ("Horizontal rapid inside stock", "Horizontal rapid motion (G0) inside the stock risks a collision.", "Raise Z to a safe height first or use G1."),
    "E032": ("Rapid plunge into stock", "Rapid motion (G0) plunges into the stock.", "Enter the stock using G1 and a suitable plunge feed."),
    "W032": ("Tool change at low Z", "The tool change occurs at a low Z position.", "Retract to a safe height before changing tools."),
    "W033": ("Tool change with spindle running", "A tool change occurs while the spindle is running.", "Stop the spindle with M5 first."),
    "I041": ("Spindle running at program end", "The spindle is still running at program end; M30 stops it, but an explicit M5 first is good practice.", ""),
    "W064": ("Cutting far below the part", "The deepest point is more than 5 mm below the part thickness; the table or fixture may be cut.", "Check cutting depth, stock thickness and fixture clearance."),
}
LEVEL_EN = {"1": "Syntax and commands", "2": "Machine compatibility", "3": "Motion safety (basic)"}
NOT_CHECKED_EN = [
    "Tool holder and workholding (vise/fixture) collisions",
    "Material removal: stock is always treated as a solid block; previously cleared regions are unknown",
    "Level 4 — comparison of machined geometry against the CAD model",
    "Controller acceleration and look-ahead behavior",
    "Macro/variable programs (#variables, O-words, subprograms)",
    "Actual path resulting from cutter radius compensation (G41/G42)",
]


def present_issue(issue: dict) -> dict:
    if language() != "en" or issue["code"] not in RULE_EN:
        return deepcopy(issue)
    out = deepcopy(issue)
    code, d = issue["code"], issue.get("data", {})
    title, message, suggestion = RULE_EN[code]
    # Use the validator's structured evidence rather than parsing translated prose.
    if code == "E032" and "R" in d:
        message = f"Rapid motion to drilling cycle R{d['R']:g} enters below stock top Z{d['stock_top']:g}."
        suggestion = "Set R above the stock top, or machine the pocket first if the hole starts on its floor."
    elif code == "E032" and "Z" in d:
        message = f"Rapid motion (G0) plunges into the stock at Z{d['Z']:g}."
    elif code in ("E031", "W032") and "Z" in d:
        message += f" Z{d['Z']:g}."
    elif code == "E040":
        message = (f"{d['axis']} axis exceeds the {d['side']} travel limit: work coordinate {d['work']:.3f} → "
                   f"machine {d['machine']:.3f}; travel {d['travel'][0]:g} … {d['travel'][1]:g}, "
                   f"work offset {d['work_offset']:g}, tool length {d['tool_length']:g}, "
                   f"excess {d['exceeds_by']:g} mm. Affected lines: {d['line_count']}.")
    elif code == "W011":
        message = f"Feed F{d['F']:g} exceeds the machine limit of {d['max_feed']:g} mm/min; the controller clamps it and machining takes longer."
        suggestion = f"Set F to at most {d['max_feed']:g}."
    elif code == "E012":
        message = f"Spindle speed S{d['S']:g} " + (f"exceeds the maximum {d['max_spindle']:g} rpm." if "max_spindle" in d else f"is below the minimum {d['min_spindle']:g} rpm." if "min_spindle" in d else "is zero or negative.")
    elif code == "E010" and "F" in d:
        message = f"Feed rate F{d['F']:g} is zero or negative."
    elif code == "E016":
        message = f"T{d['tool']}: the machine magazine has {d['max_tools']} tool slots."
    elif code == "E022":
        message = f"Drilling cycle R plane ({d['R']:g}) is below the hole bottom Z{d['Z']:g}."
    elif code == "E051" and "R" in d:
        message = f"Arc radius R{d['R']:g} is too small for the start-to-end chord ({d['chord']:g} mm)."
    elif code == "E053":
        message = f"Inconsistent arc geometry: start radius {d['r_start']:g}, end radius {d['r_end']:g}."
    elif code == "I071":
        message = f"H{d['H']} (T{d['H']}): " + message
    elif code == "W064":
        message = f"Deepest point Z{d['lowest_z']:g} is more than 5 mm below the part thickness ({d['stock_height']:g} mm); table/fixture collision risk."
    elif code == "E003":
        message += f" Group: {d['group']}; codes: {', '.join(d['codes'])}."
    elif code == "E004":
        message += f" Letters: {', '.join(d['letters'])}."
    if "code" in d:
        message += f" Code: {d['code']}."
        if d["code"] == "G43":
            suggestion = "This controller has no G43 H; reset Z zero for each tool (GRBL supports G43.1 dynamic compensation)."
        elif d["code"] == "G65":
            suggestion = "Macro calls (G65) are disabled in this profile."
        elif d["code"] == "G83":
            suggestion = "Expand peck drilling into G1 plunges and G0 retracts."
    out.update(rule=title, message=message, suggestion=suggestion)
    return out


def present_report(report: dict | None) -> dict | None:
    if report is None or language() != "en":
        return report
    out = deepcopy(report)
    out["issues"] = [present_issue(i) for i in report["issues"]]
    for level, data in out["levels"].items():
        data["name"] = LEVEL_EN.get(level, data["name"])
    out["not_checked"] = list(NOT_CHECKED_EN)
    out["disclaimer"] = ("This report covers only the checks listed above; risks under 'not checked' have not been assessed. "
                         "Passing simulation does not guarantee safe operation on a physical machine.")
    return out


def present_rules(rules: list[dict]) -> list[dict]:
    return [{**r, "title": RULE_EN[r["code"]][0]} for r in rules] if language() == "en" else rules
