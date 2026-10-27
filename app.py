"""
Floating AI SQL Assistant for Windows + SQL Server Management Studio (SSMS)

Workflow:
1. Connect to SQL Server and read schema metadata only.
2. Ask for a query in plain English.
3. Generate SQL with local Ollama first, then optional Gemini/cloud fallback.
4. Review the generated SQL and explicitly copy it to the clipboard.
5. Click SSMS and paste manually (Ctrl+V). This avoids typing into the wrong window.

The app does NOT execute SQL.
"""
import os
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
from pathlib import Path
from configparser import ConfigParser

from db_schema import load_schema
from sql_generator import generate_sql, ProviderError

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.ini")


UI_FONT = "Georgia"
RESPONSE_FONT = "Segoe UI"


def configure_fonts(root):
    """Use installed fonts or private font files from the project's fonts folder."""
    global UI_FONT, RESPONSE_FONT
    if os.name == "nt":
        import ctypes
        for path in (Path(APP_DIR) / "fonts").glob("*"):
            if path.suffix.lower() in (".ttf", ".otf"):
                ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)
    families = {name.casefold(): name for name in tkfont.families(root)}
    UI_FONT = next((families[name] for name in ("mileast", "mileast regular") if name in families), "Georgia")
    RESPONSE_FONT = next((families[name] for name in ("roboto", "roboto regular") if name in families), "Segoe UI")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont", "TkTooltipFont", "TkIconFont"):
        tkfont.nametofont(name, root=root).configure(family=UI_FONT, size=10)
    root.option_add("*Font", (UI_FONT, 10))


def load_config():
    config = ConfigParser()
    config.read(CONFIG_PATH, encoding="utf-8")
    return config


def swing_easing(progress):
    """Animista's cubic-bezier(0.175, 0.885, 0.320, 1.275)."""
    if progress <= 0:
        return 0.0
    if progress >= 1:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(16):
        t = (low+high)/2
        x = 3*(1-t)**2*t*.175 + 3*(1-t)*t*t*.320 + t**3
        if x < progress:
            low = t
        else:
            high = t
    return 3*(1-t)**2*t*.885 + 3*(1-t)*t*t*1.275 + t**3


