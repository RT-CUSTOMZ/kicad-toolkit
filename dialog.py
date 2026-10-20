import os
import re
import threading
from functools import partial

import pcbnew
import wx

from . import tasks
from .common import load_config, save_config

HEADER_BG = wx.Colour(30, 90, 140)
BLUE = wx.Colour(30, 90, 140)
GREEN = wx.Colour(46, 125, 50)
ORANGE = wx.Colour(230, 130, 0)
RED = wx.Colour(198, 40, 40)
GRAY = wx.Colour(120, 120, 120)


def _bold(ctrl, size_delta=0):
    font = ctrl.GetFont()
    font.SetWeight(wx.FONTWEIGHT_BOLD)
    if size_delta:
        font.SetPointSize(font.GetPointSize() + size_delta)
    ctrl.SetFont(font)


class Card(wx.Panel):
    """Ein Bereich mit Haken, Beschreibung, Optionen und Statusanzeige."""

    def __init__(self, parent, dialog, key, title, subtitle, on_change=None):
        super().__init__(parent, style=wx.BORDER_SIMPLE)
        self.dialog = dialog
        self.on_change = on_change
        self.options = []

        outer = wx.BoxSizer(wx.VERTICAL)
        head = wx.BoxSizer(wx.HORIZONTAL)
        self.check = (dialog.reg(wx.CheckBox(self, label=title), key) if key
                      else wx.StaticText(self, label=title))
        _bold(self.check, 1)
        self.status = wx.StaticText(self, label="")
        _bold(self.status)
        head.Add(self.check, 1, wx.ALIGN_CENTER_VERTICAL)
        head.Add(self.status, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(head, 0, wx.EXPAND | wx.ALL, 8)

        sub = wx.StaticText(self, label=subtitle)
        sub.SetForegroundColour(GRAY)
        outer.Add(sub, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.body = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self.body, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(outer)
        if key:
            self.check.Bind(wx.EVT_CHECKBOX, lambda e: self.refresh())

    def add_checks(self, items):
        wrap = wx.WrapSizer(wx.HORIZONTAL)
        out = {}
        for label, key, tip in items:
            chk = self.dialog.reg(wx.CheckBox(self, label=label), key)
            chk.SetToolTip(tip)
            wrap.Add(chk, 0, wx.RIGHT | wx.BOTTOM, 16)
            self.options.append(chk)
            out[key] = chk
        self.body.Add(wrap, 0, wx.EXPAND | wx.LEFT, 22)
        return out

    def add_grid(self, rows):
        grid = wx.FlexGridSizer(4, 6, 12)
        grid.AddGrowableCol(1)
        grid.AddGrowableCol(3)
        out = {}
        for label, key, kind, tip in rows:
            if kind == "text":
                ctrl = wx.TextCtrl(self, value=str(self.dialog.cfg[key]))
            elif len(kind) == 3:
                lo, hi, inc = kind
                ctrl = wx.SpinCtrlDouble(self, min=lo, max=hi, initial=float(self.dialog.cfg[key]), inc=inc)
                ctrl.SetDigits(2)
            else:
                lo, hi = kind
                ctrl = wx.SpinCtrl(self, min=lo, max=hi, initial=int(self.dialog.cfg[key]))
            ctrl.SetToolTip(tip)
            self.dialog.reg(ctrl, key)
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
            self.options.append(ctrl)
            out[key] = ctrl
        self.body.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.TOP, 22)
        return out

    def refresh(self):
        on = self.check.GetValue() if isinstance(self.check, wx.CheckBox) else True
        for o in self.options:
            o.Enable(on)
        if self.on_change:
            self.on_change()

    def set_status(self, text, colour=GRAY):
        self.status.SetLabel(text)
        self.status.SetForegroundColour(colour)
        self.Layout()


class ToolkitDialog(wx.Dialog):
    def __init__(self, parent, board_file):
        super().__init__(parent, title="RT Toolkit", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.board_file = board_file
        self.cfg = load_config()
        self.upload_url = None
        self._ctrls = {}

        root = wx.BoxSizer(wx.VERTICAL)
        scroll = wx.ScrolledWindow(self, style=wx.VSCROLL)
        scroll.SetScrollRate(0, 20)
        display_height = wx.GetDisplaySize().GetHeight()
        scroll_height = max(240, min(600, display_height - 360))
        scroll.SetMinSize((580, scroll_height))
        content = wx.BoxSizer(wx.VERTICAL)

        header = wx.Panel(scroll)
        header.SetBackgroundColour(HEADER_BG)
        hs = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(header, label="RT Toolkit")
        _bold(title, 6)
        title.SetForegroundColour(wx.WHITE)
        board = wx.StaticText(header, label="Platine: " + os.path.basename(board_file))
        board.SetForegroundColour(wx.Colour(210, 225, 240))
        hs.Add(title, 0, wx.LEFT | wx.TOP | wx.RIGHT, 14)
        hs.Add(board, 0, wx.LEFT | wx.BOTTOM | wx.RIGHT, 14)
        header.SetSizer(hs)
        content.Add(header, 0, wx.EXPAND)

        body = wx.BoxSizer(wx.VERTICAL)

        self.card_tools = Card(scroll, self, None, "Vorab-Prüfung & Standardwerte",
                               "Prüft Größe, Passermarken, Breiten und Abstände; setzt Bahnen und Vias auf Standardwerte")
        self.card_tools.add_grid([
            ("VCC/GND Bahn (mm)", "pw_track", (0.1, 5.0, 0.05), "Leiterbahnbreite der VCC/GND-Netze"),
            ("Signal Bahn (mm)", "sg_track", (0.1, 5.0, 0.05), "Leiterbahnbreite der Signale"),
            ("VCC/GND Via-Loch", "pw_via_drill", (0.2, 5.0, 0.05), "Bohrdurchmesser der VCC/GND-Vias"),
            ("Signal Via-Loch", "sg_via_drill", (0.2, 5.0, 0.05), "Bohrdurchmesser Signal-Vias"),
            ("VCC/GND Via-Ø", "pw_via_dia", (0.4, 6.0, 0.05), "Via-Außendurchmesser (Pad)"),
            ("Signal Via-Ø", "sg_via_dia", (0.4, 6.0, 0.05), "Via-Außendurchmesser (Pad)"),
            ("Min. Abstand (mm)", "min_clearance", (0.05, 2.0, 0.05), "Kleinster zuverlässig ätzbarer Abstand"),
            ("Min. Via-Abstand", "min_via_gap", (0.0, 3.0, 0.05),
             "Abstand zwischen den Via-Rändern, damit Drahtvias von Hand lötbar bleiben"),
            ("VCC/GND-Netze", "power_regex", "text", "Regex auf den Netznamen, z. B. GND|VCC")])
        self._test_labels = {}
        test_row = wx.WrapSizer(wx.HORIZONTAL)
        tests = (
            ("size", "Größe", "Passt die Platine ins Ätzbad (A4, 210 × 297 mm, auch gedreht)?\n"
                                  "Der Fräsbereich des LPKF ist größer und wird nicht geprüft."),
            ("fid", "Passermarken", "Der LPKF braucht genau 3 Passermarken (Footprints mit Referenz FID...)."),
            ("drc", "Breiten/Abstände", "Prüft mit den Standardwerten oben (Bahnbreite, Via-Größe, Bohrung, Restring,\n"
                                         "Mindestabstand) auf Stellen, die sich schlecht ätzen oder fräsen lassen."),
            ("via", "Via-Abstände", "Zwei Vias liegen so nah beieinander, dass das Handlöten der Drahtvias schwierig\n"
                                    "wird (Rand-zu-Rand-Abstand unter 'Min. Via-Abstand')."),
            ("solder", "Beidseitig löten", "Durchsteck-Pins mit Kupfer (Leiterbahn oder Fläche) auf Top UND Bottom.\n"
                                           "Ohne Galvanik müssen sie von beiden Seiten gelötet werden."),
            ("access", "Zugänglichkeit", "Durchsteck-Pins mit Kupfer auf der Bauteilseite, die unter dem Gehäuse liegen\n"
                                         "(z. B. Stiftleisten, Elkos, Relais, Wannenstecker). Dort ist Löten schwer;\n"
                                         "besser auf der Gegenseite routen."))
        for key, name, tip in tests:
            lbl = wx.StaticText(self.card_tools, label="○ " + name)
            lbl.SetToolTip(tip)
            lbl.SetForegroundColour(GRAY)
            self._test_labels[key] = (lbl, name)
            test_row.Add(lbl, 0, wx.RIGHT | wx.BOTTOM, 16)
        self.card_tools.body.Add(test_row, 0, wx.EXPAND | wx.LEFT | wx.TOP, 22)
        tool_row = wx.BoxSizer(wx.HORIZONTAL)
        self.check_btn = wx.Button(self.card_tools, label="Platine prüfen")
        self.apply_btn = wx.Button(self.card_tools, label="Standardwerte auf Platine anwenden")
        self.check_btn.SetToolTip("Prüft die gespeicherte Platine auf die Punkte oben und erzeugt bei Problemen\n"
                                  "eine Risikokarte (PDF).")
        self.apply_btn.SetToolTip("Setzt alle Leiterbahnen und Durchgangs-Vias im geöffneten Board auf die\n"
                                  "Standardwerte (VCC/GND und Signal). Das Board im Editor wird verändert.")
        tool_row.Add(self.check_btn, 0, wx.RIGHT, 8)
        tool_row.Add(self.apply_btn, 0)
        self.card_tools.body.Add(tool_row, 0, wx.LEFT | wx.TOP, 22)
        body.Add(self.card_tools, 0, wx.EXPAND | wx.BOTTOM, 8)

        self.card_sch = Card(scroll, self, "sch_enabled", "Schaltplan-PDF",
                             "Schaltplan als PDF in den Projektordner")
        self.card_sch.add_checks([("Schwarz-Weiß", "sch_bw", "Ohne Farben ausgeben")])
        self.card_sch.check.SetToolTip("Schaltplan per kicad-cli als PDF exportieren.\n"
                                       "Er muss denselben Namen wie die Platine haben.")

        self.card_stencil = Card(scroll, self, "stencil_enabled", "Viaschablone",
                                 "Schablone für den Lasercutter (nur Vias und Umriss)")
        self.card_stencil.add_checks([
            ("SVG", "stencil_svg", "Rote 0.01 mm Kontur für den Laser"),
            ("Gerber", "stencil_gerber", "Gerber-Datei der Schablone"),
            ("Passermarken", "stencil_fiducials", "Passermarken (Footprints FID...) mit in die Schablone ausgeben")])
        self.card_stencil.check.SetToolTip("Schablone für den Lasercutter: nur Vias, Platinenumriss\n"
                                           "und optional Passermarken.")

        self.card_lpkf = Card(scroll, self, "lpkf_enabled", "Fräs-/Bohrplotter",
                              "Gerber, PDF und Bohrdaten in RT-production/LPKF")
        self.card_lpkf.add_checks([
            ("PDF", "lpkf_pdf", "CuTop (gespiegelt) und CuBottom"),
            ("Gerber", "lpkf_gerber", "Cu, Edge.Cuts und Passermarken"),
            ("Bohrdaten", "lpkf_drill", "Bohrdateien, Map und Report")])
        self.card_lpkf.check.SetToolTip("Daten für den LPKF Fräs-/Bohrplotter. Braucht genau 3 Passermarken (FID...).")

        self.card_uv = Card(scroll, self, "uv_enabled", "Platinenbelichter (UV)",
                            "UV-Gerber erzeugen und zum Belichter hochladen",
                            on_change=self._refresh_uv)
        up = self.card_uv.add_checks([("Nach der Erzeugung hochladen", "uv_upload",
                                       "Ohne Haken werden nur die Dateien erzeugt")])
        self.uv_upload = up["uv_upload"]
        self.card_uv.check.SetToolTip("UV-Gerber (Top und Bottom) für den Belichter erzeugen und optional hochladen.")
        self.uv_upload.Bind(wx.EVT_CHECKBOX, lambda e: self._refresh_uv())
        grid = self.card_uv.add_grid([
            ("Server", "uv_server", "text", "Adresse des Belichter-Servers"),
            ("Benutzer", "uv_user", "text", "Benutzer- oder Projektname (optional)"),
            ("Belichtung (s)", "uv_exposure", (1, 1200), "Belichtungszeit in Sekunden"),
            ("X-Offset (mm)", "uv_x_offset", (0, 128), "Verschiebung in X"),
            ("Y-Offset (mm)", "uv_y_offset", (0, 128), "Verschiebung in Y")])
        self.uv_server = grid["uv_server"]
        self._uv_fields = list(grid.values())

        state_row = wx.BoxSizer(wx.HORIZONTAL)
        self.uv_state = wx.StaticText(self.card_uv, label="● Belichter: unbekannt")
        self.uv_state.SetForegroundColour(GRAY)
        _bold(self.uv_state)
        ping_btn = wx.Button(self.card_uv, label="Status prüfen")
        ping_btn.SetToolTip("Prüft, ob der Belichter-Server erreichbar ist (eingeschaltet?).")
        self.uv_state.SetToolTip("Grün: Belichter antwortet. Rot: nicht erreichbar, eventuell noch einschalten.")
        state_row.Add(self.uv_state, 1, wx.ALIGN_CENTER_VERTICAL)
        state_row.Add(ping_btn, 0)
        self.card_uv.body.Add(state_row, 0, wx.EXPAND | wx.LEFT | wx.TOP, 22)
        ping_btn.Bind(wx.EVT_BUTTON, lambda e: self._check_uv())

        self.cards = [self.card_sch, self.card_stencil, self.card_lpkf, self.card_uv]
        for card in self.cards:
            body.Add(card, 0, wx.EXPAND | wx.BOTTOM, 8)

        content.Add(body, 0, wx.EXPAND | wx.ALL, 12)
        scroll.SetSizer(content)
        scroll.FitInside()
        root.Add(scroll, 1, wx.EXPAND | wx.ALL, 12)

        self.gauge = wx.Gauge(self, range=1, size=(-1, 8))
        root.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.log_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
                                    size=(-1, 110))
        self.log_ctrl.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                                      wx.FONTWEIGHT_NORMAL))
        self.log_ctrl.SetToolTip("Meldungen und Fehler der Aktionen")
        root.Add(self.log_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        folder_btn = wx.Button(self, label="Ausgabeordner öffnen")
        self.run_btn = wx.Button(self, label="▶  Ausgewählte ausführen")
        folder_btn.SetToolTip("Öffnet den Ordner RT-production im Projektordner.")
        self.run_btn.SetToolTip("Führt alle angehakten Bereiche nacheinander aus.")
        _bold(self.run_btn)
        self.run_btn.SetDefault()
        btns.Add(folder_btn, 0, wx.RIGHT, 8)
        btns.AddStretchSpacer()
        btns.Add(self.run_btn, 0, wx.RIGHT, 8)
        btns.Add(wx.Button(self, wx.ID_CLOSE, "Schließen"), 0)
        root.Add(btns, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizerAndFit(root)
        self.SetMinSize((580, self.GetSize().GetHeight()))
        self._tidy_tooltips(self)

        for card in self.cards:
            card.refresh()

        folder_btn.Bind(wx.EVT_BUTTON, lambda e: wx.LaunchDefaultApplication(
            tasks.export_dir(self.board_file)))
        self.run_btn.Bind(wx.EVT_BUTTON, self._on_run)
        self.check_btn.Bind(wx.EVT_BUTTON, self._on_check)
        self.apply_btn.Bind(wx.EVT_BUTTON, self._on_apply)
        self.Bind(wx.EVT_BUTTON, lambda e: self.Close(), id=wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        if self.cfg["uv_enabled"]:
            self._check_uv()

    def _tidy_tooltips(self, window):
        """Feste Zeilenumbrüche entfernen, damit wx die Tooltips selbst sauber umbricht."""
        wx.ToolTip.SetMaxWidth(380)
        for child in window.GetChildren():
            tip = child.GetToolTipText()
            if tip and "\n" in tip:
                child.SetToolTip(" ".join(tip.split()))
            self._tidy_tooltips(child)

    def reg(self, ctrl, key):
        """Steuerelement mit Konfigurationsschlüssel verknüpfen und Startwert setzen."""
        self._ctrls[key] = ctrl
        if isinstance(ctrl, wx.CheckBox):
            ctrl.SetValue(bool(self.cfg[key]))
        return ctrl

    def _refresh_uv(self):
        on = self.card_uv.check.GetValue()
        self.uv_upload.Enable(on)
        for w in self._uv_fields:
            w.Enable(on and self.uv_upload.GetValue())

    def _collect(self):
        for key, ctrl in self._ctrls.items():
            value = ctrl.GetValue()
            self.cfg[key] = value.strip() if isinstance(value, str) else value
        save_config(self.cfg)

    def _log(self, text):
        self.log_ctrl.AppendText(text + "\n")
        wx.GetApp().Yield()

    def _on_close(self, event):
        self._collect()
        event.Skip()

    def _set_busy(self, busy):
        for btn in (self.run_btn, self.check_btn, self.apply_btn):
            btn.Enable(not busy)

    def _check_uv(self):
        self.uv_state.SetForegroundColour(GRAY)
        self.uv_state.SetLabel("● Belichter: prüfe …")
        url = self.uv_server.GetValue().strip()
        threading.Thread(target=self._ping_worker, args=(url,), daemon=True).start()

    def _ping_worker(self, url):
        ok = tasks.ping_server(url)
        wx.CallAfter(self._show_uv_state, ok)

    def _show_uv_state(self, ok):
        try:
            self.uv_state.SetForegroundColour(GREEN if ok else RED)
            self.uv_state.SetLabel("● Belichter: erreichbar" if ok
                                   else "● Belichter: nicht erreichbar – eingeschaltet?")
            self.card_uv.Layout()
        except RuntimeError:
            pass  # Dialog wurde schon geschlossen

    def _show_test(self, key, severity):
        label, name = self._test_labels[key]
        symbol, colour = {"OK": ("✔", GREEN), "WARN": ("⚠", ORANGE), "FEHLER": ("✘", RED)}.get(
            severity, ("○", GRAY))
        label.SetLabel("%s %s" % (symbol, name))
        label.SetForegroundColour(colour)
        label.Refresh()
        self.card_tools.Layout()

    def _on_check(self, _event):
        self._collect()
        self._set_busy(True)
        self.card_tools.set_status("läuft …", BLUE)
        for key in self._test_labels:
            self._show_test(key, None)
        self._log("== Vorab-Prüfung ==")
        rank = {"OK": 0, "WARN": 1, "FEHLER": 2}
        results = {}

        def log(text):
            m = re.match(r"^(\w+)\|(OK|WARN|FEHLER)\|(.*)$", text)
            if not m:
                self._log(text)
                return
            key, severity, message = m.groups()
            if rank[severity] >= rank[results.get(key, "OK")]:
                results[key] = severity
            self._show_test(key, results[key])
            self._log("%-7s%s" % (severity, message))

        try:
            path = tasks.run_isolated("preflight", self.board_file, self.cfg, log)
            good = sum(1 for s in results.values() if s == "OK")
            worst = max(results.values(), key=rank.get, default="OK")
            self.card_tools.set_status("%s %d/%d in Ordnung" % (
                {"OK": "✔", "WARN": "⚠", "FEHLER": "✘"}[worst], good, len(self._test_labels)),
                {"OK": GREEN, "WARN": ORANGE, "FEHLER": RED}[worst])
            if path:
                wx.LaunchDefaultApplication(path)
        except Exception as exc:
            self.card_tools.set_status("✘ Fehler", RED)
            for key in self._test_labels:
                if key not in results:
                    self._show_test(key, "FEHLER")
            self._log("FEHLER: %s" % exc)
        finally:
            self._set_busy(False)

    def _on_apply(self, _event):
        self._collect()
        if wx.MessageBox("Alle Leiterbahnen und Durchkontaktierungen im offenen Board auf die "
                         "Standardwerte setzen?\n\nDas Board im Editor wird verändert.",
                         "Standardwerte anwenden", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
                         self) != wx.YES:
            return
        self._log("== Standardwerte ==")
        try:
            tasks.apply_defaults(pcbnew.GetBoard(), self.cfg, self._log)
            self.card_tools.set_status("✔ Angewendet", GREEN)
        except Exception as exc:
            self.card_tools.set_status("✘ Fehler", RED)
            self._log("FEHLER: %s" % exc)

    def _on_run(self, _event):
        self._collect()
        jobs = [(self.card_sch, "Schaltplan-PDF", tasks.run_schematic),
                (self.card_stencil, "Viaschablone", partial(tasks.run_isolated, "stencil")),
                (self.card_lpkf, "Fräs-/Bohrplotter", partial(tasks.run_isolated, "lpkf")),
                (self.card_uv, "Belichter", partial(tasks.run_isolated, "uv"))]
        active = [j for j in jobs if j[0].check.GetValue()]
        for card in self.cards:
            card.set_status("")
        if not active:
            self._log("Nichts ausgewählt.")
            return

        self._set_busy(True)
        self.upload_url = None
        self.gauge.SetRange(len(active))
        self.gauge.SetValue(0)
        try:
            for card, title, fn in active:
                card.set_status("läuft …", BLUE)
                self._log("== %s ==" % title)
                try:
                    result = fn(self.board_file, self.cfg, self._log)
                    if title == "Belichter" and result:
                        self.upload_url = result
                    card.set_status("✔ OK", GREEN)
                except Exception as exc:
                    card.set_status("✘ Fehler", RED)
                    self._log("FEHLER: %s" % exc)
                self.gauge.SetValue(self.gauge.GetValue() + 1)
        finally:
            self._set_busy(False)

        if self.upload_url and wx.MessageBox("Status-Seite des Belichters im Browser öffnen?",
                                             "Upload abgeschlossen",
                                             wx.YES_NO | wx.ICON_QUESTION, self) == wx.YES:
            wx.LaunchDefaultBrowser(self.upload_url)
