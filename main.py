"""
main.py
Entry point dell'applicazione Tap Tap Box.

Avvio:
    pip install pyserial
    python main.py
"""

import tkinter as tk
from gui import TapTapBoxGUI


def main():
    root = tk.Tk()
    app = TapTapBoxGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
