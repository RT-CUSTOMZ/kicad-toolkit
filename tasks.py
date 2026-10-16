"""Die drei Export-Aufgaben. Jede lädt die gespeicherte Platine frisch von der Platte."""
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.parse
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pcbnew

try:
    from .common import fiducials, plot, set_opt
except ImportError:  # als eigenständiges Skript (runner.py) gestartet
    from common import fiducials, plot, set_opt


class FidError(RuntimeError):
    pass


def on_board_copy(copy_name=None):
    """Führt die Aufgabe auf einer Kopie aus, damit das im Editor geöffnete Board unberührt bleibt."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(board_file, cfg, log):
            tmp = tempfile.mkdtemp()
            try:
                name = copy_name or os.path.basename(board_file)
                path = os.path.join(tmp, name)
                shutil.copy2(board_file, path)
                pro = os.path.splitext(board_file)[0] + ".kicad_pro"
                if os.path.exists(pro):
                    shutil.copy2(pro, os.path.splitext(path)[0] + ".kicad_pro")
                return fn(pcbnew.LoadBoard(path), board_file, cfg, log)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return wrapper
    return deco

SVG_NS = "http://www.w3.org/2000/svg"
_FID_CLICKS = 5
_FID_WINDOW_S = 10
_fid_fail_times = []


def _fid_bypass():
    """Versteckt: 5 fehlgeschlagene Versuche innerhalb weniger Sekunden lassen den Export durch."""
    now = time.monotonic()
    _fid_fail_times[:] = [t for t in _fid_fail_times if now - t <= _FID_WINDOW_S] + [now]
    if len(_fid_fail_times) >= _FID_CLICKS:
        _fid_fail_times.clear()
        return True
    return False


def project_dir(board_file):
    return os.path.dirname(os.path.abspath(board_file))


def export_dir(board_file, sub=None):
    """Zentraler Ausgabeordner <Projekt>/RT-production[/sub], wird bei Bedarf angelegt."""
    path = os.path.join(project_dir(board_file), "RT-production", *([sub] if sub else []))
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------- Schaltplan-PDF

def _find_kicad_cli():
    exe = "kicad-cli.exe" if os.name == "nt" else "kicad-cli"
    candidates = [os.path.dirname(sys.executable), sys.prefix, os.path.join(sys.prefix, "bin")]
    for d in candidates:
        path = os.path.join(d, exe)
        if os.path.exists(path):
            return path
    return shutil.which("kicad-cli")


def run_schematic(board_file, cfg, log):
    project = project_dir(board_file)
    name = os.path.splitext(os.path.basename(board_file))[0]
    sch = os.path.join(project, name + ".kicad_sch")
    if not os.path.exists(sch):
        raise RuntimeError("Schaltplan nicht gefunden: " + sch)
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli nicht gefunden.")

    out = os.path.join(export_dir(board_file), name + "_Schaltplan.pdf")
    cmd = [cli, "sch", "export", "pdf", "-o", out]
    if cfg["sch_bw"]:
        cmd.append("--black-and-white")
    cmd.append(sch)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip() or "kicad-cli Fehler %d" % res.returncode)
    log("PDF: " + out)


# ---------------------------------------------------------------- Viaschablone

@on_board_copy()
def run_stencil(board, board_file, cfg, log):
    out_dir = export_dir(board_file, "Viaschablone")
    name = "LaserStencil"

    to_remove = []
    for fp in board.Footprints():
        if not (cfg["stencil_fiducials"] and fp.GetReference().upper().startswith("FID")):
            to_remove.append(fp)
    to_remove.extend(board.Zones())
    for drawing in board.Drawings():
        if drawing.GetLayer() == pcbnew.Edge_Cuts:
            drawing.SetLayer(pcbnew.F_Cu)
        else:
            to_remove.append(drawing)
    for track in board.Tracks():
        if not isinstance(track, pcbnew.PCB_VIA):
            to_remove.append(track)
    for item in to_remove:
        board.Remove(item)

    def no_drill_marks(popt):
        set_opt(popt, "SetDrillMarksType", getattr(pcbnew, "DRILL_MARKS_NO_DRILL_SHAPE", 0))
        set_opt(popt, "SetPlotBackground", False)

    if cfg["stencil_gerber"]:
        path = plot(board, out_dir, name, [pcbnew.F_Cu], pcbnew.PLOT_FORMAT_GERBER,
                    "Laser Stencil Gerber", no_drill_marks)
        log("Gerber: " + path)

    if cfg["stencil_svg"]:
        path = plot(board, out_dir, name, [pcbnew.F_Cu], pcbnew.PLOT_FORMAT_SVG,
                    "Laser Stencil SVG", no_drill_marks)
        _postprocess_svg(path)
        log("SVG: " + path)


def _postprocess_svg(path):
    """Kreise zu Pfaden, alles als rote 0.01mm-Kontur ohne Füllung (Laser-Profil)."""
    ET.register_namespace("", SVG_NS)
    tree = ET.parse(path)
    root = tree.getroot()

    for parent in root.iter():
        for child in list(parent):
            if child.tag.endswith("style"):
                parent.remove(child)

    bad = ["style", "fill", "stroke", "stroke-width", "class", "opacity",
           "fill-opacity", "stroke-opacity", "stroke-linecap", "stroke-linejoin"]
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        for attr in bad:
            elem.attrib.pop(attr, None)

        if tag == "circle":
            try:
                cx, cy, r = (float(elem.get(k)) for k in ("cx", "cy", "r"))
            except (TypeError, ValueError):
                continue
            elem.tag = "{%s}path" % SVG_NS
            elem.set("d", "M %s,%s A %s,%s 0 1,0 %s,%s A %s,%s 0 1,0 %s,%s"
                     % (cx - r, cy, r, r, cx + r, cy, r, r, cx - r, cy))
            for k in ("cx", "cy", "r"):
                del elem.attrib[k]
            tag = "path"

        if tag in ("path", "rect", "polygon", "polyline", "line"):
            elem.set("style", "fill:none; stroke:#FF0000; stroke-width:0.01mm;")
            elem.set("fill", "none")
            elem.set("stroke", "#FF0000")
            elem.set("stroke-width", "0.01mm")
        elif tag in ("g", "svg"):
            elem.set("fill", "none")

    tree.write(path, encoding="utf-8", xml_declaration=True)


# ------------------------------------------------------- Fräs-/Bohrplotter (LPKF)

@on_board_copy()
def run_lpkf(board, board_file, cfg, log):
    out_dir = export_dir(board_file, "LPKF")

    fids = fiducials(board)
    if len(fids) != 3 and not cfg.get("skip_fid_check"):
        raise FidError("%d Passermarken (FID*) gefunden, 3 erforderlich." % len(fids))

    def common(popt):
        set_opt(popt, "SetPlotFrameRef", False)
        set_opt(popt, "SetAutoScale", False)
        set_opt(popt, "SetScale", 1)
        set_opt(popt, "SetMirror", False)
        set_opt(popt, "SetUseAuxOrigin", False)
        set_opt(popt, "SetSubtractMaskFromSilk", False)

    if cfg["lpkf_pdf"]:
        def pdf_bottom(popt):
            common(popt)
            set_opt(popt, "SetUseGerberAttributes", True)

        def pdf_top(popt):
            pdf_bottom(popt)
            set_opt(popt, "SetMirror", True)

        log("PDF: " + plot(board, out_dir, "CuBottom", [pcbnew.Edge_Cuts, pcbnew.B_Cu],
                           pcbnew.PLOT_FORMAT_PDF, "Bottom layer", pdf_bottom))
        log("PDF: " + plot(board, out_dir, "CuTop", [pcbnew.Edge_Cuts, pcbnew.F_Cu],
                           pcbnew.PLOT_FORMAT_PDF, "Top layer", pdf_top))

    if cfg["lpkf_gerber"]:
        def gbr(popt):
            common(popt)
            set_opt(popt, "SetPlotReference", False)
            set_opt(popt, "SetUseGerberAttributes", False)

        # Passermarken auf Eco1.User sichtbar machen
        for fp in fids:
            for pad in fp.Pads():
                layers = pad.GetLayerSet()
                if not layers.Contains(pcbnew.Eco1_User):
                    layers.AddLayer(pcbnew.Eco1_User)
                    pad.SetLayerSet(layers)

        for fname, layer, desc in (("Eco1.User", pcbnew.Eco1_User, "LPKF Passermarken"),
                                   ("F.Cu", pcbnew.F_Cu, "Top Copper"),
                                   ("B.Cu", pcbnew.B_Cu, "Bot Copper"),
                                   ("Edge.Cuts", pcbnew.Edge_Cuts, "Cut layer")):
            log("Gerber: " + plot(board, out_dir, fname, [layer],
                                  pcbnew.PLOT_FORMAT_GERBER, desc, gbr))

    if cfg["lpkf_drill"]:
        os.makedirs(out_dir, exist_ok=True)
        writer = pcbnew.GERBER_WRITER(board)
        writer.SetMapFileFormat(pcbnew.PLOT_FORMAT_PDF)
        writer.SetOptions(pcbnew.VECTOR2I(0, 0))
        writer.SetFormat(True)
        writer.CreateDrillandMapFilesSet(out_dir + os.sep, True, True)
        writer.GenDrillReportFile(os.path.join(out_dir, "drill_report.rpt"))
        log("Bohrdaten: " + out_dir)


# --------------------------------------------------------------- Belichter (UV)

@on_board_copy()
def run_uv(board, board_file, cfg, log):
    out_dir = export_dir(board_file, "UV-Belichter")

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    def uv(popt):
        set_opt(popt, "SetMirror", False)
        set_opt(popt, "SetPlotValue", False)
        set_opt(popt, "SetPlotReference", False)
        set_opt(popt, "SetDisableGerberMacros", False)
        set_opt(popt, "SetUseGerberX2format", False)
        set_opt(popt, "SetIncludeGerberNetlistInfo", False)

    files = {}
    for layer_name, suffix, layer in (("Top", "TOP", pcbnew.F_Cu), ("Bottom", "BOT", pcbnew.B_Cu)):
        files[layer_name] = plot(board, out_dir, suffix, [pcbnew.Edge_Cuts, layer],
                                 pcbnew.PLOT_FORMAT_GERBER, layer_name + " Copper UV", uv)
        log("UV-Gerber: " + files[layer_name])

    if not cfg["uv_upload"]:
        return

    base = cfg["uv_server"].strip()
    if not base.endswith("/"):
        base += "/"

    ids = {}
    for layer_name, path in files.items():
        params = {"exposure": cfg["uv_exposure"], "offset_x": cfg["uv_x_offset"],
                  "offset_y": cfg["uv_y_offset"], "layer": layer_name,
                  "filename": os.path.basename(path)}
        if cfg["uv_user"]:
            params["user"] = cfg["uv_user"]
        with open(path, "rb") as f:
            data = f.read()
        req = Request(base + "upload?" + urllib.parse.urlencode(params), data=data,
                      headers={"Content-Type": "text/plain"}, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                if resp.status == 202:
                    ids[layer_name] = resp.read().decode()
        except HTTPError as err:
            raise RuntimeError("Upload-Fehler %s bei %s" % (err.code, os.path.basename(path)))
        except (URLError, OSError):
            raise RuntimeError("Server nicht erreichbar: " + base)

    if len(ids) == 2:
        url = base + "?" + urllib.parse.urlencode({"top": ids["Top"], "bot": ids["Bottom"]})
        log("Upload erfolgreich. Status: " + url)
        return url
    raise RuntimeError("Upload unvollständig (unerwartete Serverantwort).")


# ------------------------------------------------- Vorab-Prüfung und Standardwerte

A4_MM = (210, 297)
DRC_LABELS = {
    "track_width": "Leiterbahn zu schmal",
    "via_diameter": "Via zu klein",
    "drill_out_of_range": "Bohrung zu klein",
    "annular_width": "Restring zu klein",
    "clearance": "Abstand zu gering",
    "copper_edge_clearance": "Kupfer zu nah am Rand",
    "hole_clearance": "Bohrung zu nah an Kupfer",
    "hole_to_hole": "Bohrungen zu nah beieinander",
    "shorting_items": "Kurzschluss",
    "tracks_crossing": "Leiterbahnen kreuzen sich",
    "via_close": "Vias zu eng (Handlöten)",
    "both_sides": "Pin beidseitig anlöten (keine Galvanik)",
    "covered": "Pad unter Gehäuse, Bahn auf Bauteilseite",
    "both_covered": "Pin beidseitig anlöten UND unter Gehäuse",
}
PAD_KINDS = {"both_sides", "covered", "both_covered"}


def _power_pattern(cfg):
    return cfg["power_regex"].replace("'", "").replace('"', "").strip()


def _rules_text(cfg):
    """Eigene DRC-Regeln: spätere Regeln gewinnen, daher Versorgung nach Signal."""
    def mm(key):
        return "%gmm" % cfg[key]

    def rule(name, cond, *constraints):
        c = " ".join("(constraint %s (min %s))" % kv for kv in constraints)
        return '(rule "%s" %s %s)' % (name, '(condition "%s")' % cond if cond else "", c)

    pw = _power_pattern(cfg)
    lines = ["(version 1)",
             rule("Bahn", "A.Type == 'Track'", ("track_width", mm("sg_track"))),
             rule("Via", "A.Type == 'Via'", ("via_diameter", mm("sg_via_dia")),
                  ("hole_size", mm("sg_via_drill")))]
    if pw:
        lines += [rule("Bahn Versorgung", "A.Type == 'Track' && A.NetName =~ '%s'" % pw,
                       ("track_width", mm("pw_track"))),
                  rule("Via Versorgung", "A.Type == 'Via' && A.NetName =~ '%s'" % pw,
                       ("via_diameter", mm("pw_via_dia")), ("hole_size", mm("pw_via_drill")))]
    lines.append(rule("Abstand", "", ("clearance", mm("min_clearance"))))
    return "\n".join(lines) + "\n"


def _run_drc(board_path, cfg):
    cli = _find_kicad_cli()
    if not cli:
        return None
    with open(os.path.splitext(board_path)[0] + ".kicad_dru", "w", encoding="utf-8") as f:
        f.write(_rules_text(cfg))
    out = os.path.join(os.path.dirname(board_path), "drc.json")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run([cli, "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
                          "--all-track-errors", "--output", out, board_path],
                         capture_output=True, text=True, creationflags=flags)
    if not os.path.exists(out):
        raise RuntimeError("DRC fehlgeschlagen: " + ((res.stderr or res.stdout).strip()[-200:] or "keine Ausgabe"))
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def _via_width(via):
    try:
        return via.GetWidth(pcbnew.F_Cu)
    except (TypeError, NotImplementedError):
        return via.GetWidth()


def _set_via_width(via, width):
    try:
        via.SetWidth(pcbnew.F_Cu, width)
    except (TypeError, NotImplementedError):
        via.SetWidth(width)


def _blend(colour, alpha=0.6):
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(c * alpha + 255 * (1 - alpha)) for c in (r, g, b))


def _close_vias(board, min_gap):
    """Via-Paare, deren Ränder näher als min_gap (mm) beieinander liegen."""
    vias = sorted((pcbnew.ToMM(t.GetStart().x), pcbnew.ToMM(t.GetStart().y), pcbnew.ToMM(_via_width(t)))
                  for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA))
    widest = max((v[2] for v in vias), default=0)
    found = []
    for i, (x1, y1, d1) in enumerate(vias):
        for x2, y2, d2 in vias[i + 1:]:
            if x2 - x1 - (d1 + widest) / 2 >= min_gap:
                break
            gap = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 - (d1 + d2) / 2
            if gap < min_gap:
                found.append(("via_close", "Rand-Abstand %.2f mm (min. %.2f)" % (gap, min_gap),
                              (x1 + x2) / 2, (y1 + y2) / 2))
    return found


def _tht_pad_sides(board):
    """Liefert (Footprint, Pad, {Kupferlayer mit direktem Anschluss}) für Durchsteck-Pads mit Netz."""
    ends = {}
    for t in board.GetTracks():
        if not isinstance(t, pcbnew.PCB_VIA):
            ends.setdefault((t.GetNetCode(), t.GetLayer()), []).extend((t.GetStart(), t.GetEnd()))
    zones = {}
    for z in board.Zones():
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            if z.IsOnLayer(layer):
                zones.setdefault((z.GetNetCode(), layer), []).append(z)

    def zone_touches(zone, layer, pos, reach):
        try:
            return zone.HitTestFilledArea(layer, pos, reach)
        except Exception:
            return False

    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetAttribute() != pcbnew.PAD_ATTRIB_PTH or pad.GetNetCode() == 0:
                continue
            pos = pad.GetPosition()
            box = pad.GetBoundingBox()
            reach = max(box.GetWidth(), box.GetHeight()) // 2
            layers = set()
            for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                key = (pad.GetNetCode(), layer)
                if (any(pad.HitTest(p) for p in ends.get(key, []))
                        or any(zone_touches(z, layer, pos, reach) for z in zones.get(key, []))):
                    layers.add(layer)
            yield fp, pad, layers


def _pad_hit(kind, fp, pad):
    pos = pad.GetPosition()
    return (kind, "%s Pin %s (%s)" % (fp.GetReference(), pad.GetNumber(), pad.GetNetname()),
            pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y))


def _both_side_pads(board):
    """Kupfer auf Top und Bottom: ohne Galvanik muss beidseitig gelötet werden."""
    return [_pad_hit("both_sides", fp, pad) for fp, pad, layers in _tht_pad_sides(board)
            if len(layers) == 2]


def _fab_box(fp):
    """Umschließende Box aller Fab-Formen auf der Bauteilseite, None ohne Fab-Umriss."""
    fab = pcbnew.F_Fab if fp.GetLayer() == pcbnew.F_Cu else pcbnew.B_Fab
    box = None
    for item in fp.GraphicalItems():
        if item.GetLayer() == fab and item.Type() == pcbnew.PCB_SHAPE_T:
            if box is None:
                box = item.GetBoundingBox()
            else:
                box.Merge(item.GetBoundingBox())
    return box


def _covered_pads(board):
    """Pads mit Kupfer auf der Bauteilseite, die unter dem Gehäuse (Fab-Umriss) liegen."""
    bodies = {fp.GetReference(): _fab_box(fp) for fp in board.GetFootprints()}
    return [_pad_hit("covered", fp, pad) for fp, pad, layers in _tht_pad_sides(board)
            if fp.GetLayer() in layers and bodies[fp.GetReference()] is not None
            and bodies[fp.GetReference()].Contains(pad.GetPosition())]


def _group_violations(violations):
    """Pin-Treffer eines Bauteils zu einer Nummer zusammenfassen, alle anderen einzeln."""
    groups, index = [], {}
    for v in violations:
        kind, desc = v[0], v[1]
        key = ("pad", desc.split()[0]) if kind in PAD_KINDS else (kind, len(groups))
        if key not in index:
            index[key] = len(groups)
            groups.append((kind, []))
        groups[index[key]][1].append(v)
    return groups


PAD_SHORT = {"both_covered": "Leiterbahn beidseitig + unter Gehäuse", "both_sides": "Leiterbahn beidseitig anlöten",
             "covered": "Leiterbahn unter Gehäuse"}


def _legend_text(n, kind, items):
    if kind not in PAD_KINDS:
        return "%d: %s - %s" % (n, DRC_LABELS[kind], items[0][1])
    ref = items[0][1].split()[0]
    by_kind = {}
    for k, desc, _, _ in items:
        pin = desc[len(ref) + 1:].split(" (")[0].replace("Pin ", "")
        by_kind.setdefault(k, {}).setdefault(pin, 0)
        by_kind[k][pin] += 1
    parts = []
    for k in ("both_covered", "both_sides", "covered"):
        if k in by_kind:
            pins = ", ".join(p if c == 1 else "%s (%dx)" % (p, c) for p, c in by_kind[k].items())
            parts.append("%s: Pin %s" % (PAD_SHORT[k], pins))
    return "%d: %s - %s" % (n, ref, " | ".join(parts))


def _write_risk_pdf(board, violations, path):
    """Vektor-PDF auf A4-Breite skaliert: Bahnen, Vias, markierte Problemstellen und Legende."""
    box = board.GetBoardEdgesBoundingBox()
    x0, y0 = pcbnew.ToMM(box.GetX()), pcbnew.ToMM(box.GetY())
    w, h = pcbnew.ToMM(box.GetWidth()), pcbnew.ToMM(box.GetHeight())
    u = max(w, 20.0) / 198.0  # Platinen-mm pro Seiten-mm; Texte werden damit seitenfest
    margin, line_h, legend_fs = 6.0 * u, 4.6 * u, 3.2 * u
    mark = max(max(w, h) / 45.0, 1.2)
    page_w = w + 2 * margin
    max_chars = int((page_w - 2 * margin) / (legend_fs * 0.55))
    groups = _group_violations(violations)
    legend = []
    for n, (kind, items) in enumerate(groups[:40], 1):
        legend.extend(textwrap.wrap(_legend_text(n, kind, items), max_chars, subsequent_indent="    "))
    page_h = h + 2 * margin + (len(legend) + 1) * line_h + 2 * u
    k = 72 / 25.4 / u
    ox, oy = x0 - margin, y0 - margin

    def px(x):
        return (x - ox) * k

    def py(y):
        return (page_h - (y - oy)) * k

    def rgb(c):
        return " ".join("%.3f" % (int(c[i:i + 2], 16) / 255) for i in (1, 3, 5))

    ops = []

    def paint(fill, stroke, sw):
        if fill:
            ops.append("%s rg" % rgb(fill))
        if stroke:
            ops.append("%s RG %.3f w" % (rgb(stroke), sw * k))
        ops.append("B" if fill and stroke else "f" if fill else "S")

    def circle(x, y, r, fill=None, stroke=None, sw=0.1):
        cx, cy, rr = px(x), py(y), r * k
        c = 0.5523 * rr
        ops.append("%.2f %.2f m" % (cx + rr, cy))
        for a, b, c1, c2 in (((cx + rr, cy + c), (cx + c, cy + rr), cx, cy + rr),
                             ((cx - c, cy + rr), (cx - rr, cy + c), cx - rr, cy),
                             ((cx - rr, cy - c), (cx - c, cy - rr), cx, cy - rr),
                             ((cx + c, cy - rr), (cx + rr, cy - c), cx + rr, cy)):
            ops.append("%.2f %.2f %.2f %.2f %.2f %.2f c" % (a + b + (c1, c2)))
        paint(fill, stroke, sw)

    def text(x, y, size, string, colour="#000000", bold=False):
        safe = string.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append("BT %s rg /%s %.2f Tf %.2f %.2f Td (%s) Tj ET"
                   % (rgb(colour), "F2" if bold else "F1", size * k, px(x), py(y), safe))

    ops.append("%.2f %.2f %.2f %.2f re" % (px(x0), py(y0 + h), w * k, h * k))
    paint("#f4f4f4", "#555555", 0.2)
    ops.append("1 J")

    problem_refs = {v[1].split()[0] for v in violations if v[0] in PAD_KINDS}
    bodies = {}
    for fp in board.GetFootprints():
        if not any(True for _ in fp.Pads()):  # reine Grafik (KiBuzzard, Logos) ohne Pads
            continue
        box = _fab_box(fp)
        if box is None:
            box = fp.GetBoundingBox(False)
        bodies[fp.GetReference()] = (pcbnew.ToMM(box.GetX()), pcbnew.ToMM(box.GetY()),
                                     pcbnew.ToMM(box.GetWidth()), pcbnew.ToMM(box.GetHeight()))
    for ref, (bx, by, bw, bh) in bodies.items():
        if ref in problem_refs:
            continue
        ops.append("%.2f %.2f %.2f %.2f re" % (px(bx), py(by + bh), bw * k, bh * k))
        paint(None, "#a0a0a0", 0.15)
        if max(bw, bh) >= 5:
            text(bx, by - 0.8 * u, 4.0 * u, ref, "#707070")
    for t in board.GetTracks():
        sx, sy = pcbnew.ToMM(t.GetStart().x), pcbnew.ToMM(t.GetStart().y)
        if isinstance(t, pcbnew.PCB_VIA):
            circle(sx, sy, pcbnew.ToMM(_via_width(t)) / 2, "#dddddd", "#a08000", 0.1)
        else:
            colour = _blend("#c83434" if t.GetLayer() == pcbnew.F_Cu else "#3464c8")
            ops.append("%s RG %.3f w %.2f %.2f m %.2f %.2f l S" % (
                rgb(colour), pcbnew.ToMM(t.GetWidth()) * k, px(sx), py(sy),
                px(pcbnew.ToMM(t.GetEnd().x)), py(pcbnew.ToMM(t.GetEnd().y))))

    magenta = "#c800c8"
    lab = 4.8 * u
    num = 6.0 * u  # Größe der Fehlernummern
    placed = []
    for ref in sorted(problem_refs):
        bx, by, bw, bh = bodies[ref]
        ops.append("%.2f %.2f %.2f %.2f re" % (px(bx), py(by + bh), bw * k, bh * k))
        paint(None, "#404040", 0.35)
        text(bx, by - 0.8 * u, lab, ref, "#404040", True)
        placed.append((bx, by - 0.8 * u - lab, bx + 0.62 * lab * len(ref), by - 0.8 * u))
    points = [(v[2], v[3]) for v in violations]
    rings = []
    for i, (x, y) in enumerate(points):
        nearest = min((((x - qx) ** 2 + (y - qy) ** 2) ** 0.5 for j, (qx, qy) in enumerate(points) if j != i),
                      default=mark * 3)
        rings.append((x, y, min(mark, max(0.5, 0.42 * nearest))))
    for x, y, r in rings:
        circle(x, y, r, None, magenta, max(r / 8, 0.12))

    def blocked(rect):
        x1, y1, x2, y2 = rect
        if x1 < ox or x2 > ox + page_w or y1 < oy or y2 > y0 + h + margin:
            return True
        for rx, ry, rr in rings:
            dx = rx - min(max(rx, x1), x2)
            dy = ry - min(max(ry, y1), y2)
            if dx * dx + dy * dy < (rr + 0.2 * u) ** 2:
                return True
        return any(not (x2 < a or x1 > c or y2 < b or y1 > d) for a, b, c, d in placed)

    directions = [(1, 0), (-1, 0), (0, -1), (0, 1), (1, -1), (1, 1), (-1, -1), (-1, 1)]
    for n, (kind, items) in enumerate(groups, 1):
        label = str(n)
        wl = 0.62 * num * len(label)
        for v in items:
            i = violations.index(v)
            x, y, r = rings[i]
            spot = None
            for extra in (0.4 * u, 1.2 * u, 2.4 * u):
                for dx, dy in directions:
                    ax, ay = x + dx * (r + extra), y + dy * (r + extra)
                    rx = ax if dx > 0 else ax - wl if dx < 0 else ax - wl / 2
                    ry = ay if dy > 0 else ay - num if dy < 0 else ay - num / 2
                    rect = (rx, ry, rx + wl, ry + num)
                    if not blocked(rect):
                        spot = rect
                        break
                if spot:
                    break
            if spot:
                placed.append(spot)
                text(spot[0], spot[1] + num * 0.85, num, label, magenta, True)

    ty = y0 + h + margin + legend_fs
    text(x0, ty, legend_fs, "Risikokarte: %d Problemstellen in %d Gruppen" % (len(violations), len(groups)),
         bold=True)
    for line in legend:
        ty += line_h
        text(x0, ty, legend_fs, line)

    content = "\n".join(ops).encode("cp1252", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>" % (page_w * k, page_h * k),
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % n + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        pdf += b"%010d 00000 n \n" % off
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "wb") as f:
            f.write(pdf)
    except PermissionError:  # alte Karte ist im PDF-Viewer geöffnet und gesperrt
        base, ext = os.path.splitext(path)
        path = "%s_%s%s" % (base, time.strftime("%H%M%S"), ext)
        with open(path, "wb") as f:
            f.write(pdf)
    return path


@on_board_copy()
def run_preflight(board, board_file, cfg, log):
    # Zeilenformat "test|STATUS|Text": der Dialog zeigt pro Test ein Symbol an
    box = board.GetBoardEdgesBoundingBox()
    w, h = pcbnew.ToMM(box.GetWidth()), pcbnew.ToMM(box.GetHeight())
    fits = (w <= A4_MM[0] and h <= A4_MM[1]) or (w <= A4_MM[1] and h <= A4_MM[0])
    log("size|%s|Größe %.1f x %.1f mm%s" % ("OK" if fits else "WARN", w, h,
                                              "" if fits else " - passt nicht ins Ätzbad (A4)"))

    n = len(fiducials(board))
    log("fid|OK|3 Passermarken" if n == 3
        else "fid|FEHLER|%d Passermarken (FID*), 3 für den LPKF nötig" % n)

    violations = []
    data = _run_drc(board.GetFileName(), cfg)
    if data is None:
        log("drc|WARN|Breiten-/Abstandsprüfung übersprungen: kicad-cli nicht gefunden")
    else:
        for v in data.get("violations", []):
            items = v.get("items") or []
            pos = items[0].get("pos") if items else None
            if v.get("type") in DRC_LABELS and pos:
                violations.append((v["type"], v.get("description", ""), pos["x"], pos["y"]))
        if not violations:
            log("drc|OK|Breiten, Via-Größen und Abstände in Ordnung")
        counts = {}
        for kind, *_ in violations:
            counts[kind] = counts.get(kind, 0) + 1
        for kind, count in counts.items():
            log("drc|WARN|%dx %s" % (count, DRC_LABELS[kind]))

    close = _close_vias(board, cfg["min_via_gap"])
    log("via|WARN|%d Via-Paare enger als %.2f mm (Handlöten)" % (len(close), cfg["min_via_gap"])
        if close else "via|OK|Via-Abstände ausreichend")
    violations += close

    both = _both_side_pads(board)
    if both:
        log("solder|WARN|%d Pins müssen auf Top und Bottom gelötet werden (keine Galvanik)" % len(both))
        for _, desc, _, _ in both[:10]:
            log("solder|WARN|  " + desc)
    else:
        log("solder|OK|Keine Pins mit Kupfer auf beiden Seiten")

    covered = _covered_pads(board)
    if covered:
        log("access|WARN|%d Pins mit Bahn auf der Bauteilseite unter dem Gehäuse (ggf. auf die "
            "Gegenseite routen)" % len(covered))
        for _, desc, _, _ in covered[:10]:
            log("access|WARN|  " + desc)
    else:
        log("access|OK|Alle Pins von der Bauteilseite erreichbar")
    both_keys = {(d, x, y) for _, d, x, y in both}
    covered_keys = {(d, x, y) for _, d, x, y in covered}
    violations += [("both_covered" if (d, x, y) in covered_keys else k, d, x, y) for k, d, x, y in both]
    violations += [c for c in covered if (c[1], c[2], c[3]) not in both_keys]
    if not violations:
        return None

    path = os.path.join(export_dir(board_file),
                        os.path.splitext(os.path.basename(board_file))[0] + "_Risikokarte.pdf")
    path = _write_risk_pdf(board, violations, path)
    log("Risikokarte: " + path)
    return path


def apply_defaults(board, cfg, log):
    """Setzt Leiterbahnbreiten und Durchkontaktierungen des offenen Boards auf die Standardwerte."""
    try:
        power = re.compile(_power_pattern(cfg)) if _power_pattern(cfg) else None
    except re.error as exc:
        raise RuntimeError("Ungültiger Regex für VCC/GND-Netze: %s" % exc)
    mm = pcbnew.FromMM
    n_tracks = n_vias = 0
    for t in board.GetTracks():
        is_power = bool(power and power.search(t.GetNetname()))
        if isinstance(t, pcbnew.PCB_VIA):
            if t.GetViaType() != pcbnew.VIATYPE_THROUGH:
                continue
            drill, dia = (cfg["pw_via_drill"], cfg["pw_via_dia"]) if is_power else \
                         (cfg["sg_via_drill"], cfg["sg_via_dia"])
            t.SetDrill(mm(drill))
            _set_via_width(t, mm(dia))
            n_vias += 1
        else:
            t.SetWidth(mm(cfg["pw_track"] if is_power else cfg["sg_track"]))
            n_tracks += 1
    pcbnew.Refresh()
    log("%d Leiterbahnen und %d Vias angepasst. Bitte prüfen und speichern (Strg+S), "
        "der Export nutzt die gespeicherte Datei." % (n_tracks, n_vias))


def ping_server(url, timeout=3):
    """True, wenn der Belichter-Server antwortet (auch mit HTTP-Fehlercode)."""
    try:
        urlopen(url, timeout=timeout).close()
        return True
    except HTTPError:
        return True
    except (URLError, OSError, ValueError):
        return False


# ------------------------------------------------- Ausführung in eigenem Prozess

TASKS = {"stencil": run_stencil, "lpkf": run_lpkf, "uv": run_uv, "preflight": run_preflight}


def _find_python():
    names = ("python.exe", "pythonw.exe") if os.name == "nt" else ("python3", "python")
    if os.path.basename(sys.executable).lower().startswith("python"):
        return sys.executable
    for d in (os.path.dirname(sys.executable), sys.prefix):
        for n in names:
            path = os.path.join(d, n)
            if os.path.exists(path):
                return path
    return None


def _clean(text):
    """Zeilen ohne das harmlose SWIG-Leak-Rauschen von KiCad."""
    return [l for l in text.splitlines() if l.strip() and "memory leak of type" not in l]


def _execute(name, board_file, cfg, log):
    """Task in frischem Python-Prozess starten (kein gemeinsamer Zustand mit dem Editor)."""
    python = _find_python()
    if python is None:
        return TASKS[name](board_file, cfg, log)

    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run([python, runner, name, board_file, json.dumps(cfg)],
                         capture_output=True, encoding="utf-8", errors="replace",
                         env=env, creationflags=flags)
    result = None
    for line in _clean(res.stdout):
        if line.startswith("RESULT:"):
            result = line[len("RESULT:"):]
        else:
            log(line)
    err = _clean(res.stderr)
    if res.returncode == 2:
        raise FidError(err[-1] if err else "Passermarken fehlen.")
    if res.returncode != 0:
        raise RuntimeError(err[-1] if err else "Unbekannter Fehler")
    return result


def run_isolated(name, board_file, cfg, log):
    try:
        return _execute(name, board_file, cfg, log)
    except FidError as exc:
        if not _fid_bypass():
            raise RuntimeError(str(exc))
        return _execute(name, board_file, dict(cfg, skip_fid_check=True), log)
