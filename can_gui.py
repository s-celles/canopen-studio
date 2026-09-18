"""
CAN & CANopen Studio - Universal Protocol Analyzer & Transmit Station.
A general-purpose tool for studying, reverse-engineering, and transmitting on CAN and CANopen buses.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles

Features:
- Multi-interface hardware support: SLCAN (Lawicel/USBtin/CANable), PEAK PCAN, Kvaser, Vector, IXXAT, gs_usb, SocketCAN, and Virtual Simulator
- Extensible application decoder profiles: Generic CANopen (CiA 301/402), SEVCON Gen4, De Haardt, J1939, or Raw CAN
- Real-time Reverse Engineering Plotter (Time-series graphs for any CAN ID or Decoded Signal)
- Multi-byte payload plotting (B0 to B7 simultaneously or 16-bit / 32-bit words)
- Universal Network Node Monitor & Live Dashboard
- Real-time Trace with filtering and CSV export
- Full Generic CAN & CANopen Transmission Console:
    - Generic NMT Master (Start/Stop/Pre-Op/Reset for any Node 0..127)
    - Periodic SYNC Clock and Heartbeat Generators
    - Arbitrary Frame Transmitter (Standard 11-bit & Extended 29-bit, RTR, single-shot & periodic)
    - Pre-set Frame Templates Library for quick experimentation
- Universal SDO Object Dictionary Reader & Writer (Expedited Read/Write for any Node ID)
- Hardware & Bus Diagnostics
- Built-in Virtual Simulator for hardware-free educational study
- Interactive CANopen Educational Reference Guide
"""

