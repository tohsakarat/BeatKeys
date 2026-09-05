"""Live waveform and command editor widgets."""

import queue
import re
import tkinter as tk
from tkinter import messagebox, ttk
import keyboard


class Tooltip:
    """Keep secondary explanations out of the working layout."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        self.timer = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def schedule(self, _event=None):
        self.hide()
        self.timer = self.widget.after(450, self.show)

    def show(self):
        self.timer = None
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.geometry(
            f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 4}"
        )
        ttk.Label(self.window, text=self.text, padding=6, relief="solid",
                  borderwidth=1, wraplength=280).pack()

    def hide(self, _event=None):
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class IconButton(ttk.Button):
    """Use the Windows icon font, without additional image dependencies."""

    def __init__(self, parent, symbol, label, command):
        super().__init__(parent, text=symbol, width=3, style="Icon.TButton", command=command)
        self.tooltip = Tooltip(self, label)


class Waveform(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, height=64, background="#f4f7f8",
                         highlightthickness=1, highlightbackground="#d5dce0")
        self.envelope = None
        self.gain = 4
        self.bind("<Configure>", lambda _event: self.redraw())

    def update_wave(self, envelope):
        self.envelope = envelope
        self.redraw()

    def redraw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        center = height / 2
        for fraction in (0.25, 0.5, 0.75):
            self.create_line(0, height * fraction, width, height * fraction, fill="#dce3e6")
        if self.envelope is not None:
            count = len(self.envelope)
            step = max(1, count // max(width, 1))
            offset = max(0, 1600 - count)
            for index in range(0, count, step):
                group = self.envelope[index:index + step]
                low, high = float(group[:, 0].min()), float(group[:, 1].max())
                x = (offset + index) / 1600 * width
                self.create_line(
                    x, center - min(1, high * self.gain) * (center - 8),
                    x, center - max(-1, low * self.gain) * (center - 8),
                    fill="#137b78",
                )
        self.create_text(8, 10, anchor="nw", text="-2 秒", fill="#65767b")
        self.create_text(width - 8, 10, anchor="ne", text="现在", fill="#65767b")


class CommandDialog(tk.Toplevel):
    def __init__(self, parent, command=None, hotkey_only=False, used_ids=()):
        super().__init__(parent)
        self.title("启停快捷键" if hotkey_only else ("编辑命令" if command else "新建命令"))
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self.key_hook = None
        self.capture_timer = None
        self.hotkey_only = hotkey_only
        self.used_ids = set(used_ids)
        self.id_var = tk.StringVar(value=command.get("id", "") if command else "")
        self.name_var = tk.StringVar(value=command["name"] if command else "")
        self.hotkey_var = tk.StringVar(value=command["hotkey"] if command else "")
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="命令名称").grid(row=0, column=0, sticky="w", pady=8)
        name = ttk.Entry(body, textvariable=self.name_var, width=36)
        name.grid(row=0, column=1, padx=(12, 0))
        if hotkey_only:
            for widget in body.grid_slaves(row=0):
                widget.grid_remove()
        else:
            ttk.Label(body, text="英文 ID").grid(row=1, column=0, sticky="w", pady=8)
            identifier = ttk.Entry(body, textvariable=self.id_var, width=36)
            identifier.grid(row=1, column=1, sticky="ew", padx=(12, 0))
            Tooltip(identifier, "仅限小写英文字母和 -，例如 fill-selection；修改后录音文件夹同步改名")
        ttk.Label(body, text="快捷键").grid(row=2, column=0, sticky="w", pady=8)
        shortcut = ttk.Frame(body)
        shortcut.grid(row=2, column=1, sticky="ew", padx=(12, 0))
        ttk.Entry(shortcut, textvariable=self.hotkey_var, width=24).pack(side="left", fill="x", expand=True)
        self.capture_button = ttk.Button(shortcut, text="录入快捷键", command=self.toggle_capture)
        self.capture_button.pack(side="left", padx=(8, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=8)
        ttk.Button(buttons, text="保存", command=self.save).pack(side="left")
        self.bind("<Return>", lambda _event: self.save() if self.key_hook is None else None)
        self.bind("<Escape>", lambda _event: self.destroy() if self.key_hook is None else None)
        self.bind("<FocusOut>", self.check_capture_focus)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        name.focus_set()

    def toggle_capture(self):
        if self.key_hook is not None:
            self.stop_capture()
            return
        events = queue.Queue()
        held = set()
        combination = []
        complete = False

        def capture(event):
            nonlocal complete
            # The hook thread only collects keys; Tk updates stay on the UI thread.
            if not complete:
                if event.event_type == keyboard.KEY_DOWN:
                    held.add(event.scan_code)
                    if event.name not in combination:
                        combination.append(event.name)
                else:
                    held.discard(event.scan_code)
                    if combination and not held:
                        complete = True
                        events.put("+".join(combination))
            return False

        self.key_hook = keyboard.hook(capture, suppress=True)
        self.capture_button.configure(text="取消录入")
        self.hotkey_before_capture = self.hotkey_var.get()
        self.hotkey_var.set("请按组合键…")

        def poll():
            self.capture_timer = None
            try:
                hotkey = events.get_nowait()
            except queue.Empty:
                self.capture_timer = self.after(30, poll)
                return
            self.stop_capture()
            self.hotkey_var.set(hotkey)

        self.capture_timer = self.after(30, poll)

    def check_capture_focus(self, _event):
        self.after_idle(self.stop_capture_if_unfocused)

    def stop_capture_if_unfocused(self):
        if self.winfo_exists() and self.focus_get() is None:
            self.stop_capture()

    def stop_capture(self):
        if self.key_hook is not None:
            keyboard.unhook(self.key_hook)
            self.key_hook = None
            self.hotkey_var.set(self.hotkey_before_capture)
            self.capture_button.configure(text="录入快捷键")
        if self.capture_timer is not None:
            self.after_cancel(self.capture_timer)
            self.capture_timer = None

    def destroy(self):
        self.stop_capture()
        super().destroy()

    def save(self):
        self.stop_capture()
        name, hotkey = self.name_var.get().strip(), self.hotkey_var.get().strip().lower()
        if not name or not hotkey:
            messagebox.showerror("无法保存", "命令名称和快捷键不能为空。", parent=self)
            return
        if not self.hotkey_only:
            identifier = self.id_var.get()
            if not re.fullmatch(r"[a-z-]+", identifier):
                messagebox.showerror("无法保存", "英文 ID 不能为空，只能包含小写英文字母和 -。", parent=self)
                return
            if identifier in {"con", "prn", "aux", "nul"}:
                messagebox.showerror("无法保存", "此 ID 是 Windows 保留名称，不能用作录音文件夹名。", parent=self)
                return
            if identifier in self.used_ids:
                messagebox.showerror("无法保存", "此英文 ID 已被命令或录音文件夹使用，请换一个。", parent=self)
                return
        try:
            keyboard.parse_hotkey(hotkey)
        except (ValueError, KeyError) as exc:
            messagebox.showerror("快捷键无效", str(exc), parent=self)
            return
        self.result = {"name": name, "hotkey": hotkey}
        if not self.hotkey_only:
            self.result["id"] = identifier
        self.destroy()
