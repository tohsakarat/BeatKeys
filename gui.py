"""Windows GUI for device diagnostics and recorded-audio command matching."""

import ctypes
import json
import math
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import wave
import webbrowser
import winsound
import keyboard

from audio_listener import list_input_devices
from audio_session import AudioSession
from audio_widgets import CommandDialog, IconButton, Tooltip, Waveform
from sample_store import SampleStore
from user_data import initialize_user_data


MODE_NAMES = {
    "monitor": "测试麦克风",
    "record": "录制样本",
    "match": "试匹配 · 不发送按键",
    "listen": "监听中 · 发送快捷键",
}


class VoiceHotkeyApp(tk.Tk):
    def __init__(self, config_path, device_override=None):
        # Set identity before Tk creates its window so the taskbar uses our icon.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("tohsakarat.BeatKeys")
        super().__init__()
        self.title("动次打次 - 声控快捷键")
        icon = tk.PhotoImage(file=str(Path(__file__).parent / "assets" / "beatkeys-icon.png"))
        self.window_icons = [icon.subsample(size, size) for size in (8, 40, 80)]
        self.iconphoto(True, *self.window_icons)
        self.geometry("900x600")
        self.minsize(800, 540)
        self.config_path = Path(config_path)
        self.config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        if device_override is not None:
            self.config_data["device_index"] = device_override
        self.store = SampleStore(self.config_data.get(
            "samples_directory", str(Path.home() / ".beatkeys" / "samples"),
        ))
        self.session = None
        self.command_drag = None
        self.toggle_events = queue.Queue()
        self.toggle_hook = None
        self.registered_toggle = ""
        self.pending_mode = None
        self.closing = False
        self.session_error = False
        self.last_scores = {}
        self.device_rows = []
        self.sample_paths = {}
        self.record_deadline = None
        self.clipped_until = 0
        self.last_driver_status = ""
        self.settings_vars = {}
        self.settings_entries = []
        self.status = tk.StringVar(value="已停止")
        self.device_info = tk.StringVar(value="设备未打开")
        self.level = tk.StringVar(value="RMS — dBFS    峰值 — dBFS")
        self.stream_info = tk.StringVar(value="尚未采集")
        self.result_text = tk.StringVar(value="")
        self.sample_title = tk.StringVar(value="录音样本")
        self.available_count = 0
        self.sample_counts = {}
        style = ttk.Style(self)
        style.theme_use("vista")
        style.configure("Treeview", rowheight=28, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Icon.TButton", font=("Segoe MDL2 Assets", 11), padding=3)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Muted.TLabel", foreground="#667178")
        style.configure(
            "Main.TNotebook.Tab",
            font=("Microsoft YaHei UI", 11, "bold"),
            padding=(18, 7),
        )
        style.map(
            "Main.TNotebook.Tab",
            foreground=[("selected", "#0067b8"), ("!selected", "#40464c")],
        )
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self._build_ui()
        self.refresh_devices(initial=True)
        self.populate_commands()
        self.apply_toggle_hotkey(self.config_data.get("toggle_hotkey", ""))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(80, self.tick)

    def _build_ui(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        devices = ttk.Frame(outer)
        devices.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        devices.columnconfigure(1, weight=1)
        ttk.Label(devices, text="麦克风").grid(row=0, column=0, padx=(0, 8))
        self.device_combo = ttk.Combobox(devices, state="readonly", width=1)
        self.device_combo.grid(row=0, column=1, sticky="ew")
        self.device_combo.bind("<<ComboboxSelected>>", self.switch_device)
        self.device_tooltip = Tooltip(self.device_combo, "")
        self.refresh_button = IconButton(devices, "\ue72c", "刷新设备", self.refresh_devices)
        self.refresh_button.grid(row=0, column=2, padx=6)
        self.monitor_button = ttk.Button(devices, text="测试麦克风", command=lambda: self.begin("monitor"))
        self.monitor_button.grid(row=0, column=3)
        Tooltip(self.monitor_button, "检查输入电平，不录制样本、不发送按键")

        self.meter = ttk.Progressbar(devices, maximum=60, length=90)
        self.meter.grid(row=0, column=4, padx=(12, 8))
        self.health = tk.Label(devices, text="● 未打开", foreground="#59646b",
                               anchor="w", width=10)
        self.health.grid(row=0, column=5, sticky="w")

        self.main_split = tk.PanedWindow(
            outer, orient="vertical", sashwidth=7, sashrelief="flat",
            borderwidth=0, background="#d5dce0", opaqueresize=True,
            sashcursor="sb_v_double_arrow",
        )
        self.main_split.grid(row=2, column=0, sticky="nsew")
        self.tabs = ttk.Notebook(self.main_split, style="Main.TNotebook")
        self.main_split.add(self.tabs, minsize=220, stretch="always")
        self.command_tab = command_tab = ttk.Frame(self.tabs, padding=10)
        settings_tab = ttk.Frame(self.tabs, padding=14)
        self.debug_tab = debug_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(command_tab, text="命令")
        self.tabs.add(settings_tab, text="设置")
        self.tabs.add(debug_tab, text="调试")
        self._build_about_tab()
        command_tab.columnconfigure(0, weight=3, uniform="content")
        command_tab.columnconfigure(2, weight=2, uniform="content")
        command_tab.rowconfigure(1, weight=1)
        ttk.Separator(command_tab, orient="vertical").grid(
            row=0, column=1, rowspan=3, sticky="ns", padx=12)

        toolbar = ttk.Frame(command_tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.new_button = ttk.Button(toolbar, text="新建命令", command=self.new_command)
        self.new_button.pack(side="left")
        self.delete_button = IconButton(toolbar, "\ue74d", "删除所选命令", self.delete_command)
        self.delete_button.pack(side="right")
        self.edit_button = IconButton(toolbar, "\ue70f", "编辑所选命令", self.edit_command)
        self.edit_button.pack(side="right", padx=4)
        self.edit_buttons = [self.new_button, self.edit_button, self.delete_button]
        table_frame = ttk.Frame(command_tab)
        table_frame.grid(row=1, column=0, rowspan=2, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.commands = ttk.Treeview(table_frame, columns=("name", "key", "count", "score"),
                                     displaycolumns=("name", "key", "count"),
                                     show="headings", selectmode="browse", height=6)
        for column, title, width in (("name", "命令", 130), ("key", "快捷键", 115),
                                     ("count", "样本", 48), ("score", "距离", 70)):
            self.commands.heading(column, text=title)
            self.commands.column(column, width=width, minwidth=45,
                                 stretch=column == "name", anchor="w")
        self.commands.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, command=self.commands.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.commands.configure(yscrollcommand=scroll.set)
        self.commands.bind("<<TreeviewSelect>>", lambda _event: self.populate_samples())
        self.commands.bind("<Double-1>", lambda _event: self.edit_command())
        self.commands.bind("<ButtonPress-1>", self.start_command_drag)
        self.commands.bind("<B1-Motion>", self.move_command_drag)
        self.commands.bind("<ButtonRelease-1>", self.finish_command_drag)

        sample_heading = ttk.Frame(command_tab)
        sample_heading.grid(row=0, column=2, sticky="ew", pady=(0, 8))
        self.sample_heading = ttk.Label(sample_heading, textvariable=self.sample_title,
                                       style="Title.TLabel", width=1, anchor="w")
        self.sample_heading.pack(fill="both", expand=True)
        self.sample_tooltip = Tooltip(self.sample_heading, "")
        self.sample_heading.bind("<Configure>", lambda event:
                                 self.sample_heading.configure(wraplength=max(1, event.width)))
        samples_frame = ttk.Frame(command_tab)
        samples_frame.grid(row=1, column=2, sticky="nsew")
        samples_frame.columnconfigure(0, weight=1)
        samples_frame.rowconfigure(0, weight=1)
        self.samples = ttk.Treeview(samples_frame, columns=("file", "duration"), show="headings",
                                    selectmode="browse", height=7)
        self.samples.heading("file", text="样本")
        self.samples.heading("duration", text="时长")
        self.samples.column("file", width=95, minwidth=60)
        self.samples.column("duration", width=80, minwidth=60)
        self.samples.grid(row=0, column=0, sticky="nsew")
        sample_scroll = ttk.Scrollbar(samples_frame, command=self.samples.yview)
        sample_scroll.grid(row=0, column=1, sticky="ns")
        self.samples.configure(yscrollcommand=sample_scroll.set)
        self.samples.bind("<<TreeviewSelect>>", lambda _event: self.update_controls())
        self.sample_empty = ttk.Label(samples_frame, text="尚无录音", style="Muted.TLabel")
        sample_actions = ttk.Frame(command_tab)
        sample_actions.grid(row=2, column=2, sticky="ew", pady=(8, 0))
        self.record_button = ttk.Button(sample_actions, text="录制首个样本", command=self.record_sample)
        self.record_button.pack(side="left")
        self.clear_samples_button = ttk.Button(
            sample_actions, text="清空", width=4, command=self.clear_samples,
        )
        self.clear_samples_button.pack(side="right", padx=(4, 0))
        Tooltip(self.clear_samples_button, "清空当前命令的所有录音")
        self.delete_sample_button = IconButton(sample_actions, "\ue74d", "删除所选录音", self.delete_sample)
        self.delete_sample_button.pack(side="right")
        self.play_button = IconButton(sample_actions, "\ue768", "试听所选录音", self.play_sample)
        self.play_button.pack(side="right", padx=4)
        self.sample_buttons = [
            self.record_button, self.play_button, self.delete_sample_button,
            self.clear_samples_button,
        ]

        # Diagnostic detail is kept out of the command workflow.
        debug_tab.columnconfigure(0, weight=1)
        debug_tab.rowconfigure(5, weight=1)
        device_detail = ttk.Label(debug_tab, textvariable=self.device_info, width=1)
        device_detail.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        device_detail.bind("<Configure>", lambda event:
                           device_detail.configure(wraplength=max(1, event.width)))
        self.waveform = Waveform(debug_tab)
        self.waveform.grid(row=1, column=0, sticky="ew")
        telemetry = ttk.Frame(debug_tab)
        telemetry.grid(row=2, column=0, sticky="ew", pady=6)
        telemetry.columnconfigure(0, weight=1)
        level_detail = ttk.Label(telemetry, textvariable=self.level, width=1)
        level_detail.grid(row=0, column=0, sticky="ew")
        level_detail.bind("<Configure>", lambda event:
                          level_detail.configure(wraplength=max(1, event.width)))
        self.zoom = ttk.Combobox(telemetry, values=("1×", "4×", "16×"), width=5, state="readonly")
        self.zoom.current(1)
        self.zoom.grid(row=0, column=2)
        self.zoom.bind("<<ComboboxSelected>>", self.change_zoom)
        ttk.Label(telemetry, text="波形倍率").grid(row=0, column=1, padx=6)
        stream_detail = ttk.Label(debug_tab, textvariable=self.stream_info, width=1)
        stream_detail.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        stream_detail.bind("<Configure>", lambda event:
                           stream_detail.configure(wraplength=max(1, event.width)))
        details = ttk.Frame(debug_tab)
        details.grid(row=5, column=0, sticky="nsew", pady=(6, 0))
        details.columnconfigure(0, weight=1)
        details.rowconfigure(1, weight=1)
        ttk.Label(details, text="各命令距离").grid(row=0, column=0, sticky="w", pady=(0, 4))
        scores = ttk.Frame(details)
        scores.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        scores.columnconfigure(0, weight=1)
        scores.rowconfigure(0, weight=1)
        self.score_table = ttk.Treeview(scores, columns=("name", "score"), show="headings", height=3)
        self.score_table.heading("name", text="命令")
        self.score_table.heading("score", text="距离")
        self.score_table.column("name", width=100, minwidth=50)
        self.score_table.column("score", width=65, minwidth=55, stretch=False)
        self.score_table.grid(row=0, column=0, sticky="nsew")
        score_scroll = ttk.Scrollbar(scores, command=self.score_table.yview)
        score_scroll.grid(row=0, column=1, sticky="ns")
        self.score_table.configure(yscrollcommand=score_scroll.set)

        fields = [
            ("match_threshold", "发音容错程度", 0.01, 5, 0.05),
            ("speech_start_rms", "开始收音的音量", 0.0001, 1, 0.001),
            ("speech_end_rms", "视为安静的音量", 0.0001, 1, 0.001),
            ("end_silence_seconds", "说完后等待（秒）", 0.1, 5, 0.05),
            ("min_utterance_seconds", "最短说话时长（秒）", 0.1, 10, 0.1),
            ("max_utterance_seconds", "最长说话时长（秒）", 0.5, 30, 0.5),
            ("cooldown_seconds", "两次按键间隔（秒）", 0, 30, 0.1),
        ]
        descriptions = {
            "match_threshold": "越大越容易匹配，也越容易误触发；越小越严格。技术值为 MFCC / DTW 距离上限，不是百分比。",
            "speech_start_rms": "声音达到这个音量才开始收音。调低能捕捉轻声，调高能忽略较轻的声音。数值为 RMS，不是系统麦克风音量百分比。",
            "speech_end_rms": "声音低于这个音量时开始计算安静时间，持续到“说完后等待”的时间便结束这一段。不能高于开始收音的音量。",
            "end_silence_seconds": "声音持续安静这么久后，认为这句话说完并开始匹配。调短响应更快，但容易把一句话拆开。",
            "min_utterance_seconds": "短于这个时长的声音不参与匹配或自动保存，避免短促杂音触发；手动点击“录完”不受此限制。",
            "max_utterance_seconds": "持续说话达到这个时长时强制分段，不会一直等下去。",
            "cooldown_seconds": "两次发送快捷键之间至少等待这么久。已经匹配的命令排队等待，不会因此被跳过。",
        }
        for index, (key, label, low, high, increment) in enumerate(fields):
            row, column = index % 4, (index // 4) * 2
            setting_label = ttk.Label(settings_tab, text=label)
            setting_label.grid(
                row=row, column=column, sticky="w", pady=7, padx=(0, 18))
            Tooltip(setting_label, descriptions[key])
            variable = tk.StringVar(value=str(self.config_data[key]))
            self.settings_vars[key] = variable
            entry = ttk.Spinbox(settings_tab, from_=low, to=high, increment=increment,
                                textvariable=variable, width=10)
            entry.grid(row=row, column=column + 1, sticky="w", pady=7, padx=(0, 20))
            self.settings_entries.append(entry)
            Tooltip(entry, descriptions[key])
        ttk.Label(settings_tab, text="启停快捷键").grid(row=4, column=0, sticky="w", pady=8)
        self.toggle_var = tk.StringVar(value=self.config_data.get("toggle_hotkey", ""))
        toggle_row = ttk.Frame(settings_tab)
        toggle_row.grid(row=4, column=1, columnspan=3, sticky="w")
        toggle_entry = ttk.Entry(toggle_row, textvariable=self.toggle_var, width=22)
        toggle_entry.pack(side="left")
        self.toggle_record_button = ttk.Button(
            toggle_row, text="录入快捷键", command=self.record_toggle_hotkey,
        )
        self.toggle_record_button.pack(side="left", padx=8)
        self.settings_entries.extend([toggle_entry, self.toggle_record_button])
        Tooltip(toggle_entry, "全局启停；留空并保存可关闭")
        ttk.Label(settings_tab, text="录音保存目录").grid(row=5, column=0, sticky="w", pady=8)
        self.samples_directory_var = tk.StringVar(value=str(self.store.root))
        directory_row = ttk.Frame(settings_tab)
        directory_row.grid(row=5, column=1, columnspan=3, sticky="ew")
        directory_row.columnconfigure(0, weight=1)
        directory_entry = ttk.Entry(directory_row, textvariable=self.samples_directory_var, width=32)
        directory_entry.grid(row=0, column=0, sticky="ew")
        directory_button = ttk.Button(directory_row, text="浏览", width=5,
                                      command=self.choose_samples_directory)
        directory_button.grid(row=0, column=1, padx=(8, 0))
        self.settings_entries.extend([directory_entry, directory_button])
        self.save_button = ttk.Button(settings_tab, text="保存设置", command=self.save_settings)
        self.save_button.grid(row=6, column=1, sticky="w", pady=8)

        footer = ttk.Frame(self.main_split, padding=(0, 6, 0, 0))
        self.main_split.add(footer, minsize=105, stretch="never")
        footer.columnconfigure(0, weight=1)
        footer.rowconfigure(2, weight=1)
        self.error_label = tk.Label(footer, text="", width=1, foreground="#a32630", anchor="w",
                                    justify="left", cursor="hand2")
        self.error_label.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.error_label.bind("<Configure>", lambda event:
                              self.error_label.configure(wraplength=max(1, event.width)))
        self.error_label.grid_remove()
        self.error_label.bind("<Button-1>", lambda _event: self.log_text.see("end"))
        self.error_tooltip = Tooltip(self.error_label, "")

        summary = ttk.Frame(footer)
        summary.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        self.status_label = ttk.Label(summary, textvariable=self.status, width=1)
        self.status_label.pack(fill="x")
        actions = ttk.Frame(footer)
        actions.grid(row=1, column=1, sticky="e")
        self.match_button = ttk.Button(actions, text="试匹配", command=lambda: self.begin("match"))
        self.match_button.grid(row=0, column=0, padx=(0, 8))
        Tooltip(self.match_button, "仅比较录音，不发送快捷键")
        self.listen_button = ttk.Button(actions, text="开始监听", command=lambda: self.begin("listen"))
        self.listen_button.grid(row=0, column=1)
        Tooltip(self.listen_button, "匹配成功后向前台应用发送快捷键")
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop)
        self.stop_button.grid(row=0, column=0, columnspan=2, sticky="e")
        self.mode_buttons = [self.monitor_button, self.match_button, self.listen_button]
        # Reserve the normal button tracks when the single stop action replaces them.
        actions.columnconfigure(0, minsize=self.match_button.winfo_reqwidth() + 8)
        actions.columnconfigure(1, minsize=self.listen_button.winfo_reqwidth())
        actions.rowconfigure(0, minsize=self.listen_button.winfo_reqheight())
        self.stop_button.grid_remove()
        self.finish_record_button = ttk.Button(actions, text="录完", command=self.finish_recording)
        self.finish_record_button.grid(row=0, column=0, padx=(0, 8))
        self.finish_record_button.grid_remove()
        self.log_text = ScrolledText(footer, height=4, state="disabled", wrap="word", width=1,
                                    font=("Microsoft YaHei UI", 9))
        self.log_text.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

    def _build_about_tab(self):
        background = "#f7faf9"
        about = ttk.Frame(self.tabs)
        self.tabs.add(about, text="关于")
        about.columnconfigure(0, weight=1)
        about.rowconfigure(0, weight=1)
        canvas = tk.Canvas(about, background=background, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(about, orient="vertical", command=canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)
        content = tk.Frame(canvas, background=background, padx=14, pady=12)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=0)
        window = canvas.create_window(0, 0, window=content, anchor="nw")
        info = tk.Frame(content, background=background)
        info.grid(row=2, column=0, sticky="new", padx=(0, 14))
        info.columnconfigure(0, weight=1)
        photo = tk.Frame(content, background=background)
        photo.grid(row=2, column=1, sticky="new")
        photo.columnconfigure(0, weight=1)

        tk.Label(content, text="动次打次 · BeatKeys", background=background, foreground="#176e65",
                 font=("Microsoft YaHei UI", 20, "bold")).grid(
                     row=0, column=0, columnspan=2, sticky="w")
        tk.Label(content, text="- 声控快捷键 -", background=background,
                 foreground="#596a70", font=("Microsoft YaHei UI", 10)).grid(
                     row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        introduction = tk.Label(
            info,
            text="通过音色与音调识别，实现的声控快捷键工具~这是一个免费软件，"
                 "您可以去作者的主页查看更新。如果有人向您出售它，请联系作者！",
            background=background, foreground="#343f43", justify="left", anchor="w",
            font=("Microsoft YaHei UI", 10), wraplength=320,
        )
        introduction.grid(row=2, column=0, sticky="ew")
        ttk.Separator(info).grid(row=3, column=0, sticky="ew", pady=8)
        links = tk.Frame(info, background=background)
        links.grid(row=4, column=0, sticky="w")
        for column, (title, url) in enumerate((
            ("GitHub · tohsakarat", "https://github.com/tohsakarat"),
            ("小红书 · 作者主页",
             "https://www.xiaohongshu.com/user/profile/6162d3b8000000000202242c"),
        )):
            link = tk.Label(
                links, text=title, background=background, foreground="#176e65",
                cursor="hand2", takefocus=True,
                font=("Microsoft YaHei UI", 10, "underline"),
            )
            link.grid(row=column, column=0, sticky="w", pady=2)
            link.bind("<Button-1>", lambda _event, address=url: webbrowser.open(address))
            link.bind("<Return>", lambda _event, address=url: webbrowser.open(address))
            Tooltip(link, url)
        tk.Label(info, text="群号：917583035", background=background,
                 foreground="#596a70", font=("Microsoft YaHei UI", 10)).grid(
                     row=5, column=0, sticky="w", pady=(4, 0))
        tk.Label(photo, text="请看猫", background=background, foreground="#176e65",
                 font=("Microsoft YaHei UI", 10, "bold")).grid(
                     row=0, column=0, sticky="w", pady=(0, 6))
        self.about_cat_original = tk.PhotoImage(
            file=str(Path(__file__).parent / "assets" / "about-cat.png"),
        )
        cat_image = tk.Label(photo, background=background, borderwidth=0)
        cat_image.grid(row=1, column=0, sticky="nw")
        self.about_cat_scale = None

        # The main divider can shrink this page; keep all about text reachable.
        def resize(event):
            canvas.itemconfigure(window, width=event.width)
            width = max(1, (event.width - 28) // 2)
            scale = max(1, math.ceil(self.about_cat_original.width() / width))
            if scale != self.about_cat_scale:
                self.about_cat_scale = scale
                self.about_cat_image = self.about_cat_original.subsample(scale, scale)
                cat_image.configure(image=self.about_cat_image)
            # Give the text the space left by the image's actual scaled width.
            introduction.configure(
                wraplength=max(1, event.width - 28 - self.about_cat_image.width() - 14),
            )

        canvas.bind("<Configure>", resize)
        content.bind("<Configure>", lambda _event:
                     canvas.configure(scrollregion=canvas.bbox("all")))
        def wheel(event):
            canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"
        for widget in (canvas, content, info, photo, *info.winfo_children(),
                       *photo.winfo_children(), *links.winfo_children()):
            widget.bind("<MouseWheel>", wheel)

    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 500:
            self.log_text.delete("1.0", "2.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def show_error(self, text):
        summary = text.splitlines()[0]
        if len(summary) > 48:
            summary = summary[:47] + "…"
        self.error_label.configure(text=f"{summary}  · 查看调试")
        self.error_tooltip.text = text
        self.error_label.grid()
        self.log(text)

    def clear_error(self):
        self.error_label.configure(text="")
        self.error_label.grid_remove()

    def report_callback_exception(self, exc_type, exc, traceback):
        self.show_error(f"操作失败：{exc}")

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config_data, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")

    def save_settings(self):
        try:
            values = {key: float(var.get()) for key, var in self.settings_vars.items()}
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError("请填写有效数字。")
            if not 0 < values["speech_end_rms"] <= values["speech_start_rms"] <= 1:
                raise ValueError("两项音量须大于 0、不超过 1；视为安静的音量不能高于开始收音的音量。")
            if not 0 < values["min_utterance_seconds"] < values["max_utterance_seconds"] <= 30:
                raise ValueError("最短说话时长须大于 0、小于最长说话时长；最长不能超过 30 秒。")
            if values["match_threshold"] <= 0 or values["cooldown_seconds"] < 0 or values["end_silence_seconds"] <= 0:
                raise ValueError("发音容错程度和说完后等待时间须大于 0；两次按键间隔不能小于 0。")
            hotkey = self.toggle_var.get().strip().lower()
            if hotkey:
                keyboard.parse_hotkey(hotkey)
            directory_text = self.samples_directory_var.get().strip()
            if not directory_text:
                raise ValueError("请选择录音保存目录。")
            directory = Path(directory_text).expanduser()
            if not directory.is_absolute():
                raise ValueError("录音保存目录需为绝对路径。")
            directory.mkdir(parents=True, exist_ok=True)
            self.apply_toggle_hotkey(hotkey)
        except (ValueError, KeyError, OSError) as exc:
            self.show_error(f"设置未保存：{exc}")
            return False
        self.config_data.update(values)
        self.config_data["toggle_hotkey"] = hotkey
        self.config_data["samples_directory"] = str(directory)
        self.save_config()
        if directory != self.store.root:
            winsound.PlaySound(None, 0)
            self.store = SampleStore(directory)
            self.last_scores.clear()
            self.populate_commands()
            self.log(f"录音目录已切换：{directory}")
        self.clear_error()
        return True

    def choose_samples_directory(self):
        directory = filedialog.askdirectory(
            parent=self, title="选择录音保存目录", initialdir=str(self.store.root),
        )
        if directory:
            self.samples_directory_var.set(directory)

    def apply_toggle_hotkey(self, hotkey):
        if hotkey == self.registered_toggle:
            return
        new_hook = keyboard.add_hotkey(
            hotkey, lambda: self.toggle_events.put(None),
            suppress=True, trigger_on_release=True,
        ) if hotkey else None
        if self.toggle_hook is not None:
            keyboard.remove_hotkey(self.toggle_hook)
        self.toggle_hook = new_hook
        self.registered_toggle = hotkey

    def record_toggle_hotkey(self):
        # Suspend the old global binding while capturing its replacement.
        previous = self.registered_toggle
        self.apply_toggle_hotkey("")
        try:
            dialog = CommandDialog(
                self, {"name": "启停", "hotkey": self.toggle_var.get()}, hotkey_only=True,
            )
            self.wait_window(dialog)
            if dialog.result:
                self.toggle_var.set(dialog.result["hotkey"])
        finally:
            self.apply_toggle_hotkey(previous)

    def refresh_devices(self, initial=False):
        if self.session:
            return
        try:
            devices = list_input_devices()
            self.device_rows = [None] + devices
            self.device_combo["values"] = ["系统默认输入"] + [
                f"{item['name']}  |  {item['api']}  |  #{item['index']}" for item in devices
            ]
            index = self.config_data.get("device_index")
            selected = next((i for i, item in enumerate(self.device_rows)
                             if item is not None and item["index"] == index), 0)
            self.device_combo.current(selected)
            self.describe_device()
            if index is not None and selected == 0:
                self.show_error("先前选定设备不在列表中；已选系统默认输入，请确认后再启动。")
                self.config_data["device_index"] = None
                self.save_config()
            elif not initial:
                self.log(f"设备列表已刷新，共 {len(devices)} 项。")
        except Exception as exc:
            self.show_error(f"无法枚举音频设备：{exc}")

    def describe_device(self):
        index = self.device_combo.current()
        if index < 0:
            return
        row = self.device_rows[index]
        self.device_tooltip.text = (
            f"{row['name']}\n{row['api']} · #{row['index']}" if row else "系统默认输入"
        )
        self.device_info.set(
            f"{row['api']} · {row['channels']} 输入通道 · 默认 {int(row['default_samplerate'])} Hz"
            if row else "系统默认输入 · 实际设备在打开后显示"
        )

    def switch_device(self, _event=None):
        row = self.device_rows[self.device_combo.current()]
        self.config_data["device_index"] = row["index"] if row else None
        self.save_config()
        self.describe_device()
        if self.session:
            # Switching never automatically rearms shortcut dispatch.
            self.pending_mode = "monitor"
            self.session.stop()
            self.status.set("正在释放旧设备")
            self.device_combo.configure(state="disabled")
        else:
            self.waveform.update_wave(None)
            self.health.configure(text="● 未打开", foreground="#59646b")

    def change_zoom(self, _event=None):
        self.waveform.gain = (1, 4, 16)[self.zoom.current()]
        self.waveform.redraw()

    def selected_command(self):
        selected = self.commands.selection()
        return next((item for item in self.config_data["commands"]
                     if selected and item["id"] == selected[0]), None)

    def start_command_drag(self, event):
        self.command_drag = None
        if self.session or self.commands.identify_region(event.x, event.y) != "cell":
            return
        item = self.commands.identify_row(event.y)
        if item:
            self.command_drag = (item, event.y, False)

    def move_command_drag(self, event):
        if self.command_drag is None:
            return
        item, initial_y, dragging = self.command_drag
        if not dragging and abs(event.y - initial_y) < 5:
            return
        self.command_drag = (item, initial_y, True)
        self.commands.configure(cursor="hand2")
        # Scroll the list at its edges so off-screen positions remain reachable.
        if event.y < 35:
            self.commands.yview_scroll(-1, "units")
        elif event.y > self.commands.winfo_height() - 20:
            self.commands.yview_scroll(1, "units")
        target = self.commands.identify_row(event.y)
        if target and target != item:
            self.commands.move(item, "", self.commands.index(target))
        return "break"

    def finish_command_drag(self, _event):
        drag = self.command_drag
        self.command_drag = None
        self.commands.configure(cursor="")
        if drag is None or not drag[2]:
            return
        order = self.commands.get_children()
        previous = self.config_data["commands"]
        if list(order) != [command["id"] for command in previous]:
            by_id = {command["id"]: command for command in previous}
            self.config_data["commands"] = [by_id[item] for item in order]
            self.save_config()
            for index, item in enumerate(order):
                self.score_table.move(item, "", index)
            self.log("命令顺序已保存")
        return "break"

    def populate_commands(self, select=None):
        selected = select or (self.commands.selection() or [None])[0]
        self.commands.delete(*self.commands.get_children())
        self.score_table.delete(*self.score_table.get_children())
        self.sample_counts = {}
        for command in self.config_data["commands"]:
            score = self.last_scores.get(command["id"])
            self.sample_counts[command["id"]] = self.store.count_samples(command["id"])
            self.commands.insert("", "end", iid=command["id"], values=(
                command["name"], command["hotkey"].upper(), self.sample_counts[command["id"]],
                "—" if score is None else f"{score:.3f}",
            ))
            self.score_table.insert("", "end", iid=command["id"],
                                    values=(command["name"], "—" if score is None else f"{score:.3f}"))
        self.available_count = sum(count > 0 for count in self.sample_counts.values())
        if not selected or not self.commands.exists(selected):
            selected = next(iter(self.commands.get_children()), None)
        if selected and self.commands.exists(selected):
            self.commands.selection_set(selected)
            self.commands.see(selected)
        self.populate_samples()

    def populate_samples(self):
        self.samples.delete(*self.samples.get_children())
        self.sample_paths = {}
        command = self.selected_command()
        name = command["name"] if command else ""
        short_name = name if len(name) <= 14 else name[:13] + "…"
        self.sample_title.set(f"{short_name} · 录音" if command else "录音样本")
        self.sample_tooltip.text = name
        if command:
            for path in self.store.list_samples(command["id"]):
                with wave.open(str(path), "rb") as recording:
                    duration = recording.getnframes() / recording.getframerate()
                self.sample_paths[path.name] = path
                self.samples.insert("", "end", iid=path.name, values=(path.name, f"{duration:.2f} 秒"))
        if not self.sample_paths:
            self.sample_empty.configure(text="尚无录音" if command else "尚无命令")
            self.sample_empty.place(relx=0.5, rely=0.45, anchor="center")
        else:
            self.sample_empty.place_forget()
        self.record_button.configure(text="再录一个样本" if self.sample_paths else "录制首个样本")
        self.update_controls()

    def update_controls(self):
        """Only show actions that are meaningful at the current workflow stage."""
        idle = self.session is None
        selected = self.selected_command() is not None
        sample_selected = bool(self.samples.selection())
        for button in self.edit_buttons + self.mode_buttons + self.sample_buttons:
            button.configure(state="normal" if idle else "disabled")
        self.edit_button.configure(state="normal" if idle and selected else "disabled")
        self.delete_button.configure(state="normal" if idle and selected else "disabled")
        self.record_button.configure(state="normal" if idle and selected else "disabled")
        for button in (self.play_button, self.delete_sample_button):
            button.configure(state="normal" if idle and sample_selected else "disabled")
        self.clear_samples_button.configure(
            state="normal" if idle and self.sample_paths else "disabled",
        )
        for button in (self.match_button, self.listen_button):
            button.configure(state="normal" if idle and self.available_count else "disabled")
        if idle:
            if not self.config_data["commands"]:
                self.status.set("尚无命令")
            elif not self.available_count:
                self.status.set("尚无录音样本")
            elif not self.session_error:
                self.status.set(f"就绪 · {self.available_count} 条命令")

    def new_command(self):
        self.edit_command(new=True)

    def edit_command(self, new=False):
        if self.session:
            return
        command = None if new else self.selected_command()
        if not new and command is None:
            self.show_error("请先选择命令。")
            return
        used_ids = {item["id"] for item in self.config_data["commands"]}
        if self.store.root.exists():
            used_ids.update(path.name for path in self.store.root.iterdir())
        if command is not None:
            used_ids.discard(command["id"])
        dialog = CommandDialog(self, command, used_ids=used_ids)
        self.wait_window(dialog)
        if dialog.result:
            if new:
                command = dict(dialog.result)
                self.config_data["commands"].append(command)
            else:
                old_id = command["id"]
                if old_id != dialog.result["id"]:
                    winsound.PlaySound(None, 0)
                    try:
                        self.store.rename_command(old_id, dialog.result["id"])
                    except OSError as exc:
                        self.show_error(f"命令未保存，录音文件夹改名失败：{exc}")
                        return
                    self.last_scores.pop(old_id, None)
                command.update(dialog.result)
            self.save_config()
            self.populate_commands(command["id"])
            self.tabs.select(self.command_tab)
            self.log(f"已保存命令：{command['name']} → {command['hotkey'].upper()}")

    def delete_command(self):
        if self.session:
            return
        command = self.selected_command()
        if command and messagebox.askyesno("删除命令",
                f"删除“{command['name']}”及其全部录音样本？", parent=self):
            self.store.delete_samples(command["id"])
            self.config_data["commands"].remove(command)
            self.last_scores.pop(command["id"], None)
            self.save_config()
            self.populate_commands()

    def play_sample(self):
        if self.session:
            return
        selected = self.samples.selection()
        if selected:
            winsound.PlaySound(str(self.sample_paths[selected[0]]),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        else:
            self.show_error("请先选择一个录音样本。")

    def delete_sample(self):
        if self.session:
            return
        selected = self.samples.selection()
        if selected and messagebox.askyesno("删除录音", f"删除样本 {selected[0]}？", parent=self):
            winsound.PlaySound(None, 0)
            self.sample_paths[selected[0]].unlink()
            self.populate_commands()

    def record_sample(self):
        command = self.selected_command()
        if command is None:
            self.show_error("请先新建或选择一个命令。")
            return
        self.begin("record", command)

    def clear_samples(self):
        if self.session:
            return
        command = self.selected_command()
        if command and self.sample_paths and messagebox.askyesno(
                "清空录音", f"清空“{command['name']}”的全部 {len(self.sample_paths)} 条录音？",
                parent=self):
            winsound.PlaySound(None, 0)
            self.store.delete_samples(command["id"])
            self.last_scores.pop(command["id"], None)
            self.populate_commands(select=command["id"])
            self.log(f"已清空“{command['name']}”的录音")

    def begin(self, mode, command=None):
        if self.session or not self.save_settings():
            return
        if self.device_combo.current() < 0:
            self.show_error("没有选定输入设备。")
            return
        if mode in ("match", "listen") and not any(
                self.store.count_samples(item["id"]) for item in self.config_data["commands"]):
            self.show_error("尚无录音样本：请先选择命令并录制样本。")
            return
        winsound.PlaySound(None, 0)
        self.pending_mode = None
        self.session_error = False
        self.record_deadline = None
        self.clipped_until = 0
        self.last_driver_status = ""
        self.clear_error()
        self.waveform.update_wave(None)
        self.health.configure(text="● 正在打开", foreground="#95660a")
        self.status.set("正在打开设备")
        self.session = AudioSession(self.config_data, self.store, mode, command)
        self.set_busy(True)
        self.session.start()

    def stop(self):
        self.pending_mode = None
        if self.session:
            self.session.stop()
            self.status.set("正在停止")
            self.stop_button.configure(state="disabled")

    def finish_recording(self):
        self.session.finish_recording()
        self.finish_record_button.configure(state="disabled")
        self.status.set("正在保存录音")

    def set_busy(self, busy):
        self.finish_record_button.grid_remove()
        for widget in self.mode_buttons + self.edit_buttons + self.sample_buttons + self.settings_entries + [
                self.refresh_button, self.save_button]:
            widget.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.device_combo.configure(state="readonly")
        if busy:
            self.match_button.grid_remove()
            self.listen_button.grid_remove()
            self.stop_button.configure(text="取消录制" if self.session.mode == "record" else "停止")
            self.stop_button.grid(column=1 if self.session.mode == "record" else 0,
                                  columnspan=1 if self.session.mode == "record" else 2)
            if self.session.mode == "record":
                self.finish_record_button.configure(state="normal")
                self.finish_record_button.grid()
        else:
            self.stop_button.grid_remove()
            self.match_button.grid()
            self.listen_button.grid()
        self.update_controls()

    def draw_health(self):
        state = self.session.listener.snapshot()
        now = time.monotonic()
        if not state["last_frame"]:
            return
        age = now - state["last_frame"]
        if state["driver_status"] and state["driver_status"] != self.last_driver_status:
            self.last_driver_status = state["driver_status"]
            self.log(f"音频驱动状态：{state['driver_status']}")
        rms, peak = state["rms"], state["peak"]
        db = 20 * math.log10(max(rms, 1e-9))
        peak_db = 20 * math.log10(max(peak, 1e-9))
        self.level.set(f"RMS {rms:.4f} / {db:.1f} dBFS    峰值 {peak_db:.1f} dBFS")
        self.meter["value"] = min(60, max(0, db + 60)) if age < 1 else 0
        self.stream_info.set(
            f"16 kHz · 单声道 · 最近帧 {int(age * 1000)} ms · "
            f"溢出 {state['overflows']} · 丢块 {state['dropped']}")
        if peak >= 0.99:
            self.clipped_until = now + 1
        if age > 1:
            label, color = "断流", "#b02632"
        elif now < self.clipped_until:
            label, color = "音量过大", "#b02632"
        elif rms < 0.0001:
            label, color = "静音", "#95660a"
        elif rms < self.session.config["speech_start_rms"]:
            label, color = "音量偏低", "#95660a"
        else:
            label, color = "有输入", "#137b78"
        self.health.configure(text=f"● {label}", foreground=color)
        self.waveform.envelope = state["wave"]
        if self.tabs.select() == str(self.debug_tab):
            self.waveform.redraw()

    def tick(self):
        try:
            while True:
                self.toggle_events.get_nowait()
                # Modal editing/capture must not start microphone work.
                if not self.closing and self.grab_current() is None:
                    if self.session:
                        self.stop()
                    else:
                        self.begin("listen")
        except queue.Empty:
            pass
        if self.session:
            self.draw_health()
            closed = False
            try:
                while True:
                    kind, value = self.session.events.get_nowait()
                    if kind == "loading":
                        self.status.set(value)
                        self.log(value)
                    elif kind == "ready":
                        self.status.set(MODE_NAMES[self.session.mode])
                        self.device_info.set(f"当前采集：{value} · 16000 Hz · 单声道")
                        self.log(f"===== 开始工作 · {MODE_NAMES[self.session.mode]} =====")
                        self.log(f"设备：{value}")
                        if self.session.mode == "record":
                            self.record_deadline = time.monotonic() + 12
                    elif kind == "recorded":
                        self.log(f"样本已保存：{value[0]}，{value[1]:.2f} 秒")
                        self.populate_commands()
                    elif kind == "matching":
                        self.status.set("正在匹配录音样本")
                    elif kind == "result":
                        self.last_scores = value["scores"]
                        for cid, score in self.last_scores.items():
                            self.commands.set(cid, "score", f"{score:.3f}")
                            self.score_table.set(cid, "score", f"{score:.3f}")
                        outcome = value["execution"] if value["accepted"] else "未达到阈值，未发送"
                        text = (f"{value['command']['name']} · 距离 {value['score']:.3f} · "
                                f"{value['elapsed_ms']:.0f} ms · {outcome}")
                        name = value["command"]["name"]
                        short_name = name if len(name) <= 18 else name[:17] + "…"
                        short_outcome = "发送失败 · 查看调试" if outcome.startswith("发送失败") else outcome
                        self.result_text.set(f"{short_name} · {short_outcome}")
                        self.log(text)
                        self.status.set(MODE_NAMES[self.session.mode])
                    elif kind == "error":
                        self.session_error = True
                        self.show_error(value)
                    elif kind == "log":
                        self.log(value)
                    elif kind == "closed":
                        closed = True
            except queue.Empty:
                pass
            if self.record_deadline and not closed:
                remaining = max(0, self.record_deadline - time.monotonic())
                self.status.set(f"录制中 · {remaining:.0f} 秒")
            if closed:
                # The closed event is sent only after the microphone is released.
                ending = "异常结束" if self.session_error else "设备已释放"
                self.log(f"===== 结束工作 · {MODE_NAMES[self.session.mode]} · {ending} =====")
                self.session = None
                self.record_deadline = None
                self.set_busy(False)
                self.meter["value"] = 0
                self.level.set("RMS — dBFS    峰值 — dBFS")
                self.stream_info.set("设备已释放 · 波形为最后采集片段")
                self.health.configure(text="● 设备故障" if self.session_error else "● 未打开",
                                      foreground="#b02632" if self.session_error else "#59646b")
                if self.session_error:
                    self.status.set("设备故障")
                if self.pending_mode and not self.closing:
                    mode = self.pending_mode
                    self.pending_mode = None
                    self.begin(mode)
        if self.closing and self.session is None:
            self.destroy()
            return
        self.after(80, self.tick)

    def close(self):
        self.apply_toggle_hotkey("")
        winsound.PlaySound(None, 0)
        self.closing = True
        self.stop()
        if self.session is None:
            self.destroy()
        else:
            self.status.set("正在释放设备并关闭")


def main(device_override=None):
    try:
        app = VoiceHotkeyApp(initialize_user_data(), device_override)
    except Exception as exc:
        messagebox.showerror("无法打开语音快捷键", str(exc))
        return
    app.mainloop()


if __name__ == "__main__":
    main()
