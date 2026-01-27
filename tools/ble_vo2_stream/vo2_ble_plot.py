#!/usr/bin/env python3
"""BLE receiver + live plotter for SpiroVO2-RAW packets."""

from __future__ import annotations

import argparse
import asyncio
import csv
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

try:
    from bleak import BleakClient, BleakScanner
except ImportError as exc:
    print("bleak is required. Install with: pip install bleak", file=sys.stderr)
    raise

try:
    import numpy as np
    # Force PyQt5 to avoid mixing Qt bindings (PyQt6/PyQt5).
    import os
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
    from PyQt5 import QtCore, QtGui, QtWidgets
    import pyqtgraph as pg
except ImportError as exc:
    print(
        "pyqtgraph + PyQt5 + numpy are required. Install with: pip install pyqtgraph PyQt5 numpy",
        file=sys.stderr,
    )
    raise

SERVICE_UUID = "7a1b7d30-2e4b-4b2f-8f1a-2dfe3e5c0e11"
STREAM_CHAR_UUID = "7a1b7d31-2e4b-4b2f-8f1a-2dfe3e5c0e11"

PKT_CONFIG = 0x01
PKT_PRESSURE = 0x02
PKT_O2 = 0x03
PKT_CO2 = 0x04


@dataclass
class StreamConfig:
    sample_rate_hz: float = 200.0
    area_1: float = 0.000531
    area_2: float = 0.000201
    correction: float = 0.92
    temp_c: float = 15.0
    pres_pa: float = 101325.0
    fi_o2: float = 20.90
    pressure_scale: float = 10.0
    pressure_sign: int = 1


class BleWorker(threading.Thread):
    def __init__(self, name: str, queue: Queue, status_cb, scan_interval: float = 2.0):
        super().__init__(daemon=True)
        self.device_name = name
        self.queue = queue
        self._stop = threading.Event()
        self._status_cb = status_cb
        self._scan_interval = scan_interval

    def stop(self):
        self._stop.set()

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        while not self._stop.is_set():
            self._status_cb(f"Scanning for {self.device_name}...")
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: d.name == self.device_name,
                timeout=5.0,
            )
            if self._stop.is_set():
                break
            if not device:
                self._status_cb("Device not found. Retrying...")
                await asyncio.sleep(self._scan_interval)
                continue

            try:
                async with BleakClient(device) as client:
                    self._status_cb("Connected. Streaming...")

                    def handle_notify(_, data: bytearray):
                        if not data:
                            return
                        self.queue.put(bytes(data))

                    await client.start_notify(STREAM_CHAR_UUID, handle_notify)

                    while not self._stop.is_set() and client.is_connected:
                        await asyncio.sleep(0.2)

                    try:
                        await client.stop_notify(STREAM_CHAR_UUID)
                    except Exception:
                        pass
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
            except Exception as exc:
                self._status_cb(f"BLE error: {exc}. Retrying...")
                await asyncio.sleep(self._scan_interval)
                continue

            if not self._stop.is_set():
                self._status_cb("Disconnected. Retrying...")
                await asyncio.sleep(self._scan_interval)
        self._status_cb("Stopped.")