import os
import time
import threading
import csv
import collections
import webbrowser
from typing import Optional, Dict, Any
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import can
from updater import (
    CURRENT_VERSION,
    GITHUB_REPO,
    check_for_updates,
    is_git_repo,
    perform_git_update,
    download_file,
    launch_installer_and_exit,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from canopen_stack import (
    CANopenLayer,
    NmtState,
    get_default_registry,
)
from can_interfaces import (
    SUPPORTED_INTERFACES,
    STANDARD_BITRATES,
    list_com_ports,
    find_canusb_port,
    open_can_bus,
    VirtualCanopenSimulator,
)

# Color palette for 8 payload bytes (Reverse engineering multi-trace)
BYTE_COLORS = [
    "#00bfff",  # Byte 0: Deep Sky Blue
    "#32cd32",  # Byte 1: Lime Green
    "#ffd700",  # Byte 2: Gold
    "#ff4500",  # Byte 3: Orange Red
    "#ba55d3",  # Byte 4: Medium Orchid
    "#00ffff",  # Byte 5: Aqua / Cyan
    "#ff69b4",  # Byte 6: Hot Pink
    "#ffffff",  # Byte 7: White
]

# Standard CAN & CANopen Frame Templates for transmission experimentation
FRAME_TEMPLATES = {
    "CANopen NMT Start (Node 1)": {
        "id": "0x000",
        "ext": False,
        "rtr": False,
        "data": "01 01",
        "desc": "Start Node 1 -> Operational",
    },
    "CANopen NMT Broadcast Start (All Nodes)": {
        "id": "0x000",
        "ext": False,
        "rtr": False,
        "data": "01 00",
        "desc": "Start all bus nodes",
    },
    "CANopen NMT Pre-Op (Node 1)": {
        "id": "0x000",
        "ext": False,
        "rtr": False,
        "data": "80 01",
        "desc": "Switch Node 1 to Pre-Operational",
    },
    "CANopen NMT Stop (Node 1)": {
        "id": "0x000",
        "ext": False,
        "rtr": False,
        "data": "02 01",
        "desc": "Stop Node 1 communication",
    },
    "CANopen NMT Reset Node (Node 1)": {
        "id": "0x000",
        "ext": False,
        "rtr": False,
        "data": "81 01",
        "desc": "Reset Node 1",
    },
    "CANopen SYNC Clock": {
        "id": "0x080",
        "ext": False,
        "rtr": False,
        "data": "",
        "desc": "CiA 301 Synchronisation pulse",
    },
    "CiA 402 Drive - Ready to Switch On": {
        "id": "0x201",
        "ext": False,
        "rtr": False,
        "data": "06 00",
        "desc": "RPDO1 Controlword 0x0006",
    },
    "CiA 402 Drive - Switch On": {
        "id": "0x201",
        "ext": False,
        "rtr": False,
        "data": "07 00",
        "desc": "RPDO1 Controlword 0x0007",
    },
    "CiA 402 Drive - Enable Operation": {
        "id": "0x201",
        "ext": False,
        "rtr": False,
        "data": "0F 00",
        "desc": "RPDO1 Controlword 0x000F",
    },
    "CiA 402 Drive - Target Velocity (1000 RPM)": {
        "id": "0x301",
        "ext": False,
        "rtr": False,
        "data": "0F 00 E8 03 00 00",
        "desc": "RPDO2 Velocity 1000",
    },
    "SEVCON Gen4 - Throttle Request": {
        "id": "0x270",
        "ext": False,
        "rtr": False,
        "data": "03 04 00 00 00 64 00",
        "desc": "RPDO1 Mode 3, Torque 100",
    },
    "J1939 - Address Claim Request": {
        "id": "0x18EAFFFE",
        "ext": True,
        "rtr": False,
        "data": "00 EE 00",
        "desc": "Request Address Claimed PGN",
    },
    "Raw CAN Ping": {"id": "0x123", "ext": False, "rtr": False, "data": "AA 55 01 02", "desc": "Arbitrary test frame"},
}


class CanStudioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CAN & CANopen Studio - Universal Protocol Analyzer & Transmit Station")
        self.geometry("1150x800")
        self.minsize(960, 680)

        # Application Icon
        base_dir = os.path.dirname(os.path.abspath(__file__))
        ico_path = os.path.join(base_dir, "assets", "icon.ico")
        png_path = os.path.join(base_dir, "assets", "icon.png")
        if os.path.exists(ico_path):
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass
        elif os.path.exists(png_path):
            try:
                icon_img = tk.PhotoImage(file=png_path)
                self.iconphoto(True, icon_img)
            except Exception:
                pass

        # Bus & Layer State
        self.bus: Optional[can.Bus] = None
        self.canopen_layer: Optional[CANopenLayer] = None
        self.simulator: Optional[VirtualCanopenSimulator] = None
        self.running = False
        self.rx_thread: Optional[threading.Thread] = None

        # Periodic Transmission Timers
        self.tx_periodic_timer = None
        self.sync_generator_timer = None
        self.heartbeat_generator_timer = None

        # Statistics & Node Discovery
        self.stats = {
            "total_rx": 0,
            "total_tx": 0,
            "rx_rate": 0,
            "last_calc_time": time.time(),
            "rx_count_interval": 0,
        }
        self.discovered_nodes: Dict[int, Dict[str, Any]] = {}

        # Captured Messages & Telemetry
        self.captured_messages = []
        self.trace_paused = False
        self.telemetry_data: Dict[str, Any] = {
            "speed": 0,
            "max_speed": 0,
            "torque": 0,
            "temp1": 0,
            "temp2": 0,
            "last_active_node": 1,
        }

        # Plotter State & Ring Buffers
        self.plot_paused = False
        self.plot_history = collections.defaultdict(lambda: collections.deque(maxlen=1500))
        self.plot_times = collections.defaultdict(lambda: collections.deque(maxlen=1500))
        self.known_can_ids = set()
        self.known_signals = set()
        self.last_plot_draw = 0

        self._create_widgets()
        self._init_interface_selection()

        # Non-blocking background update check
        threading.Thread(target=self._background_update_check, daemon=True).start()

    def _create_widgets(self):
        # 0. Menu Bar
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Export Trace to CSV...", command=self._export_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)

        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Send CANopen SYNC", command=self._send_sync)
        tools_menu.add_command(label="Send NMT Broadcast Start", command=lambda: self._send_nmt(0x01, 0))
        tools_menu.add_command(label="Send NMT Broadcast Reset", command=lambda: self._send_nmt(0x81, 0))

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Check for Updates...", command=lambda: self._check_updates_dialog(manual=True))
        help_menu.add_separator()
        help_menu.add_command(label="About...", command=self._show_about)

        # 1. Universal Hardware Connection Toolbar
        top_bar = ttk.LabelFrame(self, text=" Hardware Connection & Device Profile ", padding=8)
        top_bar.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(top_bar, text="Interface:").pack(side=tk.LEFT, padx=3)
        self.iface_combo = ttk.Combobox(
            top_bar,
            values=[cfg["name"] for cfg in SUPPORTED_INTERFACES.values()],
            state="readonly",
            width=28,
        )
        self.iface_combo.pack(side=tk.LEFT, padx=3)
        self.iface_combo.bind("<<ComboboxSelected>>", self._on_interface_changed)

        ttk.Label(top_bar, text="Channel:").pack(side=tk.LEFT, padx=(8, 3))
        self.channel_combo = ttk.Combobox(top_bar, width=14)
        self.channel_combo.pack(side=tk.LEFT, padx=3)

        self.btn_refresh = ttk.Button(top_bar, text="↻", width=3, command=self._refresh_channels)
        self.btn_refresh.pack(side=tk.LEFT, padx=2)

        ttk.Label(top_bar, text="Bitrate:").pack(side=tk.LEFT, padx=(8, 3))
        self.bitrate_combo = ttk.Combobox(top_bar, values=[str(b) for b in STANDARD_BITRATES], width=9)
        self.bitrate_combo.set("500000")
        self.bitrate_combo.pack(side=tk.LEFT, padx=3)

        ttk.Label(top_bar, text="Profile:").pack(side=tk.LEFT, padx=(8, 3))
        self.profile_combo = ttk.Combobox(
            top_bar,
            values=[
                "All / Auto",
                "CiA 402 Generic Drive",
                "SEVCON Gen4 Inverter",
                "De Haardt Safety Transponder",
                "J1939 Extended (29-bit)",
                "Raw CAN (No Decoders)",
            ],
            state="readonly",
            width=18,
        )
        self.profile_combo.set("All / Auto")
        self.profile_combo.pack(side=tk.LEFT, padx=3)
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_changed)

        self.btn_connect = ttk.Button(top_bar, text="Connect", command=self._toggle_connection)
        self.btn_connect.pack(side=tk.LEFT, padx=10)

        btn_about = ttk.Button(top_bar, text="ℹ About", width=7, command=self._show_about)
        btn_about.pack(side=tk.RIGHT, padx=4)

        self.status_lbl = ttk.Label(top_bar, text="Status: Disconnected", foreground="red")
        self.status_lbl.pack(side=tk.RIGHT, padx=8)

        # 2. Main Notebook (Tabs)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Tab 1: Network Monitor & Dashboard
        self.tab_dash = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_dash, text=" 🌐 Network & Dashboard ")
        self._build_dashboard_tab()

        # Tab 2: Reverse Engineering Plotter
        self.tab_plot = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.tab_plot, text=" 📈 Reverse Plotter ")
        self._build_plot_tab()

        # Tab 3: Real-time Trace
        self.tab_trace = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_trace, text=" 📋 Real-time Trace ")
        self._build_trace_tab()

        # Tab 4: Transmit & Control Station
        self.tab_tx = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_tx, text=" 🚀 Transmit & Control ")
        self._build_tx_tab()

        # Tab 5: SDO Object Dictionary Explorer
        self.tab_sdo = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_sdo, text=" 📖 SDO Object Dictionary ")
        self._build_sdo_tab()

        # Tab 6: Hardware & Bus Diagnostics
        self.tab_hw = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_hw, text=" 🔧 Hardware Diagnostics ")
        self._build_hardware_tab()

        # Tab 7: Educational Reference Guide
        self.tab_ref = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_ref, text=" 📚 CANopen Reference ")
        self._build_reference_tab()

        # Bottom Status Bar with Version & Update Alert
        self.status_bar = ttk.Frame(self, relief=tk.SUNKEN, padding=(6, 3))
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_version_lbl = ttk.Label(
            self.status_bar,
            text=f"CAN & CANopen Studio v{CURRENT_VERSION}",
            font=("Segoe UI", 9),
        )
        self.status_version_lbl.pack(side=tk.LEFT, padx=5)

        self.status_update_btn = ttk.Button(
            self.status_bar,
            text="✨ Update Available!",
            command=lambda: self._check_updates_dialog(manual=True),
        )

    def _init_interface_selection(self):
        """Set default interface to SLCAN (or auto-detect CANUSB on COM port)."""
        self.iface_combo.set(SUPPORTED_INTERFACES["slcan"]["name"])
        self._refresh_channels()
        auto_port = find_canusb_port()
        if auto_port:
            self.channel_combo.set(auto_port)

    def _get_selected_iface_key(self) -> str:
        sel_name = self.iface_combo.get()
        for k, v in SUPPORTED_INTERFACES.items():
            if v["name"] == sel_name:
                return k
        return "slcan"

    def _on_interface_changed(self, event=None):
        self._refresh_channels()

    def _refresh_channels(self):
        iface_key = self._get_selected_iface_key()
        cfg = SUPPORTED_INTERFACES[iface_key]

        if cfg["has_ports"]:
            ports = list_com_ports()
            self.channel_combo["values"] = ports or cfg["default_channels"]
            auto_port = find_canusb_port()
            if auto_port:
                self.channel_combo.set(auto_port)
            elif ports:
                self.channel_combo.set(ports[0])
            elif cfg["default_channels"]:
                self.channel_combo.set(cfg["default_channels"][0])
        else:
            self.channel_combo["values"] = cfg["default_channels"]
            if cfg["default_channels"]:
                self.channel_combo.set(cfg["default_channels"][0])

    def _on_profile_changed(self, event=None):
        prof = self.profile_combo.get()
        reg = get_default_registry()
        reg.active_profile = prof
        if self.canopen_layer:
            self.canopen_layer._rebuild_custom_cob_map()

    # =========================================================================
    # TAB 1: Network Monitor & Live Dashboard
    # =========================================================================
    def _build_dashboard_tab(self):
        # Top Stats Bar
        stat_frame = ttk.LabelFrame(self.tab_dash, text=" Bus Statistics ", padding=8)
        stat_frame.pack(fill=tk.X, pady=(0, 8))

        self.lbl_rate = ttk.Label(
            stat_frame, text="Bus Rate: 0 msgs/s", font=("Segoe UI", 10, "bold"), foreground="#007acc"
        )
        self.lbl_rate.pack(side=tk.LEFT, padx=15)

        self.lbl_total_rx = ttk.Label(stat_frame, text="Total Received: 0", font=("Segoe UI", 10))
        self.lbl_total_rx.pack(side=tk.LEFT, padx=15)

        self.lbl_total_tx = ttk.Label(stat_frame, text="Total Transmitted: 0", font=("Segoe UI", 10))
        self.lbl_total_tx.pack(side=tk.LEFT, padx=15)

        self.lbl_nodes_cnt = ttk.Label(
            stat_frame, text="Active Nodes: 0", font=("Segoe UI", 10, "bold"), foreground="green"
        )
        self.lbl_nodes_cnt.pack(side=tk.RIGHT, padx=15)

        # Split: Left = Discovered CANopen Nodes, Right = Telemetry Gauges
        main_paned = ttk.PanedWindow(self.tab_dash, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # Left panel: Discovered Nodes Treeview
        left_box = ttk.LabelFrame(main_paned, text=" Discovered CANopen Network Nodes (CiA 301) ", padding=8)
        main_paned.add(left_box, weight=1)

        node_cols = ("node", "state", "last_seen", "services")
        self.node_tree = ttk.Treeview(left_box, columns=node_cols, show="headings", height=8)
        self.node_tree.heading("node", text="Node ID")
        self.node_tree.heading("state", text="NMT State")
        self.node_tree.heading("last_seen", text="Last Seen")
        self.node_tree.heading("services", text="Active Services")

        self.node_tree.column("node", width=70, anchor="center")
        self.node_tree.column("state", width=110, anchor="center")
        self.node_tree.column("last_seen", width=80, anchor="center")
        self.node_tree.column("services", width=160)
        self.node_tree.pack(fill=tk.BOTH, expand=True)

        # Right panel: Telemetry Gauges
        right_box = ttk.LabelFrame(main_paned, text=" Dynamic Telemetry (CiA 402 / SEVCON) ", padding=10)
        main_paned.add(right_box, weight=1)

        grid = ttk.Frame(right_box)
        grid.pack(fill=tk.BOTH, expand=True)

        # Motor / Velocity
        g1 = ttk.LabelFrame(grid, text=" Motor Speed (0x606C) ", padding=10)
        g1.grid(row=0, column=0, padx=8, pady=6, sticky="nsew")
        self.val_speed = ttk.Label(g1, text="0 RPM", font=("Consolas", 26, "bold"), foreground="#007acc")
        self.val_speed.pack(pady=4)
        self.val_max_speed = ttk.Label(g1, text="Max: 0 RPM", font=("Segoe UI", 9))
        self.val_max_speed.pack()

        # Target Torque / Status
        g2 = ttk.LabelFrame(grid, text=" Target Torque / Status ", padding=10)
        g2.grid(row=0, column=1, padx=8, pady=6, sticky="nsew")
        self.val_torque = ttk.Label(g2, text="0", font=("Consolas", 26, "bold"), foreground="#d9534f")
        self.val_torque.pack(pady=4)
        self.val_status_str = ttk.Label(g2, text="State: -", font=("Segoe UI", 9))
        self.val_status_str.pack()

        # Heatsink / Primary Temp
        g3 = ttk.LabelFrame(grid, text=" Inverter / Heatsink Temp ", padding=10)
        g3.grid(row=1, column=0, padx=8, pady=6, sticky="nsew")
        self.val_temp1 = ttk.Label(g3, text="0 °C", font=("Consolas", 24, "bold"), foreground="#f0ad4e")
        self.val_temp1.pack(pady=4)

        # Motor / Secondary Temp
        g4 = ttk.LabelFrame(grid, text=" Motor Temperature ", padding=10)
        g4.grid(row=1, column=1, padx=8, pady=6, sticky="nsew")
        self.val_temp2 = ttk.Label(g4, text="0", font=("Consolas", 24, "bold"), foreground="#5cb85c")
        self.val_temp2.pack(pady=4)

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

    # =========================================================================
    # TAB 2: Reverse Engineering Plotter
    # =========================================================================
    def _build_plot_tab(self):
        tb = ttk.Frame(self.tab_plot)
        tb.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(tb, text="Target Mode:").pack(side=tk.LEFT, padx=3)
        self.plot_mode_combo = ttk.Combobox(
            tb,
            values=["CAN ID (Raw Bytes)", "Decoded Signal"],
            state="readonly",
            width=18,
        )
        self.plot_mode_combo.set("CAN ID (Raw Bytes)")
        self.plot_mode_combo.pack(side=tk.LEFT, padx=3)
        self.plot_mode_combo.bind("<<ComboboxSelected>>", self._on_plot_mode_changed)

        ttk.Label(tb, text="Target ID / Signal:").pack(side=tk.LEFT, padx=(6, 3))
        self.plot_target_combo = ttk.Combobox(tb, width=22, postcommand=self._refresh_plot_targets)
        self.plot_target_combo.set("0x473")
        self.plot_target_combo.pack(side=tk.LEFT, padx=3)

        self.lbl_format = ttk.Label(tb, text="Format:")
        self.lbl_format.pack(side=tk.LEFT, padx=(6, 3))
        self.plot_format_combo = ttk.Combobox(
            tb,
            values=[
                "All Bytes (B0..B7)",
                "Byte 0",
                "Byte 1",
                "Byte 2",
                "Byte 3",
                "Byte 4",
                "Byte 5",
                "Byte 6",
                "Byte 7",
                "Word 0..1 (int16 LE)",
                "Word 2..3 (int16 LE)",
                "Word 4..5 (int16 LE)",
                "Word 6..7 (int16 LE)",
                "DWord 0..3 (int32 LE)",
                "DWord 4..7 (int32 LE)",
            ],
            state="readonly",
            width=20,
        )
        self.plot_format_combo.set("All Bytes (B0..B7)")
        self.plot_format_combo.pack(side=tk.LEFT, padx=3)

        ttk.Label(tb, text="Window:").pack(side=tk.LEFT, padx=(6, 3))
        self.plot_win_spin = ttk.Spinbox(tb, from_=5, to=120, width=5)
        self.plot_win_spin.set("15")
        self.plot_win_spin.pack(side=tk.LEFT, padx=2)
        ttk.Label(tb, text="s").pack(side=tk.LEFT, padx=(0, 6))

        self.btn_plot_pause = ttk.Button(tb, text="Pause Plot", command=self._toggle_plot_pause)
        self.btn_plot_pause.pack(side=tk.LEFT, padx=4)

        btn_plot_clear = ttk.Button(tb, text="Clear", command=self._clear_plot)
        btn_plot_clear.pack(side=tk.LEFT, padx=4)

        # Embedded Matplotlib Figure
        self.fig = Figure(figsize=(8, 4.5), dpi=100)
        self.fig.patch.set_facecolor("#222222")
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#161616")
        self.ax.grid(True, color="#3a3a3a", linestyle="--", alpha=0.7)
        self.ax.tick_params(colors="#cccccc")
        self.ax.set_xlabel("Elapsed Time (s)", color="#cccccc")
        self.ax.set_ylabel("Value", color="#cccccc")
        for spine in self.ax.spines.values():
            spine.set_color("#444444")

        self.plot_canvas = FigureCanvasTkAgg(self.fig, master=self.tab_plot)
        self.plot_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        toolbar_frame = ttk.Frame(self.tab_plot)
        toolbar_frame.pack(fill=tk.X)
        self.plot_toolbar = NavigationToolbar2Tk(self.plot_canvas, toolbar_frame)
        self.plot_toolbar.update()

    def _refresh_plot_targets(self):
        mode = self.plot_mode_combo.get()
        cur = self.plot_target_combo.get().strip()
        if "Signal" in mode:
            signals_list = sorted(list(self.known_signals)) or [
                "actual_speed_rpm",
                "velocity_actual",
                "target_torque",
                "heatsink_temp_c",
                "motor_temp_raw",
            ]
            self.plot_target_combo["values"] = signals_list
            if not cur and signals_list:
                self.plot_target_combo.set(signals_list[0])
        else:
            ids_list = sorted(list(self.known_can_ids)) or [
                "0x473",
                "0x181",
                "0x281",
                "0x148",
                "0x156",
                "0x270",
                "0x701",
            ]
            self.plot_target_combo["values"] = ids_list
            if not cur and ids_list:
                self.plot_target_combo.set(ids_list[0])

    def _on_plot_mode_changed(self, event=None):
        mode = self.plot_mode_combo.get()
        if "Signal" in mode:
            self.lbl_format.pack_forget()
            self.plot_format_combo.pack_forget()
        else:
            self.lbl_format.pack(side=tk.LEFT, padx=(6, 3))
            self.plot_format_combo.pack(side=tk.LEFT, padx=3)
        self._refresh_plot_targets()

    def _toggle_plot_pause(self):
        self.plot_paused = not self.plot_paused
        self.btn_plot_pause.configure(text="Resume Plot" if self.plot_paused else "Pause Plot")

    def _clear_plot(self):
        self.plot_history.clear()
        self.plot_times.clear()
        self.ax.cla()
        self.ax.grid(True, color="#3a3a3a", linestyle="--", alpha=0.7)
        self.ax.tick_params(colors="#cccccc")
        for spine in self.ax.spines.values():
            spine.set_color("#444444")
        self.plot_canvas.draw_idle()

    # =========================================================================
    # TAB 3: Real-time Trace
    # =========================================================================
    def _build_trace_tab(self):
        tb = ttk.Frame(self.tab_trace)
        tb.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(tb, text="Filter ID:").pack(side=tk.LEFT, padx=3)
        self.filter_entry = ttk.Entry(tb, width=10)
        self.filter_entry.pack(side=tk.LEFT, padx=3)

        self.filter_ext_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tb, text="Extended Only", variable=self.filter_ext_var).pack(side=tk.LEFT, padx=6)

        self.btn_pause_trace = ttk.Button(tb, text="Pause Trace", command=self._toggle_trace_pause)
        self.btn_pause_trace.pack(side=tk.LEFT, padx=6)

        btn_clear = ttk.Button(tb, text="Clear", command=self._clear_trace)
        btn_clear.pack(side=tk.LEFT, padx=3)

        btn_export = ttk.Button(tb, text="Export CSV...", command=self._export_csv)
        btn_export.pack(side=tk.RIGHT, padx=3)

        cols = ("time", "type", "id", "dlc", "data", "info")
        self.tree = ttk.Treeview(self.tab_trace, columns=cols, show="headings", height=18)
        self.tree.heading("time", text="Time (s)")
        self.tree.heading("type", text="Type")
        self.tree.heading("id", text="CAN ID (Hex)")
        self.tree.heading("dlc", text="DLC")
        self.tree.heading("data", text="Data Bytes (Hex)")
        self.tree.heading("info", text="Decoded CANopen Service / Signal Description")

        self.tree.column("time", width=90, anchor="e")
        self.tree.column("type", width=55, anchor="center")
        self.tree.column("id", width=95, anchor="center")
        self.tree.column("dlc", width=45, anchor="center")
        self.tree.column("data", width=190)
        self.tree.column("info", width=440)

        scroll = ttk.Scrollbar(self.tab_trace, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _toggle_trace_pause(self):
        self.trace_paused = not self.trace_paused
        self.btn_pause_trace.configure(text="Resume Trace" if self.trace_paused else "Pause Trace")

    def _clear_trace(self):
        self.tree.delete(*self.tree.get_children())
        self.captured_messages.clear()

    def _export_csv(self):
        if not self.tree.get_children():
            messagebox.showinfo("Export", "No frames to export.")
            return

        filename = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not filename:
            return

        with open(filename, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Timestamp", "Type", "CAN_ID", "DLC", "Data_Hex", "Decode_Info"])
            for row in self.tree.get_children():
                w.writerow(self.tree.item(row)["values"])

        messagebox.showinfo("Export", f"Exported successfully to:\n{filename}")

    # =========================================================================
    # TAB 4: Transmit & Control Station
    # =========================================================================
    def _build_tx_tab(self):
        # 1. Generic CANopen NMT Master & Periodic Producers
        box_nmt = ttk.LabelFrame(self.tab_tx, text=" Generic CANopen NMT Master (CiA 301) ", padding=10)
        box_nmt.pack(fill=tk.X, pady=(0, 6))

        r1 = ttk.Frame(box_nmt)
        r1.pack(fill=tk.X, pady=4)

        ttk.Label(r1, text="Target Node ID:").pack(side=tk.LEFT, padx=3)
        self.nmt_target_spin = ttk.Spinbox(r1, from_=0, to=127, width=4)
        self.nmt_target_spin.set("0")
        self.nmt_target_spin.pack(side=tk.LEFT, padx=3)
        ttk.Label(r1, text="(0 = Broadcast to All Nodes)", font=("Segoe UI", 9, "italic"), foreground="#555555").pack(
            side=tk.LEFT, padx=4
        )

        btn_start = ttk.Button(r1, text="▶ Start Node (Operational)", command=lambda: self._send_nmt_from_ui(0x01))
        btn_start.pack(side=tk.LEFT, padx=6)

        btn_preop = ttk.Button(r1, text="⏸ Pre-Operational", command=lambda: self._send_nmt_from_ui(0x80))
        btn_preop.pack(side=tk.LEFT, padx=4)

        btn_stop = ttk.Button(r1, text="⏹ Stop Node", command=lambda: self._send_nmt_from_ui(0x02))
        btn_stop.pack(side=tk.LEFT, padx=4)

        btn_reset = ttk.Button(r1, text="⟲ Reset Node", command=lambda: self._send_nmt_from_ui(0x81))
        btn_reset.pack(side=tk.LEFT, padx=4)

        btn_reset_comm = ttk.Button(r1, text="⟲ Reset Comm", command=lambda: self._send_nmt_from_ui(0x82))
        btn_reset_comm.pack(side=tk.LEFT, padx=4)

        # Periodic SYNC & Heartbeat producers
        r2 = ttk.Frame(box_nmt)
        r2.pack(fill=tk.X, pady=(6, 2))

        ttk.Button(r2, text="⚡ Send SYNC Once (0x080)", command=self._send_sync).pack(side=tk.LEFT, padx=4)

        self.btn_periodic_sync = ttk.Button(r2, text="Start Periodic SYNC (50 Hz)", command=self._toggle_periodic_sync)
        self.btn_periodic_sync.pack(side=tk.LEFT, padx=6)

        ttk.Label(r2, text="| Producer Node:").pack(side=tk.LEFT, padx=(10, 3))
        self.hb_node_spin = ttk.Spinbox(r2, from_=1, to=127, width=4)
        self.hb_node_spin.set("127")
        self.hb_node_spin.pack(side=tk.LEFT, padx=2)

        self.btn_periodic_hb = ttk.Button(r2, text="Start Heartbeat (1 Hz)", command=self._toggle_periodic_hb)
        self.btn_periodic_hb.pack(side=tk.LEFT, padx=6)

        # 2. Pre-set Frame Templates Library
        box_tpl = ttk.LabelFrame(self.tab_tx, text=" Quick Frame Templates & Presets ", padding=10)
        box_tpl.pack(fill=tk.X, pady=6)

        ttk.Label(box_tpl, text="Select Template:").pack(side=tk.LEFT, padx=4)
        self.template_combo = ttk.Combobox(box_tpl, values=list(FRAME_TEMPLATES.keys()), state="readonly", width=38)
        self.template_combo.set(list(FRAME_TEMPLATES.keys())[0])
        self.template_combo.pack(side=tk.LEFT, padx=4)

        btn_load_tpl = ttk.Button(box_tpl, text="Load into Transmitter", command=self._load_template)
        btn_load_tpl.pack(side=tk.LEFT, padx=8)

        self.lbl_tpl_desc = ttk.Label(
            box_tpl,
            text="Description: " + FRAME_TEMPLATES[list(FRAME_TEMPLATES.keys())[0]]["desc"],
            font=("Segoe UI", 9, "italic"),
            foreground="#007acc",
        )
        self.lbl_tpl_desc.pack(side=tk.LEFT, padx=10)
        self.template_combo.bind("<<ComboboxSelected>>", self._on_template_selected)

        # 3. Arbitrary Frame Transmitter
        box_raw = ttk.LabelFrame(
            self.tab_tx, text=" Arbitrary Frame Transmitter (Standard 11-bit & Extended 29-bit) ", padding=12
        )
        box_raw.pack(fill=tk.BOTH, expand=True, pady=6)

        grid = ttk.Frame(box_raw)
        grid.pack(fill=tk.X, pady=6)

        ttk.Label(grid, text="CAN ID (Hex):").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        self.tx_id_entry = ttk.Entry(grid, width=14)
        self.tx_id_entry.insert(0, "0x181")
        self.tx_id_entry.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        self.tx_ext_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(grid, text="Extended (29-bit)", variable=self.tx_ext_var).grid(row=0, column=2, padx=6, pady=4)

        self.tx_rtr_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(grid, text="RTR (Remote Frame)", variable=self.tx_rtr_var).grid(row=0, column=3, padx=6, pady=4)

        ttk.Label(grid, text="Payload (Hex Bytes):").grid(row=1, column=0, padx=4, pady=4, sticky="w")
        self.tx_data_entry = ttk.Entry(grid, width=32)
        self.tx_data_entry.insert(0, "00 00 00 00 00 00 00 00")
        self.tx_data_entry.grid(row=1, column=1, columnspan=3, padx=4, pady=4, sticky="w")

        btn_send = ttk.Button(grid, text="▶ Send Frame Once", command=self._send_custom_frame)
        btn_send.grid(row=2, column=1, padx=4, pady=8, sticky="w")

        # Periodic Box
        per_box = ttk.LabelFrame(box_raw, text=" Periodic Transmission ", padding=8)
        per_box.pack(fill=tk.X, pady=(6, 2))

        ttk.Label(per_box, text="Cycle Time (ms):").pack(side=tk.LEFT, padx=4)
        self.tx_period_entry = ttk.Entry(per_box, width=8)
        self.tx_period_entry.insert(0, "100")
        self.tx_period_entry.pack(side=tk.LEFT, padx=4)

        self.btn_periodic = ttk.Button(per_box, text="Start Periodic Transmission", command=self._toggle_periodic)
        self.btn_periodic.pack(side=tk.LEFT, padx=12)

        self.tx_status_lbl = ttk.Label(per_box, text="Ready", foreground="#555555")
        self.tx_status_lbl.pack(side=tk.LEFT, padx=10)

    def _on_template_selected(self, event=None):
        name = self.template_combo.get()
        if name in FRAME_TEMPLATES:
            self.lbl_tpl_desc.configure(text="Description: " + FRAME_TEMPLATES[name]["desc"])

    def _load_template(self):
        name = self.template_combo.get()
        if name in FRAME_TEMPLATES:
            tpl = FRAME_TEMPLATES[name]
            self.tx_id_entry.delete(0, tk.END)
            self.tx_id_entry.insert(0, tpl["id"])
            self.tx_ext_var.set(tpl["ext"])
            self.tx_rtr_var.set(tpl["rtr"])
            self.tx_data_entry.delete(0, tk.END)
            self.tx_data_entry.insert(0, tpl["data"])

    def _send_nmt_from_ui(self, cmd_val: int):
        try:
            node_id = int(self.nmt_target_spin.get().strip(), 0)
        except Exception:
            node_id = 0
        self._send_nmt(cmd_val, node_id)

    def _send_nmt(self, cmd_val: int, node_id: int):
        if not self.bus:
            messagebox.showwarning("Warning", "Connect to the CAN bus first.")
            return
        msg = can.Message(arbitration_id=0x000, is_extended_id=False, data=[cmd_val, node_id])
        try:
            self.bus.send(msg)
            self.stats["total_tx"] += 1
        except Exception as e:
            messagebox.showerror("NMT Error", f"Failed to send NMT frame:\n{e}")

    def _send_sync(self):
        if not self.bus:
            messagebox.showwarning("Warning", "Connect to the CAN bus first.")
            return
        try:
            self.bus.send(can.Message(arbitration_id=0x080, is_extended_id=False, data=[]))
            self.stats["total_tx"] += 1
        except Exception as e:
            messagebox.showerror("SYNC Error", f"Failed to send SYNC frame:\n{e}")

    def _toggle_periodic_sync(self):
        if self.sync_generator_timer is None:
            self.btn_periodic_sync.configure(text="Stop Periodic SYNC")
            self._sync_tick()
        else:
            self.after_cancel(self.sync_generator_timer)
            self.sync_generator_timer = None
            self.btn_periodic_sync.configure(text="Start Periodic SYNC (50 Hz)")

    def _sync_tick(self):
        self._send_sync()
        self.sync_generator_timer = self.after(20, self._sync_tick)

    def _toggle_periodic_hb(self):
        if self.heartbeat_generator_timer is None:
            self.btn_periodic_hb.configure(text="Stop Heartbeat")
            self._hb_tick()
        else:
            self.after_cancel(self.heartbeat_generator_timer)
            self.heartbeat_generator_timer = None
            self.btn_periodic_hb.configure(text="Start Heartbeat (1 Hz)")

    def _hb_tick(self):
        if self.bus:
            try:
                node = int(self.hb_node_spin.get(), 0)
            except Exception:
                node = 127
            msg = can.Message(arbitration_id=0x700 + node, is_extended_id=False, data=[0x05])  # 0x05 Operational
            try:
                self.bus.send(msg)
                self.stats["total_tx"] += 1
            except Exception:
                pass
        self.heartbeat_generator_timer = self.after(1000, self._hb_tick)

    def _send_custom_frame(self):
        if not self.bus:
            messagebox.showwarning("Warning", "Connect to the CAN bus first.")
            return
        try:
            cid_str = self.tx_id_entry.get().strip()
            cid = int(cid_str, 16 if not cid_str.lower().startswith("0x") else 0)
            is_ext = self.tx_ext_var.get()
            is_rtr = self.tx_rtr_var.get()
            dhex = self.tx_data_entry.get().strip().replace(" ", "")
            data = b"" if is_rtr else (bytes.fromhex(dhex) if dhex else b"")
            msg = can.Message(arbitration_id=cid, is_extended_id=is_ext, is_remote_frame=is_rtr, data=data)
            self.bus.send(msg)
            self.stats["total_tx"] += 1
            self.tx_status_lbl.configure(
                text=f"Frame 0x{cid:X} sent at {time.strftime('%H:%M:%S')}", foreground="green"
            )
        except Exception as e:
            self.tx_status_lbl.configure(text=f"Error: {e}", foreground="red")
            messagebox.showerror("Send Error", f"Failed to send frame:\n{e}")

    def _toggle_periodic(self):
        if self.tx_periodic_timer is None:
            try:
                _ = int(self.tx_period_entry.get())
                self.btn_periodic.configure(text="Stop Periodic Transmission")
                self._run_periodic_tick()
            except Exception as e:
                messagebox.showerror("Error", f"Invalid period: {e}")
        else:
            self.after_cancel(self.tx_periodic_timer)
            self.tx_periodic_timer = None
            self.btn_periodic.configure(text="Start Periodic Transmission")

    def _run_periodic_tick(self):
        self._send_custom_frame()
        try:
            ms = max(10, int(self.tx_period_entry.get()))
        except Exception:
            ms = 100
        self.tx_periodic_timer = self.after(ms, self._run_periodic_tick)

    # =========================================================================
    # TAB 5: SDO Object Dictionary Reader & Writer
    # =========================================================================
    def _build_sdo_tab(self):
        box_sdo = ttk.LabelFrame(
            self.tab_sdo, text=" Universal CANopen SDO Client (CiA 301 Expedited Protocol) ", padding=14
        )
        box_sdo.pack(fill=tk.BOTH, expand=True)

        grid = ttk.Frame(box_sdo)
        grid.pack(fill=tk.X, pady=6)

        ttk.Label(grid, text="Server Node ID:").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        self.sdo_node_spin = ttk.Spinbox(grid, from_=1, to=127, width=5)
        self.sdo_node_spin.set("1")
        self.sdo_node_spin.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(grid, text="Index (Hex, e.g. 0x1000):").grid(row=0, column=2, padx=8, pady=4, sticky="w")
        self.sdo_idx_entry = ttk.Entry(grid, width=10)
        self.sdo_idx_entry.insert(0, "0x1000")
        self.sdo_idx_entry.grid(row=0, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(grid, text="Sub-Index (Hex/Dec):").grid(row=0, column=4, padx=8, pady=4, sticky="w")
        self.sdo_sub_entry = ttk.Entry(grid, width=6)
        self.sdo_sub_entry.insert(0, "0x00")
        self.sdo_sub_entry.grid(row=0, column=5, padx=4, pady=4, sticky="w")

        btn_read = ttk.Button(grid, text="📖 SDO Read (Upload)", command=self._sdo_read)
        btn_read.grid(row=0, column=6, padx=12, pady=4)

        # SDO Write section
        sep = ttk.Separator(box_sdo, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=10)

        wr_grid = ttk.Frame(box_sdo)
        wr_grid.pack(fill=tk.X, pady=4)

        ttk.Label(wr_grid, text="Write Value:").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        self.sdo_wr_val_entry = ttk.Entry(wr_grid, width=16)
        self.sdo_wr_val_entry.insert(0, "0x00")
        self.sdo_wr_val_entry.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(wr_grid, text="Type:").grid(row=0, column=2, padx=8, pady=4, sticky="w")
        self.sdo_wr_type_combo = ttk.Combobox(
            wr_grid,
            values=["uint8 (1 byte)", "uint16 (2 bytes)", "uint32 (4 bytes)", "int16 (2 bytes)", "int32 (4 bytes)"],
            state="readonly",
            width=16,
        )
        self.sdo_wr_type_combo.set("uint16 (2 bytes)")
        self.sdo_wr_type_combo.grid(row=0, column=3, padx=4, pady=4, sticky="w")

        btn_write = ttk.Button(wr_grid, text="✍ SDO Write (Download)", command=self._sdo_write)
        btn_write.grid(row=0, column=4, padx=12, pady=4)

        # SDO Response Output Panel
        resp_box = ttk.LabelFrame(box_sdo, text=" SDO Transaction Result ", padding=10)
        resp_box.pack(fill=tk.X, pady=10)

        self.sdo_resp_lbl = ttk.Label(
            resp_box, text="Ready. Click SDO Read or Write.", font=("Consolas", 11), foreground="#007acc"
        )
        self.sdo_resp_lbl.pack(fill=tk.X, pady=4)

        # Quick Inquiries
        quick_frame = ttk.LabelFrame(box_sdo, text=" Standard CiA 301 & CiA 402 Object Inquiries ", padding=10)
        quick_frame.pack(fill=tk.X, pady=8)

        ttk.Button(quick_frame, text="0x1000:00 Device Type", command=lambda: self._quick_sdo(0x1000, 0)).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(quick_frame, text="0x1001:00 Error Register", command=lambda: self._quick_sdo(0x1001, 0)).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(quick_frame, text="0x1008:00 Device Name", command=lambda: self._quick_sdo(0x1008, 0)).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(quick_frame, text="0x1018:01 Vendor ID", command=lambda: self._quick_sdo(0x1018, 1)).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(quick_frame, text="0x6041:00 Statusword", command=lambda: self._quick_sdo(0x6041, 0)).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(quick_frame, text="0x606C:00 Actual Velocity", command=lambda: self._quick_sdo(0x606C, 0)).pack(
            side=tk.LEFT, padx=3
        )

    def _quick_sdo(self, idx: int, sub: int):
        self.sdo_idx_entry.delete(0, tk.END)
        self.sdo_idx_entry.insert(0, f"0x{idx:04X}")
        self.sdo_sub_entry.delete(0, tk.END)
        self.sdo_sub_entry.insert(0, f"0x{sub:02X}")
        self._sdo_read()

    def _sdo_read(self):
        if not self.canopen_layer:
            messagebox.showwarning("Warning", "Connect to the CAN bus first.")
            return
        try:
            node_id = int(self.sdo_node_spin.get().strip(), 0)
            idx_str = self.sdo_idx_entry.get().strip()
            sub_str = self.sdo_sub_entry.get().strip()
            idx = int(idx_str, 16 if not idx_str.lower().startswith("0x") else 0)
            sub = int(sub_str, 16 if not sub_str.lower().startswith("0x") else 0)

            self.canopen_layer.send_sdo_read(node_id, idx, sub)
            self.stats["total_tx"] += 1
            self.sdo_resp_lbl.configure(
                text=f"Waiting for SDO response from Node {node_id} (0x{0x580 + node_id:03X})...", foreground="#007acc"
            )
            self.after(200, lambda: self._check_sdo_reply(node_id, idx, sub))
        except Exception as e:
            messagebox.showerror("SDO Read Error", f"Invalid parameters:\n{e}")

    def _sdo_write(self):
        if not self.canopen_layer:
            messagebox.showwarning("Warning", "Connect to the CAN bus first.")
            return
        try:
            node_id = int(self.sdo_node_spin.get().strip(), 0)
            idx_str = self.sdo_idx_entry.get().strip()
            sub_str = self.sdo_sub_entry.get().strip()
            idx = int(idx_str, 16 if not idx_str.lower().startswith("0x") else 0)
            sub = int(sub_str, 16 if not sub_str.lower().startswith("0x") else 0)

            val_str = self.sdo_wr_val_entry.get().strip()
            val_int = int(val_str, 16 if not val_str.lower().startswith("0x") else 0)
            type_sel = self.sdo_wr_type_combo.get()

            if "uint8" in type_sel:
                data_bytes = val_int.to_bytes(1, "little")
            elif "uint16" in type_sel or "int16" in type_sel:
                data_bytes = val_int.to_bytes(2, "little", signed=("int16" in type_sel))
            else:
                data_bytes = val_int.to_bytes(4, "little", signed=("int32" in type_sel))

            self.canopen_layer.send_sdo_write(node_id, idx, sub, data_bytes)
            self.stats["total_tx"] += 1
            self.sdo_resp_lbl.configure(
                text=f"SDO Download sent to Node {node_id} [0x{idx:04X}:{sub:02X}] = {val_str}", foreground="green"
            )
        except Exception as e:
            messagebox.showerror("SDO Write Error", f"Invalid parameters:\n{e}")

    def _check_sdo_reply(self, req_node: int, req_idx: int, req_sub: int):
        expected_cid = 0x580 + req_node
        for item in reversed(self.captured_messages):
            _, _, _, _, dhex, _, cid, _ = item
            if cid == expected_cid:
                bytes_list = [int(b, 16) for b in dhex.split()]
                if (
                    len(bytes_list) >= 4
                    and (bytes_list[1] | (bytes_list[2] << 8)) == req_idx
                    and bytes_list[3] == req_sub
                ):
                    cs = bytes_list[0]
                    if cs == 0x80:  # SDO Abort
                        err_code = int.from_bytes(bytes_list[4:8], "little")
                        self.sdo_resp_lbl.configure(
                            text=f"SDO Abort received from Node {req_node}! Error Code: 0x{err_code:08X}",
                            foreground="red",
                        )
                        return

                    payload = bytes_list[4:]
                    raw_val = int.from_bytes(payload, "little")
                    signed_val = int.from_bytes(payload, "little", signed=True)
                    ascii_str = bytes(payload).decode("latin-1", errors="ignore").replace("\x00", "")
                    self.sdo_resp_lbl.configure(
                        text=f"Reply from Node {req_node} [0x{req_idx:04X}:{req_sub:02X}] -> "
                        f"Hex: 0x{raw_val:X} | Unsigned: {raw_val} | Signed: {signed_val} | ASCII: '{ascii_str}'",
                        foreground="green",
                    )
                    return
        self.sdo_resp_lbl.configure(
            text=f"SDO Timeout: No response from Node {req_node} within 200 ms.", foreground="#888888"
        )

    # =========================================================================
    # TAB 6: Hardware & Bus Diagnostics
    # =========================================================================
    def _build_hardware_tab(self):
        box_hw = ttk.LabelFrame(self.tab_hw, text=" Hardware Adapter Information & SLCAN Commands ", padding=14)
        box_hw.pack(fill=tk.BOTH, expand=True)

        self.lbl_hw_info = ttk.Label(
            box_hw, text="Adapter: Not Connected", font=("Consolas", 11, "bold"), foreground="#007acc"
        )
        self.lbl_hw_info.pack(fill=tk.X, pady=6)

        slcan_box = ttk.LabelFrame(
            box_hw, text=" LAWICEL / SLCAN ASCII Hardware Commands (for CANUSB, USBtin, CANable) ", padding=10
        )
        slcan_box.pack(fill=tk.X, pady=8)

        btn_row = ttk.Frame(slcan_box)
        btn_row.pack(fill=tk.X, pady=4)

        ttk.Button(btn_row, text="Hardware Version (V)", command=lambda: self._send_slcan_cmd("V")).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_row, text="Serial Number (N)", command=lambda: self._send_slcan_cmd("N")).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_row, text="Read Status Flags (F)", command=lambda: self._send_slcan_cmd("F")).pack(
            side=tk.LEFT, padx=4
        )

        self.slcan_resp_lbl = ttk.Label(
            slcan_box, text="SLCAN Response: -", font=("Consolas", 11), foreground="#555555"
        )
        self.slcan_resp_lbl.pack(fill=tk.X, pady=8)

        # Flags guide
        guide = ttk.LabelFrame(box_hw, text=" SJA1000 / CAN Status Flags Guide (F command) ", padding=10)
        guide.pack(fill=tk.BOTH, expand=True, pady=6)

        flags_text = (
            "• F00 : Normal (No bus errors)\n"
            "• Bit 0 (0x01) : CAN receive FIFO queue full\n"
            "• Bit 1 (0x02) : CAN transmit FIFO queue full\n"
            "• Bit 2 (0x04) : Error warning flag (EI)\n"
            "• Bit 3 (0x08) : Data Overrun flag (DOI)\n"
            "• Bit 5 (0x20) : Error Passive flag (EPI)\n"
            "• Bit 7 (0x80) : Bus Off flag (BEI)\n"
        )
        ttk.Label(guide, text=flags_text, font=("Consolas", 10)).pack(anchor="w", padx=8, pady=4)

    def _send_slcan_cmd(self, cmd_char: str):
        if not self.bus or not hasattr(self.bus, "serialPortOrig"):
            messagebox.showinfo("SLCAN Command", "This command is only applicable to direct SLCAN/serial adapters.")
            return
        try:
            ser = self.bus.serialPortOrig
            ser.write(f"{cmd_char}\r".encode("ascii"))
            time.sleep(0.08)
            resp = ser.read(ser.in_waiting or 1)
            resp_str = resp.decode("ascii", errors="replace").replace("\r", " ").replace("\x07", "[BELL/ERR]").strip()
            self.slcan_resp_lbl.configure(
                text=f"Command '{cmd_char}' -> Response: {resp_str if resp_str else '(OK/empty)'}", foreground="green"
            )
        except Exception as e:
            self.slcan_resp_lbl.configure(text=f"Command Error: {e}", foreground="red")

    # =========================================================================
    # TAB 7: Educational CANopen Reference
    # =========================================================================
    def _build_reference_tab(self):
        txt_frame = ttk.Frame(self.tab_ref)
        txt_frame.pack(fill=tk.BOTH, expand=True)

        txt = tk.Text(txt_frame, wrap=tk.WORD, font=("Consolas", 10), padx=10, pady=10)
        scroll = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=scroll.set)

        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ref_content = (
            "====================================================================================\n"
            "              CANopen (CiA 301 & CiA 402) EDUCATIONAL QUICK REFERENCE SHEET        \n"
            "====================================================================================\n\n"
            "1. PREDEFINED CONNECTION SET (Standard COB-IDs on 11-bit CAN):\n"
            "------------------------------------------------------------------------------------\n"
            "  COB-ID Range         | Service Name                 | Description\n"
            "-----------------------|------------------------------|-----------------------------\n"
            "  0x000                | NMT Master                   | Network management (broadcast / node)\n"
            "  0x080                | SYNC                         | Bus synchronization clock\n"
            "  0x081 - 0x0FF        | EMCY (Emergency)             | 0x080 + NodeID (Error report)\n"
            "  0x100                | TIME STAMP                   | High-resolution time distribution\n"
            "  0x181 - 0x1FF        | TPDO 1 (Transmit PDO)        | 0x180 + NodeID (Device -> Bus)\n"
            "  0x201 - 0x27F        | RPDO 1 (Receive PDO)         | 0x200 + NodeID (Bus -> Device)\n"
            "  0x281 - 0x2FF        | TPDO 2                       | 0x280 + NodeID\n"
            "  0x301 - 0x37F        | RPDO 2                       | 0x300 + NodeID\n"
            "  0x381 - 0x3FF        | TPDO 3                       | 0x380 + NodeID\n"
            "  0x401 - 0x47F        | RPDO 3                       | 0x400 + NodeID\n"
            "  0x481 - 0x4FF        | TPDO 4                       | 0x480 + NodeID\n"
            "  0x501 - 0x57F        | RPDO 4                       | 0x500 + NodeID\n"
            "  0x581 - 0x5FF        | SDO Tx (Server -> Client)    | 0x580 + NodeID (Dictionary reply)\n"
            "  0x601 - 0x67F        | SDO Rx (Client -> Server)    | 0x600 + NodeID (Dictionary request)\n"
            "  0x701 - 0x77F        | Heartbeat / Bootup           | 0x700 + NodeID (State: 05=Op, 7F=PreOp)\n\n"
            "2. NMT STATE MACHINE (COB-ID 0x000 [Command, NodeID]):\n"
            "------------------------------------------------------------------------------------\n"
            "  Command 0x01 : Start Remote Node -> Operational (PDO transmission active)\n"
            "  Command 0x80 : Enter Pre-Operational -> Only SDOs and Heartbeats allowed\n"
            "  Command 0x02 : Stop Remote Node -> Stopped (Only NMT and Heartbeats allowed)\n"
            "  Command 0x81 : Reset Node -> Software power cycle\n"
            "  Command 0x82 : Reset Communication -> Reset CAN communication parameters\n\n"
            "3. SDO EXPEDITED PROTOCOL (COB-ID 0x600+Node -> 0x580+Node):\n"
            "------------------------------------------------------------------------------------\n"
            "  Upload Request (Read)   : [0x40, Index_L, Index_H, SubIndex, 00, 00, 00, 00]\n"
            "  Upload Response (Data)  : [0x43/0x4B/0x4F, Index_L, Index_H, SubIndex, D0, D1, D2, D3]\n"
            "  Download Request (Write): [0x23/0x2B/0x2F, Index_L, Index_H, SubIndex, D0, D1, D2, D3]\n"
            "  Abort Response (Error)  : [0x80, Index_L, Index_H, SubIndex, Err0, Err1, Err2, Err3]\n\n"
            "4. STANDARD OBJECT DICTIONARY INDICES (CiA 301 & 402):\n"
            "------------------------------------------------------------------------------------\n"
            "  0x1000 : Device Type (Standard profile definition, e.g. CiA 402 drive = 0x00020192)\n"
            "  0x1001 : Error Register (0 = No error)\n"
            "  0x1008 : Manufacturer Device Name (ASCII string)\n"
            "  0x1018 : Identity Object (Vendor ID, Product Code, Revision, Serial Number)\n"
            "  0x6040 : Controlword (Command word for CiA 402 state transitions)\n"
            "  0x6041 : Statusword (Current drive state: Ready, Switched On, Operation Enabled)\n"
            "  0x6060 : Modes of Operation (1=Profile Position, 3=Profile Velocity, 4=Profile Torque)\n"
            "  0x606C : Velocity Actual Value (Signed 32-bit motor speed in RPM / internal units)\n"
            "  0x6071 : Target Torque (Signed 16-bit torque request)\n"
        )
        txt.insert(tk.END, ref_content)
        txt.configure(state=tk.DISABLED)

    # =========================================================================
    # Connection Management & Processing Loop
    # =========================================================================
    def _toggle_connection(self):
        if not self.running:
            iface_key = self._get_selected_iface_key()
            channel = self.channel_combo.get().strip()
            bitrate = int(self.bitrate_combo.get())

            if not channel:
                messagebox.showerror("Error", "Please enter or select a valid channel/port.")
                return

            try:
                self.bus = open_can_bus(iface_key, channel, bitrate)
            except Exception as e:
                messagebox.showerror(
                    "Connection Error",
                    f"Could not open {iface_key} on '{channel}':\n{e}\n\n"
                    f"Check adapter connections and ensure no other process is locking the device.",
                )
                return

            # Start Virtual Simulator thread if Virtual mode selected
            if iface_key == "virtual":
                self.simulator = VirtualCanopenSimulator(channel)
                self.simulator.start()

            # Set up CANopen stack layer and active profile
            reg = get_default_registry()
            reg.active_profile = self.profile_combo.get()
            self.canopen_layer = CANopenLayer(bus=self.bus, registry=reg)

            self.running = True
            self.btn_connect.configure(text="Disconnect")
            self.status_lbl.configure(
                text=f"Connected: {iface_key} [{channel}] @ {bitrate / 1000:g} kbps", foreground="green"
            )
            self.lbl_hw_info.configure(
                text=f"Active Interface: {SUPPORTED_INTERFACES[iface_key]['name']}\n"
                f"Channel: {channel} | Bitrate: {bitrate / 1000:g} kbps | Backend: {iface_key}"
            )

            self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
            self.rx_thread.start()
            self._update_gui_loop()

        else:
            self._disconnect()

    def _disconnect(self):
        self.running = False
        if self.tx_periodic_timer:
            self.after_cancel(self.tx_periodic_timer)
            self.tx_periodic_timer = None
            self.btn_periodic.configure(text="Start Periodic Transmission")

        if self.sync_generator_timer:
            self.after_cancel(self.sync_generator_timer)
            self.sync_generator_timer = None
            self.btn_periodic_sync.configure(text="Start Periodic SYNC (50 Hz)")

        if self.heartbeat_generator_timer:
            self.after_cancel(self.heartbeat_generator_timer)
            self.heartbeat_generator_timer = None
            self.btn_periodic_hb.configure(text="Start Heartbeat (1 Hz)")

        if self.simulator:
            self.simulator.stop()
            self.simulator = None

        if self.bus:
            try:
                self.bus.shutdown()
            except Exception:
                pass
            self.bus = None

        self.canopen_layer = None
        self.btn_connect.configure(text="Connect")
        self.status_lbl.configure(text="Status: Disconnected", foreground="red")
        self.lbl_hw_info.configure(text="Adapter: Not Connected")

    def _rx_loop(self):
        start_time = time.time()
        while self.running:
            try:
                msg = self.bus.recv(timeout=0.08)
                if not msg:
                    continue

                now = time.time()
                elapsed = now - start_time
                self.stats["total_rx"] += 1
                self.stats["rx_count_interval"] += 1

                parsed = self.canopen_layer.process_can_message(msg)
                cid = msg.arbitration_id
                cid_str = f"0x{cid:08X}" if msg.is_extended_id else f"0x{cid:03X}"
                self.known_can_ids.add(cid_str)

                # Track Discovered Network Nodes
                if parsed.node_id is not None and parsed.node_id > 0:
                    nid = parsed.node_id
                    if nid not in self.discovered_nodes:
                        self.discovered_nodes[nid] = {
                            "state": "Unknown",
                            "last_seen": now,
                            "services": set(),
                        }
                    self.discovered_nodes[nid]["last_seen"] = now
                    self.discovered_nodes[nid]["services"].add(parsed.service.name)

                    if parsed.service.name == "HEARTBEAT" and msg.data:
                        st = NmtState.from_byte(msg.data[0])
                        self.discovered_nodes[nid]["state"] = str(st)

                # Store payload bytes and words for reverse engineering graph
                for b_idx in range(len(msg.data)):
                    key = (cid, f"B{b_idx}")
                    self.plot_times[key].append(now)
                    self.plot_history[key].append(msg.data[b_idx])

                if len(msg.data) >= 2:
                    self.plot_times[(cid, "W01")].append(now)
                    self.plot_history[(cid, "W01")].append(int.from_bytes(msg.data[0:2], "little", signed=True))
                if len(msg.data) >= 4:
                    self.plot_times[(cid, "W23")].append(now)
                    self.plot_history[(cid, "W23")].append(int.from_bytes(msg.data[2:4], "little", signed=True))
                    self.plot_times[(cid, "DW03")].append(now)
                    self.plot_history[(cid, "DW03")].append(int.from_bytes(msg.data[0:4], "little", signed=True))
                if len(msg.data) >= 6:
                    self.plot_times[(cid, "W45")].append(now)
                    self.plot_history[(cid, "W45")].append(int.from_bytes(msg.data[4:6], "little", signed=True))
                if len(msg.data) >= 8:
                    self.plot_times[(cid, "W67")].append(now)
                    self.plot_history[(cid, "W67")].append(int.from_bytes(msg.data[6:8], "little", signed=True))
                    self.plot_times[(cid, "DW47")].append(now)
                    self.plot_history[(cid, "DW47")].append(int.from_bytes(msg.data[4:8], "little", signed=True))

                # Store decoded signals
                for s_name, s_val in parsed.signals.items():
                    if isinstance(s_val, (int, float)):
                        self.known_signals.add(s_name)
                        self.plot_times[("sig", s_name)].append(now)
                        self.plot_history[("sig", s_name)].append(s_val)

                # Extract telemetry signals
                if "actual_speed_rpm" in parsed.signals:
                    self.telemetry_data["speed"] = parsed.signals["actual_speed_rpm"]
                    self.telemetry_data["max_speed"] = parsed.signals.get(
                        "max_speed_rpm", self.telemetry_data["max_speed"]
                    )
                elif "velocity_actual" in parsed.signals:
                    self.telemetry_data["speed"] = parsed.signals["velocity_actual"]

                if "target_torque" in parsed.signals:
                    self.telemetry_data["torque"] = parsed.signals["target_torque"]
                if "cia402_state" in parsed.signals:
                    self.telemetry_data["status_str"] = parsed.signals["cia402_state"]
                if "heatsink_temp_c" in parsed.signals:
                    self.telemetry_data["temp1"] = parsed.signals["heatsink_temp_c"]
                    self.telemetry_data["temp2"] = parsed.signals.get("motor_temp_raw", self.telemetry_data["temp2"])

                # Record message for trace
                if not self.trace_paused:
                    type_str = "EXT" if msg.is_extended_id else "STD"
                    dhex = msg.data.hex(" ").upper() if msg.data else ""
                    self.captured_messages.append(
                        (
                            f"{elapsed:.3f}",
                            type_str,
                            cid_str,
                            str(msg.dlc),
                            dhex,
                            parsed.decoded_info,
                            cid,
                            msg.is_extended_id,
                        )
                    )

            except Exception:
                break

    def _update_gui_loop(self):
        if not self.running:
            return

        now = time.time()

        # 1. Update Bus Statistics
        dt = now - self.stats["last_calc_time"]
        if dt >= 1.0:
            rate = int(self.stats["rx_count_interval"] / dt)
            self.stats["rx_rate"] = rate
            self.stats["rx_count_interval"] = 0
            self.stats["last_calc_time"] = now

            self.lbl_rate.configure(text=f"Bus Rate: {rate} msgs/s")
            self.lbl_total_rx.configure(text=f"Total Received: {self.stats['total_rx']}")
            self.lbl_total_tx.configure(text=f"Total Transmitted: {self.stats['total_tx']}")
            self.lbl_nodes_cnt.configure(text=f"Active Nodes: {len(self.discovered_nodes)}")

            # Update Node Treeview table
            self.node_tree.delete(*self.node_tree.get_children())
            for nid in sorted(self.discovered_nodes.keys()):
                info = self.discovered_nodes[nid]
                sec_ago = f"{now - info['last_seen']:.1f}s ago"
                serv_str = ", ".join(sorted(info["services"]))
                self.node_tree.insert("", tk.END, values=(f"Node {nid}", info["state"], sec_ago, serv_str))

        # 2. Update Telemetry Gauges
        self.val_speed.configure(text=f"{self.telemetry_data['speed']} RPM")
        self.val_max_speed.configure(text=f"Max: {self.telemetry_data['max_speed']} RPM")
        self.val_torque.configure(text=f"{self.telemetry_data['torque']}")
        if "status_str" in self.telemetry_data:
            self.val_status_str.configure(text=f"State: {self.telemetry_data['status_str']}")
        self.val_temp1.configure(text=f"{self.telemetry_data['temp1']} °C")
        self.val_temp2.configure(text=f"{self.telemetry_data['temp2']}")

        # 3. Update Trace Treeview (batch update up to 30 items)
        if not self.trace_paused and self.captured_messages:
            filter_text = self.filter_entry.get().strip().lower()
            ext_only = self.filter_ext_var.get()

            batch = self.captured_messages[-30:]
            self.captured_messages = self.captured_messages[-500:]

            for item in batch:
                t, typ, cid_str, dlc, dhex, info, raw_id, is_ext = item
                if ext_only and not is_ext:
                    continue
                if filter_text and filter_text not in cid_str.lower():
                    continue
                self.tree.insert("", 0, values=(t, typ, cid_str, dlc, dhex, info))

            rows = self.tree.get_children()
            if len(rows) > 200:
                for r in rows[200:]:
                    self.tree.delete(r)

        # 4. Update Reverse Plotter (~10 FPS)
        if not self.plot_paused and (now - self.last_plot_draw) > 0.1:
            try:
                current_tab = self.notebook.tab(self.notebook.select(), "text")
                if "Reverse Plotter" in current_tab:
                    self._render_plot(now)
                    self.last_plot_draw = now
            except Exception:
                pass

        self.after(100, self._update_gui_loop)

    def _render_plot(self, current_time: float):
        mode = self.plot_mode_combo.get()
        target = self.plot_target_combo.get().strip()
        fmt = self.plot_format_combo.get()
        try:
            window_sec = float(self.plot_win_spin.get())
        except Exception:
            window_sec = 15.0

        t_min = current_time - window_sec
        self.ax.cla()
        self.ax.grid(True, color="#3a3a3a", linestyle="--", alpha=0.7)

        has_data = False

        if "Signal" in mode:
            key = ("sig", target)
            times = list(self.plot_times[key])
            vals = list(self.plot_history[key])
            if times:
                x = [t - current_time for t in times if t >= t_min]
                y = [vals[i] for i, t in enumerate(times) if t >= t_min]
                if x and y:
                    has_data = True
                    cur_val = y[-1]
                    self.ax.plot(x, y, color="#00ffff", lw=2, label=f"{target}: {cur_val:.1f}")
                    self.ax.set_title(
                        f"Decoded Signal: {target}\nCurrent: {cur_val:.1f} | Min: {min(y):.1f} | Max: {max(y):.1f}",
                        color="white",
                        fontsize=10,
                    )
            self.ax.set_ylabel(target, color="#cccccc")

        else:
            try:
                target_clean = target.strip()
                cid = int(target_clean, 16 if not target_clean.lower().startswith("0x") else 0)
            except ValueError:
                self.ax.text(
                    0.5,
                    0.5,
                    f"Invalid CAN ID: {target}",
                    color="red",
                    ha="center",
                    va="center",
                    transform=self.ax.transAxes,
                )
                self.plot_canvas.draw_idle()
                return

            dev_desc = ""
            if self.canopen_layer and self.canopen_layer.registry:
                dec = self.canopen_layer.registry.lookup(cid)
                if dec and cid in dec.custom_cob_ids:
                    dev_desc = f" ({dec.custom_cob_ids[cid]})"

            if "All Bytes" in fmt:
                active_count = 0
                for b_idx in range(8):
                    key = (cid, f"B{b_idx}")
                    times = list(self.plot_times[key])
                    vals = list(self.plot_history[key])
                    if times:
                        x = [t - current_time for t in times if t >= t_min]
                        y = [vals[i] for i, t in enumerate(times) if t >= t_min]
                        if x and y:
                            active_count += 1
                            has_data = True
                            self.ax.plot(x, y, color=BYTE_COLORS[b_idx], lw=1.5, label=f"B{b_idx}: {y[-1]}")

                self.ax.set_ylim(-5, 260)
                self.ax.set_ylabel("Byte Value (0 - 255)", color="#cccccc")
                self.ax.set_title(f"CAN ID: 0x{cid:X}{dev_desc}\nPayload Bytes 0..7", color="white", fontsize=10)

            elif "Word" in fmt or "DWord" in fmt:
                word_key_map = {
                    "Word 0..1 (int16 LE)": "W01",
                    "Word 2..3 (int16 LE)": "W23",
                    "Word 4..5 (int16 LE)": "W45",
                    "Word 6..7 (int16 LE)": "W67",
                    "DWord 0..3 (int32 LE)": "DW03",
                    "DWord 4..7 (int32 LE)": "DW47",
                }
                wk = word_key_map.get(fmt, "W01")
                key = (cid, wk)
                times = list(self.plot_times[key])
                vals = list(self.plot_history[key])
                if times:
                    x = [t - current_time for t in times if t >= t_min]
                    y = [vals[i] for i, t in enumerate(times) if t >= t_min]
                    if x and y:
                        has_data = True
                        self.ax.plot(x, y, color="#32cd32", lw=2, label=f"{fmt}: {y[-1]}")
                        self.ax.set_title(
                            f"0x{cid:X}{dev_desc}\n{fmt} (Current: {y[-1]} | Min: {min(y)} | Max: {max(y)})",
                            color="white",
                            fontsize=10,
                        )
                self.ax.set_ylabel("Numerical Value", color="#cccccc")

            else:
                b_num = int(fmt.split()[-1])
                key = (cid, f"B{b_num}")
                times = list(self.plot_times[key])
                vals = list(self.plot_history[key])
                if times:
                    x = [t - current_time for t in times if t >= t_min]
                    y = [vals[i] for i, t in enumerate(times) if t >= t_min]
                    if x and y:
                        has_data = True
                        self.ax.plot(x, y, color=BYTE_COLORS[b_num], lw=2, label=f"Byte {b_num}: {y[-1]}")
                        self.ax.set_title(
                            f"0x{cid:X}{dev_desc}\nByte {b_num} (Current: {y[-1]} | Min: {min(y)} | Max: {max(y)})",
                            color="white",
                            fontsize=10,
                        )
                self.ax.set_ylim(-5, 260)
                self.ax.set_ylabel("Byte Value (0 - 255)", color="#cccccc")

        if not has_data:
            self.ax.text(
                0.5,
                0.5,
                f"No data received yet for '{target}'\n(Waiting for bus traffic in the last {window_sec:g}s...)",
                color="#888888",
                ha="center",
                va="center",
                transform=self.ax.transAxes,
                fontsize=11,
            )

        self.ax.set_xlim(-window_sec, 0)
        self.ax.set_xlabel("Time (seconds ago)", color="#cccccc")
        self.ax.tick_params(colors="#cccccc")
        if has_data:
            leg = self.ax.legend(
                loc="upper left", facecolor="#1e1e1e", edgecolor="#555555", labelcolor="#dddddd", fontsize=8
            )
            if leg:
                leg.get_frame().set_alpha(0.8)

        self.plot_canvas.draw_idle()

    # =========================================================================
    # About Dialog
    # =========================================================================
    def _show_about(self):
        dlg = tk.Toplevel(self)
        dlg.title("About - CAN & CANopen Studio")
        dlg.geometry("540x480")
        dlg.minsize(480, 420)
        dlg.transient(self)
        dlg.grab_set()

        f = ttk.Frame(dlg, padding=18)
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            f,
            text="CAN & CANopen Studio\nUniversal Protocol Analyzer & Transmit Station",
            font=("Segoe UI", 13, "bold"),
            justify=tk.CENTER,
            foreground="#007acc",
        ).pack(pady=(0, 6))

        ttk.Label(f, text="Version 0.2.1", font=("Segoe UI", 9, "italic"), foreground="#666666").pack(pady=(0, 8))

        info_box = ttk.LabelFrame(f, text=" Project & Author ", padding=10)
        info_box.pack(fill=tk.X, pady=4)

        ttk.Label(info_box, text="Author: Sébastien Celles", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=2)
        ttk.Label(
            info_box, text="License: GNU General Public License v3.0 (GPL-3.0-or-later)", font=("Segoe UI", 9)
        ).pack(anchor="w", pady=2)
        ttk.Label(
            info_box, text="Copyright © 2026 Sébastien Celles", font=("Segoe UI", 9, "italic"), foreground="#555555"
        ).pack(anchor="w", pady=2)

        desc_text = (
            "General-purpose educational and engineering platform for learning, analyzing, "
            "and transmitting on CAN and CANopen networks. Supports market converters (SLCAN, PCAN, "
            "Kvaser, Vector, IXXAT, Candlelight, SocketCAN) and hardware-free simulation."
        )
        ttk.Label(f, text=desc_text, wraplength=480, font=("Segoe UI", 9)).pack(pady=8)

        gpl_box = ttk.LabelFrame(f, text=" Open Source License Notice ", padding=10)
        gpl_box.pack(fill=tk.BOTH, expand=True, pady=4)

        gpl_text = (
            "This program is free software: you can redistribute it and/or modify "
            "it under the terms of the GNU General Public License as published by "
            "the Free Software Foundation, either version 3 of the License, or "
            "(at your option) any later version.\n\n"
            "This program is distributed in the hope that it will be useful, "
            "but WITHOUT ANY WARRANTY; without even the implied warranty of "
            "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. "
            "See the GNU General Public License for more details."
        )
        ttk.Label(gpl_box, text=gpl_text, wraplength=460, font=("Segoe UI", 8), foreground="#444444").pack(
            fill=tk.BOTH, expand=True
        )

        btn_row = ttk.Frame(f)
        btn_row.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_row, text="View License (GPLv3)", command=self._show_full_license).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Close", command=dlg.destroy).pack(side=tk.RIGHT)

    def _show_full_license(self):
        lic_dlg = tk.Toplevel(self)
        lic_dlg.title("GNU General Public License v3.0 - LICENSE")
        lic_dlg.geometry("640x520")
        lic_dlg.transient(self)

        txt_frame = ttk.Frame(lic_dlg, padding=10)
        txt_frame.pack(fill=tk.BOTH, expand=True)

        txt = tk.Text(txt_frame, wrap=tk.WORD, font=("Consolas", 9), padx=8, pady=8)
        txt_scroll = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=txt_scroll.set)

        txt_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        lic_path = os.path.join(os.path.dirname(__file__), "LICENSE")
        if os.path.exists(lic_path):
            with open(lic_path, "r", encoding="utf-8") as f:
                txt.insert(tk.END, f.read())
        else:
            txt.insert(
                tk.END,
                "GNU General Public License v3.0\n\nCopyright (C) 2026 Sébastien Celles\n\nSee: https://www.gnu.org/licenses/gpl-3.0.html",
            )
        txt.configure(state=tk.DISABLED)

    # ==========================================================================
    # Application Update Mechanism
    # ==========================================================================
    def _background_update_check(self):
        """Runs in background on launch to notify if a newer version exists."""
        time.sleep(1.5)  # Let UI finish initial window display
        has_update, info = check_for_updates()
        if has_update and info:
            self.after(0, self._notify_update_available, info)

    def _notify_update_available(self, info: Dict[str, Any]):
        tag = info.get("tag_name", "new version")
        self.status_version_lbl.config(
            text=f"CAN & CANopen Studio v{CURRENT_VERSION}  —  ⚡ New release {tag} available!"
        )
        self.status_update_btn.config(text=f"🚀 Update to {tag}")
        self.status_update_btn.pack(side=tk.RIGHT, padx=6)

    def _check_updates_dialog(self, manual: bool = True):
        top = tk.Toplevel(self)
        top.title("CANopen Studio - Check for Updates")
        top.geometry("540x440")
        top.transient(self)

        content = ttk.Frame(top, padding=16)
        content.pack(fill=tk.BOTH, expand=True)

        status_hdr = ttk.Label(content, text="Checking GitHub for latest release...", font=("Segoe UI", 11, "bold"))
        status_hdr.pack(anchor=tk.W, pady=(0, 10))

        details_txt = tk.Text(content, wrap=tk.WORD, height=12, font=("Consolas", 9), padx=8, pady=8)
        details_txt.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        details_txt.insert(tk.END, f"Querying GitHub Releases for {GITHUB_REPO}...\n")
        details_txt.configure(state=tk.DISABLED)

        btn_bar = ttk.Frame(content)
        btn_bar.pack(fill=tk.X)

        close_btn = ttk.Button(btn_bar, text="Close", command=top.destroy)
        close_btn.pack(side=tk.RIGHT, padx=4)

        def worker():
            has_update, info = check_for_updates()
            self.after(0, lambda: on_finish(has_update, info))

        def on_finish(has_update, info):
            details_txt.configure(state=tk.NORMAL)
            details_txt.delete("1.0", tk.END)

            if info is None:
                status_hdr.config(text="⚠️ Could not check for updates")
                details_txt.insert(
                    tk.END,
                    "Unable to reach GitHub. Please verify your internet connection.\n"
                    f"Repository: https://github.com/{GITHUB_REPO}\n",
                )
                details_txt.configure(state=tk.DISABLED)
                return

            if info.get("rate_limited"):
                status_hdr.config(text="⚠️ GitHub API Rate Limit Exceeded")
                details_txt.insert(
                    tk.END,
                    "GitHub API rate limit reached (60 requests/hour unauthenticated).\n"
                    "Please wait a while before checking again, or visit the repository directly:\n"
                    f"{info.get('html_url', f'https://github.com/{GITHUB_REPO}/releases')}\n",
                )
                details_txt.configure(state=tk.DISABLED)
                return

            if info.get("no_releases"):
                status_hdr.config(text="ℹ️ No releases published yet")
                details_txt.insert(
                    tk.END,
                    f"Current Version: v{CURRENT_VERSION}\n\n"
                    "No releases have been published yet on GitHub for this repository.\n"
                    f"Repository: {info.get('html_url', f'https://github.com/{GITHUB_REPO}/releases')}\n",
                )
                details_txt.configure(state=tk.DISABLED)
                return

            tag = info.get("tag_name", "v0.0.0")
            if has_update:
                status_hdr.config(text=f"✨ Update Available: {tag}!")
                details_txt.insert(
                    tk.END,
                    f"Current Version: v{CURRENT_VERSION}\n"
                    f"Latest Version:  {tag}\n"
                    f"Published Date:  {info.get('published_at', '')}\n\n"
                    f"--- Release Notes ---\n{info.get('body', 'No release notes provided.')}\n",
                )

                setup_asset = info.get("setup_asset")
                if setup_asset:
                    dl_btn = ttk.Button(
                        btn_bar,
                        text="📥 Download & Install",
                        command=lambda: self._download_and_run_installer(top, setup_asset),
                    )
                    dl_btn.pack(side=tk.LEFT, padx=4)

                if is_git_repo():

                    def do_git_update():
                        status_hdr.config(text="Updating repository via git pull & uv sync...")
                        ok, msg = perform_git_update()
                        if ok:
                            messagebox.showinfo("Git Update", f"{msg}\nPlease restart CANopen Studio.")
                            top.destroy()
                        else:
                            messagebox.showerror("Git Update Failed", msg)

                    git_btn = ttk.Button(btn_bar, text="🔄 Update via Git & uv", command=do_git_update)
                    git_btn.pack(side=tk.LEFT, padx=4)

                gh_btn = ttk.Button(
                    btn_bar,
                    text="🌐 View on GitHub",
                    command=lambda: webbrowser.open(info.get("html_url")),
                )
                gh_btn.pack(side=tk.LEFT, padx=4)
            else:
                status_hdr.config(text="✅ You are using the latest version!")
                details_txt.insert(
                    tk.END,
                    f"Current Version: v{CURRENT_VERSION}\n"
                    f"Latest Version:  {tag}\n\n"
                    "Your installation of CAN & CANopen Studio is up to date.\n",
                )

            details_txt.configure(state=tk.DISABLED)

        threading.Thread(target=worker, daemon=True).start()

    def _download_and_run_installer(self, parent_win, asset: Dict[str, Any]):
        url = asset.get("browser_download_url")
        name = asset.get("name", "CANopen-Studio-Setup.exe")
        if not url:
            return

        import tempfile

        tmp_dir = tempfile.gettempdir()
        dest_path = os.path.join(tmp_dir, name)

        prog_win = tk.Toplevel(parent_win)
        prog_win.title("Downloading Update...")
        prog_win.geometry("400x120")
        prog_win.transient(parent_win)

        lbl = ttk.Label(prog_win, text=f"Downloading {name}...", font=("Segoe UI", 9))
        lbl.pack(padx=16, pady=(16, 8), anchor=tk.W)

        pbar = ttk.Progressbar(prog_win, mode="determinate")
        pbar.pack(fill=tk.X, padx=16, pady=8)

        def progress_cb(downloaded, total):
            pct = int((downloaded / total) * 100) if total > 0 else 0
            self.after(0, lambda: pbar.configure(value=pct))

        def dl_worker():
            success = download_file(url, dest_path, progress_callback=progress_cb)
            if success:
                self.after(
                    0,
                    lambda: (
                        prog_win.destroy(),
                        messagebox.showinfo(
                            "Download Complete",
                            "Installer downloaded successfully.\nCANopen Studio will now close to start the installer.",
                        ),
                        launch_installer_and_exit(dest_path),
                    ),
                )
            else:
                self.after(
                    0,
                    lambda: (
                        prog_win.destroy(),
                        messagebox.showerror("Download Failed", f"Failed to download update installer {name}."),
                    ),
                )

        threading.Thread(target=dl_worker, daemon=True).start()


def main():
    app = CanStudioApp()
    app.mainloop()


if __name__ == "__main__":
    main()
