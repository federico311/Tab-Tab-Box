"""
ui_widgets.py
Widget "pillola" arrotondati disegnati su Canvas (ttk non supporta
angoli arrotondati nativamente). Usati al posto dei pulsanti grigi di
default per dare all'interfaccia un look moderno, colorato e touch
friendly, coerente con lo schizzo fornito dall'utente.
"""

import tkinter as tk


def _round_rect_points(x1, y1, x2, y2, radius):
    r = radius
    return [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]


class PillButton(tk.Canvas):
    """Pulsante arrotondato disegnato su Canvas, con hover/press e stato disabled."""

    def __init__(self, parent, text, command=None, bg_color="#EAF3F8",
                 fill="#1D6F8C", fg="white", hover_fill=None,
                 font=("TkDefaultFont", 16, "bold"), width=240, height=56,
                 radius=None, **kwargs):
        super().__init__(parent, width=width, height=height, bg=bg_color,
                          highlightthickness=0, bd=0, **kwargs)
        self.command = command
        self.text = text
        self.fill = fill
        self.hover_fill = hover_fill or self._darken(fill)
        self.fg = fg
        self.font = font
        self.width = width
        self.height = height
        self.radius = radius if radius is not None else height // 2
        self._auto_radius = radius is None
        self.enabled = True
        self._current_fill = self.fill

        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", self._on_configure)
        self._draw(self.fill)

    def _on_configure(self, event):
        if event.width > 4 and event.height > 4:
            self.width = event.width
            self.height = event.height
            if self.radius is None or self._auto_radius:
                self.radius = self.height // 2
            self._draw(self._current_fill)

    @staticmethod
    def _darken(hex_color, factor=0.85):
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
        r, g, b = int(r * factor), int(g * factor), int(b * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw(self, fill_color):
        self._current_fill = fill_color
        self.delete("all")
        color = fill_color if self.enabled else "#B7C4CC"
        self.create_polygon(
            _round_rect_points(2, 2, self.width - 2, self.height - 2, self.radius),
            smooth=True, fill=color, outline=""
        )
        text_color = self.fg if self.enabled else "#E8ECEE"
        self.create_text(self.width / 2, self.height / 2, text=self.text,
                          fill=text_color, font=self.font)

    def _on_enter(self, _e):
        if self.enabled:
            self._draw(self.hover_fill)

    def _on_leave(self, _e):
        if self.enabled:
            self._draw(self.fill)

    def _on_press(self, _e):
        if self.enabled:
            self._draw(self._darken(self.fill, 0.7))

    def _on_release(self, e):
        if not self.enabled:
            return
        self._draw(self.hover_fill)
        # Only fire if the release happens back inside the button
        if 0 <= e.x <= self.width and 0 <= e.y <= self.height and self.command:
            self.command()

    def set_text(self, text):
        self.text = text
        self._draw(self._current_fill if self.enabled else self.fill)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._draw(self.fill)

    def highlight_on(self):
        """Used to give visual feedback when something is being dragged over this button
        (e.g. a patient row being dropped onto a folder tile)."""
        self._draw(self.hover_fill)

    def highlight_off(self):
        self._draw(self.fill)


class RoundedCard(tk.Canvas):
    """Sfondo a scheda con angoli arrotondati, usato come contenitore per raggruppare
    sezioni della UI (sostituisce i LabelFrame grigi/squadrati)."""

    def __init__(self, parent, bg_color="#EAF3F8", fill="#FFFFFF", radius=24, **kwargs):
        super().__init__(parent, bg=bg_color, highlightthickness=0, bd=0, **kwargs)
        self.fill = fill
        self.radius = radius
        self.inner = tk.Frame(self, bg=fill)
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        self.delete("bg")
        w, h = event.width, event.height
        if w > 4 and h > 4:
            self.create_polygon(
                _round_rect_points(2, 2, w - 2, h - 2, self.radius),
                smooth=True, fill=self.fill, outline="", tags="bg"
            )
            self.tag_lower("bg")
        self.inner.place(x=14, y=14, width=max(w - 28, 0), height=max(h - 28, 0))