class Vo2Processor:
    def __init__(
        self,
        weight_kg: float,
        vo2_window_sec: float,
        pressure_deadband_pa: float,
        flow_start_l_s: float,
        flow_end_l_s: float,
        min_breath_l: float,
        min_breath_s: float,
        start_hold_s: float,
        end_hold_s: float,
    ):
        self.weight_kg = weight_kg
        self.vo2_window_sec = vo2_window_sec
        self.pressure_deadband_pa = pressure_deadband_pa
        self.flow_start_l_s = flow_start_l_s
        self.flow_end_l_s = flow_end_l_s
        self.min_breath_l = min_breath_l
        self.min_breath_s = min_breath_s
        self.start_hold_s = start_hold_s
        self.end_hold_s = end_hold_s
        self.config = StreamConfig()
        self._config_seen = False
        self._sample_t0 = None

        self.pressure_times = deque(maxlen=4000)  # 20s at 200Hz
        self.pressure_vals = deque(maxlen=4000)

        self.breath_times = deque(maxlen=600)
        self.breath_vols = deque(maxlen=600)

        self.vo2_times = deque(maxlen=600)
        self.vo2_vals = deque(maxlen=600)

        self.o2_times = deque(maxlen=600)
        self.o2_vals = deque(maxlen=600)
        self.co2_times = deque(maxlen=600)
        self.co2_vals = deque(maxlen=600)

        self._last_o2 = None
        self._last_co2 = None
        self._ve_mean = 0.0
        self._vo2_roll_max = 0.0
        self._kcal_total = 0.0
        self._last_vo2_time = None
        self._last_vo2_value = None
        self._last_vo2_roll = None
        self._vo2_window = deque()
        self._record_samples = []

        self._breath_active = False
        self._breath_start_t = None
        self._breath_vol_ml = 0.0
        self._last_sample_time = None
        self._start_count = 0
        self._end_count = 0

    def update_config(self, payload: bytes) -> None:
        if len(payload) < 1 + 1 + (8 * 4) + 1:
            return
        _, version = payload[0], payload[1]
        if version != 1:
            return
        floats = struct.unpack_from("<8f", payload, 2)
        self.config = StreamConfig(
            sample_rate_hz=floats[0],
            area_1=floats[1],
            area_2=floats[2],
            correction=floats[3],
            temp_c=floats[4],
            pres_pa=floats[5],
            fi_o2=floats[6],
            pressure_scale=floats[7],
            pressure_sign=struct.unpack_from("<b", payload, 2 + 8 * 4)[0],
        )
        self._config_seen = True

    def update_o2(self, payload: bytes) -> None:
        if len(payload) < 1 + 4 + 2:
            return
        _, time_ms, o2_x100 = struct.unpack_from("<BIH", payload, 0)
        self._last_o2 = o2_x100 / 100.0
        t = time_ms / 1000.0
        self.o2_times.append(t)
        self.o2_vals.append(self._last_o2)

    def update_co2(self, payload: bytes) -> None:
        if len(payload) < 1 + 4 + 2:
            return
        _, time_ms, co2_x100 = struct.unpack_from("<BIH", payload, 0)
        self._last_co2 = co2_x100 / 100.0
        t = time_ms / 1000.0
        self.co2_times.append(t)
        self.co2_vals.append(self._last_co2)

    def _calc_flow_l_s(self, pressure_pa: float) -> float:
        cfg = self.config
        rho = cfg.pres_pa / (cfg.temp_c + 273.15) / 287.058
        denom = (1.0 / (cfg.area_2 ** 2)) - (1.0 / (cfg.area_1 ** 2))
        if denom <= 0:
            return 0.0
        mass_flow = 1000.0 * ((abs(pressure_pa) * 2.0 * rho) / denom) ** 0.5
        vol_flow = (mass_flow / rho) * cfg.correction
        return vol_flow

    def _update_vo2(self, ve_l_min: float) -> None:
        if self._last_o2 is None:
            return
        cfg = self.config
        o2_diff = max(0.0, cfg.fi_o2 - self._last_o2)
        rho_bpts = cfg.pres_pa / (35.0 + 273.15) / 292.9
        rho_stpd = 1.292
        vo2_total = ve_l_min * (rho_bpts / rho_stpd) * o2_diff * 10.0
        vo2_max = vo2_total / max(1e-6, self.weight_kg)
        t = self._last_sample_time if self._last_sample_time else 0.0
        self.vo2_times.append(t)
        self.vo2_vals.append(vo2_max)
        self._last_vo2_value = vo2_max
        self._vo2_window.append((t, vo2_max))
        while self._vo2_window and (t - self._vo2_window[0][0]) > self.vo2_window_sec:
            self._vo2_window.popleft()
        if self._vo2_window:
            avg = sum(v for _, v in self._vo2_window) / len(self._vo2_window)
            self._last_vo2_roll = avg
            if avg > self._vo2_roll_max:
                self._vo2_roll_max = avg
        if self._last_vo2_time is not None:
            dt_min = max(0.0, t - self._last_vo2_time) / 60.0
            kcal_per_min = (vo2_total / 1000.0) * 5.0
            self._kcal_total += kcal_per_min * dt_min
        self._last_vo2_time = t

    def update_pressure(self, payload: bytes) -> None:
        if len(payload) < 1 + 4 + 2:
            return
        _, start_idx, count = struct.unpack_from("<BIH", payload, 0)
        expected_len = 1 + 4 + 2 + (count * 2)
        if len(payload) < expected_len:
            return
        samples = struct.unpack_from("<" + "h" * count, payload, 7)

        if self._sample_t0 is None:
            self._sample_t0 = time.monotonic()

        cfg = self.config
        cfg = self.config
        sr = max(1.0, cfg.sample_rate_hz)
        start_hold_samples = max(1, int(self.start_hold_s * sr))
        end_hold_samples = max(1, int(self.end_hold_s * sr))

        for i, raw in enumerate(samples):
            pressure_pa = (raw / cfg.pressure_scale)
            sample_idx = start_idx + i
            t = sample_idx / cfg.sample_rate_hz
            self._last_sample_time = t
            self.pressure_times.append(t)
            self.pressure_vals.append(pressure_pa)

            if abs(pressure_pa) < self.pressure_deadband_pa:
                flow_l_s = 0.0
            else:
                flow_l_s = self._calc_flow_l_s(pressure_pa)
            dt = 1.0 / cfg.sample_rate_hz
            if not self._breath_active:
                if flow_l_s >= self.flow_start_l_s:
                    self._start_count += 1
                    if self._start_count >= start_hold_samples:
                        self._breath_active = True
                        self._breath_start_t = t
                        self._breath_vol_ml = 0.0
                        self._end_count = 0
                else:
                    self._start_count = 0
            else:
                if flow_l_s >= self.flow_end_l_s:
                    self._end_count = 0
                else:
                    self._end_count += 1
                    if self._end_count >= end_hold_samples:
                        vol_l = self._breath_vol_ml / 1000.0
                        if self._breath_start_t is not None:
                            dur = max(1e-3, t - self._breath_start_t)
                            if vol_l >= self.min_breath_l and dur >= self.min_breath_s:
                                self.breath_times.append(t)
                                self.breath_vols.append(vol_l)
                                ve = (vol_l / dur) * 60.0
                                self._ve_mean = (self._ve_mean * 0.75) + (ve * 0.25)
                                self._update_vo2(self._ve_mean)
                        self._breath_vol_ml = 0.0
                        self._breath_active = False
                        self._start_count = 0
                        self._end_count = 0

            if self._breath_active and flow_l_s > 0.0:
                self._breath_vol_ml += flow_l_s * dt * 1000.0

            breath_vol_l = self._breath_vol_ml / 1000.0

            self._record_samples.append(
                (
                    t,
                    sample_idx,
                    pressure_pa,
                    flow_l_s,
                    breath_vol_l,
                    self._last_o2,
                    self._last_co2,
                    self._last_vo2_value,
                    self._last_vo2_roll,
                    self._vo2_roll_max,
                    self._kcal_total,
                )
            )

    def handle_packet(self, payload: bytes) -> None:
        if not payload:
            return
        pkt_type = payload[0]
        if pkt_type == PKT_CONFIG:
            self.update_config(payload)
        elif pkt_type == PKT_PRESSURE:
            self.update_pressure(payload)
        elif pkt_type == PKT_O2:
            self.update_o2(payload)
        elif pkt_type == PKT_CO2:
            self.update_co2(payload)

    def pop_record_samples(self):
        if not self._record_samples:
            return []
        items = self._record_samples
        self._record_samples = []
        return items

    @property
    def vo2_max(self) -> float:
        return self._vo2_roll_max

    @property
    def kcal_total(self) -> float:
        return self._kcal_total

    @property
    def vo2_roll(self) -> Optional[float]:
        return self._last_vo2_roll


