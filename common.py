"""Gemeinsame Hilfsfunktionen (KiCad 9/10 kompatibel)."""
import json
import os
import re

import pcbnew

_m = re.search(r"\d+", pcbnew.Version())
KICAD_MAJOR = int(_m.group(0)) if _m else 9

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".kicad_rt_toolkit.json")

DEFAULT_CONFIG = {
    "stencil_enabled": True,
    "stencil_svg": True,
    "stencil_gerber": True,
    "stencil_fiducials": True,
    "lpkf_enabled": True,
    "lpkf_pdf": True,
    "lpkf_gerber": True,
    "lpkf_drill": True,
    "pw_track": 0.4,
    "pw_via_drill": 0.8,
    "pw_via_dia": 1.4,
    "sg_track": 0.3,
    "sg_via_drill": 0.6,
    "sg_via_dia": 1.2,
    "min_clearance": 0.3,
    "min_via_gap": 0.6,
    "power_regex": "GND|VCC",
    "sch_enabled": True,
    "sch_bw": False,
    "uv_enabled": False,
    "uv_upload": True,
    "uv_server": "http://10.10.13.190:8000/",
    "uv_user": "",
    "uv_exposure": 60,
    "uv_x_offset": 0,
    "uv_y_offset": 0,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in cfg})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def set_opt(popt, name, *args):
    """Setter aufrufen, falls er in dieser KiCad-Version existiert."""
    fn = getattr(popt, name, None)
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        pass


def plot(board, out_dir, name, layers, fmt, description, configure=None):
    """Plottet einen oder mehrere Layer in eine Datei und liefert den Dateipfad."""
    os.makedirs(out_dir, exist_ok=True)
    pctl = pcbnew.PLOT_CONTROLLER(board)
    popt = pctl.GetPlotOptions()
    popt.SetOutputDirectory(out_dir)
    if configure:
        configure(popt)
    pctl.SetLayer(layers[0])
    pctl.OpenPlotfile(name, fmt, description)
    if len(layers) > 1:
        seq = pcbnew.LSEQ()
        for layer in layers:
            seq.push_back(layer)
        pctl.PlotLayers(seq)
    else:
        pctl.PlotLayer()
    path = pctl.GetPlotFileName()
    pctl.ClosePlot()
    return path


def fiducials(board):
    return [fp for fp in board.GetFootprints() if fp.GetReference().upper().startswith("FID")]
