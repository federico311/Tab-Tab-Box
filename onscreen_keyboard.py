"""
onscreen_keyboard.py
Tastiera alfanumerica e tastierino numerico a comparsa, per l'inserimento
dati su schermo touch senza tastiera fisica collegata.

Uso:
    attach_numeric_keypad(entry_widget, root)
    attach_text_keyboard(entry_widget, root)

Questi bind aprono un popup quando l'utente tocca il campo di testo.
"""

import tkinter as tk
from tkinter import ttk


class _KeyboardPopup(tk.Toplevel):
    def __init__(self, parent, entry: tk.Entry, rows: list):
        super().__init__(parent)
        self.entry = entry
        self.overrideredirect(False)
        self.title("Inserisci")
        self.attributes("-topmost", True)
        self.resizable(False, False)

        container = ttk.Frame(self, padding=8)
        container.pack(fill="both", expand=True)

        # Mostra il valore corrente in grande, sopra la tastiera
        self.preview_var = tk.StringVar(value=self.entry.get())
        preview = ttk.Label(
            container, textvariable=self.preview_var,
            font=("TkDefaultFont", 22), relief="sunken", padding=10, anchor="w"
        )
        preview.grid(row=0, column=0, columnspan=20, sticky="ew", pady=(0, 10))

        for r, row in enumerate(rows, start=1):
            for c, key in enumerate(row):
                btn = tk.Button(
                    container, text=key, font=("TkDefaultFont", 18),
                    width=4, height=2,
                    command=lambda k=key: self._on_key(k)
                )
                btn.grid(row=r, column=c, padx=2, pady=2, sticky="nsew")

        last_row = len(rows) + 1
        ttk.Button(container, text="Cancella", command=self._backspace).grid(
            row=last_row, column=0, columnspan=8, sticky="ew", pady=(10, 0)
        )
        ttk.Button(container, text="OK", command=self._confirm).grid(
            row=last_row, column=8, columnspan=8, sticky="ew", pady=(10, 0)
        )

        self._center_on_parent(parent)

    def _center_on_parent(self, parent):
        self.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        w = self.winfo_width()
        h = self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{max(x,0)}+{max(y,0)}")

    def _on_key(self, key):
        current = self.preview_var.get()
        self.preview_var.set(current + key)

    def _backspace(self):
        current = self.preview_var.get()
        self.preview_var.set(current[:-1])

    def _confirm(self):
        self.entry.delete(0, tk.END)
        self.entry.insert(0, self.preview_var.get())
        self.destroy()


NUMERIC_ROWS = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    [".", "0", "-"],
]

TEXT_ROWS = [
    list("QWERTYUIOP"),
    list("ASDFGHJKL"),
    list("ZXCVBNM"),
    list("0123456789"),
]


def attach_numeric_keypad(entry: tk.Entry, root: tk.Tk):
    entry.bind("<Button-1>", lambda e: _KeyboardPopup(root, entry, NUMERIC_ROWS))


def attach_text_keyboard(entry: tk.Entry, root: tk.Tk):
    entry.bind("<Button-1>", lambda e: _KeyboardPopup(root, entry, TEXT_ROWS))
