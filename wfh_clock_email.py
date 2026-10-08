#!/usr/bin/env python3
"""
WFH Clock In / Out Email Generator
==================================
A Tkinter app that generates clock in / break / back / clock out emails while
you work from home.

Features
  * Live clock with a 12-hour / 24-hour toggle
  * Working-hours timer (time since clock-in minus breaks)
  * Location dropdown   - populated from wfh_config.json
  * Recipient dropdown  - populated from wfh_config.json
  * Buttons: Clock IN, Take Break, Back From Break, Clock OUT
  * Emails are generated ONLY on Clock IN and Clock OUT. Breaks are just
    recorded and reported in the clock-out email.
  * Times in emails are rounded to the nearest 15 minutes, and total hours
    worked are shown as a decimal (e.g. 7.75 hrs). The live on-screen timer
    stays exact.
  * Task list: planned task + status/notes + "complete" checkbox per row.
    In emails, a ticked task shows "Status: Completed" (no checkbox).
  * Email preview (To, Subject and Body are all editable), then open it in
    your email app or copy it. The greeting uses the recipient's name.
    "Update email preview" rebuilds it from your current tasks/details.
  * Today's session is auto-saved, so closing the app by accident is safe.
    Once you've clocked out, closing the window resets the day automatically
    (the next launch starts clean).

Run:   python wfh_clock_email.py
Needs: Python 3.8+ with Tkinter (standard library only).
       On some Linux distros: sudo apt install python3-tk

On first run a wfh_config.json is created next to this script. Edit it, then
click "Reload config" in the app (no restart needed). Example:

{
  "your_name": "Jane Smith",
  "use_24h": true,
  "default_task_rows": 6,
  "locations": ["Home Office", "Kitchen Table"],
  "recipients": [
    {"name": "My Manager", "email": "manager@example.com"},
    {"name": "Payroll",    "email": "payroll@example.com"},
    "plain-address@example.com"
  ]
}
"""

import json
import tkinter as tk
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "wfh_config.json"
SESSION_FILE = APP_DIR / "wfh_session.json"

DEFAULT_CONFIG = {
    "your_name": "",
    "use_24h": True,
    "default_task_rows": 6,
    "locations": ["Home Office", "Living Room", "Kitchen Table"],
    "recipients": [
        {"name": "My Manager", "email": "manager@example.com"},
        {"name": "Payroll", "email": "payroll@example.com"},
    ],
}

STATUS_STYLES = {
    "idle": ("Not clocked in", "#757575"),
    "working": ("Working", "#2e7d32"),
    "break": ("On break", "#ef6c00"),
    "done": ("Clocked out", "#c62828"),
}

EMAIL_TITLES = {
    "in": "Clock In",
    "out": "Clock Out",
}


def fmt_dur(td, seconds=True):
    """Format a timedelta as HH:MM:SS (or '7h 05m')."""
    total = max(0, int(td.total_seconds()))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if seconds:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{h}h {m:02d}m"


def fmt_hours(td):
    """Decimal hours to 2 places, e.g. 7.75"""
    return f"{max(0, td.total_seconds()) / 3600:.2f}"


