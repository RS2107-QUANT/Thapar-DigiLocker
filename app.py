"""Tkinter desktop interface for Thapar DigiLocker."""

import os
import json
import subprocess
from pathlib import Path
import sqlite3
import sys
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageEnhance, ImageOps, ImageTk

from locker import DAYS, Locker, read_upload
from models import Admin, GATES, GateStaff, Student, USER_TYPES, open_photo


# Theme inspired by the official Thapar Institute website.
INK = "#242424"
MUTED = "#686868"
PAPER = "#ffffff"
SURFACE = "#f6f5f3"
NAVY = "#202020"  # charcoal site navigation
ACCENT = "#c71f28"  # Thapar red
ACCENT_HOVER = "#a8171f"
BORDER = "#e6e3e0"
FONT = "Arial"  # installed on both Windows and macOS
ASSET_DIR = Path(__file__).resolve().parent / "assets"

PHOTO_TYPES = [("JPEG or PNG photos", "*.jpg *.jpeg *.png")]
PDF_TYPES = [("PDF certificates", "*.pdf")]


# Inheritance: DigiLocker extends Tk's window and event-handling behavior.
class DigiLocker(tk.Tk):
    """Build the interface; delegate account and database operations to Locker."""

    def __init__(self, locker):
        super().__init__()
        # Has-a relationship: the UI uses a Locker object for business operations.
        self.locker = locker
        self.user = None
        self.workspace = None
        self.preview_files = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.title("Thapar DigiLocker")
        self.geometry("1220x790")
        self.minsize(980, 680)
        self.configure(bg=SURFACE)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.styles()
        self.login_page()

    def close(self):
        self.preview_files.cleanup()
        self.destroy()

    def styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        self.option_add("*Font", (FONT, 10))
        style.configure(".", font=(FONT, 10), foreground=INK, background=PAPER)
        style.configure("TFrame", background=PAPER)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("TLabel", background=PAPER, foreground=INK)
        style.configure("Muted.TLabel", background=PAPER, foreground=MUTED)
        style.configure("Title.TLabel", font=(FONT, 24, "bold"))
        style.configure("Section.TLabel", font=(FONT, 12, "bold"))
        style.configure("TEntry", padding=(11, 9), fieldbackground=PAPER,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TCombobox", padding=(11, 8), fieldbackground=PAPER,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", PAPER)])
        style.configure("TButton", font=(FONT, 10, "bold"), padding=(17, 11),
                        background="#efeeeb", foreground=INK, borderwidth=0)
        style.map("TButton", background=[("active", "#e2e0dc")])
        style.configure("Primary.TButton", background=ACCENT, foreground=PAPER)
        style.map("Primary.TButton", background=[("active", ACCENT_HOVER)],
                  foreground=[("active", PAPER)])
        style.configure("Treeview", font=(FONT, 10), rowheight=42,
                        fieldbackground=PAPER, background=PAPER,
                        bordercolor=BORDER, borderwidth=0)
        style.configure("Treeview.Heading", font=(FONT, 10, "bold"), padding=(10, 12),
                        background="#f1efec", foreground="#424242", borderwidth=0)
        style.map("Treeview", background=[("selected", "#fbe6e7")],
                  foreground=[("selected", INK)])

    def logo(self, parent, *, width=165, height=105, bg=PAPER):
        """Keep the PhotoImage referenced so it remains visible in Tkinter."""
        image_path = ASSET_DIR / "thapar_logo.png"
        if not image_path.is_file():
            return tk.Label(parent, text="THAPAR INSTITUTE", bg=bg,
                            fg=ACCENT, font=(FONT, 14, "bold"))
        with Image.open(image_path) as source:
            thumbnail = ImageOps.contain(source.convert("RGB"), (width, height),
                                         Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(thumbnail, master=self)
        label = tk.Label(parent, image=photo, bg=bg, borderwidth=0)
        label.image = photo
        return label

    def reset(self):
        for child in self.winfo_children():
            child.destroy()

    @staticmethod
    def text(parent, value, style="TLabel", **options):
        return ttk.Label(parent, text=value, style=style, **options)

    def timetable_data(self):
        """Load the pre-parsed university Excel once; no Excel dependency at runtime."""
        if not hasattr(self, "_university_timetable"):
            path = ASSET_DIR / "timetable_index.json"
            if not path.is_file():
                raise ValueError("The timetable index is missing from the assets folder. "
                                 "Run build_timetable.py to recreate it.")
            self._university_timetable = json.loads(path.read_text(encoding="utf-8"))
        return self._university_timetable

    def resource_button(self, parent, title, subtitle, icon, callback):
        """Readable campus link for the upper masthead on macOS and Windows."""
        normal, hovered = "#f8f7f5", "#f9e9e9"
        card = tk.Frame(parent, bg=normal, highlightthickness=1,
                        highlightbackground=BORDER, cursor="hand2", width=193, height=58)
        card.pack(side="left", padx=5, pady=0)
        card.pack_propagate(False)
        icon_label = tk.Label(card, text=icon, bg=normal, fg=ACCENT,
                              font=(FONT, 21, "bold"), width=2, cursor="hand2")
        icon_label.pack(side="left", padx=(9, 3))
        labels = tk.Frame(card, bg=normal, cursor="hand2")
        labels.pack(side="left", fill="both", expand=True)
        tk.Label(labels, text=title, bg=normal, fg=INK, cursor="hand2",
                 font=(FONT, 10, "bold"), anchor="w").pack(anchor="w", pady=(10, 1))
        tk.Label(labels, text=subtitle, bg=normal, fg=MUTED, cursor="hand2",
                 font=(FONT, 8), anchor="w").pack(anchor="w")
        children = (card, icon_label, labels, *labels.winfo_children())
        for widget in children:
            widget.bind("<Button-1>", lambda event: callback())
            widget.bind("<Enter>", lambda event: [w.configure(bg=hovered) for w in children])
            widget.bind("<Leave>", lambda event: [w.configure(bg=normal) for w in children])
        return card

    def login_background(self, panel):
        """Photo backdrop resizes with the login panel without obscuring the form."""
        photo_path = ASSET_DIR / "campus_evening.png"
        if not photo_path.is_file():
            return
        with Image.open(photo_path) as original:
            original = original.convert("RGB")
            self._campus_source = ImageEnhance.Brightness(original).enhance(.72)
        backdrop = tk.Label(panel, bg="#33383e", bd=0, highlightthickness=0)
        backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        self._backdrop_label = backdrop
        self._backdrop_size = None

        def resize(event):
            dimensions = (max(event.width, 1), max(event.height, 1))
            if dimensions == self._backdrop_size:
                return
            self._backdrop_size = dimensions
            # Crop to fill; the white login card remains fully legible on top.
            fitted = ImageOps.fit(self._campus_source, dimensions,
                                  Image.Resampling.LANCZOS, centering=(.54, .48))
            backdrop.photo = ImageTk.PhotoImage(fitted, master=self)
            backdrop.configure(image=backdrop.photo)
            backdrop.lower()

        panel.bind("<Configure>", resize)

    def login_page(self):
        self.user = None
        self.reset()
        masthead = tk.Frame(self, bg=PAPER, height=95,
                              highlightbackground=BORDER, highlightthickness=1)
        masthead.pack(fill="x")
        masthead.pack_propagate(False)
        self.logo(masthead, width=126, height=78).pack(side="left", padx=(23, 12), pady=6)
        tk.Frame(masthead, bg=BORDER, width=1, height=48).pack(side="left", pady=22)
        site_name = tk.Frame(masthead, bg=PAPER)
        site_name.pack(side="left", padx=(15, 10))
        tk.Label(site_name, text="DigiLocker", fg=INK, bg=PAPER,
                 font=(FONT, 18, "bold")).pack(anchor="w", pady=(18, 1))
        tk.Label(site_name, text="THAPAR DIGITAL CAMPUS", fg=MUTED, bg=PAPER,
                 font=(FONT, 8, "bold")).pack(anchor="w")
        buttons = tk.Frame(masthead, bg=PAPER)
        buttons.pack(side="right", padx=(0, 24))
        self.resource_button(buttons, "See your timetable", "Search by batch", "▦",
                             self.show_university_timetable)
        self.resource_button(buttons, "Academic calendar", "2026–27 odd semester", "▣",
                             self.show_academic_calendar)

        main = tk.Frame(self, bg=SURFACE)
        main.pack(fill="both", expand=True)
        banner = tk.Frame(main, bg=ACCENT, width=390)
        banner.pack(side="left", fill="both")
        banner.pack_propagate(False)
        copy = tk.Frame(banner, bg=ACCENT)
        copy.pack(fill="x", padx=38, pady=(94, 0))
        tk.Label(copy, text="WELCOME TO THAPAR", bg=ACCENT, fg="#ffe4e6",
                 font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(copy, text="Your campus.\nOne secure locker.", bg=ACCENT,
                 fg=PAPER, justify="left", font=(FONT, 28, "bold"),
                 wraplength=315).pack(anchor="w", pady=(19, 19))
        tk.Frame(copy, bg=PAPER, height=3, width=60).pack(anchor="w", pady=(0, 20))
        tk.Label(copy, text="Certificates, exams and campus access\n"
                 "in one secure student workspace.", justify="left",
                 bg=ACCENT, fg="#fff0f1", font=(FONT, 11)).pack(anchor="w")
        summary = tk.Frame(banner, bg=ACCENT)
        summary.pack(fill="x", padx=38, pady=(58, 0))
        tk.Label(summary, text="CAMPUS RESOURCES", bg=ACCENT, fg="#ffe2e3",
                 font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 12))
        tk.Label(summary, text="Timetables and the academic calendar\n"
                 "are available in the header — no login needed.",
                 bg=ACCENT, fg=PAPER, font=(FONT, 11),
                 justify="left", wraplength=310).pack(anchor="w")
        tk.Label(banner, text="THAPAR DIGILOCKER   •   CAMPUS PORTAL", bg=ACCENT,
                 fg="#ffe2e3", font=(FONT, 9, "bold")).pack(
                     side="bottom", anchor="w", padx=38, pady=28)

        panel = tk.Frame(main, bg="#31353a")
        panel.pack(side="left", fill="both", expand=True)
        self.login_background(panel)
        card = tk.Frame(panel, bg=PAPER, highlightbackground=BORDER,
                        highlightthickness=1)
        card.place(relx=.5, rely=.5, anchor="center", relwidth=.77)
        form = ttk.Frame(card, padding=(34, 29))
        form.pack(fill="both", expand=True)
        tk.Label(form, text="ACCOUNT ACCESS", fg=ACCENT, bg=PAPER,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 9))
        self.text(form, "Sign in to DigiLocker", "Title.TLabel").pack(anchor="w")
        self.text(form, "Select your campus role and enter your credentials.",
                  "Muted.TLabel", wraplength=420).pack(anchor="w", pady=(7, 17))
        fields = ttk.Frame(form)
        fields.pack(fill="x")
        role = self.field(fields, 0, "Role", choices=tuple(USER_TYPES), initial=Student.role)
        username = self.field(fields, 1, "Username / roll no.")
        password = self.field(fields, 2, "Password", secret=True)
        status = self.text(form, "", wraplength=420, foreground=ACCENT)
        status.pack(anchor="w", pady=(6, 0))

        def sign_in():
            try:
                self.user = self.locker.login(username.get(), password.get(), role.get())
            except (ValueError, sqlite3.Error) as exc:
                status.configure(text=str(exc))
                return
            self.shell()

        ttk.Button(form, text="Sign in  →", style="Primary.TButton",
                   command=sign_in).pack(fill="x", pady=(9, 17))
        for widget in fields.winfo_children():
            if isinstance(widget, (ttk.Entry, ttk.Combobox)):
                widget.bind("<Return>", lambda event: sign_in())
        tk.Frame(form, bg=BORDER, height=1).pack(fill="x", pady=(0, 11))
        self.text(form, "Demo accounts", "Section.TLabel").pack(anchor="w")
        self.text(form, "Student: student / Student123\n"
                  "Faculty: faculty / Faculty123\nGate Staff: gate / Gate1234",
                  "Muted.TLabel", justify="left").pack(anchor="w", pady=(7, 0))

    def show_university_timetable(self):
        """Keyboard-searchable in-app viewer using actual parsed Excel cells."""
        try:
            data = self.timetable_data()
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("Timetable unavailable", str(exc), parent=self)
            return
        batches = data["batches"]
        names = sorted(batches, key=lambda b: (int(b[0]) if b[0].isdigit() else 9, b))
        days = data["days"]
        win = tk.Toplevel(self)
        win.title("University Timetable  |  Thapar DigiLocker")
        win.geometry("1160x795")
        win.minsize(875, 650)
        win.configure(bg=SURFACE)
        win._selected_batch = None

        head = tk.Frame(win, bg=PAPER, padx=29, pady=17)
        head.pack(fill="x")
        tk.Label(head, text="UNIVERSITY TIMETABLE", bg=PAPER, fg=ACCENT,
                 font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(head, text="Find your class schedule", bg=PAPER, fg=INK,
                 font=(FONT, 23, "bold")).pack(anchor="w", pady=(4, 3))
        tk.Label(head, text="July–December 2026  ·  from the university timetable workbook",
                 bg=PAPER, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        tk.Frame(win, bg=ACCENT, height=3).pack(fill="x")

        search_area = tk.Frame(win, bg=PAPER, padx=29, pady=17)
        search_area.pack(fill="x", padx=24, pady=(18, 8))
        tk.Label(search_area, text="YOUR BATCH", fg=MUTED, bg=PAPER,
                 font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 8))
        search_row = tk.Frame(search_area, bg=PAPER)
        search_row.pack(fill="x")
        query = tk.StringVar()
        picker = ttk.Combobox(search_row, textvariable=query, values=names,
                              font=(FONT, 12), width=23, state="normal")
        picker.pack(side="left", ipady=4)
        tk.Label(search_row, text="Example: 2Q1A, 2C1B, 3C1A  •  Type or use ↓ to select",
                 bg=PAPER, fg=MUTED, font=(FONT, 10)).pack(side="left", padx=(16, 10))
        status = tk.Label(search_area, text="Type your batch code and press Enter.",
                          bg=PAPER, fg=MUTED, font=(FONT, 10), anchor="w")
        status.pack(anchor="w", pady=(8, 0))

        suggest = tk.Listbox(search_area, height=5, font=(FONT, 11),
                             bg=PAPER, fg=INK, selectbackground="#ffe5e7",
                             selectforeground=INK, relief="solid", borderwidth=1,
                             activestyle="none")
        results = tk.Frame(win, bg=SURFACE)
        results.pack(fill="both", expand=True, padx=24, pady=(3, 18))
        overview = tk.Frame(results, bg=PAPER, padx=24, pady=13)
        overview.pack(fill="x")
        title = tk.Label(overview, text="Select a batch to see your schedule", bg=PAPER,
                         fg=INK, font=(FONT, 16, "bold"), anchor="w")
        title.pack(anchor="w")
        subline = tk.Label(overview, text="Batch data will be shown here.", bg=PAPER,
                           fg=MUTED, font=(FONT, 10), anchor="w")
        subline.pack(anchor="w", pady=(5, 0))

        controls = tk.Frame(results, bg=SURFACE)
        controls.pack(fill="x", pady=(12, 8))
        day_controls = tk.Frame(controls, bg=SURFACE)
        day_controls.pack(side="left")
        mode_controls = tk.Frame(controls, bg=SURFACE)
        mode_controls.pack(side="right")
        display = tk.Frame(results, bg=PAPER)
        display.pack(fill="both", expand=True)
        state = {"batch": None, "day": "Monday", "mode": "day"}
        day_buttons = {}
        mode_buttons = {}

        def show_suggestions():
            q = query.get().strip().upper().replace(" ", "")
            matched = [b for b in names if q in b] if q else names
            picker.configure(values=matched)
            suggest.delete(0, "end")
            if q and q not in batches:
                for b in matched[:7]:
                    suggest.insert("end", b)
                if matched:
                    suggest.pack(anchor="w", fill="x", padx=(0, 200), pady=(9, 0))
                else:
                    suggest.pack_forget()
            else:
                suggest.pack_forget()

        def set_batch(name=None):
            raw = (name if name is not None else query.get())
            key = raw.strip().upper().replace(" ", "")
            if key not in batches:
                # Enter with a filtered list can choose the first visible entry.
                if suggest.winfo_manager() and suggest.size() and name is None:
                    key = suggest.get(0)
                else:
                    status.configure(text="Choose a valid batch from the suggestions.", fg=ACCENT)
                    return
            query.set(key)
            picker.configure(values=names)
            suggest.pack_forget()
            state["batch"] = key
            win._selected_batch = key
            item = batches[key]
            title.configure(text=f"{key}   ·   {item['branch']}")
            subline.configure(text=f"{item['sheet']}  |  Lecture: {item['lecture'] or '—'}"
                              f"  |  Tutorial: {item['tutorial'] or '—'}"
                              "  |  Timings in IST")
            count = sum(len(a) for a in item["days"].values())
            status.configure(text=f"Found {count} occupied slots across the week. "
                             "Switch days or choose Weekly view.", fg="#246647")
            refresh()

        def choose_suggestion(event=None):
            selection = suggest.curselection()
            if selection:
                set_batch(suggest.get(selection[0]))
            elif suggest.size():
                set_batch(suggest.get(0))
            picker.focus_set()
            return "break"

        def on_key(event):
            if event.keysym == "Down" and suggest.winfo_manager() and suggest.size():
                suggest.focus_set(); suggest.selection_clear(0, "end")
                suggest.selection_set(0); suggest.activate(0)
                return "break"
            if event.keysym in ("Return", "KP_Enter"):
                set_batch()
                return "break"
            if event.keysym == "Escape":
                suggest.pack_forget(); return "break"
            if event.keysym not in ("Up", "Down", "Left", "Right", "Tab"):
                show_suggestions()

        picker.bind("<KeyRelease>", on_key)
        picker.bind("<<ComboboxSelected>>", lambda event: set_batch())
        suggest.bind("<Return>", choose_suggestion)
        suggest.bind("<Double-Button-1>", choose_suggestion)
        suggest.bind("<Escape>", lambda event: (suggest.pack_forget(), picker.focus_set()))
        tk.Button(search_row, text="View timetable  →", bg=ACCENT, fg=PAPER,
                  activebackground=ACCENT_HOVER, activeforeground=PAPER,
                  bd=0, padx=22, pady=10, font=(FONT, 10, "bold"),
                  cursor="hand2", command=set_batch).pack(side="right", padx=3)

        def refresh():
            for parent in (display, day_controls, mode_controls):
                for w in parent.winfo_children():
                    w.destroy()
            for d in days:
                active = state["day"] == d and state["mode"] == "day"
                btn = tk.Button(day_controls, text=d[:3], padx=13, pady=9,
                                font=(FONT, 10, "bold"), cursor="hand2",
                                bg=ACCENT if active else "#ffffff",
                                fg=PAPER if active else INK,
                                activeforeground=PAPER if active else INK,
                                bd=0, command=lambda day=d: change_day(day))
                btn.pack(side="left", padx=(0, 6))
                day_buttons[d] = btn
            for mode, caption in (("day", "Day view"), ("week", "Weekly view")):
                active = state["mode"] == mode
                btn = tk.Button(mode_controls, text=caption, padx=12, pady=9,
                                font=(FONT, 10, "bold"), cursor="hand2",
                                bg=NAVY if active else "#ffffff",
                                fg=PAPER if active else INK,
                                bd=0, command=lambda m=mode: change_mode(m))
                btn.pack(side="left", padx=(6, 0))
                mode_buttons[mode] = btn
            if state["batch"] is None:
                tk.Label(display, text="Enter your batch above to view the timetable.",
                         bg=PAPER, fg=MUTED, font=(FONT, 13)).pack(pady=90)
                return
            selected = batches[state["batch"]]
            if state["mode"] == "day":
                # A readable day table, plus a full-width detail field.
                entries = selected["days"][state["day"]]
                tk.Label(display, text=f"{state['day']}  ·  {len(entries)} scheduled slots",
                         bg=PAPER, fg=INK, font=(FONT, 13, "bold")).pack(
                             anchor="w", padx=20, pady=(17, 9))
                columns = ("Time", "Subject / activity", "Type", "Room / instructor")
                frame = tk.Frame(display, bg=PAPER)
                frame.pack(fill="both", expand=True, padx=18)
                table = ttk.Treeview(frame, columns=columns, show="headings", height=10)
                for column, width in (("Time", 125), ("Subject / activity", 285),
                                      ("Type", 100), ("Room / instructor", 420)):
                    table.heading(column, text=column)
                    table.column(column, width=width, minwidth=80,
                                 stretch=column in ("Subject / activity", "Room / instructor"))
                scroll = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
                table.configure(yscrollcommand=scroll.set)
                table.pack(side="left", fill="both", expand=True)
                scroll.pack(side="right", fill="y")
                for i, slot in enumerate(entries):
                    table.insert("", "end", iid=str(i),
                                 values=(slot["time"], slot["subject"], slot["type"], slot["details"]))
                note = tk.Label(display, text="Select a row to read complete subject and room details.",
                                bg="#f8f7f5", fg=MUTED, font=(FONT, 10),
                                anchor="w", justify="left", wraplength=1000,
                                padx=16, pady=12)
                note.pack(fill="x", padx=18, pady=14)
                def selected_row(event=None):
                    if not table.selection():
                        return
                    item = entries[int(table.selection()[0])]
                    note.configure(text=f"{item['time']}  |  {item['subject']}  |  "
                                   f"{item['details'] or 'No location / instructor given'}")
                table.bind("<<TreeviewSelect>>", selected_row)
                if entries:
                    table.selection_set("0")
                    selected_row()
                else:
                    note.configure(text="No classes are listed for this batch on this day.")
            else:
                # Week grid with scroll, so students can glance across all six days.
                outer = tk.Frame(display, bg=PAPER)
                outer.pack(fill="both", expand=True, padx=9, pady=8)
                canvas = tk.Canvas(outer, bg=PAPER, bd=0, highlightthickness=0)
                vs = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
                hs = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
                canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
                vs.pack(side="right", fill="y")
                hs.pack(side="bottom", fill="x")
                canvas.pack(side="left", fill="both", expand=True)
                grid = tk.Frame(canvas, bg=BORDER)
                ref = canvas.create_window((0, 0), window=grid, anchor="nw")
                palette = {"Lecture": "#f6f6fc", "Practical": "#fff0f1",
                           "Tutorial": "#eef7f3", "Class": "#f4f4f2"}
                tk.Label(grid, text="TIME", bg=NAVY, fg=PAPER, width=12,
                         font=(FONT, 10, "bold"), pady=12).grid(row=0, column=0,
                                                                 padx=1, pady=1, sticky="nsew")
                for c, day in enumerate(days, 1):
                    tk.Label(grid, text=day.upper(), bg=NAVY, fg=PAPER,
                             font=(FONT, 10, "bold"), width=20, pady=12).grid(
                                 row=0, column=c, padx=1, pady=1, sticky="nsew")
                by_day = {d: {e["period"]: e for e in selected["days"][d]} for d in days}
                for period in range(1, 15):
                    minutes = 8*60+(period-1)*50
                    start = f"{minutes//60:02}:{minutes%60:02}"
                    minutes += 50
                    end = f"{minutes//60:02}:{minutes%60:02}"
                    tk.Label(grid, text=f"{start}\n–\n{end}", bg="#edece9", fg=INK,
                             font=(FONT, 10, "bold"), width=12, pady=8).grid(
                                 row=period, column=0, padx=1, pady=1, sticky="nsew")
                    for c, day in enumerate(days, 1):
                        entry = by_day[day].get(period)
                        if entry:
                            subject = entry["subject"]
                            location = entry["details"]
                            content = subject + ("\n" + location if location else "")
                            col = palette[entry["type"]]
                            color = INK
                        else:
                            content, col, color = "—", PAPER, "#b2b2b2"
                        tk.Label(grid, text=content, bg=col, fg=color, width=19,
                                 height=4, wraplength=145, justify="center",
                                 font=(FONT, 9), padx=5, pady=5).grid(
                                     row=period, column=c, padx=1, pady=1, sticky="nsew")
                grid.update_idletasks()
                canvas.configure(scrollregion=canvas.bbox("all"))
                grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
                canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
                win._week_canvas = canvas

        def change_day(day):
            state["day"] = day
            state["mode"] = "day"
            refresh()

        def change_mode(mode):
            state["mode"] = mode
            refresh()

        win._choose_batch = set_batch  # Handy for test automation.
        win._switch_mode = change_mode
        win._switch_day = change_day
        win._search_picker = picker
        win._suggestions = suggest
        refresh()
        picker.focus_set()
        return win

    def show_academic_calendar(self):
        """Academic calendar PDF preview and highlights inside the app itself."""
        preview_path = ASSET_DIR / "academic_calendar_preview.png"
        if not preview_path.is_file():
            messagebox.showerror("Calendar unavailable", "Calendar preview is missing.", parent=self)
            return
        win = tk.Toplevel(self)
        win.title("Academic Calendar  |  Thapar DigiLocker")
        win.geometry("1140x800")
        win.minsize(800, 610)
        win.configure(bg=SURFACE)
        top = tk.Frame(win, bg=PAPER, padx=28, pady=17)
        top.pack(fill="x")
        tk.Label(top, text="TIET  /  ACADEMIC CALENDAR", bg=PAPER, fg=ACCENT,
                 font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(top, text="Odd semester 2026–27", bg=PAPER, fg=INK,
                 font=(FONT, 23, "bold")).pack(anchor="w", pady=(4, 2))
        tk.Label(top, text="UG-II, III, IV and PG-II  ·  Official uploaded calendar",
                 bg=PAPER, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        tk.Frame(win, bg=ACCENT, height=3).pack(fill="x")
        highlights = tk.Frame(win, bg=SURFACE, padx=23, pady=18)
        highlights.pack(fill="x")
        milestones = [
            ("MID-SEMESTER TESTS", "21 Sep – 3 Oct"),
            ("DIWALI BREAK", "9 – 13 Nov"),
            ("ASSESSMENT", "30 Nov – 5 Dec"),
            ("END-SEM TESTS", "7 – 19 Dec"),
        ]
        for heading, period in milestones:
            tile = tk.Frame(highlights, bg=PAPER, highlightthickness=1,
                            highlightbackground=BORDER, padx=15, pady=13)
            tile.pack(side="left", fill="x", expand=True, padx=(0, 8))
            tk.Label(tile, text=heading, bg=PAPER, fg=ACCENT,
                     font=(FONT, 9, "bold")).pack(anchor="w")
            tk.Label(tile, text=period, bg=PAPER, fg=INK,
                     font=(FONT, 13, "bold")).pack(anchor="w", pady=(6, 0))
        viewer = tk.Frame(win, bg=PAPER, padx=16, pady=13)
        viewer.pack(fill="both", expand=True, padx=23, pady=(0, 18))
        toolbar = tk.Frame(viewer, bg=PAPER)
        toolbar.pack(fill="x", pady=(0, 12))
        tk.Label(toolbar, text="COMPLETE ACADEMIC CALENDAR", bg=PAPER,
                 fg=INK, font=(FONT, 11, "bold")).pack(side="left")
        zoom_label = tk.Label(toolbar, text="", bg=PAPER, fg=MUTED,
                              font=(FONT, 10))
        zoom_label.pack(side="right", padx=10)
        viewport = tk.Frame(viewer, bg=PAPER)
        viewport.pack(fill="both", expand=True)
        canvas = tk.Canvas(viewport, bg="#e9e6e2", bd=0, highlightthickness=0)
        vs = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        hs = ttk.Scrollbar(viewport, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)
        with Image.open(preview_path) as f:
            original = f.convert("RGB")
        image_id = canvas.create_image(0, 0, anchor="nw")
        current = [0.4]

        def zoom(value=None, fit=False):
            if fit:
                scale = (canvas.winfo_width() - 16) / original.width
            else:
                scale = value if value is not None else current[0]
            scale = max(.25, min(1.8, scale))
            current[0] = scale
            resized = original.resize((max(1,int(original.width*scale)),
                                       max(1,int(original.height*scale))),
                                      Image.Resampling.LANCZOS)
            canvas.photo = ImageTk.PhotoImage(resized, master=win)
            canvas.itemconfigure(image_id, image=canvas.photo)
            canvas.configure(scrollregion=(0,0,*resized.size))
            zoom_label.configure(text=f"{round(scale*100)}%")

        tk.Button(toolbar, text="Fit width", bg="#f2f1ee", fg=INK, bd=0,
                  padx=12, pady=7, cursor="hand2", font=(FONT, 10, "bold"),
                  command=lambda: zoom(fit=True)).pack(side="right", padx=2)
        tk.Button(toolbar, text="+", bg="#f2f1ee", fg=INK, bd=0,
                  padx=12, pady=7, cursor="hand2", font=(FONT, 12, "bold"),
                  command=lambda: zoom(current[0]*1.25)).pack(side="right", padx=2)
        tk.Button(toolbar, text="−", bg="#f2f1ee", fg=INK, bd=0,
                  padx=12, pady=7, cursor="hand2", font=(FONT, 12, "bold"),
                  command=lambda: zoom(current[0]/1.25)).pack(side="right", padx=2)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-2, "units"))
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(2, "units"))
        win.after(80, lambda: zoom(fit=True) if win.winfo_exists() else None)
        win._calendar_zoom = zoom
        win._calendar_canvas = canvas
        return win

    def shell(self):
        self.reset()
        header = tk.Frame(self, bg=PAPER, height=88,
                          highlightbackground=BORDER, highlightthickness=1)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.logo(header, width=145, height=73).pack(side="left", padx=(25, 18), pady=7)
        tk.Frame(header, bg=BORDER, width=1, height=35).pack(side="left", pady=23)
        tk.Label(header, text="DigiLocker", bg=PAPER, fg=INK,
                 font=(FONT, 19, "bold")).pack(side="left", padx=18)
        tk.Button(header, text="Sign out", bg="#f3eeee", fg=ACCENT,
                  activebackground="#fbe0e0", activeforeground=ACCENT,
                  borderwidth=0, padx=18, pady=11, cursor="hand2",
                  font=(FONT, 10, "bold"), command=self.login_page).pack(
                      side="right", padx=25)
        userinfo = tk.Frame(header, bg=PAPER)
        userinfo.pack(side="right", padx=12)
        tk.Label(userinfo, text=self.user.name, bg=PAPER, fg=INK,
                 font=(FONT, 10, "bold")).pack(anchor="e")
        tk.Label(userinfo, text=self.user.role, bg=PAPER, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="e")
        tk.Frame(self, bg=ACCENT, height=4).pack(fill="x")
        body = tk.Frame(self, bg=SURFACE)
        body.pack(fill="both", expand=True)
        # Explicit Tk frames instead of native macOS buttons: consistent contrast.
        sidebar_bg = "#191b20"
        navigation = tk.Frame(body, bg=sidebar_bg, width=258)
        navigation.pack(side="left", fill="y")
        navigation.pack_propagate(False)
        tk.Label(navigation, text="WORKSPACE", bg=sidebar_bg, fg="#9299a5",
                 font=(FONT, 9, "bold")).pack(anchor="w", padx=24, pady=(33, 6))
        tk.Label(navigation, text="Campus services", bg=sidebar_bg, fg="#ffffff",
                 font=(FONT, 17, "bold")).pack(anchor="w", padx=24, pady=(0, 23))

        if isinstance(self.user, Student):
            pages = (
                ("Certificate Locker", self.certificates_page, "DOC"),
                ("Exams & Marks", self.exams_page, "EXM"),
                ("My Timetable", self.timetable_page, "CAL"),
                ("Face & e-ID Profile", self.identity_page, " ID"),
                ("My Gate History", self.history_page, "LOG"),
            )
        elif isinstance(self.user, Admin):
            pages = (
                ("Add New Student", self.add_student_page, " + "),
                ("Student Directory", self.accounts_page, "STU"),
                ("Upload Certificate", self.upload_page, "DOC"),
                ("Publish Exam & Marks", self.publish_page, "EXM"),
            )
        else:
            pages = (
                ("Verify at Gate", self.gate_page, " ID"),
                ("Gate History", self.history_page, "LOG"),
            )

        content = tk.Frame(body, bg=SURFACE)
        content.pack(side="left", fill="both", expand=True, padx=27, pady=26)
        self.workspace = ttk.Frame(content, padding=(29, 26))
        self.workspace.pack(fill="both", expand=True)
        self.nav_buttons = []

        def build_nav_item(title, page, glyph):
            outer = tk.Frame(navigation, bg=sidebar_bg, height=53, cursor="hand2")
            outer.pack(fill="x", padx=(12, 12), pady=4)
            outer.pack_propagate(False)
            stripe = tk.Frame(outer, bg=sidebar_bg, width=4)
            stripe.pack(side="left", fill="y")
            inner = tk.Frame(outer, bg=sidebar_bg)
            inner.pack(side="left", fill="both", expand=True)
            icon = tk.Label(inner, text=glyph, bg="#292d35", fg="#d0d6de",
                            font=(FONT, 8, "bold"), width=4, height=2)
            icon.pack(side="left", padx=(13, 13), pady=8)
            label = tk.Label(inner, text=title, bg=sidebar_bg, fg="#e2e5eb",
                             font=(FONT, 11, "bold"), anchor="w")
            label.pack(side="left", fill="x", expand=True)
            widgets = (outer, inner, icon, label, stripe)
            for widget in widgets:
                widget.bind("<Button-1>", lambda event, p=page: self.navigate(p))
                widget.bind("<Enter>", lambda event, p=page: self.nav_hover(p, True))
                widget.bind("<Leave>", lambda event, p=page: self.nav_hover(p, False))
            self.nav_buttons.append((widgets, page))

        for title, page, glyph in pages:
            build_nav_item(title, page, glyph)

        footer = tk.Frame(navigation, bg=sidebar_bg)
        footer.pack(side="bottom", fill="x", padx=24, pady=23)
        tk.Frame(footer, bg="#353941", height=1).pack(fill="x", pady=(0, 17))
        tk.Label(footer, text="TIET  /  DIGILOCKER", bg=sidebar_bg, fg="#a0a6b0",
                 font=(FONT, 9, "bold")).pack(anchor="w")
        tk.Label(footer, text="Campus management portal", bg=sidebar_bg,
                 fg="#79808d", font=(FONT, 9)).pack(anchor="w", pady=(5, 0))
        self.navigate(pages[0][1])

    def paint_nav(self, widgets, selected=False, hovered=False):
        outer, inner, icon, label, stripe = widgets
        background = "#392529" if selected else "#30343b" if hovered else "#191b20"
        text_color = "#ffffff" if selected else "#f1f3f6" if hovered else "#d7dbe2"
        outer.configure(bg=background)
        inner.configure(bg=background)
        stripe.configure(bg=ACCENT if selected else background)
        icon.configure(bg=ACCENT if selected else "#414650" if hovered else "#292d35",
                       fg="#ffffff" if selected else "#e2e6eb")
        label.configure(bg=background, fg=text_color)

    def nav_hover(self, page, hovered):
        for widgets, target in self.nav_buttons:
            if target == page:
                self.paint_nav(widgets, selected=(target == self.active_page),
                               hovered=hovered)

    def navigate(self, page):
        self.active_page = page
        for widgets, target in self.nav_buttons:
            self.paint_nav(widgets, selected=(target == page))
        try:
            page()
        except (ValueError, OSError, sqlite3.Error) as exc:
            messagebox.showerror("Thapar DigiLocker", str(exc), parent=self)

    def page(self, title, description):
        for child in self.workspace.winfo_children():
            child.destroy()
        tk.Label(self.workspace, text="THAPAR  /  CAMPUS SERVICES", bg=PAPER,
                 fg=ACCENT, font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 12))
        self.text(self.workspace, title, "Title.TLabel").pack(anchor="w")
        self.text(self.workspace, description, "Muted.TLabel", wraplength=700).pack(
            anchor="w", pady=(9, 20))
        tk.Frame(self.workspace, bg=BORDER, height=1).pack(fill="x", pady=(0, 20))
        return self.workspace

    def field(self, parent, row, label, *, column=0, choices=None, initial="", secret=False):
        variable = tk.StringVar(value=initial)
        self.text(parent, label).grid(row=row, column=column, sticky="w", padx=(0, 10), pady=4)
        if choices is None:
            widget = ttk.Entry(parent, textvariable=variable, show="*" if secret else "")
        else:
            widget = ttk.Combobox(parent, textvariable=variable, values=choices, state="readonly")
        widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=4)
        parent.columnconfigure(column + 1, weight=1)
        return variable

    def form(self, parent):
        form = ttk.Frame(parent)
        form.pack(fill="x", pady=(0, 9))
        return form

    def file_picker(self, parent, row, label, types, on_choose=None):
        selected = tk.StringVar()
        name = tk.StringVar(value="No file selected")
        self.text(parent, label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        picker = ttk.Frame(parent)
        picker.grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)

        def choose():
            path = filedialog.askopenfilename(parent=self, filetypes=types)
            if path:
                if on_choose and not self.run(lambda: on_choose(path)):
                    return
                selected.set(path)
                name.set(Path(path).name)

        ttk.Button(picker, text="Choose file", command=choose).pack(side="left")
        ttk.Label(picker, textvariable=name, style="Muted.TLabel", wraplength=320).pack(
            side="left", padx=9
        )
        return selected

    def student_field(self, form):
        usernames = [row["username"] for row in self.locker.students(self.user)]
        return self.field(form, 0, "Student", choices=usernames,
                          initial=usernames[0] if len(usernames) == 1 else "")

    def run(self, operation, success=None, refresh=None):
        try:
            operation()
        except (ValueError, OSError, sqlite3.Error) as exc:
            messagebox.showerror("Thapar DigiLocker", str(exc), parent=self)
            return False
        if success:
            messagebox.showinfo("Thapar DigiLocker", success, parent=self)
        if refresh:
            self.navigate(refresh)
        return True

    def action(self, parent, title, operation, *, success=None, refresh=None, primary=False):
        ttk.Button(
            parent, text=title, style="Primary.TButton" if primary else "TButton",
            command=lambda: self.run(operation, success, refresh),
        ).pack(side="left", padx=(0, 8), pady=4)

    def table(self, parent, columns, records, empty, height=9):
        if not records:
            self.text(parent, empty, "Muted.TLabel", wraplength=560).pack(anchor="w", pady=10)
            return None
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(5, 7))
        labels = [label for label, _ in columns]
        tree = ttk.Treeview(frame, columns=labels, show="headings", height=height, selectmode="browse")
        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        for label, width in columns:
            tree.heading(label, text=label)
            tree.column(label, width=width, minwidth=width)
        tree.tag_configure("stripe", background="#faf8f7")
        for index, (key, values) in enumerate(records):
            tree.insert("", "end", iid=str(key), values=values, tags=("stripe",) if index % 2 else ())
        tree.selection_set(tree.get_children()[0])
        return tree

    @staticmethod
    def selected(tree):
        if tree is None or not tree.selection():
            raise ValueError("Select a record first.")
        return int(tree.selection()[0])

    def certificates_page(self):
        page = self.page("Certificate Locker", "Hackathon certificates issued to your student account.")
        records = self.locker.certificates(self.user)
        actions = self.form(page)
        tree = self.table(page, (("Hackathon", 170), ("Issued by", 150), ("PDF", 150)),
                          [(row["id"], (row["event"], row["issuer"], row["filename"])) for row in records],
                          "Your locker is empty. Certificates appear here when faculty uploads them.")
        if tree is None:
            return

        def certificate():
            return self.locker.certificate(self.user, self.selected(tree))

        def view():
            cert = certificate()
            path = Path(self.preview_files.name) / f"{self.selected(tree)}.pdf"
            path.write_bytes(cert.content)
            if sys.platform == "win32":
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])

        def download():
            cert = certificate()
            target = filedialog.asksaveasfilename(
                parent=self, initialfile=cert.filename, defaultextension=".pdf", filetypes=PDF_TYPES
            )
            if target:
                Path(target).write_bytes(cert.content)

        def verify():
            cert = certificate()
            messagebox.showinfo("Certificate verified", cert.display() + "\n\nSHA-256\n" +
                                cert.fingerprint, parent=self)

        self.action(actions, "View PDF", view, primary=True)
        self.action(actions, "Download", download)
        self.action(actions, "Verify", verify)
        tree.bind("<Double-1>", lambda event: self.run(view))

    def exams_page(self):
        page = self.page("Exams & Marks", "Exam centres, dates, timings and faculty-published results.")
        records = self.locker.exams(self.user)
        self.table(
            page, (("Subject", 125), ("Exam centre", 125), ("Date", 99), ("Time", 105), ("Marks", 100)),
            [(index, (exam.course.subject, exam.centre.name, exam.day,
                      f"{exam.start}–{exam.end}", str(sheet.marks) if sheet else "Pending"))
             for index, (exam, sheet) in enumerate(records)],
            "Faculty has not published any exams for your account.",
        )

    def timetable_page(self):
        page = self.page("My Timetable", "Add a class to your weekly schedule. The same day and start time updates that slot.")
        form = self.form(page)
        day = self.field(form, 0, "Day", choices=DAYS, initial=DAYS[0])
        subject = self.field(form, 0, "Subject", column=2)
        instructor = self.field(form, 1, "Instructor")
        start = self.field(form, 1, "Start HH:MM", column=2)
        end = self.field(form, 2, "End HH:MM")
        actions = self.form(page)
        tree = self.table(
            page, (("Day", 95), ("Subject", 145), ("Instructor", 145), ("Time", 105)),
            [(key, (entry.day, entry.course.subject, entry.instructor.name,
                    f"{entry.start}–{entry.end}")) for key, entry in self.locker.timetable(self.user)],
            "Add your first class to start building your timetable.",
        )
        self.action(actions, "Save class", lambda: self.locker.save_class(
            self.user, day.get(), subject.get(), instructor.get(), start.get(), end.get()
        ), refresh=self.timetable_page, primary=True)
        if tree is not None:
            self.action(actions, "Remove selected", lambda: self.locker.delete_class(
                self.user, self.selected(tree)
            ), refresh=self.timetable_page)

    @staticmethod
    def show_photo(label, content):
        photo = ImageOps.contain(open_photo(content), (245, 195))
        label.preview = ImageTk.PhotoImage(photo, master=label)
        label.configure(image=label.preview, text="")

    def identity_page(self):
        page = self.page("Face & e-ID Profile", "Enroll a reference portrait and campus e-ID for the simulated gate check.")
        identity = self.locker.identity(self.user)
        form = self.form(page)
        number = self.field(form, 0, "Campus e-ID")
        preview = self.text(page, "No reference photo enrolled.", "Muted.TLabel")
        if identity:
            card, profile = identity
            self.text(page, f"Enrolled · {card.display()}", "Muted.TLabel").pack(anchor="w", pady=5)
            self.show_photo(preview, profile.reference_photo)
        photo = self.file_picker(form, 1, "Reference photo", PHOTO_TYPES,
                                 lambda path: self.show_photo(preview, read_upload(path)))
        actions = self.form(page)
        self.action(actions, "Save identity", lambda: self.locker.enroll(
            self.user, number.get(), read_upload(photo.get())
        ), success="Reference photo and e-ID enrolled.", refresh=self.identity_page, primary=True)
        preview.pack(anchor="w", pady=8)

    def upload_page(self):
        page = self.page("Upload Certificate", "Issue a Thapar hackathon certificate to a student account.")
        form = self.form(page)
        student = self.student_field(form)
        event = self.field(form, 1, "Hackathon")
        upload = self.file_picker(form, 2, "Certificate PDF", PDF_TYPES)
        self.action(self.form(page), "Issue certificate", lambda: self.locker.upload_certificate(
            self.user, student.get(), event.get(), upload.get()
        ), success="The certificate is available in the student's locker.",
            refresh=self.upload_page, primary=True)

    def publish_page(self):
        page = self.page("Publish Exam & Marks", "Use the same student, subject, date and start time to update an exam. Leave both marks fields blank to preserve existing marks.")
        form = self.form(page)
        student = self.student_field(form)
        subject = self.field(form, 1, "Subject")
        centre = self.field(form, 1, "Exam centre", column=2)
        day = self.field(form, 2, "Date YYYY-MM-DD")
        start = self.field(form, 2, "Start HH:MM", column=2)
        end = self.field(form, 3, "End HH:MM")
        score = self.field(form, 4, "Marks awarded")
        maximum = self.field(form, 4, "Maximum marks", column=2)
        self.action(self.form(page), "Publish exam", lambda: self.locker.save_exam(
            self.user, student.get(), subject.get(), centre.get(), day.get(), start.get(),
            end.get(), score.get(), maximum.get()
        ), success="Exam details saved to the student's account.", refresh=self.publish_page,
            primary=True)

    def add_student_page(self):
        page = self.page("Add New Student", "Create a separate campus account for a new student.")
        tk.Label(page, text="STUDENT REGISTRATION", bg=PAPER, fg=ACCENT,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(3, 12))
        form = self.form(page)
        username = self.field(form, 0, "Username / roll no.")
        name = self.field(form, 1, "Student full name")
        password = self.field(form, 2, "Initial password", secret=True)
        confirm = self.field(form, 3, "Confirm password", secret=True)
        self.text(page, "Use a unique username (letters, numbers, - or _) and a password "
                  "of at least 8 characters.", "Muted.TLabel", wraplength=650).pack(
                      anchor="w", pady=(8, 13))

        def create_student():
            if password.get() != confirm.get():
                raise ValueError("Passwords do not match.")
            self.locker.add_student(self.user, username.get(), name.get(), password.get())
            messagebox.showinfo(
                "Student account created",
                f"Username: {username.get().strip().lower()}\nRole: Student\n\n"
                "Sign out, select Student and sign in with the initial password you entered.",
                parent=self,
            )

        self.action(self.form(page), "Create student account", create_student,
                    refresh=self.add_student_page, primary=True)
        tk.Frame(page, bg=BORDER, height=1).pack(fill="x", pady=(24, 20))
        self.text(page, "Already registered?", "Section.TLabel").pack(anchor="w")
        ttk.Button(page, text="View Student Directory",
                   command=lambda: self.navigate(self.accounts_page)).pack(
                       anchor="w", pady=(12, 0))

    def accounts_page(self):
        page = self.page("Student Directory", "View the student accounts registered in DigiLocker.")
        records = self.locker.students(self.user)
        toolbar = self.form(page)
        self.text(toolbar, f"{len(records)} registered student(s)",
                  "Section.TLabel").pack(side="left", padx=(0, 18))
        ttk.Button(toolbar, text="+ Add New Student", style="Primary.TButton",
                   command=lambda: self.navigate(self.add_student_page)).pack(side="right")
        delete_button = None
        if isinstance(self.user, Admin):
            delete_button = ttk.Button(toolbar, text="Delete selected student")
            delete_button.pack(side="right", padx=(0, 8))
        tree = self.table(page, (("Username / Roll No.", 200), ("Student Name", 300)),
                   [(index, (row["username"], row["name"]))
                    for index, row in enumerate(records)],
                   "No students yet. Click Add New Student to create an account.")

        def delete_student():
            student = records[self.selected(tree)]
            if messagebox.askyesno(
                "Delete student", f"Permanently delete {student['name']} ({student['username']})?\n\n"
                "This also deletes their certificates, exams, timetable, identity profile "
                "and gate history. This cannot be undone.", parent=self,
            ):
                self.locker.delete_student(self.user, student["username"])
                messagebox.showinfo("Thapar DigiLocker", "Student account deleted.", parent=self)
                self.navigate(self.accounts_page)

        if delete_button is not None:
            delete_button.configure(command=lambda: self.run(delete_student),
                                    state="normal" if records else "disabled")

    def gate_page(self):
        page = self.page("Verify at Gate", "Submit a separate, similarly framed portrait for the simulated photo, e-ID and gate checks.")
        form = self.form(page)
        student = self.student_field(form)
        direction = self.field(form, 0, "Direction", column=2, choices=("Entry", "Exit"), initial="Exit")
        location = self.field(form, 1, "Campus gate", choices=GATES)
        number = self.field(form, 1, "Campus e-ID", column=2)
        preview = self.text(page, "Choose a gate photo to preview it here.", "Muted.TLabel")
        capture = self.file_picker(form, 2, "Gate photo", PHOTO_TYPES,
                                    lambda path: self.show_photo(preview, read_upload(path)))

        def verify():
            log = self.locker.verify_gate(
                self.user, student.get(), direction.get(), location.get(), number.get(),
                read_upload(capture.get())
            )
            messagebox.showinfo("Gate check passed", f"{log.direction} recorded for {log.student} at "
                                f"{log.location}.\n{log.time}", parent=self)

        self.action(self.form(page), "Verify and log", verify, refresh=self.gate_page, primary=True)
        preview.pack(anchor="w", pady=8)

    def history_page(self):
        page = self.page("Gate History", "Successful entry and exit checks, with gate location and timestamp.")
        actions = self.form(page)
        tree = self.table(
            page, (("Student", 95), ("Direction", 74), ("Gate", 100), ("Timestamp", 189), ("Staff", 90)),
            [(row["id"], (row["student"], row["direction"], row["location"],
                          row["time"].replace("T", " "), row["staff"]))
             for row in self.locker.history(self.user)],
            "No successful gate checks have been recorded.",
        )
        if tree is not None and isinstance(self.user, GateStaff):
            def view():
                photo = self.locker.log_photo(self.user, self.selected(tree))
                window = tk.Toplevel(self)
                window.title("Gate capture")
                label = self.text(window, "", padding=15)
                self.show_photo(label, photo)
                label.pack()

            self.action(actions, "View gate photo", view)


if __name__ == "__main__":
    data = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ThaparDigiLocker"
    data.mkdir(parents=True, exist_ok=True)
    print(f"DigiLocker database: {data / 'locker.sqlite3'}", flush=True)
    DigiLocker(Locker(data / "locker.sqlite3")).mainloop()