class TimeAxisItem(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        labels = []
        for v in values:
            if not np.isfinite(v):
                labels.append("")
                continue
            total = int(round(v))
            minutes = total // 60
            seconds = total % 60
            labels.append(f"{minutes}:{seconds:02d}")
        return labels


class Recorder:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.file = None
        self.writer = None
        self.path = None

    def start(self, config: StreamConfig) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = self.base_dir / f"vo2_recording_{timestamp}.csv"
        self.file = self.path.open("w", newline="")
        self.writer = csv.writer(self.file)
        # Config summary as commented lines
        self.file.write("# SpiroVO2 recording\n")
        self.file.write(
            f"# sample_rate_hz={config.sample_rate_hz},"
            f" area_1={config.area_1}, area_2={config.area_2},"
            f" correction={config.correction}, temp_c={config.temp_c},"
            f" pres_pa={config.pres_pa}, fi_o2={config.fi_o2},"
            f" pressure_scale={config.pressure_scale}, pressure_sign={config.pressure_sign}\n"
        )
        self.writer.writerow(
            [
                "t_s",
                "sample_idx",
                "pressure_pa",
                "flow_l_s",
                "breath_vol_l",
                "o2_pct",
                "co2_pct",
                "vo2_ml_kg_min",
                "vo2_roll_ml_kg_min",
                "vo2_roll_max_ml_kg_min",
                "kcal_total",
            ]
        )
        self.file.flush()
        return self.path

    def stop(self) -> None:
        if self.file:
            self.file.flush()
            self.file.close()
        self.file = None
        self.writer = None
        self.path = None

    def is_recording(self) -> bool:
        return self.writer is not None

    def write_samples(self, samples) -> None:
        if not self.writer or not samples:
            return
        for row in samples:
            self.writer.writerow(row)
        self.file.flush()


class PlotWindow(QtWidgets.QWidget):
    def __init__(self, processor: Vo2Processor, recorder: Recorder, status_cb):
        super().__init__()
        self.processor = processor
        self.recorder = recorder
        self._status_cb = status_cb
        self._on_close = None
        self._build_ui()

    def _build_ui(self):
        self.setWindowTitle("VO2 BLE Stream")
        layout = QtWidgets.QVBoxLayout(self)

        self.status_label = QtWidgets.QLabel("Waiting for data...")
        layout.addWidget(self.status_label)

        stats = QtWidgets.QHBoxLayout()
        self.vo2max_label = QtWidgets.QLabel("VO2Max (15s avg): --")
        self.kcal_label = QtWidgets.QLabel("kcal: --")
        stats.addWidget(self.vo2max_label)
        stats.addStretch(1)
        stats.addWidget(self.kcal_label)
        layout.addLayout(stats)

        controls = QtWidgets.QHBoxLayout()
        self.record_button = QtWidgets.QPushButton("Start Recording")
        self.record_button.clicked.connect(self.toggle_recording)
        controls.addWidget(self.record_button)

        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItems(["Dark", "Light"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        controls.addStretch(1)
        controls.addWidget(QtWidgets.QLabel("Theme:"))
        controls.addWidget(self.theme_combo)
        layout.addLayout(controls)

        self.pressure_plot = pg.PlotWidget(
            title="Pressure (Pa)",
            axisItems={"bottom": TimeAxisItem(orientation="bottom")},
        )
        self.pressure_plot.showGrid(x=True, y=True, alpha=0.3)
        self.pressure_curve = self.pressure_plot.plot(pen=pg.mkPen("#40c4ff", width=2))
        layout.addWidget(self.pressure_plot)

        self.breath_plot = pg.PlotWidget(
            title="Breath Volume (L)",
            axisItems={"bottom": TimeAxisItem(orientation="bottom")},
        )
        self.breath_plot.showGrid(x=True, y=True, alpha=0.3)
        self.breath_scatter = pg.ScatterPlotItem(size=8, brush=pg.mkBrush("#ffab40"))
        self.breath_plot.addItem(self.breath_scatter)
        self.breath_plot.setXLink(self.pressure_plot)
        layout.addWidget(self.breath_plot)

        self.gas_plot = pg.PlotWidget(
            title="O2 / CO2 (%)",
            axisItems={"bottom": TimeAxisItem(orientation="bottom")},
        )
        self.gas_plot.showGrid(x=True, y=True, alpha=0.3)
        self.o2_curve = self.gas_plot.plot(pen=pg.mkPen("#00e676", width=2), name="O2")
        self.co2_curve = self.gas_plot.plot(pen=pg.mkPen("#ff5252", width=2), name="CO2")
        self.gas_plot.addLegend()
        layout.addWidget(self.gas_plot)

        self.vo2_plot = pg.PlotWidget(
            title="VO2 (ml/kg/min)",
            axisItems={"bottom": TimeAxisItem(orientation="bottom")},
        )
        self.vo2_plot.showGrid(x=True, y=True, alpha=0.3)
        self.vo2_curve = self.vo2_plot.plot(pen=pg.mkPen("#cddc39", width=2))
        layout.addWidget(self.vo2_plot)

        self.apply_theme("Dark")

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def toggle_recording(self) -> None:
        if self.recorder.is_recording():
            self.recorder.stop()
            self.record_button.setText("Start Recording")
            self._status_cb("Recording stopped.")
            return
        path = self.recorder.start(self.processor.config)
        self.record_button.setText("Stop Recording")
        self._status_cb(f"Recording to {path}")

    def apply_theme(self, theme: str) -> None:
        if theme == "Light":
            bg = "#f6f6f6"
            fg = "#202020"
        else:
            bg = "#121212"
            fg = "#e0e0e0"
        self.setStyleSheet(f"QWidget {{ background-color: {bg}; color: {fg}; }}")
        for plot in [self.pressure_plot, self.breath_plot, self.gas_plot, self.vo2_plot]:
            plot.setBackground(bg)
            for axis_name in ("left", "bottom"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(fg))
                axis.setTextPen(pg.mkPen(fg))

    def refresh(self) -> None:
        p = self.processor
        if p.pressure_times:
            self.pressure_curve.setData(list(p.pressure_times), list(p.pressure_vals))
        if p.breath_times:
            points = [
                {"pos": (t, v)} for t, v in zip(p.breath_times, p.breath_vols)
            ]
            self.breath_scatter.setData(points)
        if p.o2_times:
            self.o2_curve.setData(list(p.o2_times), list(p.o2_vals))
        if p.co2_times:
            self.co2_curve.setData(list(p.co2_times), list(p.co2_vals))
        if p.vo2_times:
            self.vo2_curve.setData(list(p.vo2_times), list(p.vo2_vals))
        roll = p.vo2_roll
        roll_text = "--" if roll is None else f"{roll:.1f}"
        self.vo2max_label.setText(
            f"VO2Max ({p.vo2_window_sec:.0f}s avg): {p.vo2_max:.1f} (current {roll_text})"
        )
        self.kcal_label.setText(f"kcal: {p.kcal_total:.1f}")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._on_close:
            self._on_close()
        super().closeEvent(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BLE VO2 raw stream plotter")
    parser.add_argument("--device-name", default="SpiroVO2-RAW")
    parser.add_argument("--weight-kg", type=float, default=None)
    parser.add_argument("--vo2-window-sec", type=float, default=15.0)
    parser.add_argument("--pressure-deadband-pa", type=float, default=0.1)
    parser.add_argument("--flow-start-l-s", type=float, default=0.3)
    parser.add_argument("--flow-end-l-s", type=float, default=0.15)
    parser.add_argument("--min-breath-l", type=float, default=0.1)
    parser.add_argument("--min-breath-s", type=float, default=0.3)
    parser.add_argument("--breath-start-hold-ms", type=float, default=50.0)
    parser.add_argument("--breath-end-hold-ms", type=float, default=150.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    weight = args.weight_kg
    if weight is None:
        try:
            weight = float(input("Weight (kg): "))
        except ValueError:
            print("Invalid weight.")
            return 1

    app = QtWidgets.QApplication([])
    queue: Queue = Queue()

    status_holder = {"text": "Starting..."}

    def status_cb(text: str):
        status_holder["text"] = text

    base_dir = Path(__file__).resolve().parents[2] / "Recordings"
    recorder = Recorder(base_dir=base_dir)
    processor = Vo2Processor(
        weight_kg=weight,
        vo2_window_sec=args.vo2_window_sec,
        pressure_deadband_pa=args.pressure_deadband_pa,
        flow_start_l_s=args.flow_start_l_s,
        flow_end_l_s=args.flow_end_l_s,
        min_breath_l=args.min_breath_l,
        min_breath_s=args.min_breath_s,
        start_hold_s=args.breath_start_hold_ms / 1000.0,
        end_hold_s=args.breath_end_hold_ms / 1000.0,
    )
    window = PlotWindow(processor, recorder, status_cb)
    window.resize(900, 800)
    window.show()

    worker = BleWorker(args.device_name, queue, status_cb)
    worker.start()

    timer = QtCore.QTimer()
    timer.setInterval(50)

    def pump_queue():
        window.set_status(status_holder["text"])
        while True:
            try:
                payload = queue.get_nowait()
            except Empty:
                break
            processor.handle_packet(payload)
        if recorder.is_recording():
            samples = processor.pop_record_samples()
            if samples:
                recorder.write_samples(samples)
        window.refresh()

    timer.timeout.connect(pump_queue)
    timer.start()

    def cleanup():
        worker.stop()

    window._on_close = cleanup
    app.aboutToQuit.connect(cleanup)

    exit_code = app.exec_()
    worker.stop()
    worker.join(timeout=2.0)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