class RoundedButton(tk.Canvas):
    """Keyboard-accessible pill button with hover, press, and selected states."""
    def __init__(self, parent, text="", command=None, style="", width=None, **kwargs):
        self.text, self.command = text, command
        self.disabled = False
        self.selected = False
        self.hover = self.pressed = False
        self.swing_progress = 1.0
        self.dark = style == "Generate.TButton"
        self.green = style == "Copy.TButton"
        small = style == "Small.TButton"
        self.font = (UI_FONT, 9 if small else 10, "normal")
        from tkinter import font
        metrics = font.Font(font=self.font)
        height = metrics.metrics("linespace") * (text.count("\n")+1) + (14 if small else 18)
        requested = max(metrics.measure(line) for line in text.split("\n")) + 26
        super().__init__(parent, width=width*8+12 if width else requested, height=height,
                         bg="#ffffff", highlightthickness=0, takefocus=True, cursor="hand2", **kwargs)
        self.bind("<Configure>", lambda event: self._draw())
        self.bind("<Enter>", lambda event: self._hover(True))
        self.bind("<Leave>", lambda event: self._hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<space>", lambda event: self.invoke())
        self.bind("<Return>", lambda event: self.invoke())
        self.bind("<FocusIn>", lambda event: self._draw())
        self.bind("<FocusOut>", lambda event: self._draw())

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf or {}, **kwargs)
        for key in ("text", "command", "selected"):
            if key in options:
                setattr(self, key, options.pop(key))
        if "state" in options:
            self.disabled = options.pop("state") == "disabled"
        result = super().configure(**options) if options else None
        self._draw()
        return result

    config = configure

    def _hover(self, value):
        self.hover = value
        self._draw()

    def _press(self, event):
        self.press_origin = (event.x_root, event.y_root)
        if not self.disabled:
            self.focus_set()
            self.pressed = True
            self._draw()

    def _release(self, event):
        origin = getattr(self, "press_origin", (event.x_root, event.y_root))
        clicked = self.pressed and abs(event.x_root-origin[0])+abs(event.y_root-origin[1]) < 6
        self.pressed = False
        self._draw()
        if clicked and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if not self.disabled and self.swing_progress >= .98 and self.command:
            return self.command()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width()-2, self.winfo_height()-2
        if w < 4 or h < 4 or self.swing_progress <= 0:
            return
        fill, fg = "#f2f3f5", "#454b54"
        if self.dark:
            fill, fg = "#202328", "#ffffff"
        elif self.green:
            fill, fg = "#13a779", "#ffffff"
        elif self.selected:
            fill, fg = "#e6edfa", "#2463c7"
        if self.disabled:
            fill, fg = "#e5e7eb", "#959aa3"
        elif self.pressed or self.hover:
            fill = "#363b43" if self.dark else "#078862" if self.green else "#dfe6f0"
        progress = self.swing_progress
        alpha = max(0, min(1, progress))
        def fade(color):
            channels = [int(color[index:index+2], 16) for index in (1, 3, 5)]
            return "#" + "".join(f"{round(255+(channel-255)*alpha):02x}" for channel in channels)
        fill, fg = fade(fill), fade(fg)
        scale = .05+.95*progress
        h *= scale
        r = min(h/2, 16)
        points = [1,r,1,1,r,1,w-r,1,w,1,w,r,w,h-r,w,h,w-r,h,r,h,1,h,1,h-r]
        self.create_polygon(points, smooth=True, splinesteps=24, fill=fill,
                            outline="#7ba4ed" if self.focus_get() == self else fill)
        self.create_text((w+1)/2, (h+1)/2+(1 if self.pressed else 0), text=self.text, fill=fg,
                         font=(self.font[0], max(1, round(self.font[1]*min(1, scale))), self.font[2]))


