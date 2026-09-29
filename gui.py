"""
gui.py
Tkinter GUI for the tap tap box, with screen navigation:

- Splash ("Tap Tap Box", tap to begin)
- Main menu (New Test / Eligibility Data / Rhythm A Data / Rhythm B Data / Patients)
- New session (Patient ID + Arduino port)
- Test run (one screen for Eligibility, Rhythm A, Rhythm B, in sequence):
  automatically uploads the sketch to Arduino, then Start, then during the
  test shows the current phase and live tap count, finally "Valid" / "Invalid"
- Final results (statistics computed in Python from Arduino's raw data,
  broken down per phase for Rhythm A / Rhythm B)
- Data views: per single test (+ phase), and per patient (all 3 tests together)
- Patients: folders (drag & drop), favorites, view data, remove patient

All screens share a single serial connection and event queue, managed
centrally by the TapTapBoxGUI class.
"""

import os
import re
import time
import threading
import queue
import subprocess
import shutil
import getpass
import tkinter as tk
from tkinter import ttk, messagebox

from serial_comm import SerialReader
from test_protocol import TapTestProtocol, TestState
from results_store import ResultsStore, TEST_PHASES
from onscreen_keyboard import attach_text_keyboard
from ui_widgets import PillButton, RoundedCard
import arduino_flash
import stats_calculator
import xlsx_export

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Responsive sizing --------------------------------------------------
# The app now targets TWO very different displays: a normal HDMI monitor
# during development, and the 3.5" SPI touchscreen (roughly 480x320 or
# 320x480) that ships with the final device. Rather than hand-maintaining
# two full sets of pixel constants, every literal width/height below goes
# through px(), which scales it by UI_SCALE (computed once at startup from
# the ACTUAL screen resolution - see TapTapBoxGUI._apply_responsive_sizing).
# Fonts are handled separately (see same method) since point-size legibility
# doesn't shrink linearly with raw pixel count.
UI_SCALE = 1.0
SCREEN_W = 1920
SCREEN_H = 1080


def px(v, floor=None, cap_frac=None):
    """Scales a pixel size by the current UI_SCALE.
    floor: never go below this many pixels (keeps buttons/rows usable).
    cap_frac: never exceed this fraction of the actual screen width
    (keeps big cards from overflowing a small screen even if UI_SCALE
    alone wouldn't shrink them enough)."""
    result = round(v * UI_SCALE)
    if floor is not None:
        result = max(result, floor)
    if cap_frac is not None:
        result = min(result, round(SCREEN_W * cap_frac))
    return max(1, result)


def external_monitor_connected():
    """True se risulta collegato un monitor esterno via HDMI.
    Usato per bloccare la consultazione dei dati paziente sul piccolo
    schermo SPI: i file si guardano solo con un monitor vero collegato."""
    drm = "/sys/class/drm"
    try:
        for name in os.listdir(drm):
            if "HDMI" in name.upper():
                try:
                    with open(os.path.join(drm, name, "status")) as f:
                        if f.read().strip() == "connected":
                            return True
                except OSError:
                    pass
    except OSError:
        pass
    return False


def _force_repaint(root):
    """Forza il pannello SPI a ridisegnare TUTTA l'area: un lampo pieno a
    schermo (nero, poi via) genera un 'danno' completo che il compositore
    spinge subito sul pannello, cancellando il fantasma/sdoppiamento della
    schermata precedente. Serve perche' su questi pannelli il compositore
    aggiorna solo le zone cambiate e lascia residui."""
    try:
        cover = tk.Toplevel(root)
        cover.overrideredirect(True)
        cover.attributes("-topmost", True)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        cover.geometry(f"{sw}x{sh}+0+0")
        cover.configure(bg="#000000")
        cover.update_idletasks()
        time.sleep(0.04)
        cover.destroy()
        root.update_idletasks()
    except Exception:
        pass


