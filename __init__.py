import os

import pcbnew
import wx

from .dialog import ToolkitDialog


class RtToolkitPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "RT Toolkit (Viaschablone, Fräsplotter, Belichter)"
        self.category = "Fabrication"
        self.description = "Viaschablone, Fräs-/Bohrplotter-Daten und UV-Belichter-Upload aus einem Dialog."
        self.show_toolbar_button = True
        icon = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon):
            self.icon_file_name = icon

    def Run(self):
        board_file = pcbnew.GetBoard().GetFileName()
        if not board_file or not os.path.exists(board_file):
            wx.MessageBox("Bitte die Platine zuerst speichern (Strg+S).", "RT Toolkit",
                          wx.OK | wx.ICON_WARNING)
            return
        parent = wx.FindWindowByName("PcbFrame")
        dlg = ToolkitDialog(parent, board_file)
        dlg.ShowModal()
        dlg.Destroy()


RtToolkitPlugin().register()