def round_15(dt):
    """Round a datetime to the nearest 15 minutes (7:08 -> 7:15, 7:07 -> 7:00)."""
    step = 15 * 60
    secs = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6
    rounded = int(secs // step + (1 if secs % step >= step / 2 else 0)) * step
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(seconds=rounded)


def to_iso(dt):
    return dt.isoformat() if dt else None


def from_iso(text):
    return datetime.fromisoformat(text) if text else None


class ClockEmailApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WFH Clock In / Out")
        self.geometry("1120x740")
        self.minsize(980, 640)

        # --- state ---
        self.clock_in_time = None
        self.clock_out_time = None
        self.breaks = []  # list of [start_datetime, end_datetime_or_None]
        self.task_rows = []  # list of dicts of tk variables
        self.locations = []
        self.recipient_map = {}  # label -> email address(es)
        self.recipient_names = {}  # label -> name (only if one was configured)
        self._startup_msg = ""
        self._status_job = None

        # --- tk variables ---
        self.use_24h = tk.BooleanVar(value=True)
        self.name_var = tk.StringVar()
        self.location_var = tk.StringVar()
        self.recipient_var = tk.StringVar()
        self.to_var = tk.StringVar()
        self.subject_var = tk.StringVar()
        self.auto_open_var = tk.BooleanVar(value=False)

        self.config_data = self._read_config() or dict(DEFAULT_CONFIG)
        self.name_var.set(str(self.config_data.get("your_name", "")))
        self.use_24h.set(bool(self.config_data.get("use_24h", True)))

        self._build_ui()
        self._populate_choices()

        rows = self.config_data.get("default_task_rows", 5)
        rows = rows if isinstance(rows, int) and 1 <= rows <= 50 else 6
        for _ in range(rows):
            self._add_task_row()

        self._load_session()
        self._update_buttons()
        self._tick()
        self.after(15000, self._autosave)
        if self._startup_msg:
            self._set_status(self._startup_msg)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # =================================================================
    # Config
    # =================================================================
    def _read_config(self):
        """Return the config dict, or None if it couldn't be read."""
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("top level must be a JSON object")
            return data
        except FileNotFoundError:
            try:
                CONFIG_FILE.write_text(
                    json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8"
                )
                self._startup_msg = (
                    f"Created {CONFIG_FILE.name} - edit it, then click Reload config."
                )
            except OSError:
                pass
            return dict(DEFAULT_CONFIG)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Config problem",
                f"Couldn't read {CONFIG_FILE.name}:\n{exc}\n\n"
                "Check it is valid JSON (commas, quotes, brackets).",
            )
            return None

    def _populate_choices(self):
        """Fill the location and recipient dropdowns from the config."""
        locs = [str(x) for x in self.config_data.get("locations", []) if str(x).strip()]
        self.locations = locs or ["Home"]

        self.recipient_map = {}
        self.recipient_names = {}
        for r in self.config_data.get("recipients", []):
            if isinstance(r, str) and r.strip():
                self.recipient_map[r.strip()] = r.strip()
            elif isinstance(r, dict) and r.get("email"):
                email = str(r["email"]).strip().replace(";", ",")
                given = str(r.get("name") or "").strip()
                label = f"{given or email} <{email}>"
                self.recipient_map[label] = email
                if given:
                    self.recipient_names[label] = given

        labels = list(self.recipient_map)
        self.location_combo["values"] = self.locations
        self.recipient_combo["values"] = labels

        if self.location_var.get() not in self.locations:
            self.location_var.set(self.locations[0])
        if self.recipient_var.get() not in self.recipient_map:
            self.recipient_var.set(labels[0] if labels else "")
        self._sync_to()

    def _sync_to(self):
        """Set the To: field from the recipient dropdown."""
        self.to_var.set(self.recipient_map.get(self.recipient_var.get(), ""))

    def _reload_config(self):
        data = self._read_config()
        if data is None:
            return
        self.config_data = data
        self._populate_choices()
        self._set_status("Config reloaded.")

    # =================================================================
    # UI
    # =================================================================
    def _build_ui(self):
        right = ttk.Frame(self)
        right.pack(side="right", fill="y", padx=(6, 12), pady=12)
        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)
        self._build_left(left)
        self._build_right(right)

    def _build_left(self, parent):
        big = ("Helvetica", 28, "bold")

        # --- clock + working-hours timer ---
        top = ttk.Frame(parent)
        top.pack(fill="x")
        top.columnconfigure((0, 1), weight=1, uniform="top")

        clock_box = ttk.LabelFrame(top, text="Current time", padding=8)
        clock_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.clock_label = ttk.Label(clock_box, font=big)
        self.clock_label.pack()
        self.date_label = ttk.Label(clock_box)
        self.date_label.pack()
        fmt = ttk.Frame(clock_box)
        fmt.pack(pady=(4, 0))
        ttk.Radiobutton(
            fmt, text="24-hour", variable=self.use_24h, value=True,
            command=self._render_clock,
        ).pack(side="left", padx=8)
        ttk.Radiobutton(
            fmt, text="12-hour", variable=self.use_24h, value=False,
            command=self._render_clock,
        ).pack(side="left", padx=8)

        timer_box = ttk.LabelFrame(top, text="Working hours", padding=8)
        timer_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.worked_label = ttk.Label(timer_box, text="00:00:00", font=big)
        self.worked_label.pack()
        self.break_label = ttk.Label(timer_box, text="Breaks: 00:00:00")
        self.break_label.pack()
        self.status_label = ttk.Label(timer_box, font=("Helvetica", 11, "bold"))
        self.status_label.pack(pady=(4, 0))

        # --- details ---
        det = ttk.LabelFrame(parent, text="Details", padding=8)
        det.pack(fill="x", pady=8)
        det.columnconfigure(1, weight=1)

        ttk.Label(det, text="Name:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(det, textvariable=self.name_var).grid(
            row=0, column=1, sticky="ew", pady=3
        )
        ttk.Label(det, text="Location:").grid(row=1, column=0, sticky="w", pady=3)
        self.location_combo = ttk.Combobox(
            det, textvariable=self.location_var, state="readonly"
        )
        self.location_combo.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(det, text="Send to:").grid(row=2, column=0, sticky="w", pady=3)
        self.recipient_combo = ttk.Combobox(
            det, textvariable=self.recipient_var, state="readonly"
        )
        self.recipient_combo.grid(row=2, column=1, sticky="ew", pady=3)
        self.recipient_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_to())
        ttk.Button(det, text="Reload config", command=self._reload_config).grid(
            row=1, column=2, rowspan=2, sticky="ns", padx=(8, 0)
        )

        # --- action buttons ---
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        bar.columnconfigure((0, 1, 2, 3), weight=1, uniform="btn")
        self.btn_in = ttk.Button(bar, text="Clock IN", command=self._clock_in)
        self.btn_break = ttk.Button(bar, text="Take Break", command=self._take_break)
        self.btn_back = ttk.Button(
            bar, text="Back From Break", command=self._back_from_break
        )
        self.btn_out = ttk.Button(bar, text="Clock OUT", command=self._clock_out)
        for i, b in enumerate((self.btn_in, self.btn_break, self.btn_back, self.btn_out)):
            b.grid(row=0, column=i, sticky="ew", padx=3, ipady=8)

        # --- tasks ---
        tasks_box = ttk.LabelFrame(parent, text="Today's tasks", padding=6)
        tasks_box.pack(fill="both", expand=True, pady=(8, 0))

        self.task_holder = ttk.Frame(tasks_box)
        self.task_holder.pack(fill="both", expand=True)
        self.task_canvas = tk.Canvas(self.task_holder, highlightthickness=0, height=180)
        sb = ttk.Scrollbar(
            self.task_holder, orient="vertical", command=self.task_canvas.yview
        )
        self.task_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.task_canvas.pack(side="left", fill="both", expand=True)

        self.task_inner = ttk.Frame(self.task_canvas)
        self._inner_id = self.task_canvas.create_window(
            (0, 0), window=self.task_inner, anchor="nw"
        )
        self.task_inner.bind(
            "<Configure>",
            lambda e: self.task_canvas.configure(scrollregion=self.task_canvas.bbox("all")),
        )
        self.task_canvas.bind(
            "<Configure>",
            lambda e: self.task_canvas.itemconfigure(self._inner_id, width=e.width),
        )
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Button-4>", self._on_wheel)
        self.bind_all("<Button-5>", self._on_wheel)

        self.task_inner.columnconfigure(1, weight=3)
        self.task_inner.columnconfigure(2, weight=2)
        ttk.Label(self.task_inner, text="Done").grid(row=0, column=0, padx=8)
        ttk.Label(self.task_inner, text="Planned task").grid(row=0, column=1, sticky="w")
        ttk.Label(self.task_inner, text="Status / notes").grid(row=0, column=2, sticky="w")

        ttk.Button(tasks_box, text="+ Add task row", command=self._add_task_row).pack(
            anchor="w", pady=(6, 0)
        )

    def _build_right(self, parent):
        ttk.Label(parent, text="Email preview", font=("Helvetica", 12, "bold")).pack(
            anchor="w"
        )
        ttk.Label(parent, text="To:").pack(anchor="w", pady=(6, 0))
        ttk.Entry(parent, textvariable=self.to_var, width=52).pack(fill="x")
        ttk.Label(parent, text="Subject:").pack(anchor="w", pady=(6, 0))
        ttk.Entry(parent, textvariable=self.subject_var, width=52).pack(fill="x")
        ttk.Label(parent, text="Body (editable):").pack(anchor="w", pady=(6, 0))
        self.body_text = tk.Text(parent, width=52, height=18, wrap="word")
        self.body_text.pack(fill="both", expand=True)

        ttk.Checkbutton(
            parent,
            text="Open email app automatically after clocking in / out",
            variable=self.auto_open_var,
        ).pack(anchor="w", pady=6)

        ttk.Button(
            parent, text="Update email preview", command=self._refresh_preview
        ).pack(fill="x", pady=2, ipady=4)
        ttk.Label(
            parent,
            text="Rebuilds the email from your current tasks (replaces manual edits).",
            wraplength=380,
            foreground="#757575",
        ).pack(anchor="w", pady=(0, 6))
        ttk.Button(parent, text="Open in email app", command=self._open_email).pack(
            fill="x", pady=2, ipady=4
        )
        ttk.Button(parent, text="Copy to clipboard", command=self._copy).pack(
            fill="x", pady=2, ipady=4
        )
        ttk.Button(parent, text="Reset day", command=self._reset_day).pack(
            fill="x", pady=(10, 2)
        )

        self.status = ttk.Label(parent, wraplength=380, foreground="#2e7d32")
        self.status.pack(anchor="w", pady=(8, 0))

    def _add_task_row(self, task="", notes="", done=False):
        r = len(self.task_rows) + 1
        row = {
            "done": tk.BooleanVar(value=done),
            "task": tk.StringVar(value=task),
            "notes": tk.StringVar(value=notes),
        }
        ttk.Checkbutton(self.task_inner, variable=row["done"]).grid(
            row=r, column=0, padx=8, pady=2
        )
        ttk.Entry(self.task_inner, textvariable=row["task"]).grid(
            row=r, column=1, sticky="ew", padx=(0, 6), pady=2
        )
        ttk.Entry(self.task_inner, textvariable=row["notes"]).grid(
            row=r, column=2, sticky="ew", padx=(0, 6), pady=2
        )
        self.task_rows.append(row)
        self.after(60, lambda: self.task_canvas.yview_moveto(1.0))

    def _on_wheel(self, event):
        """Scroll the task list when the mouse is over it."""
        w = self.winfo_containing(event.x_root, event.y_root)
        holder = str(self.task_holder)
        if w is None or not (str(w) == holder or str(w).startswith(holder + ".")):
            return
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.task_canvas.yview_scroll(step, "units")

    # =================================================================
    # Clock + timer
    # =================================================================
    def _fmt_time(self, dt, seconds=True):
        if self.use_24h.get():
            return dt.strftime("%H:%M:%S" if seconds else "%H:%M")
        text = dt.strftime("%I:%M:%S %p" if seconds else "%I:%M %p")
        return text.lstrip("0")

    def _durations(self, now):
        """Return (worked, total_break) as timedeltas."""
        if not self.clock_in_time:
            return timedelta(0), timedelta(0)
        end = self.clock_out_time or now
        brk = timedelta(0)
        for start, stop in self.breaks:
            brk += (stop or end) - start
        worked = (end - self.clock_in_time) - brk
        return max(worked, timedelta(0)), brk

    def _render_clock(self):
        now = datetime.now()
        self.clock_label.config(text=self._fmt_time(now))
        self.date_label.config(text=now.strftime("%A, %d %B %Y"))
        worked, brk = self._durations(now)
        self.worked_label.config(text=fmt_dur(worked))
        self.break_label.config(text=f"Breaks: {fmt_dur(brk)}")
        self._render_status()

    def _tick(self):
        self._render_clock()
        self.after(200, self._tick)

    # =================================================================
    # State machine
    # =================================================================
    @property
    def state(self):
        if self.clock_out_time:
            return "done"
        if self.clock_in_time:
            if self.breaks and self.breaks[-1][1] is None:
                return "break"
            return "working"
        return "idle"

    def _render_status(self):
        text, colour = STATUS_STYLES[self.state]
        if self.clock_in_time and self.state != "idle":
            text += f" (in at {self._fmt_time(self.clock_in_time, False)})"
        self.status_label.config(text=text, foreground=colour)

    def _update_buttons(self):
        s = self.state
        self.btn_in.config(state="normal" if s == "idle" else "disabled")
        self.btn_break.config(state="normal" if s == "working" else "disabled")
        self.btn_back.config(state="normal" if s == "break" else "disabled")
        self.btn_out.config(state="normal" if s == "working" else "disabled")
        self._render_status()

    def _name_ok(self):
        if self.name_var.get().strip():
            return True
        messagebox.showwarning("Missing name", "Please enter your name first.")
        return False

    def _clock_in(self):
        if not self._name_ok():
            return
        now = datetime.now()
        self.clock_in_time = now
        self._after_action("in", now)

    def _take_break(self):
        now = datetime.now()
        self.breaks.append([now, None])
        self._after_action("break", now)

    def _back_from_break(self):
        now = datetime.now()
        self.breaks[-1][1] = now
        self._after_action("back", now)

    def _clock_out(self):
        if not messagebox.askyesno("Clock out", "Clock out now?"):
            return
        now = datetime.now()
        self.clock_out_time = now
        self._after_action("out", now)

    def _after_action(self, kind, now):
        self._update_buttons()
        self._save_session()
        t = self._fmt_time(now, False)
        if kind == "break":
            self._set_status(f"Break started at {t}. No email needed.")
        elif kind == "back":
            self._set_status(f"Back from break at {t}. No email needed.")
        else:
            self._generate(kind, now)
            if self.auto_open_var.get():
                self._open_email()

    def _clear_day(self):
        """Wipe clock times, breaks, tasks and the email preview."""
        self.clock_in_time = None
        self.clock_out_time = None
        self.breaks = []
        for row in self.task_rows:
            row["done"].set(False)
            row["task"].set("")
            row["notes"].set("")
        self.task_rows = self.task_rows[:self.config_data.get("default_task_rows", 5)]
        self.subject_var.set("")
        self.body_text.delete("1.0", "end")
        self._update_buttons()

    def _reset_day(self):
        if not messagebox.askyesno(
            "Reset day", "Clear today's clock times, breaks, tasks and email preview?"
        ):
            return
        self._clear_day()
        self._save_session()
        self._set_status("Day reset.")

    # =================================================================
    # Email
    # =================================================================
    def _task_lines(self, final=False):
        """Return (lines, completed_count, total_count) for non-empty task rows.

        A ticked task reads "Status: Completed". Otherwise the status/notes text
        is used. With final=True (clock-out), an unfinished task with no notes
        reads "Status: Not completed".
        """
        lines, done_count = [], 0
        for row in self.task_rows:
            text = row["task"].get().strip()
            if not text:
                continue
            notes = row["notes"].get().strip()
            line = f"{len(lines) + 1}. {text}"
            if row["done"].get():
                done_count += 1
                line += " - Status: Completed"
                if notes:
                    line += f" ({notes})"
            elif notes:
                line += f" - Status: {notes}"
            elif final:
                line += " - Status: Not completed"
            lines.append(line)
        return lines, done_count, len(lines)

    def _rounded_summary(self, now):
        """Return clock-in/out and breaks rounded to 15 min, with totals worked
        out from the rounded times so the numbers in the email add up."""
        clock_in = round_15(self.clock_in_time)
        clock_out = round_15(self.clock_out_time or now)
        breaks = [
            (round_15(start), round_15(stop))
            for start, stop in self.breaks
            if stop is not None
        ]
        break_total = sum((e - s for s, e in breaks), timedelta(0))
        worked = max((clock_out - clock_in) - break_total, timedelta(0))
        return clock_in, clock_out, breaks, break_total, worked

    def _generate(self, kind, now):
        """Build the clock-in ('in') or clock-out ('out') email."""
        name = self.name_var.get().strip()
        location = self.location_var.get().strip()
        date_long = now.strftime("%A, %d %B %Y")
        clock_in, clock_out, breaks, break_total, worked = self._rounded_summary(now)

        def tm(dt):
            return self._fmt_time(dt, False)

        t = tm(clock_in if kind == "in" else clock_out)
        subject = f"{name} - WFH - {now:%d/%m/%Y}"

        verb = "clocking in" if kind == "in" else "clocking out"
        rname = self.recipient_names.get(self.recipient_var.get(), "")
        lines = [
            f"Hi {rname}," if rname else "Hi,",
            "",
            f"Please see below for my WFH information",
            #f"I am {verb} at {t} on {date_long}.",
            f"WFH Start: {tm(clock_in)}",
            f"Estimated End time:{tm(clock_in + timedelta(hours=8))}",
            f"Location: {location}",
        ]

        if kind == "out":
            lines.clear()
            lines = [
                f"Hi {rname}," if rname else "Hi,",
                "",
                f"Please see below for the WFH day completion information.",
                f"Let me know if there are any issues.",
                "",
                f"Work from Home Update – {now:%d/%m/%Y}",
                "",
            ]
            lines += [f"WFH Start: {tm(clock_in)}", f"WFH Finish: {tm(clock_out)}", ""]
            lines.append("Breaks:")
            if breaks:
                for i, (start, stop) in enumerate(breaks, 1):
                    lines.append(
                        f"{i}. {tm(start)} - {tm(stop)} ({fmt_dur(stop - start, False)})"
                    )
                lines.append(
                    f"Total break time: {fmt_dur(break_total, False)} "
                    f"({fmt_hours(break_total)} hrs)"
                )
            else:
                lines.append("None taken")
            lines += [
                "",
                f"Total hours worked: {fmt_hours(worked)} hrs",
            ]

        task_lines, done, total = self._task_lines(final=(kind == "out"))
        if task_lines:
            header = (
                "Planned tasks for today:"
                if kind == "in"
                else f"Tasks ({done} of {total} completed):"
            )
            lines += ["", header] + task_lines

        lines += ["", "Kind regards,", name]

        self._sync_to()
        self.subject_var.set(subject)
        self.body_text.delete("1.0", "end")
        self.body_text.insert("1.0", "\n".join(lines))
        self._set_status(f"{EMAIL_TITLES[kind]} email generated for {t} (rounded to 15 min).")

    def _refresh_preview(self):
        """Rebuild the preview from the current tasks/details (overwrites edits)."""
        if self.state == "idle":
            messagebox.showinfo(
                "Nothing to update",
                "Clock in first. After that, use this button to refresh the email "
                "once you've changed your tasks or details.",
            )
            return
        if not self._name_ok():
            return
        if self.clock_out_time:
            self._generate("out", self.clock_out_time)
        else:
            self._generate("in", self.clock_in_time)
        self._set_status("Email preview updated from your current tasks and details.")

    def _open_email(self):
        subject = self.subject_var.get().strip()
        body = self.body_text.get("1.0", "end").strip()
        if not subject or not body:
            messagebox.showinfo("Nothing to send", "Use one of the clock buttons first.")
            return
        to = self.to_var.get().strip().replace(";", ",")
        if not to:
            messagebox.showwarning(
                "No recipient",
                f"Add recipients to {CONFIG_FILE.name}, then click Reload config.",
            )
            return

        mailto = (
            f"mailto:{urllib.parse.quote(to, safe='@,')}"
            f"?subject={urllib.parse.quote(subject)}"
            f"&body={urllib.parse.quote(body.replace(chr(10), chr(13) + chr(10)))}"
        )
        if not webbrowser.open(mailto):
            self._set_status("Couldn't open an email app - use Copy to clipboard.")
        elif len(mailto) > 1900:
            self._set_status(
                "Opened, but the email is long. If it looks cut off, use Copy to clipboard."
            )
        else:
            self._set_status("Opened in your default email app.")

    def _copy(self):
        subject = self.subject_var.get().strip()
        body = self.body_text.get("1.0", "end").strip()
        if not subject or not body:
            messagebox.showinfo("Nothing to copy", "Use one of the clock buttons first.")
            return
        to = self.to_var.get().strip()
        self.clipboard_clear()
        self.clipboard_append(f"To: {to}\nSubject: {subject}\n\n{body}")
        self._set_status("Copied to clipboard.")

    def _set_status(self, text):
        if self._status_job:
            self.after_cancel(self._status_job)
        self.status.config(text=text)
        self._status_job = self.after(6000, lambda: self.status.config(text=""))

    # =================================================================
    # Session persistence (so a closed window doesn't lose your clock-in)
    # =================================================================
    def _save_session(self):
        data = {
            "date": datetime.now().date().isoformat(),
            "clock_in": to_iso(self.clock_in_time),
            "clock_out": to_iso(self.clock_out_time),
            "breaks": [[to_iso(a), to_iso(b)] for a, b in self.breaks],
            "location": self.location_var.get(),
            "recipient": self.recipient_var.get(),
            "tasks": [
                {
                    "task": r["task"].get(),
                    "notes": r["notes"].get(),
                    "done": r["done"].get(),
                }
                for r in self.task_rows
            ],
        }
        try:
            SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _load_session(self):
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            if data.get("date") != datetime.now().date().isoformat():
                return  # a previous day - start fresh
            clock_in = from_iso(data.get("clock_in"))
            clock_out = from_iso(data.get("clock_out"))
            breaks = [[from_iso(a), from_iso(b)] for a, b in data.get("breaks", [])]
        except (OSError, ValueError, TypeError, AttributeError):
            return

        self.clock_in_time, self.clock_out_time, self.breaks = clock_in, clock_out, breaks

        if data.get("location") in self.locations:
            self.location_var.set(data["location"])
        if data.get("recipient") in self.recipient_map:
            self.recipient_var.set(data["recipient"])
            self._sync_to()

        for i, t in enumerate(data.get("tasks", [])):
            if not isinstance(t, dict):
                continue
            while i >= len(self.task_rows):
                self._add_task_row()
            row = self.task_rows[i]
            row["task"].set(str(t.get("task", "")))
            row["notes"].set(str(t.get("notes", "")))
            row["done"].set(bool(t.get("done", False)))

    def _autosave(self):
        self._save_session()
        self.after(15000, self._autosave)

    def _on_close(self):
        if self.state == "done":
            self._clear_day()  # clocked out for the day - start fresh next time
        self._save_session()
        try:
            try:
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if not isinstance(cfg, dict):
                    cfg = dict(self.config_data)
            except (OSError, ValueError):
                cfg = dict(self.config_data)
            cfg["your_name"] = self.name_var.get().strip()
            cfg["use_24h"] = self.use_24h.get()
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except OSError:
            pass
        self.destroy()


if __name__ == "__main__":
    ClockEmailApp().mainloop()