# =====================================================================
#  Tastiera a schermo ridisegnata per il pannello piccolo (480x320).
#  La versione originale apriva un popup grande centrato che su 480x320
#  finiva fuori schermo: sembrava non aprirsi. Questa si aggancia in
#  basso, occupa tutta la larghezza e sta dentro lo schermo. Le funzioni
#  qui sotto SOSTITUISCONO quelle importate da onscreen_keyboard.
# =====================================================================
class _SmallKeyboard(tk.Toplevel):
    _open = None

    def __init__(self, root, entry, mode="text"):
        if _SmallKeyboard._open is not None:
            try:
                _SmallKeyboard._open.destroy()
            except Exception:
                pass
        super().__init__(root)
        _SmallKeyboard._open = self
        self.entry = entry
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#12333f")

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        kb_h = int(sh * 0.66)
        self.geometry(f"{sw}x{kb_h}+0+{sh - kb_h}")

        self.var = tk.StringVar(value=self.entry.get())
        tk.Label(self, textvariable=self.var,
                 font=("TkDefaultFont", max(12, int(sh * 0.06)), "bold"),
                 bg="white", fg="#12333f", anchor="w", padx=6).pack(fill="x", padx=3, pady=(3, 1))

        keys = tk.Frame(self, bg="#12333f")
        keys.pack(fill="both", expand=True, padx=2, pady=2)

        if mode == "numeric":
            rows = ["123", "456", "789", ".0-"]
        else:
            rows = ["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
        ncols = max(len(r) for r in rows)
        kf = ("TkDefaultFont", max(11, int(sh * 0.045)), "bold")

        for c in range(ncols):
            keys.columnconfigure(c, weight=1, uniform="kc")
        for r, rowstr in enumerate(rows):
            keys.rowconfigure(r, weight=1, uniform="kr")
            offset = (ncols - len(rowstr)) // 2
            for i, ch in enumerate(rowstr):
                tk.Button(keys, text=ch, font=kf, bg="#eaf3f8", fg="#12333f",
                          relief="raised", bd=1, activebackground="#1d6f8c",
                          command=lambda k=ch: self._type(k)
                          ).grid(row=r, column=offset + i, sticky="nsew", padx=1, pady=1)

        fr = len(rows)
        keys.rowconfigure(fr, weight=1, uniform="kr")
        third = max(1, ncols // 3)
        tk.Button(keys, text="Canc", font=kf, bg="#b23a3a", fg="white", bd=1,
                  command=self._back).grid(row=fr, column=0, columnspan=third,
                                           sticky="nsew", padx=1, pady=1)
        tk.Button(keys, text="Spazio", font=kf, bg="#555555", fg="white", bd=1,
                  command=lambda: self._type(" ")).grid(row=fr, column=third,
                                           columnspan=ncols - 2 * third, sticky="nsew", padx=1, pady=1)
        tk.Button(keys, text="OK", font=kf, bg="#2a9d6a", fg="white", bd=1,
                  command=self._confirm).grid(row=fr, column=ncols - third,
                                           columnspan=third, sticky="nsew", padx=1, pady=1)

        self.update_idletasks()
        self.lift()
        self.after(60, lambda: _force_repaint(root))

    def _type(self, k):
        self.var.set(self.var.get() + k)

    def _back(self):
        self.var.set(self.var.get()[:-1])

    def _confirm(self):
        self.entry.delete(0, tk.END)
        self.entry.insert(0, self.var.get())
        self.destroy()

    def destroy(self):
        if _SmallKeyboard._open is self:
            _SmallKeyboard._open = None
        super().destroy()


def attach_text_keyboard(entry, root):
    """Sostituisce quella importata: apre la tastiera piccola in basso."""
    entry.bind("<Button-1>", lambda e: _SmallKeyboard(root, entry, "text"))


def attach_numeric_keypad(entry, root):
    entry.bind("<Button-1>", lambda e: _SmallKeyboard(root, entry, "numeric"))


# --- Touch-friendly style / sizes (large-screen defaults; overwritten at
# runtime by _apply_responsive_sizing() once the real screen size is known) ---
FONT_TITLE = ("TkDefaultFont", 40, "bold")
FONT_SECTION = ("TkDefaultFont", 22, "bold")
FONT_NORMAL = ("TkDefaultFont", 15)
FONT_LABEL = ("TkDefaultFont", 15, "bold")
FONT_BIG_BTN = ("TkDefaultFont", 16, "bold")
FONT_HUGE_BTN = ("TkDefaultFont", 20, "bold")
FONT_PHASE = ("TkDefaultFont", 18, "bold")
FONT_TAP_COUNT = ("TkDefaultFont", 30, "bold")
FONT_TABLE = ("TkDefaultFont", 12)
FONT_SUBHEAD = ("TkDefaultFont", 15, "bold")
FONT_METRIC = ("TkDefaultFont", 14)

# Splash screen: bigger standalone title (kept separate from FONT_TITLE so
# the main menu heading is unaffected).
FONT_SPLASH_TITLE = ("TkDefaultFont", 76, "bold")

# Main menu: buttons are made a bit smaller (see show_menu) but with
# larger text, so these fonts are bumped up accordingly.
FONT_MENU_BTN = ("TkDefaultFont", 28, "bold")
FONT_MENU_BTN_ACCENT = ("TkDefaultFont", 32, "bold")

# New Test Session screen: everything enlarged.
FONT_SESSION_HEADER = ("TkDefaultFont", 32, "bold")
FONT_SESSION_LABEL = ("TkDefaultFont", 20, "bold")
FONT_SESSION_ENTRY = ("TkDefaultFont", 20)

# --- Color palette (no grey - soft clinical blue + warm accents) ---
COLOR_BG_SPLASH = "#cfe8f3"
COLOR_BG = "#EAF3F8"
COLOR_CARD = "#FFFFFF"
COLOR_PRIMARY = "#1D6F8C"
COLOR_PRIMARY_DARK = "#123F52"
COLOR_GREEN = "#2E9E4F"
COLOR_RED = "#D6483F"
COLOR_AMBER = "#DB9A2C"
COLOR_TEXT = "#17303D"
COLOR_MUTED = "#5C7A8A"
COLOR_STAR_ON = "#F2B705"
COLOR_STAR_OFF = "#B9C7CE"

TEST_ORDER = ["eligibility", "rhythmA", "rhythmB"]
TEST_LABELS = {
    "eligibility": "Eligibility Test",
    "rhythmA": "Rhythm A",
    "rhythmB": "Rhythm B",
}
SKETCH_DIRS = {
    "eligibility": os.path.join(BASE_DIR, "sketches", "eligibility"),
    "rhythmA": os.path.join(BASE_DIR, "sketches", "rhythmA"),
    "rhythmB": os.path.join(BASE_DIR, "sketches", "rhythmB"),
}
METRIC_LINES = [
    ("n_tap", "Tap count", ""),
    ("frequenza_media_hz", "Mean frequency", "Hz"),
    ("iti_medio_ms", "Mean ITI", "ms"),
    ("iti_sd_ms", "ITI SD", "ms"),
    ("asincronia_media_ms", "Mean asynchrony", "ms"),
    ("asincronia_sd_ms", "Asynchrony SD", "ms"),
    ("forza_media_adc", "Mean impact force", "ADC"),
]

# Maps (substring of Arduino's message) -> friendly label shown in the GUI.
# During the actual tapping phase we intentionally show NO phase text -
# only the raw per-tap table matters at that point.
PHASE_LABELS = [
    ("Listening phase", "Listening phase"),
    ("Start recording", ""),
    ("Start tapping", ""),
    ("Test completed", "Test completed, analyzing..."),
]


def friendly_phase(status_messages: list) -> str:
    if not status_messages:
        return ""
    last = status_messages[-1]
    for key, label in PHASE_LABELS:
        if key in last:
            return label
    return last


def metrics_text_lines(metrics: dict) -> list:
    lines = []
    for key, label, unit in METRIC_LINES:
        value = metrics.get(key, "")
        suffix = f" {unit}" if unit else ""
        lines.append(f"{label}: {value}{suffix}")
    return lines


class TapTapBoxGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Tap Tap Box")

        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        self._apply_responsive_sizing(w, h)
        self.root.overrideredirect(True)
        self.root.geometry(f"{w}x{h}+0+0")
        self.root.bind("<Escape>", lambda e: self.on_close())

        # I dialoghi nativi (messagebox) sono modali e su questa finestra a
        # schermo intero senza window manager (overrideredirect) restano
        # nascosti dietro, bloccando l'app. Li reindirizziamo a finestrelle
        # interne che compaiono davvero sul pannello (_info / _confirm_overlay).
        messagebox.showinfo = lambda title="", message="", **k: self._info(title, message)
        messagebox.showwarning = lambda title="", message="", **k: self._info(title, message)
        messagebox.showerror = lambda title="", message="", **k: self._info(title, message)
        messagebox.askyesno = lambda title="", message="", **k: self._confirm_overlay(title, message)

        self._setup_style()

        # --- State shared across screens ---
        self.data_queue = queue.Queue()
        self.serial_reader = SerialReader(self.data_queue, baudrate=9600)
        self.protocol = TapTestProtocol()
        self.store = ResultsStore(os.path.join(BASE_DIR, "sessions.csv"))

        self.port = None
        self.session_patient_id = None
        self.session_results = {}   # test_key -> {phase_key: {label, ...metrics}}
        self.session_raw_events = {}   # test_key -> [(timestamp_ms, forza_adc, stato_motore), ...]
        self.patients_folder_filter = "All"

        # Callbacks the active screen can register to react to queue events
        # (serial data lines, flash result).
        self.on_data_line = None
        self.on_flash_done = None

        # Single container: every screen clears and redraws inside this.
        self.content = tk.Frame(self.root, bg=COLOR_BG)
        self.content.pack(fill="both", expand=True)

        self._poll_queue()
        self.show_splash()

        # Precompila tutti gli sketch in background: la prima compilazione
        # e' lenta, ma da qui in poi ogni Start richiedera' solo un upload
        # rapido (pochi secondi) invece di una compilazione completa.
        # Precompilazione all'avvio DISATTIVATA: su Pi 3 saturava la CPU e
        # bloccava la GUI subito dopo l'apertura. Gli sketch vengono
        # compilati comunque (e messi in cache) al primo Start di ogni test.
        # threading.Thread(target=lambda: arduino_flash.prewarm_all(SKETCH_DIRS), daemon=True).start()

    def _apply_responsive_sizing(self, sw, sh):
        """
        Called once at startup, before any screen is built. Detects the
        small 3.5" touchscreen (roughly 480x320 / 320x480) vs a normal
        development monitor, and rescales every font + pixel size
        accordingly, so the exact same code produces a usable layout on
        either display.
        """
        global UI_SCALE, SCREEN_W, SCREEN_H
        global FONT_TITLE, FONT_SECTION, FONT_NORMAL, FONT_LABEL, FONT_BIG_BTN
        global FONT_HUGE_BTN, FONT_PHASE, FONT_TAP_COUNT, FONT_TABLE, FONT_SUBHEAD
        global FONT_METRIC, FONT_SPLASH_TITLE, FONT_MENU_BTN, FONT_MENU_BTN_ACCENT
        global FONT_SESSION_HEADER, FONT_SESSION_LABEL, FONT_SESSION_ENTRY

        SCREEN_W, SCREEN_H = sw, sh
        small_screen = sw <= 800 or sh <= 600
        UI_SCALE = 0.45 if small_screen else 1.0

        def f(pt, bold=False, min_pt=8):
            size = max(min_pt, round(pt * UI_SCALE))
            return ("TkDefaultFont", size, "bold") if bold else ("TkDefaultFont", size)

        FONT_TITLE = f(40, True)
        FONT_SECTION = f(22, True)
        FONT_NORMAL = f(15)
        FONT_LABEL = f(15, True)
        FONT_BIG_BTN = f(16, True)
        FONT_HUGE_BTN = f(20, True)
        FONT_PHASE = f(18, True)
        FONT_TAP_COUNT = f(30, True, min_pt=14)   # stays prominent even when tiny
        FONT_TABLE = f(12, min_pt=7)
        FONT_SUBHEAD = f(15, True)
        FONT_METRIC = f(14, min_pt=7)
        FONT_SPLASH_TITLE = f(76, True, min_pt=20)
        FONT_MENU_BTN = f(28, True)
        FONT_MENU_BTN_ACCENT = f(32, True)
        FONT_SESSION_HEADER = f(32, True)
        FONT_SESSION_LABEL = f(20, True)
        FONT_SESSION_ENTRY = f(20)

    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", font=FONT_BIG_BTN, padding=(10, 12))
        style.configure("TLabel", font=FONT_NORMAL, background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("TEntry", font=FONT_NORMAL)
        style.configure("TFrame", background=COLOR_BG)
        style.configure("Treeview", font=FONT_TABLE, rowheight=px(30, floor=20),
                         background=COLOR_CARD, fieldbackground=COLOR_CARD,
                         foreground=COLOR_TEXT, borderwidth=0)
        style.configure("Treeview.Heading", font=("TkDefaultFont", 12, "bold"),
                         background=COLOR_PRIMARY, foreground="white", relief="flat")
        style.map("Treeview.Heading", background=[("active", COLOR_PRIMARY_DARK)])
        style.map("Treeview", background=[("selected", COLOR_PRIMARY)],
                  foreground=[("selected", "white")])

    # ---------- helpers ----------
    def _confirm(self, title, message) -> bool:
        return self._confirm_overlay(title, message)

    def _dialog_geometry(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = max(240, int(sw * 0.88))
        h = max(150, int(sh * 0.60))
        return sw, sh, w, h, (sw - w) // 2, (sh - h) // 2

    def _info(self, title, message):
        """Finestrella informativa interna (sostituisce messagebox.show*):
        compare DAVVERO sopra la finestra a schermo intero. Si chiude con OK."""
        sw, sh, w, h, x, y = self._dialog_geometry()
        dlg = tk.Toplevel(self.root)
        dlg.overrideredirect(True)
        dlg.attributes("-topmost", True)
        dlg.configure(bg=COLOR_PRIMARY_DARK)
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        fr = tk.Frame(dlg, bg="white")
        fr.pack(fill="both", expand=True, padx=3, pady=3)
        tk.Label(fr, text=str(title), font=("TkDefaultFont", max(12, int(sh * 0.055)), "bold"),
                 bg="white", fg=COLOR_PRIMARY_DARK, wraplength=int(w * 0.9)).pack(pady=(int(sh * 0.03), 4), padx=6)
        tk.Label(fr, text=str(message), font=("TkDefaultFont", max(10, int(sh * 0.042))),
                 bg="white", fg="#333333", wraplength=int(w * 0.9), justify="center").pack(pady=4, padx=8, expand=True)
        tk.Button(fr, text="OK", font=("TkDefaultFont", max(11, int(sh * 0.05)), "bold"),
                  bg="#2a9d6a", fg="white", bd=1, padx=int(sw * 0.05), pady=int(sh * 0.02),
                  command=dlg.destroy).pack(pady=(4, int(sh * 0.03)))
        dlg.update_idletasks()
        dlg.lift()
        self.root.after(60, lambda: _force_repaint(self.root))

    def _confirm_overlay(self, title, message) -> bool:
        """Conferma Si/No interna (sostituisce messagebox.askyesno): compare
        sopra la finestra a schermo intero e ritorna True/False. Niente
        grab_set: su questo setup bloccherebbe l'app."""
        sw, sh, w, h, x, y = self._dialog_geometry()
        result = {"v": False}
        dlg = tk.Toplevel(self.root)
        dlg.overrideredirect(True)
        dlg.attributes("-topmost", True)
        dlg.configure(bg=COLOR_PRIMARY_DARK)
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        fr = tk.Frame(dlg, bg="white")
        fr.pack(fill="both", expand=True, padx=3, pady=3)
        tk.Label(fr, text=str(title), font=("TkDefaultFont", max(12, int(sh * 0.055)), "bold"),
                 bg="white", fg=COLOR_PRIMARY_DARK, wraplength=int(w * 0.9)).pack(pady=(int(sh * 0.03), 4), padx=6)
        tk.Label(fr, text=str(message), font=("TkDefaultFont", max(10, int(sh * 0.042))),
                 bg="white", fg="#333333", wraplength=int(w * 0.9), justify="center").pack(pady=4, padx=8, expand=True)
        row = tk.Frame(fr, bg="white")
        row.pack(pady=(4, int(sh * 0.03)))
        bf = ("TkDefaultFont", max(11, int(sh * 0.05)), "bold")

        def choose(v):
            result["v"] = v
            dlg.destroy()

        tk.Button(row, text="Si", font=bf, bg="#2a9d6a", fg="white", bd=1,
                  padx=int(sw * 0.04), pady=int(sh * 0.02),
                  command=lambda: choose(True)).pack(side="left", padx=6)
        tk.Button(row, text="No", font=bf, bg="#b23a3a", fg="white", bd=1,
                  padx=int(sw * 0.04), pady=int(sh * 0.02),
                  command=lambda: choose(False)).pack(side="left", padx=6)
        dlg.update_idletasks()
        dlg.lift()
        self.root.after(60, lambda: _force_repaint(self.root))
        self.root.wait_window(dlg)
        return result["v"]

    def _detect_usb_drives(self) -> list:
        """
        Ritorna i mount point delle chiavette USB attualmente inserite.
        Prima controlla se qualcosa e' gia' montato (via lsblk, che non
        dipende da un percorso fisso ne' da un vero automount) e, se la
        chiavetta e' inserita ma non ancora montata (capita spesso su
        immagini Ubuntu/Pi minimali senza automount attivo), prova a
        montarla da solo con udisksctl.
        """
        drives = self._mounted_removable_partitions()
        if drives:
            return drives
        self._try_automount_removable_partitions()
        return self._mounted_removable_partitions()

    def _list_removable_partitions(self) -> list:
        """[(device_name, mountpoint_or_empty), ...] per ogni partizione RM=1 (rimovibile)."""
        try:
            out = subprocess.run(
                ["lsblk", "-P", "-o", "NAME,MOUNTPOINT,RM,TYPE"],
                capture_output=True, text=True, timeout=5
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None  # lsblk non disponibile - impossibile diagnosticare
        result = []
        for line in out.stdout.splitlines():
            fields = dict(re.findall(r'(\w+)="([^"]*)"', line))
            if fields.get("TYPE") == "part" and fields.get("RM") == "1":
                result.append((fields.get("NAME", ""), fields.get("MOUNTPOINT", "")))
        return result

    def _mounted_removable_partitions(self) -> list:
        parts = self._list_removable_partitions()
        if not parts:
            return []
        return [mp for _name, mp in parts if mp]

    def _try_automount_removable_partitions(self):
        parts = self._list_removable_partitions()
        if not parts:
            return
        for name, mountpoint in parts:
            if mountpoint:
                continue  # gia' montata
            try:
                # stdin=DEVNULL: se polkit chiedesse comunque una password,
                # non trovando input da leggere fallisce subito invece di
                # restare in attesa e bloccare l'app.
                subprocess.run(["udisksctl", "mount", "-b", f"/dev/{name}"],
                                capture_output=True, text=True, timeout=6,
                                stdin=subprocess.DEVNULL)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return  # udisksctl non disponibile o bloccato, niente da fare

    def _style_tree_stripes(self, tree):
        tree.tag_configure("odd", background="#F2F8FB")
        tree.tag_configure("even", background=COLOR_CARD)

    # ---------- NAVIGATION ----------
    def _clear_content(self):
        self.on_data_line = None
        self.on_flash_done = None
        # The splash screen binds a "tap anywhere to begin" handler directly
        # on self.content (a widget shared by every screen, never recreated).
        # Destroying its children (below) does NOT remove that binding, so
        # without this it stays active forever: any stray tap on empty
        # background of ANY later screen - including the final results
        # screen - would silently kick the user back to the menu without
        # saving. Unbind it here every time a new screen is built.
        self.content.unbind("<Button-1>")
        for widget in self.content.winfo_children():
            widget.destroy()
        # Dopo che la nuova schermata e' stata costruita, forza un ridisegno
        # completo del pannello per cancellare il fantasma della precedente.
        self.root.after(60, lambda: _force_repaint(self.root))

    def _screen_header(self, text):
        tk.Label(self.content, text=text, font=FONT_SECTION, bg=COLOR_BG, fg=COLOR_PRIMARY_DARK).pack(pady=(24, 10))

    # ================= SCREEN: SPLASH =================
    def show_splash(self):
        self._clear_content()
        self.content.configure(bg=COLOR_BG_SPLASH)

        frame = tk.Frame(self.content, bg=COLOR_BG_SPLASH)
        frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            frame, text="Tap Tap Box", font=FONT_SPLASH_TITLE,
            bg=COLOR_BG_SPLASH, fg="#0d3b52"
        ).pack()
        tk.Label(
            frame, text="Tap the screen to begin", font=FONT_NORMAL,
            bg=COLOR_BG_SPLASH, fg="#0d3b52"
        ).pack(pady=(28, 0))

        self.content.bind("<Button-1>", lambda e: self.show_menu())
        frame.bind("<Button-1>", lambda e: self.show_menu())
        for child in frame.winfo_children():
            child.bind("<Button-1>", lambda e: self.show_menu())

    # ================= SCREEN: MAIN MENU =================
    def show_menu(self):
        self._clear_content()
        self.content.configure(bg=COLOR_BG)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        tk.Label(self.content, text="Tap Tap Box", font=FONT_TITLE, bg=COLOR_BG,
                 fg=COLOR_PRIMARY_DARK).pack(pady=(int(sh * 0.03), int(sh * 0.02)))

        # Card sized relative to the ACTUAL screen resolution, so the
        # buttons fill the screen on any display instead of sitting in a
        # tiny fixed-size box.
        card_w = int(sw * 0.94)
        card_h = int(sh * 0.78)
        card = RoundedCard(self.content, bg_color=COLOR_BG, fill=COLOR_CARD,
                            radius=36, width=card_w, height=card_h)
        card.pack(pady=6)
        inner = card.inner

        # Due soli pulsanti grandi, che riempiono la card: Patients e New Test.
        # (I vecchi pulsanti dati Eligibility/Rhythm A/B sono stati rimossi:
        # i dati si consultano solo con un monitor collegato, vedi Patients.)
        row = tk.Frame(inner, bg=COLOR_CARD)
        row.pack(fill="both", expand=True,
                 pady=int(card_h * 0.14), padx=int(card_w * 0.08))

        PillButton(row, text="Patients", command=self.show_patients_list,
                   bg_color=COLOR_CARD, fill=COLOR_PRIMARY,
                   width=int(card_w * 0.42), height=int(card_h * 0.55),
                   font=FONT_MENU_BTN).pack(side="left", fill="both", expand=True, padx=(0, 20))
        PillButton(row, text="NEW TEST", command=self.show_new_session,
                   bg_color=COLOR_CARD, fill=COLOR_GREEN,
                   width=int(card_w * 0.42), height=int(card_h * 0.55),
                   font=FONT_MENU_BTN_ACCENT).pack(side="left", fill="both", expand=True, padx=(20, 0))

        exit_lbl = tk.Label(self.content, text="Exit", font=FONT_NORMAL, bg=COLOR_BG,
                             fg=COLOR_MUTED, cursor="hand2")
        exit_lbl.pack(side="bottom", pady=int(sh * 0.02))
        exit_lbl.bind("<Button-1>", lambda e: self.on_close())

    # ================= SCREEN: NEW SESSION =================
    def show_new_session(self):
        self._clear_content()
        tk.Label(self.content, text="New Test Session", font=FONT_SESSION_HEADER, bg=COLOR_BG,
                 fg=COLOR_PRIMARY_DARK).pack(pady=(px(30, floor=8), px(16, floor=6)))

        card_w = min(px(760), round(SCREEN_W * 0.92))
        card_h = min(px(380), round(SCREEN_H * 0.55))
        card = RoundedCard(self.content, bg_color=COLOR_BG, fill=COLOR_CARD,
                            radius=px(32, floor=10), width=card_w, height=card_h)
        card.pack(pady=px(10, floor=4))
        form = card.inner

        ttk.Label(form, text="Patient ID:", font=FONT_SESSION_LABEL,
                  background=COLOR_CARD).grid(row=0, column=0, padx=px(14, floor=6), pady=px(26, floor=8), sticky="w")
        patient_var = tk.StringVar()
        patient_entry = ttk.Entry(form, textvariable=patient_var, font=FONT_SESSION_ENTRY, width=10)
        patient_entry.grid(row=0, column=1, padx=px(14, floor=6), pady=px(26, floor=8), ipady=px(16, floor=4))
        attach_text_keyboard(patient_entry, self.root)

        ttk.Label(form, text="Arduino Port:", font=FONT_SESSION_LABEL,
                  background=COLOR_CARD).grid(row=1, column=0, padx=px(14, floor=6), pady=px(22, floor=6), sticky="w")
        port_var = tk.StringVar()
        port_combo = ttk.Combobox(form, textvariable=port_var, state="readonly", font=FONT_SESSION_ENTRY, width=10)
        port_combo.grid(row=1, column=1, padx=px(14, floor=6), pady=px(22, floor=6), ipady=px(10, floor=2))

        def refresh_ports():
            ports = SerialReader.list_ports()
            port_combo["values"] = ports
            if ports:
                port_combo.current(0)

        refresh_ports()
        PillButton(form, text="Refresh", command=refresh_ports, bg_color=COLOR_CARD,
                   fill=COLOR_PRIMARY, width=px(140, floor=70), height=px(48, floor=28),
                   font=FONT_BIG_BTN).grid(row=1, column=2, padx=px(12, floor=4))

        def start_session():
            pid = patient_var.get().strip()
            port = port_var.get()
            if not pid:
                messagebox.showwarning("Warning", "Please enter the Patient ID.")
                return
            if not port:
                messagebox.showwarning("Warning", "Please select the Arduino port.")
                return

            self.session_patient_id = pid
            self.port = port
            self.session_results = {}
            self.session_raw_events = {}
            self.show_test_run("eligibility")

        btn_row = tk.Frame(self.content, bg=COLOR_BG)
        btn_row.pack(pady=px(30, floor=8))
        PillButton(btn_row, text="Back", command=self.show_menu, bg_color=COLOR_BG,
                   fill=COLOR_MUTED, width=px(180, floor=80), height=px(64, floor=30),
                   font=FONT_BIG_BTN).pack(side="left", padx=px(14, floor=6))
        PillButton(btn_row, text="Start", command=start_session, bg_color=COLOR_BG,
                   fill=COLOR_GREEN, width=px(220, floor=90), height=px(72, floor=34),
                   font=FONT_MENU_BTN).pack(side="left", padx=px(14, floor=6))

    # ================= SCREEN: TEST RUN =================
    def show_test_run(self, test_key: str):
        self._clear_content()
        # Every time we land on a test screen - whether it's a brand new
        # test or the same one restarted - we must start from a completely
        # empty state: no leftover taps, no leftover state from before.
        self.protocol.reset()
        test_label = TEST_LABELS[test_key]
        is_last = (test_key == TEST_ORDER[-1])

        tk.Label(self.content, text=test_label, font=FONT_SECTION, bg=COLOR_BG,
                 fg=COLOR_PRIMARY_DARK).pack(pady=(20, 4))
        tk.Label(self.content, text=f"Patient: {self.session_patient_id}", font=FONT_NORMAL,
                 bg=COLOR_BG, fg=COLOR_MUTED).pack(pady=(0, 8))

        # Annulla SEMPRE visibile in alto: su 480x320 la barra in basso
        # (Restart / Cancel Session) puo' finire fuori schermo, quindi
        # garantiamo qui una via d'uscita sempre raggiungibile.
        PillButton(self.content, text="\u2715 Annulla", command=self._cancel_session,
                   bg_color=COLOR_BG, fill=COLOR_RED, width=px(150, floor=90),
                   height=px(44, floor=28), font=FONT_BIG_BTN).pack(pady=(0, px(6, floor=2)))

        status_label = tk.Label(self.content, text="Uploading sketch to Arduino...",
                                 fg=COLOR_AMBER, bg=COLOR_BG, font=FONT_LABEL)
        status_label.pack(pady=px(6, floor=2))

        progress = ttk.Progressbar(self.content, mode="indeterminate", length=px(400, floor=160))
        progress.pack(pady=px(8, floor=3))
        progress.start(15)

        body = tk.Frame(self.content, bg=COLOR_BG)
        body.pack(expand=True, fill="both", padx=px(20, floor=6), pady=px(6, floor=2))

        def on_start():
            start_btn.set_enabled(False)
            self.protocol.start()
            self.serial_reader.send("s")

        start_btn = PillButton(body, text="START", command=on_start, bg_color=COLOR_BG,
                                fill=COLOR_GREEN, width=px(220, floor=110), height=px(70, floor=34), font=FONT_HUGE_BTN)
        start_btn.set_enabled(False)
        start_btn.pack(pady=px(16, floor=4))

        phase_label = tk.Label(body, text="", font=FONT_PHASE, bg=COLOR_BG, fg=COLOR_PRIMARY)
        phase_label.pack(pady=px(6, floor=1))

        tap_label = tk.Label(body, text="Taps: 0", font=FONT_TAP_COUNT, bg=COLOR_BG, fg=COLOR_TEXT)
        tap_label.pack(pady=(px(6, floor=1), px(4, floor=1)))

        # Tabella con i dati grezzi di ogni singolo tap, aggiornata in
        # tempo reale (le statistiche aggregate si vedono solo dopo,
        # nella schermata dei risultati finali).
        table_frame = tk.Frame(body, bg=COLOR_BG)
        table_frame.pack(pady=px(6, floor=2), fill="both", expand=True)

        tap_columns = ("t_ms", "force", "motor")
        tap_headers = ("Time (ms)", "Force (ADC)", "Motor State")
        tap_tree = ttk.Treeview(table_frame, columns=tap_columns, show="headings", height=px(8, floor=4))
        for col, head in zip(tap_columns, tap_headers):
            tap_tree.heading(col, text=head)
            tap_tree.column(col, width=px(140, floor=60), anchor="center")
        self._style_tree_stripes(tap_tree)
        tap_vsb = ttk.Scrollbar(table_frame, orient="vertical", command=tap_tree.yview)
        tap_tree.configure(yscrollcommand=tap_vsb.set)
        tap_tree.pack(side="left", fill="both", expand=True)
        tap_vsb.pack(side="right", fill="y")

        decision_frame = tk.Frame(body, bg=COLOR_BG)  # shown only once the test finishes

        def reset_for_retry():
            """Discards the current attempt and brings back the Start button
            for the SAME test (sketch is already on the Arduino, no reflash
            needed)."""
            self.protocol.reset()
            decision_frame.pack_forget()
            for w in decision_frame.winfo_children():
                w.destroy()
            for item in tap_tree.get_children():
                tap_tree.delete(item)
            tap_label.config(text="Taps: 0")
            phase_label.config(text="")
            start_btn.set_enabled(True)
            self.on_data_line = handle_data_line

        def handle_data_line(line: str):
            prev_count = len(self.protocol.events)
            self.protocol.process_line(line)

            if len(self.protocol.events) > prev_count:
                t, force, motor = self.protocol.events[-1]
                tag = "odd" if len(tap_tree.get_children()) % 2 else "even"
                tap_tree.insert("", "end", values=(f"{t:.0f}", f"{force:.0f}", int(motor)), tags=(tag,))
                tap_tree.yview_moveto(1)

            tap_label.config(text=f"Taps: {len(self.protocol.events)}")
            phase_label.config(text=friendly_phase(self.protocol.status_messages))

            if self.protocol.state == TestState.FINISHED:
                show_decision()

        def show_decision():
            self.on_data_line = None
            phase_label.config(text="Test completed")

            valid_text = "Valid - View Results" if is_last else "Valid - Next"

            def on_valid():
                metrics = stats_calculator.compute_stats(test_key, self.protocol.events)
                self.session_results[test_key] = metrics
                self.session_raw_events[test_key] = list(self.protocol.events)
                if is_last:
                    self.show_final_results()
                else:
                    next_key = TEST_ORDER[TEST_ORDER.index(test_key) + 1]
                    self.show_test_run(next_key)

            def on_invalid():
                if self._confirm("Invalid - Retry", "Discard this attempt and restart the test?"):
                    reset_for_retry()

            for w in decision_frame.winfo_children():
                w.destroy()
            PillButton(decision_frame, text=valid_text, command=on_valid, bg_color=COLOR_BG,
                       fill=COLOR_GREEN, width=px(240, floor=110), height=px(60, floor=30),
                       font=FONT_BIG_BTN).pack(side="left", padx=px(10, floor=4), pady=px(10, floor=4))
            PillButton(decision_frame, text="Invalid - Retry", command=on_invalid, bg_color=COLOR_BG,
                       fill=COLOR_RED, width=px(220, floor=110), height=px(60, floor=30),
                       font=FONT_BIG_BTN).pack(side="left", padx=px(10, floor=4), pady=px(10, floor=4))
            decision_frame.pack(pady=px(14, floor=4))

        self.on_data_line = handle_data_line

        # --- Automatically start uploading the sketch ---
        if self.serial_reader.ser and self.serial_reader.ser.is_open:
            self.serial_reader.disconnect()

        def flash_worker():
            result = arduino_flash.flash_sketch(SKETCH_DIRS[test_key], self.port)
            self.data_queue.put(("flash_done", result))

        def handle_flash_done(result):
            progress.stop()
            progress.pack_forget()
            if not result.success:
                status_label.config(text=f"Upload error: {result.message}", fg=COLOR_RED)
                messagebox.showerror("Arduino Upload Error", f"{result.message}\n\n{result.log}")
                return

            def reconnect():
                if self.serial_reader.connect(self.port):
                    status_label.config(text="Ready", fg=COLOR_GREEN)
                    start_btn.set_enabled(True)
                else:
                    status_label.config(text="Error reconnecting to serial port", fg=COLOR_RED)

            self.root.after(1000, reconnect)

        self.on_flash_done = handle_flash_done
        threading.Thread(target=flash_worker, daemon=True).start()

        def confirm_restart():
            if self._confirm("Restart Test", "Are you sure you want to restart this test? "
                                              "The Arduino will be stopped and the test will "
                                              "start over completely - all recorded taps for "
                                              "this test will be discarded."):
                # Stop the Arduino dead in its tracks right away (don't wait
                # for the reflash below to reset it).
                self._stop_arduino_now()
                # Re-running show_test_run for this same test_key redoes the
                # full startup sequence: it disconnects the serial port,
                # re-uploads the sketch (which resets the Arduino board),
                # reconnects, and only then re-enables the "Start" button -
                # exactly like starting the test fresh, rather than just
                # clearing the Python-side state while the Arduino could
                # still be mid-sequence.
                self.show_test_run(test_key)

        bottom_bar = tk.Frame(self.content, bg=COLOR_BG)
        bottom_bar.pack(side="bottom", pady=px(14, floor=4))
        PillButton(bottom_bar, text="Restart Test", command=confirm_restart, bg_color=COLOR_BG,
                   fill=COLOR_AMBER, width=px(200, floor=100), height=px(52, floor=28),
                   font=FONT_BIG_BTN).pack(side="left", padx=px(10, floor=4))
        PillButton(bottom_bar, text="Cancel Session", command=self._cancel_session, bg_color=COLOR_BG,
                   fill=COLOR_RED, width=px(200, floor=100), height=px(52, floor=28),
                   font=FONT_BIG_BTN).pack(side="left", padx=px(10, floor=4))

    def _cancel_session(self):
        if self._confirm("Cancel Session", "Are you sure you want to cancel the whole session? "
                                            "All tests recorded so far will be discarded."):
            # Stop the Arduino dead in its tracks right away.
            self._stop_arduino_now()
            self.session_results = {}
            self.session_raw_events = {}
            self.session_patient_id = None
            self.show_menu()

    def _stop_arduino_now(self):
        """
        Immediately halts whatever the Arduino is doing (mid-test motor
        pulses, etc.): closes our connection if open, then forces a
        hardware reset via a quick DTR pulse - used by both Restart Test
        and Cancel Session so the Arduino stops the instant the user
        confirms, rather than waiting for a later reflash/reconnect.
        """
        if self.serial_reader.ser and self.serial_reader.ser.is_open:
            self.serial_reader.disconnect()
        if self.port:
            SerialReader.hard_reset(self.port)

    # ---------- shared: render one test's stats (all phases, stacked) ----------
    def _render_test_stats(self, parent, test_key, phases: dict):
        card_w = min(px(680), round(SCREEN_W * 0.86))
        box = RoundedCard(parent, bg_color=COLOR_BG, fill=COLOR_CARD, radius=px(22, floor=8), width=card_w, height=10)
        box.pack(fill="x", pady=px(10, floor=4))
        inner = box.inner

        tk.Label(inner, text=TEST_LABELS[test_key], font=FONT_SUBHEAD, bg=COLOR_CARD,
                 fg=COLOR_PRIMARY_DARK).pack(anchor="w", padx=px(6, floor=2), pady=(px(4, floor=1), px(6, floor=2)))

        phase_defs = TEST_PHASES.get(test_key, [("full", None)])
        for phase_key, default_label in phase_defs:
            metrics = phases.get(phase_key, {})
            label = metrics.get("label", default_label)
            if label:
                tk.Label(inner, text=label, font=FONT_LABEL, bg=COLOR_CARD,
                         fg=COLOR_PRIMARY).pack(anchor="w", padx=px(10, floor=4), pady=(px(8, floor=2), px(2, floor=1)))
            for line in metrics_text_lines(metrics):
                tk.Label(inner, text=line, font=FONT_METRIC, bg=COLOR_CARD, fg=COLOR_TEXT,
                         justify="left").pack(anchor="w", padx=px(20, floor=8), pady=1)

        # Measure the ACTUAL required height of everything we just packed
        # (instead of estimating it), so nothing ever gets clipped/cut off.
        inner.update_idletasks()
        content_height = inner.winfo_reqheight()
        box.configure(height=content_height + 28)

    # ================= SCREEN: FINAL RESULTS =================
    def show_final_results(self):
        self._clear_content()
        self._screen_header("Results")
        tk.Label(self.content, text=f"Patient: {self.session_patient_id}", font=FONT_NORMAL,
                 bg=COLOR_BG, fg=COLOR_MUTED).pack(pady=(0, 10))

        canvas = tk.Canvas(self.content, bg=COLOR_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.content, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=COLOR_BG)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="n", width=min(px(720), round(SCREEN_W * 0.9)))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(px(40, floor=8), 0))
        scrollbar.pack(side="right", fill="y")

        # Statistiche impilate una sotto l'altra, per ogni test.
        for test_key in TEST_ORDER:
            phases = self.session_results.get(test_key, {})
            self._render_test_stats(scroll_frame, test_key, phases)

        def save_and_return():
            session_timestamp = self.store.save_session(self.session_patient_id, self.session_results)
            try:
                self.store.save_patient_report(self.session_patient_id, session_timestamp,
                                                self.session_raw_events, self.session_results)
            except Exception as e:
                messagebox.showwarning(
                    "Report not saved",
                    "The test statistics were saved successfully, but the detailed Excel "
                    "report could not be generated:\n\n" + str(e) +
                    "\n\nIf this mentions 'openpyxl', run on the Raspberry Pi:\n"
                    "pip install openpyxl --break-system-packages"
                )
            messagebox.showinfo("Saved", "Test saved successfully.")
            self.session_results = {}
            self.session_raw_events = {}
            self.session_patient_id = None
            self.show_menu()

        PillButton(self.content, text="Save Test", command=save_and_return, bg_color=COLOR_BG,
                   fill=COLOR_GREEN, width=px(360, floor=140), height=px(92, floor=40),
                   font=FONT_HUGE_BTN).pack(side="bottom", pady=px(20, floor=6))

    # ================= SCREEN: DATA VIEW PER TEST (+ phase) =================
    def show_data_view(self, test_key: str, phase_key: str = None):
        if not external_monitor_connected():
            self._show_connect_monitor_message()
            return
        self._clear_content()
        phase_defs = TEST_PHASES.get(test_key, [("full", None)])
        if phase_key is None:
            phase_key = phase_defs[0][0]

        self._screen_header(f"Data - {TEST_LABELS[test_key]}")

        if len(phase_defs) > 1:
            selector = tk.Frame(self.content, bg=COLOR_BG)
            selector.pack(pady=(0, px(10, floor=3)))
            for pkey, plabel in phase_defs:
                is_selected = (pkey == phase_key)
                PillButton(
                    selector, text=plabel, bg_color=COLOR_BG,
                    fill=COLOR_PRIMARY_DARK if is_selected else COLOR_PRIMARY,
                    width=px(200, floor=90), height=px(44, floor=26), font=FONT_BIG_BTN,
                    command=lambda k=pkey: self.show_data_view(test_key, k)
                ).pack(side="left", padx=px(6, floor=3))

        columns = ("paziente_id", "timestamp", "n_tap", "frequenza_media_hz",
                   "iti_medio_ms", "iti_sd_ms", "asincronia_media_ms", "asincronia_sd_ms", "forza_media_adc")
        headers = ("Patient", "Date/Time", "Taps", "Freq (Hz)", "Mean ITI (ms)",
                   "ITI SD (ms)", "Mean Async (ms)", "Async SD (ms)", "Force (ADC)")

        tree_frame = tk.Frame(self.content, bg=COLOR_BG)
        tree_frame.pack(expand=True, fill="both", padx=px(16, floor=4), pady=px(10, floor=3))

        tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
        for col, head in zip(columns, headers):
            tree.heading(col, text=head)
            tree.column(col, width=px(110, floor=70), anchor="center")
        self._style_tree_stripes(tree)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        # 9 columns never fit on the small touchscreen no matter how much
        # they're shrunk (text would become unreadable) - a horizontal
        # scrollbar lets the doctor swipe across instead of losing columns.
        hsb = ttk.Scrollbar(self.content, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.pack(fill="x", padx=px(16, floor=4))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        rows = self.store.read_for_test_phase(test_key, phase_key)
        for i, row in enumerate(rows):
            values = [row.get(c, "") for c in columns]
            tag = "odd" if i % 2 else "even"
            tree.insert("", "end", values=values, tags=(tag,))

        if not rows:
            tk.Label(self.content, text="No data available yet.", font=FONT_NORMAL,
                     bg=COLOR_BG, fg=COLOR_MUTED).pack(pady=px(10, floor=3))

        PillButton(self.content, text="Back", command=self.show_menu, bg_color=COLOR_BG,
                   fill=COLOR_MUTED, width=px(140, floor=80), height=px(52, floor=28)).pack(pady=px(10, floor=3))

    # ================= SCREEN: PATIENT DETAIL (all 3 tests together) =================
    def show_patient_detail(self, row: dict):
        if not external_monitor_connected():
            self._show_connect_monitor_message()
            return
        self._clear_content()
        self._screen_header(f"Patient: {row.get('paziente_id', '')}")
        tk.Label(self.content, text=f"Session: {row.get('timestamp', '')}", font=FONT_NORMAL,
                 bg=COLOR_BG, fg=COLOR_MUTED).pack(pady=(0, px(10, floor=3)))

        canvas = tk.Canvas(self.content, bg=COLOR_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.content, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=COLOR_BG)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="n", width=min(px(720), round(SCREEN_W * 0.9)))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(px(40, floor=8), 0))
        scrollbar.pack(side="right", fill="y")

        for test_key in TEST_ORDER:
            prefix = {"eligibility": "elig", "rhythmA": "rhA", "rhythmB": "rhB"}[test_key]
            phases = {}
            for phase_key, label in TEST_PHASES[test_key]:
                metrics = {"label": label}
                for mk in stats_calculator.EMPTY_METRICS:
                    metrics[mk] = row.get(f"{prefix}_{phase_key}_{mk}", "")
                phases[phase_key] = metrics
            self._render_test_stats(scroll_frame, test_key, phases)

        PillButton(self.content, text="Back", command=self.show_patients_list, bg_color=COLOR_BG,
                   fill=COLOR_MUTED, width=px(140, floor=80), height=px(52, floor=28)).pack(side="bottom", pady=px(14, floor=4))

    # ================= SCREEN: PATIENT SESSIONS (pick a session to view) =================
    def show_patient_sessions(self, pid: str):
        sessions = self.store.read_for_patient(pid)
        if not sessions:
            messagebox.showinfo("No Data", f"No sessions found for patient {pid}.")
            return
        if len(sessions) == 1:
            self.show_patient_detail(sessions[0])
            return

        self._clear_content()
        self._screen_header(f"Sessions - {pid}")

        canvas = tk.Canvas(self.content, bg=COLOR_BG, highlightthickness=0)
        canvas.pack(expand=True, fill="both", padx=px(40, floor=8), pady=px(10, floor=3))
        for row in sorted(sessions, key=lambda r: r.get("timestamp", ""), reverse=True):
            PillButton(
                self.content, text=row.get("timestamp", ""), bg_color=COLOR_BG,
                fill=COLOR_PRIMARY, width=min(px(400), round(SCREEN_W * 0.8)), height=px(52, floor=28), font=FONT_BIG_BTN,
                command=lambda r=row: self.show_patient_detail(r)
            ).pack(pady=px(6, floor=2))

        PillButton(self.content, text="Back", command=self.show_patients_list, bg_color=COLOR_BG,
                   fill=COLOR_MUTED, width=px(140, floor=80), height=px(52, floor=28)).pack(side="bottom", pady=px(14, floor=4))

    # ================= SCREEN: PATIENTS LIST (folders, favorites, drag&drop) =================
    def _show_connect_monitor_message(self):
        """Mostrata quando si tocca Patients senza un monitor esterno.
        Sul piccolo schermo SPI i dati paziente non si consultano: serve
        un monitor. I dati restano comunque salvati (CSV, Excel, USB)."""
        self._clear_content()
        self.content.configure(bg=COLOR_BG)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        box = tk.Frame(self.content, bg=COLOR_BG)
        box.place(relx=0.5, rely=0.40, anchor="center")
        tk.Label(box, text="Please connect a monitor", font=FONT_SECTION,
                 bg=COLOR_BG, fg=COLOR_PRIMARY_DARK, wraplength=int(sw * 0.9),
                 justify="center").pack(pady=(0, int(sh * 0.03)))
        tk.Label(box, text="Collega un monitor esterno per consultare i dati\n"
                           "dei pazienti. I dati vengono comunque salvati\n"
                           "(CSV, Excel e su USB se collegata).",
                 font=FONT_NORMAL, bg=COLOR_BG, fg=COLOR_MUTED,
                 wraplength=int(sw * 0.9), justify="center").pack()

        PillButton(self.content, text="Back", command=self.show_menu,
                   bg_color=COLOR_BG, fill=COLOR_MUTED,
                   width=px(160, floor=90), height=px(56, floor=34),
                   font=FONT_BIG_BTN).pack(side="bottom", pady=px(16, floor=6))

    def show_patients_list(self):
        self._clear_content()
        self._screen_header("Patients")

        # ---- Folder bar (horizontally scrollable: on the small touchscreen
        # there are too many buttons here - USB, folders, delete/rename - to
        # ever fit in one fixed-width row without becoming unreadable, so we
        # let the doctor swipe across instead of losing/cramming buttons) ----
        folder_bar_h = px(60, floor=40)
        folder_scroll = tk.Canvas(self.content, bg=COLOR_BG, highlightthickness=0, height=folder_bar_h)
        folder_scroll.pack(fill="x", padx=px(20, floor=6), pady=(0, px(4, floor=2)))
        folder_hsb = ttk.Scrollbar(self.content, orient="horizontal", command=folder_scroll.xview)
        folder_hsb.pack(fill="x", padx=px(20, floor=6), pady=(0, px(10, floor=3)))
        folder_bar = tk.Frame(folder_scroll, bg=COLOR_BG)
        folder_bar.bind("<Configure>", lambda e: folder_scroll.configure(scrollregion=folder_scroll.bbox("all")))
        folder_scroll.create_window((0, 0), window=folder_bar, anchor="nw")
        folder_scroll.configure(xscrollcommand=folder_hsb.set)

        self._folder_tiles = []  # (name, widget) for drag & drop hit-testing

        def select_folder(name):
            self.patients_folder_filter = name
            self.show_patients_list()

        all_tile = PillButton(
            folder_bar, text="All", bg_color=COLOR_BG,
            fill=COLOR_PRIMARY_DARK if self.patients_folder_filter == "All" else COLOR_PRIMARY,
            width=px(120, floor=70), height=px(48, floor=32), font=FONT_BIG_BTN,
            command=lambda: select_folder("All")
        )
        all_tile.pack(side="left", padx=px(6, floor=3))
        self._folder_tiles.append(("All", all_tile))

        for folder_name in self.store.get_folders():
            tile = PillButton(
                folder_bar, text=folder_name, bg_color=COLOR_BG,
                fill=COLOR_PRIMARY_DARK if self.patients_folder_filter == folder_name else COLOR_PRIMARY,
                width=px(140, floor=80), height=px(48, floor=32), font=FONT_BIG_BTN,
                command=lambda n=folder_name: select_folder(n)
            )
            tile.pack(side="left", padx=px(6, floor=3))
            self._folder_tiles.append((folder_name, tile))

        def new_folder():
            self._show_new_folder_dialog()

        PillButton(folder_bar, text="+ New Folder", command=new_folder, bg_color=COLOR_BG,
                   fill=COLOR_GREEN, width=px(170, floor=90), height=px(48, floor=32),
                   font=FONT_BIG_BTN).pack(side="left", padx=px(6, floor=3))

        def check_usb():
            def work():
                parts = self._list_removable_partitions()
                if parts is None:
                    self.root.after(0, lambda: messagebox.showerror(
                        "USB Drive",
                        "Can't check for USB drives: the 'lsblk' tool isn't available on this system."
                    ))
                    return
                drives = self._detect_usb_drives()

                def show_result():
                    if not drives:
                        if parts:
                            messagebox.showwarning(
                                "USB Drive",
                                "A removable drive is inserted but couldn't be mounted automatically "
                                "(is 'udisksctl' installed?). Try:\nsudo apt install udisks2"
                            )
                        else:
                            messagebox.showinfo("USB Drive", "No USB drive detected. Insert one and try again.")
                    else:
                        names = "\n".join(os.path.basename(d.rstrip("/")) or d for d in drives)
                        messagebox.showinfo("USB Drive", f"USB drive detected:\n{names}\n\n"
                                                          f"Open a patient and use 'Save to USB' to export their report.")

                self.root.after(0, show_result)

            threading.Thread(target=work, daemon=True).start()

        PillButton(folder_bar, text="\U0001F50C USB Drive", command=check_usb, bg_color=COLOR_BG,
                   fill=COLOR_PRIMARY, width=px(190, floor=90), height=px(48, floor=32),
                   font=FONT_BIG_BTN).pack(side="left", padx=px(6, floor=3))

        if self.patients_folder_filter != "All":
            current = self.patients_folder_filter

            def delete_current_folder():
                if self._confirm("Delete Folder",
                                  f"Delete folder '{current}'? Patients inside it will not be "
                                  f"deleted, they'll just no longer be filed in this folder."):
                    self.store.delete_folder(current)
                    self.patients_folder_filter = "All"
                    self.show_patients_list()

            PillButton(folder_bar, text="Delete Folder", command=delete_current_folder, bg_color=COLOR_BG,
                       fill=COLOR_RED, width=px(170, floor=90), height=px(48, floor=32),
                       font=FONT_BIG_BTN).pack(side="left", padx=px(6, floor=3))

            def rename_current_folder():
                self._show_rename_folder_dialog(current)

            PillButton(folder_bar, text="Rename Folder", command=rename_current_folder, bg_color=COLOR_BG,
                       fill=COLOR_PRIMARY, width=px(190, floor=90), height=px(48, floor=32),
                       font=FONT_BIG_BTN).pack(side="left", padx=px(6, floor=3))

            def save_folder_to_usb():
                def work():
                    drives = self._detect_usb_drives()
                    if not drives:
                        self.root.after(0, lambda: messagebox.showwarning(
                            "USB Drive", "No USB drive detected. Insert one and try again."))
                        return
                    pids = [pid for pid in self.store.get_all_patient_ids()
                            if self.store.get_patient_folder(pid) == current]
                    if not pids:
                        self.root.after(0, lambda: messagebox.showinfo(
                            "USB Drive", f"No patients in folder '{current}'."))
                        return
                    drive = drives[0]
                    dest_dir = os.path.join(drive, "TapTapBox_Export", current)
                    try:
                        os.makedirs(dest_dir, exist_ok=True)
                    except Exception as e:
                        self.root.after(0, lambda: messagebox.showerror(
                            "Error", f"Could not create export folder on USB drive:\n{e}"))
                        return
                    copied, missing = 0, 0
                    for pid in pids:
                        src = xlsx_export.patient_report_path(
                            pid, base_dir=os.path.dirname(self.store.filepath))
                        if not os.path.isfile(src):
                            missing += 1
                            continue
                        try:
                            shutil.copy2(src, os.path.join(dest_dir, os.path.basename(src)))
                            copied += 1
                        except Exception:
                            missing += 1

                    def show_result():
                        msg = f"Saved {copied} report(s) from folder '{current}' to:\n{dest_dir}"
                        if missing:
                            msg += (f"\n\n{missing} patient(s) had no saved report yet "
                                    f"and were skipped.")
                        messagebox.showinfo("Saved to USB", msg)

                    self.root.after(0, show_result)

                threading.Thread(target=work, daemon=True).start()

            PillButton(folder_bar, text="\U0001F50C Save Folder to USB", command=save_folder_to_usb,
                       bg_color=COLOR_BG, fill=COLOR_PRIMARY, width=px(230, floor=100), height=px(48, floor=32),
                       font=FONT_BIG_BTN).pack(side="left", padx=px(6, floor=3))

        tk.Label(self.content, text="Tip: press and drag a patient onto a folder to file it there.",
                 font=("TkDefaultFont", max(7, round(11 * UI_SCALE))), bg=COLOR_BG, fg=COLOR_MUTED).pack(pady=(0, px(6, floor=2)))

        # ---- Patient list ----
        list_canvas = tk.Canvas(self.content, bg=COLOR_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(self.content, orient="vertical", command=list_canvas.yview)
        list_frame = tk.Frame(list_canvas, bg=COLOR_BG)
        list_frame.bind("<Configure>", lambda e: list_canvas.configure(scrollregion=list_canvas.bbox("all")))
        list_canvas.create_window((0, 0), window=list_frame, anchor="n", width=min(px(760), round(SCREEN_W * 0.92)))
        list_canvas.configure(yscrollcommand=vsb.set)
        list_canvas.pack(side="left", fill="both", expand=True, padx=(px(30, floor=6), 0))
        vsb.pack(side="right", fill="y")

        pids = self.store.get_all_patient_ids()
        visible = []
        for pid in pids:
            folder = self.store.get_patient_folder(pid)
            if self.patients_folder_filter == "All" or folder == self.patients_folder_filter:
                visible.append(pid)

        if not visible:
            tk.Label(list_frame, text="No patients in this view.", font=FONT_NORMAL,
                     bg=COLOR_BG, fg=COLOR_MUTED).pack(pady=px(20, floor=6))

        for pid in visible:
            sessions = self.store.read_for_patient(pid)
            last_ts = max((s.get("timestamp", "") for s in sessions), default="")
            self._build_patient_row(list_frame, pid, last_ts)

        back_row = tk.Frame(self.content, bg=COLOR_BG)
        back_row.pack(side="bottom", pady=px(12, floor=4))
        PillButton(back_row, text="Back", command=self.show_menu, bg_color=COLOR_BG,
                   fill=COLOR_MUTED, width=px(140, floor=80), height=px(52, floor=28)).pack()

    def _build_patient_row(self, parent, pid, last_ts):
        is_fav = self.store.is_favorite(pid)
        row = tk.Frame(parent, bg=COLOR_CARD, highlightbackground="#DCE9EF",
                        highlightthickness=1)
        row.pack(fill="x", pady=px(5, floor=2), padx=px(4, floor=2), ipady=px(6, floor=3))

        accent = tk.Frame(row, bg=COLOR_PRIMARY, width=px(6, floor=3))
        accent.pack(side="left", fill="y")

        star_var = tk.StringVar(value="\u2605" if is_fav else "\u2606")
        star_lbl = tk.Label(row, textvariable=star_var, font=("TkDefaultFont", max(9, round(18 * UI_SCALE))),
                             bg=COLOR_CARD, fg=COLOR_STAR_ON if is_fav else COLOR_STAR_OFF)
        star_lbl.pack(side="left", padx=(px(12, floor=6), px(10, floor=5)))

        text_col = tk.Frame(row, bg=COLOR_CARD)
        text_col.pack(side="left", fill="x", expand=True)
        tk.Label(text_col, text=pid, font=FONT_LABEL, bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor="w")
        tk.Label(text_col, text=f"Last session: {last_ts or '-'}", font=("TkDefaultFont", max(7, round(11 * UI_SCALE))),
                 bg=COLOR_CARD, fg=COLOR_MUTED).pack(anchor="w")

        widgets = [row, accent, star_lbl, text_col] + list(text_col.winfo_children())

        drag_state = {"start_x": None, "start_y": None, "win": None, "dragging": False}

        def on_press(e):
            drag_state["start_x"] = e.x_root
            drag_state["start_y"] = e.y_root
            drag_state["dragging"] = False

        def on_motion(e):
            dx = abs(e.x_root - drag_state["start_x"])
            dy = abs(e.y_root - drag_state["start_y"])
            if not drag_state["dragging"] and (dx > 10 or dy > 10):
                drag_state["dragging"] = True
                lbl = tk.Label(self.root, text=pid, bg=COLOR_PRIMARY, fg="white",
                                font=FONT_LABEL, padx=14, pady=8)
                lbl.place(x=0, y=0)
                lbl.lift()
                drag_state["win"] = lbl
            if drag_state["win"] is not None:
                rx = e.x_root - self.root.winfo_rootx() + 14
                ry = e.y_root - self.root.winfo_rooty() + 14
                drag_state["win"].place(x=rx, y=ry)
                self._highlight_folder_under(e.x_root, e.y_root)

        def on_release(e):
            if drag_state["win"] is not None:
                drag_state["win"].destroy()
                drag_state["win"] = None
            self._clear_folder_highlights()

            if drag_state["dragging"]:
                target = self._folder_at(e.x_root, e.y_root)
                if target is not None:
                    new_folder = None if target == "All" else target
                    self.store.set_patient_folder(pid, new_folder)
                    self.show_patients_list()
            else:
                self._show_patient_popup(pid, e.x_root, e.y_root)

        for w in widgets:
            w.bind("<ButtonPress-1>", on_press)
            w.bind("<B1-Motion>", on_motion)
            w.bind("<ButtonRelease-1>", on_release)
            w.configure(cursor="hand2") if hasattr(w, "configure") else None

    def _folder_at(self, x_root, y_root):
        for name, widget in getattr(self, "_folder_tiles", []):
            wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
            ww, wh = widget.winfo_width(), widget.winfo_height()
            if wx <= x_root <= wx + ww and wy <= y_root <= wy + wh:
                return name
        return None

    def _highlight_folder_under(self, x_root, y_root):
        target = self._folder_at(x_root, y_root)
        for name, widget in getattr(self, "_folder_tiles", []):
            widget.highlight_on() if name == target else widget.highlight_off()

    def _clear_folder_highlights(self):
        for _name, widget in getattr(self, "_folder_tiles", []):
            widget.highlight_off()

    # ---------- in-window overlay (NOT a Toplevel: the Pi runs X with no
    # window manager, so overrideredirect()+grab_set() popups can freeze
    # input entirely - the same reason the working on-screen keyboard uses
    # a plain, non-grabbed Toplevel instead). This overlay is just an
    # ordinary child widget of the existing window, so it always works. ----------
    def _open_overlay(self, width_ratio=0.42, height_ratio=0.5):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        backdrop = tk.Frame(self.root, bg="#0B2430")
        backdrop.place(relx=0, rely=0, relwidth=1, relheight=1)

        w, h = int(sw * width_ratio), int(sh * height_ratio)
        card = RoundedCard(backdrop, bg_color="#0B2430", fill=COLOR_CARD,
                            radius=px(28, floor=10), width=w, height=h)
        card.place(relx=0.5, rely=0.5, anchor="center")

        def close():
            backdrop.destroy()

        # Tapping the dark backdrop (outside the card) closes the overlay.
        backdrop.bind("<Button-1>", lambda e: close())

        return card.inner, close

    def _show_new_folder_dialog(self):
        inner, close = self._open_overlay(width_ratio=0.55, height_ratio=0.4)

        tk.Label(inner, text="New Folder", font=FONT_SUBHEAD, bg=COLOR_CARD,
                 fg=COLOR_PRIMARY_DARK).pack(pady=(px(20, floor=8), px(12, floor=5)))
        name_var = tk.StringVar()
        entry = ttk.Entry(inner, textvariable=name_var, font=FONT_NORMAL, width=14)
        entry.pack(pady=px(6, floor=2), ipady=px(6, floor=2))
        attach_text_keyboard(entry, self.root)

        def confirm():
            name = name_var.get().strip()
            if name:
                self.store.create_folder(name)
            close()
            self.show_patients_list()

        btn_row = tk.Frame(inner, bg=COLOR_CARD)
        btn_row.pack(pady=px(18, floor=6))
        PillButton(btn_row, text="Cancel", command=close, bg_color=COLOR_CARD,
                   fill=COLOR_MUTED, width=px(120, floor=70), height=px(48, floor=30)).pack(side="left", padx=px(8, floor=4))
        PillButton(btn_row, text="Create", command=confirm, bg_color=COLOR_CARD,
                   fill=COLOR_GREEN, width=px(120, floor=70), height=px(48, floor=30)).pack(side="left", padx=px(8, floor=4))

    def _show_rename_folder_dialog(self, old_name):
        inner, close = self._open_overlay(width_ratio=0.55, height_ratio=0.4)

        tk.Label(inner, text="Rename Folder", font=FONT_SUBHEAD, bg=COLOR_CARD,
                 fg=COLOR_PRIMARY_DARK).pack(pady=(px(20, floor=8), px(12, floor=5)))
        name_var = tk.StringVar(value=old_name)
        entry = ttk.Entry(inner, textvariable=name_var, font=FONT_NORMAL, width=14)
        entry.pack(pady=px(6, floor=2), ipady=px(6, floor=2))
        attach_text_keyboard(entry, self.root)

        def confirm():
            new_name = name_var.get().strip()
            if new_name and new_name != old_name:
                self.store.rename_folder(old_name, new_name)
                self.patients_folder_filter = new_name
            close()
            self.show_patients_list()

        btn_row = tk.Frame(inner, bg=COLOR_CARD)
        btn_row.pack(pady=px(18, floor=6))
        PillButton(btn_row, text="Cancel", command=close, bg_color=COLOR_CARD,
                   fill=COLOR_MUTED, width=px(120, floor=70), height=px(48, floor=30)).pack(side="left", padx=px(8, floor=4))
        PillButton(btn_row, text="Rename", command=confirm, bg_color=COLOR_CARD,
                   fill=COLOR_GREEN, width=px(120, floor=70), height=px(48, floor=30)).pack(side="left", padx=px(8, floor=4))

    def _launch_and_show_back_button(self, cmd_list):
        """
        Lancia il primo comando disponibile in cmd_list (es. per aprire il
        report in LibreOffice) e mostra un piccolo pulsante "Back to Tap Tap
        Box" sempre in primo piano, cosi' su un touchscreen senza tastiera
        ne' gestore finestre si puo' comunque tornare all'app.
        Il pulsante NON e' visibile mentre il file e' aperto: compare solo
        quando l'utente chiude il documento con la X in alto (LibreOffice
        resta aperto sul suo "Start Center" ma il file non c'e' piu' - a
        quel punto serve un modo per tornare all'app). Se il programma
        viene chiuso del tutto, si torna comunque in automatico.
        Ritorna True se un comando e' stato lanciato con successo.
        """
        proc = None
        fname_hint = None
        for cmd in cmd_list:
            try:
                proc = subprocess.Popen(cmd)
                fname_hint = os.path.basename(cmd[-1]) if len(cmd) > 1 else None
                break
            except FileNotFoundError:
                continue
        if proc is None:
            return False

        # La finestra di Tap Tap Box e' sempre in primo piano (necessario per
        # il kiosk): senza nasconderla, LibreOffice si aprirebbe dietro di
        # lei e resterebbe invisibile. La ripristiniamo (deiconify) appena
        # si torna indietro.
        self.root.withdraw()

        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        w, h = 300, 90
        sw, sh = overlay.winfo_screenwidth(), overlay.winfo_screenheight()
        overlay.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 24}")
        overlay.configure(bg=COLOR_PRIMARY_DARK)
        overlay.withdraw()  # nascosto finche' il documento non viene chiuso

        closed = {"value": False}
        shown = {"value": False}

        def restore_focus():
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
            self.root.after(300, lambda: self.root.attributes("-topmost", False))

        def go_back():
            if closed["value"]:
                return
            closed["value"] = True
            # Torniamo subito a Tap Tap Box senza far aspettare l'utente;
            # la chiusura vera e propria di LibreOffice avviene in
            # background (vedi sotto), cosi' l'interfaccia non si blocca.
            overlay.destroy()
            restore_focus()

            def close_in_background():
                try:
                    if proc and proc.poll() is None:
                        closed_gracefully = False
                        if fname_hint and shutil.which("wmctrl"):
                            # Chiusura "gentile" (come premere la X): evita
                            # che LibreOffice pensi di essere andato in
                            # crash e proponga il recupero documenti al
                            # prossimo avvio.
                            try:
                                subprocess.run(["wmctrl", "-c", fname_hint], timeout=2)
                                closed_gracefully = True
                            except Exception:
                                pass
                        if closed_gracefully:
                            try:
                                proc.wait(timeout=5)
                            except Exception:
                                pass
                        if proc.poll() is None:
                            # Non ha risposto alla chiusura gentile entro
                            # 5 secondi: lo forziamo, come fallback.
                            proc.terminate()
                except Exception:
                    pass

            threading.Thread(target=close_in_background, daemon=True).start()

        def keep_on_top():
            # Alcuni window manager rimettono in primo piano l'app appena
            # aperta (LibreOffice) sopra le finestre "topmost" esistenti:
            # ripetiamo lift()/attributes ogni tanto per restare visibili.
            if closed["value"] or not shown["value"]:
                return
            try:
                overlay.attributes("-topmost", True)
                overlay.lift()
            except tk.TclError:
                return
            overlay.after(700, keep_on_top)

        def show_overlay():
            if closed["value"] or shown["value"]:
                return
            shown["value"] = True
            overlay.deiconify()
            keep_on_top()

        def watch_process():
            # Se l'utente chiude LibreOffice del tutto (non solo il file),
            # il processo termina: torniamo a Tap Tap Box in automatico.
            proc.wait()
            if not closed["value"]:
                closed["value"] = True
                self.root.after(0, lambda: (overlay.destroy(), restore_focus()))

        def watch_document_closed():
            # Facciamo comparire il pulsante solo quando il documento viene
            # chiuso (la sua finestra sparisce, anche se LibreOffice resta
            # aperto su un'altra schermata). Usiamo 'wmctrl' per guardare i
            # titoli delle finestre aperte e cerchiamo il nome del file:
            # appena non compare piu', il documento e' stato chiuso con la X.
            if shutil.which("wmctrl") is None:
                self.root.after(0, show_overlay)
                return

            fname = None
            if cmd_list and cmd_list[0]:
                fname = os.path.basename(cmd_list[0][-1])
            if not fname:
                self.root.after(0, show_overlay)
                return

            def list_titles():
                try:
                    result = subprocess.run(["wmctrl", "-l"], capture_output=True,
                                             text=True, timeout=2)
                    return result.stdout if result.returncode == 0 else ""
                except Exception:
                    return ""

            # 1) aspettiamo che compaia la finestra del documento
            doc_seen = False
            deadline = time.time() + 20
            while not closed["value"] and proc.poll() is None and time.time() < deadline:
                if fname in list_titles():
                    doc_seen = True
                    break
                time.sleep(0.5)

            if not doc_seen:
                # non siamo riusciti a vedere la finestra del documento:
                # mostriamo comunque il pulsante per sicurezza, cosi' non si
                # resta mai bloccati senza un modo per tornare indietro.
                self.root.after(0, show_overlay)
                return

            # 2) aspettiamo che quella finestra sparisca (= file chiuso con la X)
            while not closed["value"] and proc.poll() is None:
                if fname not in list_titles():
                    self.root.after(0, show_overlay)
                    return
                time.sleep(1)

        PillButton(overlay, text="\u2190 Back to Tap Tap Box", command=go_back,
                   bg_color=COLOR_PRIMARY_DARK, fill=COLOR_GREEN,
                   width=w - 20, height=h - 20, font=FONT_BIG_BTN).pack(padx=10, pady=10)
        threading.Thread(target=watch_process, daemon=True).start()
        threading.Thread(target=watch_document_closed, daemon=True).start()
        return True

    def _launch_and_show_back_button(self, cmd_list):
        """
        Lancia il primo comando disponibile in cmd_list (es. per aprire il
        report in LibreOffice) e mostra un semplice pulsante verde "Back to
        Tap Tap Box" in basso a destra, sempre visibile, cosi' su un
        touchscreen senza tastiera ne' gestore finestre si puo' sempre
        tornare all'app con un tocco.
        Ritorna True se un comando e' stato lanciato con successo.
        """
        proc = None
        fname_hint = None
        for cmd in cmd_list:
            try:
                proc = subprocess.Popen(cmd)
                fname_hint = os.path.basename(cmd[-1]) if len(cmd) > 1 else None
                break
            except FileNotFoundError:
                continue
        if proc is None:
            return False

        # La finestra di Tap Tap Box e' sempre in primo piano (necessario
        # per il kiosk): senza nasconderla, il programma appena aperto
        # resterebbe invisibile dietro di lei.
        self.root.withdraw()

        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg=COLOR_BG)
        w, h = 210, 64
        sw, sh = overlay.winfo_screenwidth(), overlay.winfo_screenheight()
        overlay.geometry(f"{w}x{h}+{sw - w - 20}+{sh - h - 20}")

        closed = {"value": False}

        def restore_focus():
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
            self.root.after(300, lambda: self.root.attributes("-topmost", False))

        def go_back():
            if closed["value"]:
                return
            closed["value"] = True
            overlay.destroy()
            restore_focus()

            def close_in_background():
                try:
                    if proc and proc.poll() is None:
                        closed_gracefully = False
                        if fname_hint and shutil.which("wmctrl"):
                            # Chiusura "gentile" (come premere la X): evita
                            # che LibreOffice pensi di essere andato in
                            # crash e proponga il recupero documenti al
                            # prossimo avvio.
                            try:
                                subprocess.run(["wmctrl", "-c", fname_hint], timeout=2)
                                closed_gracefully = True
                            except Exception:
                                pass
                        if closed_gracefully:
                            try:
                                proc.wait(timeout=5)
                            except Exception:
                                pass
                        if proc.poll() is None:
                            proc.terminate()
                except Exception:
                    pass

            threading.Thread(target=close_in_background, daemon=True).start()

        def keep_on_top():
            if closed["value"]:
                return
            try:
                overlay.attributes("-topmost", True)
                overlay.lift()
            except tk.TclError:
                return
            overlay.after(700, keep_on_top)

        def watch_process():
            # Se l'utente chiude LibreOffice del tutto (con la X, non con
            # il pulsante), il processo termina: torniamo a Tap Tap Box
            # in automatico.
            proc.wait()
            if not closed["value"]:
                closed["value"] = True
                self.root.after(0, lambda: (overlay.destroy(), restore_focus()))

        btn = tk.Canvas(overlay, width=w, height=h, bg=COLOR_BG, highlightthickness=0)
        btn.pack()
        pad = 4
        btn.create_oval(pad, pad, h - pad, h - pad, fill=COLOR_GREEN, outline="")
        btn.create_oval(w - h + pad, pad, w - pad, h - pad, fill=COLOR_GREEN, outline="")
        btn.create_rectangle(h / 2, pad, w - h / 2, h - pad, fill=COLOR_GREEN, outline="")
        btn.create_text(w / 2, h / 2, text="\u2190 Tap Tap Box", fill="white",
                         font=FONT_BIG_BTN)
        btn.bind("<Button-1>", lambda e: go_back())
        btn.configure(cursor="hand2")

        keep_on_top()
        threading.Thread(target=watch_process, daemon=True).start()
        return True

    def _show_patient_popup(self, pid, x_root=None, y_root=None):
        inner, close = self._open_overlay(width_ratio=0.85, height_ratio=0.85)

        # Scrollable content: up to 6 stacked buttons + title never all fit
        # on the small touchscreen's ~320px height, so make this area
        # scrollable as a safety net (harmless on a big screen, where it
        # simply never needs to scroll).
        pop_canvas = tk.Canvas(inner, bg=COLOR_CARD, highlightthickness=0)
        pop_scrollbar = ttk.Scrollbar(inner, orient="vertical", command=pop_canvas.yview)
        content = tk.Frame(pop_canvas, bg=COLOR_CARD)
        content.bind("<Configure>", lambda e: pop_canvas.configure(scrollregion=pop_canvas.bbox("all")))
        pop_canvas.create_window((0, 0), window=content, anchor="n")
        pop_canvas.configure(yscrollcommand=pop_scrollbar.set)
        pop_canvas.pack(side="left", fill="both", expand=True)
        pop_scrollbar.pack(side="right", fill="y")

        tk.Label(content, text=pid, font=FONT_SUBHEAD, bg=COLOR_CARD,
                 fg=COLOR_PRIMARY_DARK).pack(pady=(px(18, floor=6), px(14, floor=5)))

        is_fav = self.store.is_favorite(pid)
        fav_text = "\u2605 Remove from Favorites" if is_fav else "\u2606 Add to Favorites"

        def toggle_fav():
            self.store.toggle_favorite(pid)
            close()
            self.show_patients_list()

        def view_data():
            close()
            path = xlsx_export.patient_report_path(pid, base_dir=os.path.dirname(self.store.filepath))
            if not os.path.isfile(path):
                messagebox.showinfo("No Data", f"No saved report found yet for patient {pid}.")
                return
            # Try a few ways to open the file, since a minimal Raspberry Pi
            # OS image may not have xdg-open (or even a desktop "opener")
            # installed - fall back to launching LibreOffice directly.
            openers = [
                ["xdg-open", path],
                ["gio", "open", path],
                ["gnome-open", path],
                ["libreoffice", "--calc", path],
                ["soffice", "--calc", path],
            ]
            launched = self._launch_and_show_back_button(openers)
            if not launched:
                messagebox.showerror(
                    "Error",
                    "Could not find a program to open the report with.\n\n"
                    "On the Raspberry Pi, run:\n"
                    "sudo apt install libreoffice-calc\n\n"
                    "Or open it manually from a file manager at:\n" + path
                )

        def save_to_usb():
            close()

            def work():
                drives = self._detect_usb_drives()
                if not drives:
                    self.root.after(0, lambda: messagebox.showwarning(
                        "USB Drive", "No USB drive detected. Insert one and try again."))
                    return
                src = xlsx_export.patient_report_path(pid, base_dir=os.path.dirname(self.store.filepath))
                if not os.path.isfile(src):
                    self.root.after(0, lambda: messagebox.showinfo(
                        "No Data", f"No saved report found yet for patient {pid}."))
                    return
                drive = drives[0]
                try:
                    dest_dir = os.path.join(drive, "TapTapBox_Export")
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = os.path.join(dest_dir, os.path.basename(src))
                    shutil.copy2(src, dest)
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Saved to USB", f"Report for {pid} saved to:\n{dest}"))
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror(
                        "Error", f"Could not save to USB drive:\n{e}"))

            threading.Thread(target=work, daemon=True).start()

        def remove_patient():
            close()
            if self._confirm("Remove Patient",
                              f"Are you sure you want to remove patient '{pid}'? "
                              f"All of their recorded sessions will be permanently deleted."):
                self.store.delete_patient(pid)
                self.show_patients_list()

        current_folder = self.store.get_patient_folder(pid)

        def remove_from_folder():
            close()
            self.store.set_patient_folder(pid, None)
            self.show_patients_list()

        def rename_patient():
            close()
            self._show_rename_patient_dialog(pid)

        btn_w = min(px(260), round(SCREEN_W * 0.55))
        btn_h = px(52, floor=32)
        PillButton(content, text=fav_text, command=toggle_fav, bg_color=COLOR_CARD,
                   fill=COLOR_STAR_ON, width=btn_w, height=btn_h, font=FONT_BIG_BTN).pack(pady=px(6, floor=3))
        PillButton(content, text="View Data", command=view_data, bg_color=COLOR_CARD,
                   fill=COLOR_PRIMARY, width=btn_w, height=btn_h, font=FONT_BIG_BTN).pack(pady=px(6, floor=3))
        PillButton(content, text="\U0001F50C Save to USB", command=save_to_usb, bg_color=COLOR_CARD,
                   fill=COLOR_PRIMARY, width=btn_w, height=btn_h, font=FONT_BIG_BTN).pack(pady=px(6, floor=3))
        PillButton(content, text="Rename Patient", command=rename_patient, bg_color=COLOR_CARD,
                   fill=COLOR_PRIMARY, width=btn_w, height=btn_h, font=FONT_BIG_BTN).pack(pady=px(6, floor=3))
        if current_folder:
            PillButton(content, text=f"Remove from '{current_folder}'", command=remove_from_folder,
                       bg_color=COLOR_CARD, fill=COLOR_MUTED, width=btn_w, height=btn_h,
                       font=FONT_BIG_BTN).pack(pady=px(6, floor=3))
        PillButton(content, text="Remove Patient", command=remove_patient, bg_color=COLOR_CARD,
                   fill=COLOR_RED, width=btn_w, height=btn_h, font=FONT_BIG_BTN).pack(pady=px(6, floor=3))

        close_lbl = tk.Label(content, text="Close", font=("TkDefaultFont", max(8, round(12 * UI_SCALE)), "underline"),
                              bg=COLOR_CARD, fg=COLOR_MUTED, cursor="hand2")
        close_lbl.pack(pady=(px(6, floor=2), px(10, floor=4)))
        close_lbl.bind("<Button-1>", lambda e: close())

    def _show_rename_patient_dialog(self, old_id):
        inner, close = self._open_overlay(width_ratio=0.7, height_ratio=0.4)

        tk.Label(inner, text="Rename Patient", font=FONT_SUBHEAD, bg=COLOR_CARD,
                 fg=COLOR_PRIMARY_DARK).pack(pady=(px(20, floor=8), px(12, floor=5)))
        name_var = tk.StringVar(value=old_id)
        entry = ttk.Entry(inner, textvariable=name_var, font=FONT_NORMAL, width=14)
        entry.pack(pady=px(6, floor=2), ipady=px(6, floor=2))
        attach_text_keyboard(entry, self.root)

        def confirm():
            new_id = name_var.get().strip()
            if not new_id or new_id == old_id:
                close()
                return
            if new_id in self.store.get_all_patient_ids():
                messagebox.showerror(
                    "Error", f"A patient named '{new_id}' already exists. "
                             f"Choose a different name.")
                return
            self.store.rename_patient(old_id, new_id)
            close()
            self.show_patients_list()

        btn_row = tk.Frame(inner, bg=COLOR_CARD)
        btn_row.pack(pady=px(18, floor=6))
        PillButton(btn_row, text="Cancel", command=close, bg_color=COLOR_CARD,
                   fill=COLOR_MUTED, width=px(120, floor=70), height=px(48, floor=30)).pack(side="left", padx=px(8, floor=4))
        PillButton(btn_row, text="Rename", command=confirm, bg_color=COLOR_CARD,
                   fill=COLOR_GREEN, width=px(120, floor=70), height=px(48, floor=30)).pack(side="left", padx=px(8, floor=4))

    # ---------- CENTRAL UPDATE LOOP ----------
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.data_queue.get_nowait()
                if kind == "data":
                    if self.on_data_line:
                        self.on_data_line(payload)
                elif kind == "error":
                    messagebox.showerror("Serial Error", payload)
                elif kind == "flash_done":
                    if self.on_flash_done:
                        self.on_flash_done(payload)
        except queue.Empty:
            pass

        self.root.after(50, self._poll_queue)

    def on_close(self):
        self.serial_reader.disconnect()
        self.root.destroy()