class FloatingSQLAssistant:
    def __init__(self, root):
        self.root = root
        self.root.title("Floating SQL Assistant")
        self.root.geometry("330x120")
        self.root.minsize(330, 120)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#ffffff", padx=14, pady=10)
        self.root.overrideredirect(True)
        self.root.bind("<Configure>", self._round_window)
        self.root.attributes("-alpha", 0.94)

        self.config = load_config()
        self.schema_text = ""
        self.schema_status = tk.StringVar(value="Not connected")
        self.ai_status = tk.StringVar(value="Ready • connect your database")
        self.events = queue.Queue()
        self.closed = False
        self.drag_offset = None
        self.generating = False
        self.connecting = False
        self.animation = None
        self.page_animation = None
        self.content_animation = None
        self.content_covers = []
        self.swing_buttons = []
        self.reveal = None
        self.full_sql = None
        self.active_page = None
        self.expanded = False
        configure_fonts(self.root)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda event: self._finish_intro() if getattr(self, "intro_canvas", None) else self.collapse())
        self.root.bind("<ButtonRelease-1>", self._end_drag, add="+")
        self.root.bind("<MouseWheel>", self._scroll_sections)
        self.root.bind("<Button-4>", lambda event: self._scroll_sections(event, -1))
        self.root.bind("<Button-5>", lambda event: self._scroll_sections(event, 1))
        self.root.bind("<Alt-Up>", lambda event: self._navigate_section(-1))
        self.root.bind("<Alt-Down>", lambda event: self._navigate_section(1))
        self.root.after(20, self._poll_events)
        self._animate_height(120)
        self._play_intro()

    def _build_ui(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(UI_FONT, 10))
        style.configure("TFrame", background="#ffffff")
        style.configure("TLabel", background="#ffffff", foreground="#697278", font=(UI_FONT, 10))
        style.configure("TButton", background="#f0f2f3", foreground="#222b30", borderwidth=0, padding=(7, 5))
        style.map("TButton", background=[("active", "#e2e7e9"), ("disabled", "#edf0f1")])
        style.configure("TCheckbutton", background="#ffffff", foreground="#697278")
        style.map("TCheckbutton", background=[("active", "#ffffff")])
        style.configure("TEntry", fieldbackground="#f5f6f7", foreground="#222b30", insertcolor="white")
        style.configure("TCombobox", fieldbackground="#f5f6f7", foreground="#222b30")
        style.configure("Generate.TButton", background="#171b20", foreground="white", font=(UI_FONT, 11), padding=(8, 9))
        style.map("Generate.TButton", background=[("active", "#353b43"), ("disabled", "#a6abb0")])
        style.configure("Copy.TButton", background="#00aa78", foreground="white", font=(UI_FONT, 11), padding=(8, 9))
        style.map("Copy.TButton", background=[("active", "#008d64")])
        style.configure("Small.TButton", padding=(4, 2), font=(UI_FONT, 9))
        top = ttk.Frame(self.root)
        top.pack(fill="x", pady=(0, 8))
        self.open_button = RoundedButton(top, text="●  SQL Mini", style="Generate.TButton", command=self.toggle_panel)
        self.open_button.pack(side="left", expand=True)
        RoundedButton(top, text="×", width=2, style="Small.TButton", command=self.close).pack(side="right")
        top.bind("<ButtonPress-1>", self._start_drag)
        top.bind("<B1-Motion>", self._drag)
        self.open_button.bind("<ButtonPress-1>", self._start_drag, add="+")
        self.open_button.bind("<B1-Motion>", self._drag)
        nav = ttk.Frame(self.root)
        nav.pack(side="bottom", fill="x", pady=(6, 0))
        self.nav_buttons = {}
        for name, label in (("Query", "○\nAsk"), ("SQL", "≡\nSQL"), ("Database", "⚙\nSetup")):
            button = RoundedButton(nav, text=label, style="Small.TButton", command=lambda key=name: self.show_page(key))
            button.pack(side="left", fill="x", expand=True)
            self.nav_buttons[name] = button
        self.viewport = ttk.Frame(self.root)
        self.viewport.pack(fill="both", expand=True)
        self.pages = {name: ttk.Frame(self.viewport) for name in self.nav_buttons}
        self.shell = self.pages["Query"]
        query = self.shell
        line = ttk.Frame(query)
        line.pack(fill="x", pady=(0, 6))
        ttk.Label(line, text="Ask your database").pack(side="left")
        self.mode_var = tk.StringVar(value="Basic SQL")
        ttk.Combobox(line, textvariable=self.mode_var, values=("Basic SQL", "CTE", "Joins", "Aggregation"), state="readonly", width=10).pack(side="right")
        self.prompt = self._editor(query, height=2)
        self.prompt.pack(fill="x")
        self.prompt.insert("1.0", "Show the first 10 rows from a table.")
        actions = ttk.Frame(query)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        self.generate_button = RoundedButton(actions, text="Generate", style="Generate.TButton", command=self.generate)
        self.generate_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        RoundedButton(actions, text="Copy SQL", style="Copy.TButton", command=self.copy_sql).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Label(query, textvariable=self.ai_status, wraplength=275, font=(UI_FONT, 9)).pack(side="bottom", anchor="w", pady=(5, 0))
        ttk.Label(query, text="Result opens in SQL • review before running", font=(UI_FONT, 9)).pack(anchor="w", pady=(8, 4))
        self.output = self._editor(self.pages["SQL"], height=8)
        self.output.configure(font=(RESPONSE_FONT, 10))
        self.output.pack(fill="both", expand=True)
        RoundedButton(self.pages["SQL"], text="Copy SQL", style="Copy.TButton", command=self.copy_sql).pack(fill="x", pady=(8, 0))
        ttk.Label(self.pages["SQL"], textvariable=self.ai_status, wraplength=290).pack(fill="x", pady=4)

        conn = self.pages["Database"]
        self.server_var = tk.StringVar(value=self.config.get("database", "server", fallback="localhost"))
        self.database_var = tk.StringVar(value=self.config.get("database", "database", fallback="SQLPracticalDB"))
        self.driver_var = tk.StringVar(value=self.config.get("database", "driver", fallback="ODBC Driver 18 for SQL Server"))
        self.trusted_var = tk.BooleanVar(value=self.config.getboolean("database", "trusted_connection", fallback=True))
        for label, variable in (("Server", self.server_var), ("Database", self.database_var), ("ODBC driver", self.driver_var)):
            ttk.Label(conn, text=label).pack(anchor="w", pady=(6, 2))
            ttk.Entry(conn, textvariable=variable).pack(fill="x")
        ttk.Checkbutton(conn, text="Windows authentication", variable=self.trusted_var).pack(anchor="w", pady=8)
        self.connect_button = RoundedButton(conn, text="Connect / refresh", command=self.connect_schema)
        self.connect_button.pack(fill="x")
        ttk.Label(conn, textvariable=self.schema_status, wraplength=300).pack(anchor="w", pady=6)
        self.pin_button = RoundedButton(conn, text="Pinned", command=self.toggle_topmost)
        self.pin_button.pack(fill="x", pady=4)
        self.root.bind("<Control-Return>", lambda event: self.generate())

    def _play_intro(self):
        """A compressed version of the reference's layered wipes and staggered title."""
        self.intro_after = None
        self.intro_canvas = tk.Canvas(self.root, bg="#ada939", highlightthickness=0)
        self.intro_canvas.place(x=-14, y=-10, relwidth=1, relheight=1, width=28, height=20)
        self.root.tk.call("raise", self.intro_canvas._w)
        self.intro_canvas.bind("<Button-1>", lambda event: self._finish_intro())
        began = time.monotonic()
        title = "SQL Mini"
        title_font = tkfont.Font(family=UI_FONT, size=21)
        widths = [title_font.measure(char) for char in title]
        colors = ("#de2053", "#f05a4e", "#f9c142", "#58b2ca")

        def ease(value):
            value = max(0, min(1, value))
            return 1-(1-value)**3

        def frame():
            if self.closed or self.intro_canvas is None:
                return
            # Keep the choreography intact while stretching playback to two seconds.
            t = (time.monotonic()-began) / (2.0 / 1.15)
            if t >= 1.15:
                self._finish_intro()
                return
            canvas = self.intro_canvas
            canvas.delete("all")
            w, h = max(330, canvas.winfo_width()), max(120, canvas.winfo_height())
            canvas.create_rectangle(0, 0, w, h, fill="#ada939", outline="")
            # Staggered diagonal color sheets sweep across the compact panel.
            for index, color in enumerate(colors):
                progress = ease((t-index*.055)/.32)
                if progress:
                    edge = -h+(w+2*h)*progress
                    canvas.create_polygon(0, 0, edge, 0, edge-h, h, 0, h, fill=color, outline="")
            # Alternating horizontal bands echo the reference's scale-in layers.
            for index, color in enumerate(("#c5d76e", "#1c998e", "#096689", "#3e6e72", "#019fb9")):
                progress = ease((t-.33-index*.025)/.25)
                if progress:
                    width = w*progress
                    left = (w-width)/2
                    canvas.create_rectangle(left, index*h/5, left+width, (index+1)*h/5+1,
                                            fill=color, outline="")
            x = (w-sum(widths))/2
            for index, char in enumerate(title):
                arrival = ease((t-.12-index*.028)/.17)
                departure = ease((t-.77-index*.018)/.20)
                if arrival > 0 and departure < 1:
                    canvas.create_text(x-10*(1-arrival)+85*departure, h/2,
                                       anchor="w", text=char, font=title_font, fill="white")
                x += widths[index]
            # Final vertical curtains uncover the real UI, inside the same window.
            if t > .94:
                progress = ease((t-.94)/.21)
                canvas.place_configure(y=round(-10-h*progress))
            self.intro_after = self.root.after(16, frame)
        frame()

    def _finish_intro(self):
        if getattr(self, "intro_after", None):
            self.root.after_cancel(self.intro_after)
            self.intro_after = None
        if getattr(self, "intro_canvas", None) is not None:
            self.intro_canvas.destroy()
            self.intro_canvas = None

    def _editor(self, parent, height, wrap="word"):
        return tk.Text(parent, height=height, wrap=wrap, font=(UI_FONT, 10),
                       bg="#f5f6f7", fg="#222b30", insertbackground="#00a675",
                       selectbackground="#c7eade", relief="flat", padx=8, pady=7, undo=True)

    def _start_drag(self, event):
        self.drag_offset = (event.x_root-self.root.winfo_x(), event.y_root-self.root.winfo_y())

    def _drag(self, event):
        if self.drag_offset is None:
            return
        x, y = self.drag_offset
        self.root.geometry(f"+{event.x_root-x}+{event.y_root-y}")

    def _end_drag(self, event):
        self.drag_offset = None

    def _round_window(self, event):
        if event.widget != self.root or os.name != "nt":
            return
        size = (event.width, event.height)
        if getattr(self, "rounded_size", None) == size:
            return
        self.rounded_size = size
        import ctypes
        from ctypes import wintypes
        gdi, user = ctypes.windll.gdi32, ctypes.windll.user32
        gdi.CreateRoundRectRgn.restype = wintypes.HRGN
        user.GetParent.argtypes = [wintypes.HWND]
        user.GetParent.restype = wintypes.HWND
        user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        region = gdi.CreateRoundRectRgn(0, 0, event.width+1, event.height+1, 32, 32)
        if not user.SetWindowRgn(user.GetParent(self.root.winfo_id()), region, True):
            gdi.DeleteObject(region)

    def close(self):
        self._cancel_page_animation()
        self._finish_intro()
        self.closed = True
        self.root.destroy()

    def _poll_events(self):
        if self.closed:
            return
        while True:
            try:
                callback, args = self.events.get_nowait()
            except queue.Empty:
                break
            callback(*args)
        self.root.after(20, self._poll_events)

    def _animate_height(self, target):
        if self.animation:
            self.root.after_cancel(self.animation)
        start = self.root.winfo_height()
        if start < 100:
            start = 100
        began = time.monotonic()
        def step():
            fraction = min(1, (time.monotonic()-began)/0.32)
            height = round(start+(target-start)*(1-(1-fraction)**3))
            self.root.geometry(f"330x{height}")
            if fraction < 1:
                self.animation = self.root.after(16, step)
            else:
                self.animation = None
        step()

    def toggle_panel(self):
        if self.expanded:
            self.collapse()
        else:
            self.show_page(self.active_page or "Query")

    def collapse(self):
        self.expanded = False
        self._cancel_page_animation()
        for button in self.nav_buttons.values():
            button.configure(selected=False)
        for page in self.pages.values():
            page.place_forget()
        self.open_button.configure(text="●  SQL Mini")
        self._animate_height(120)

    def _cancel_page_animation(self):
        for button in self.swing_buttons:
            button.swing_progress = 1.0
            button._draw()
        self.swing_buttons.clear()
        if self.content_animation:
            self.root.after_cancel(self.content_animation)
            self.content_animation = None
        for cover in self.content_covers:
            cover.destroy()
        self.content_covers.clear()
        if self.page_animation:
            self.root.after_cancel(self.page_animation)
            self.page_animation = None

    def _pop_page_content(self, name):
        # Wait for the window's expansion to settle before revealing controls.
        if self.animation:
            self.content_animation = self.root.after(16, self._pop_page_content, name)
            return
        self.content_animation = None
        page = self.pages[name]
        children = [child for child in page.winfo_children()
                    if child not in self.content_covers and child.winfo_manager()]
        children.sort(key=lambda child: child.winfo_y())
        def buttons_in(widget):
            if isinstance(widget, RoundedButton):
                yield widget
            for nested in widget.winfo_children():
                yield from buttons_in(nested)
        self.swing_buttons = list(buttons_in(page))
        button_delays = []
        for index, button in enumerate(self.swing_buttons):
            button.swing_progress = 0.0
            button._draw()
            button_delays.append((button, .14+index*.065))
        masks = []
        for index, child in enumerate(children):
            cover = tk.Frame(page, bg="white", borderwidth=0)
            x, y, w, h = child.winfo_x(), child.winfo_y(), child.winfo_width(), child.winfo_height()
            cover.place(x=x, y=y, width=w, height=h)
            cover.lift()
            masks.append((cover, x, y, w, h, index*.035))
        for cover in self.content_covers:
            cover.destroy()
        self.content_covers = [mask[0] for mask in masks]
        began = time.monotonic()
        def pop():
            elapsed = time.monotonic()-began
            remaining = False
            for button, delay in button_delays:
                progress = max(0, min(1, (elapsed-delay)/.65))
                button.swing_progress = swing_easing(progress)
                button._draw()
                remaining |= progress < 1
            for cover, x, y, w, h, delay in masks:
                progress = max(0, min(1, (elapsed-delay)/.18))
                eased = 1-(1-progress)**3
                cover.place_configure(y=y+round(h*eased), height=max(0, round(h*(1-eased))))
                remaining |= progress < 1
            if remaining:
                self.content_animation = self.root.after(16, pop)
            else:
                for cover in self.content_covers:
                    cover.destroy()
                self.content_covers.clear()
                self.swing_buttons.clear()
                self.content_animation = None
        pop()

    def _navigate_section(self, direction):
        if self.page_animation:
            return "break"
        keys = list(self.pages)
        if not self.expanded:
            self.show_page(self.active_page or keys[0])
        else:
            index = keys.index(self.active_page)
            target = max(0, min(len(keys)-1, index+direction))
            if target != index:
                self.show_page(keys[target])
        return "break"

    def _scroll_sections(self, event, direction=None):
        if getattr(self, "intro_canvas", None) is not None:
            return "break"
        # Editors, dropdowns and fields retain their native scrolling behavior.
        if isinstance(event.widget, (tk.Text, tk.Entry, ttk.Entry, ttk.Combobox, ttk.Scale)):
            return
        delta = getattr(event, "delta", 0)
        if direction is None and not delta:
            return
        return self._navigate_section(direction if direction is not None else (-1 if delta > 0 else 1))

    def show_page(self, name):
        if name not in self.pages:
            return
        previous = self.active_page if self.expanded else None
        if previous == name and self.page_animation:
            return
        self._cancel_page_animation()
        self.active_page = name
        self.expanded = True
        self.open_button.configure(text="−  SQL Mini")
        for key, page in self.pages.items():
            if key not in (previous, name):
                page.place_forget()
            self.nav_buttons[key].configure(selected=key == name)
        target = self.pages[name]
        if previous != name:
            cover = tk.Frame(target, bg="white", borderwidth=0)
            cover.place(x=0, y=0, relwidth=1, relheight=1)
            cover.lift()
            self.content_covers.append(cover)
        if previous == name:
            target.place(x=0, y=0, relwidth=1, relheight=1)
        else:
            keys = list(self.pages)
            direction = 1 if previous is None or keys.index(name) > keys.index(previous) else -1
            distance = max(200, self.viewport.winfo_height())
            outgoing = self.pages.get(previous)
            outgoing_start = outgoing.winfo_y() if outgoing is not None else 0
            began = time.monotonic()
            target.place(x=0, y=direction*distance, relwidth=1, relheight=1)
            target.lift()
            def slide():
                fraction = min(1, (time.monotonic()-began)/0.34)
                # Quintic smoothstep: no velocity or acceleration jump at either end.
                eased = fraction**3 * (fraction*(fraction*6-15)+10)
                target.place_configure(y=round(direction*distance*(1-eased)))
                if outgoing is not None:
                    outgoing.place_configure(y=round(outgoing_start*(1-eased)-direction*distance*eased))
                if fraction < 1:
                    self.page_animation = self.root.after(16, slide)
                else:
                    if outgoing is not None:
                        outgoing.place_forget()
                    target.place_configure(x=0, y=0)
                    self.page_animation = None
                    self._pop_page_content(name)
            slide()
        self._animate_height(420 if name == "Database" else 370)

    def _stop_reveal(self, complete=False):
        if self.reveal:
            self.root.after_cancel(self.reveal)
            self.reveal = None
        if complete and self.full_sql is not None:
            self.output.configure(state="normal")
            self.output.delete("1.0", "end")
            self.output.insert("1.0", self.full_sql)
        self.output.configure(state="normal")
        self.full_sql = None

    def copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.ai_status.set("Copied • paste into SSMS")

    def toggle_topmost(self):
        current = bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", not current)
        self.pin_button.configure(text="Pin" if current else "Pinned")

    def connect_schema(self):
        if self.connecting:
            return
        self.connecting = True
        self.schema_text = ""
        self.connect_button.configure(state="disabled")
        self.schema_status.set("Reading schema...")
        connection = dict(server=self.server_var.get().strip(), database=self.database_var.get().strip(),
                          driver=self.driver_var.get().strip(), trusted=self.trusted_var.get(), config=self.config)
        def worker():
            try:
                schema = load_schema(**connection)
                self.events.put((self._schema_loaded, (schema, connection["database"])))
            except Exception as exc:
                self.events.put((self._connection_failed, (str(exc),)))
        threading.Thread(target=worker, daemon=True).start()

    def _connection_failed(self, error):
        self.connecting = False
        self.connect_button.configure(state="normal")
        self.schema_status.set("Connection failed: " + error)

    def _schema_loaded(self, schema, database):
        self.connecting = False
        self.connect_button.configure(state="normal")
        self.schema_text = schema
        self.schema_status.set(f"Connected: {database}")
        self.ai_status.set("Connected • ready to generate")

    def generate(self):
        if getattr(self, "generating", False):
            return
        question = self.prompt.get("1.0", "end").strip()
        if not question:
            messagebox.showwarning("Missing request", "Type what SQL you want to generate.")
            return
        if not self.schema_text:
            self.show_page("Database")
            self.schema_status.set("Connect to load your tables first")
            return
        mode = self.mode_var.get()
        question += f"\nUse {mode}. Keep the T-SQL simple and readable; use a WITH CTE when CTE mode is selected."
        schema = self.schema_text
        self.generating = True
        self.generate_button.configure(state="disabled")
        self._stop_reveal(complete=True)
        self._pulse_generation()
        def worker():
            try:
                sql, provider = generate_sql(question, schema, self.config)
                self.events.put((self._show_sql, (sql, provider)))
            except Exception as exc:
                self.events.put((self._generation_failed, (str(exc),)))
            finally:
                self.events.put((self._generation_finished, ()))
        threading.Thread(target=worker, daemon=True).start()

    def _generation_finished(self):
        self.generating = False
        self.generate_button.configure(state="normal")

    def _pulse_generation(self, frame=0):
        if self.generating and not self.closed:
            self.ai_status.set("Generating" + "." * (frame % 4))
            self.root.after(280, self._pulse_generation, frame+1)

    def _generation_failed(self, error):
        self.ai_status.set("Generation failed • " + error)
        self.show_page("Query")

    def _show_sql(self, sql, provider):
        self._stop_reveal()
        self.full_sql = sql
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self.show_page("SQL")
        self.ai_status.set(f"{provider} • review before use")
        chunk = max(1, len(sql)//65)
        def type_next(index=0):
            self.output.configure(state="normal")
            self.output.insert("end", sql[index:index+chunk])
            self.output.see("end")
            if index+chunk < len(sql):
                self.output.configure(state="disabled")
                self.reveal = self.root.after(16, type_next, index+chunk)
            else:
                self.reveal = None
                self.full_sql = None
        type_next()

    def copy_sql(self):
        self._stop_reveal(complete=True)
        sql = self.output.get("1.0", "end").strip()
        if not sql:
            messagebox.showinfo("No SQL", "Generate SQL first.")
            return
        self.copy_text(sql)

    def copy_for_ssms(self):
        self.copy_sql()


if __name__ == "__main__":
    root = tk.Tk()
    app = FloatingSQLAssistant(root)
    root.mainloop()
