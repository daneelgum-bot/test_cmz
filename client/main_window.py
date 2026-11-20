#main_window.py
import sys
import struct
import json
import os
import time
import threading
import numpy as np
from datetime import datetime, timedelta
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QSlider, QCheckBox, QLabel, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox,
    QFrame, QListWidget, QListWidgetItem, QMessageBox, QInputDialog, QDialog,
    QTableWidget, QTableWidgetItem, QDateEdit, QTimeEdit, QGroupBox, QDateTimeEdit,
    QToolTip, QFileDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QDate, QTime, QMutex, QDateTime
from PyQt5.QtGui import QColor, QBrush
import pyqtgraph as pg
from pyqtgraph import DateAxisItem
from server_client import ServerClient

BUF_SIZE = 2048
SAMPLING_RATE = 1 / 0.000313
FREQ_AXIS = np.linspace(0, SAMPLING_RATE / 2, BUF_SIZE // 2)

# ==================== Вспомогательные классы ====================

class AlarmIndicator(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedSize(30, 30)
        self.setFrameShape(QFrame.Box)
        self.alarm_active = False
        self.blink = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._toggle)
        self._update_style()

    def set_alarm(self, active):
        if active and not self.alarm_active:
            self.alarm_active = True
            self.timer.start(500)
        elif not active and self.alarm_active:
            self.alarm_active = False
            self.timer.stop()
            self.blink = False
            self._update_style()

    def _toggle(self):
        self.blink = not self.blink
        self._update_style()

    def _update_style(self):
        if self.alarm_active and self.blink:
            color = QColor(255, 0, 0)
        elif self.alarm_active:
            color = QColor(50, 0, 0)
        else:
            color = QColor(0, 50, 0)
        self.setStyleSheet(f"background-color: rgb({color.red()},{color.green()},{color.blue()});")

class AlarmRegionItem(pg.LinearRegionItem):
    def __init__(self, cause, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cause = cause
        self.setAcceptHoverEvents(True)

    def hoverEvent(self, ev):
        if ev.isEnter():
            QToolTip.showText(ev.screenPos().toPoint(), self.cause)
        else:
            QToolTip.hideText()

class SensorTab(QWidget):
    def __init__(self, sensor_id, server_client, get_current_device_func):
        super().__init__()
        self.sensor_id = sensor_id
        self.server_client = server_client
        self.get_current_device = get_current_device_func

        self.accel_data = {k: np.zeros(BUF_SIZE) for k in range(4)}
        self.spectrum_data = {k: np.zeros(BUF_SIZE//2) for k in range(4)}
        self.rms_accel = {k: 0.0 for k in range(4)}
        self.rms_speed = {k: 0.0 for k in range(4)}
        self.crest_factor = {k: 0.0 for k in range(4)}
        self.current_axis = 0

        pg.setConfigOptions(antialias=True)

        self.graph_accel = pg.PlotWidget(title=f'Датчик {sensor_id} – Ускорение')
        self.graph_accel.setLabel('left', 'м/с²')
        self.graph_accel.setLabel('bottom', 'Отсчёт')
        self.graph_accel.showGrid(x=True, y=True, alpha=0.5)
        self.graph_accel.setXRange(0, BUF_SIZE)
        self.graph_accel.setLimits(xMin=0, xMax=BUF_SIZE)
        self.graph_accel.enableAutoRange(False)
        self.curve_accel = self.graph_accel.plot(pen='c')

        self.graph_spec = pg.PlotWidget(title=f'Датчик {sensor_id} – Спектр скорости')
        self.graph_spec.setLabel('left', 'мм/с')
        self.graph_spec.setLabel('bottom', 'Частота (Гц)')
        self.graph_spec.showGrid(x=True, y=True, alpha=0.5)
        self.graph_spec.setXRange(0, 1000)
        self.graph_spec.setLimits(xMin=0, yMin=0)
        self.graph_spec.enableAutoRange(False)
        self.curve_spec = self.graph_spec.plot(pen='lime')

        self.rms_label = QLabel("RMS уск: -- | RMS скор: -- | Пик-фактор: --")
        self.rms_label.setStyleSheet("font-weight: bold; color: white; background-color: black; padding: 4px;")
        self.peak_label = QLabel("Пик: -- мм/с @ -- Гц")
        self.peak_label.setStyleSheet("font-weight: bold; color: white; background-color: #333; padding: 4px;")

        self.axis_combo = QComboBox()
        self.axis_combo.addItems(["Модуль", "X", "Y", "Z"])
        self.axis_combo.currentIndexChanged.connect(self.on_axis_changed)

        control_layout = QHBoxLayout()
        control_layout.addWidget(QLabel("Ось:"))
        control_layout.addWidget(self.axis_combo)
        control_layout.addStretch()
        control_layout.addWidget(self.rms_label)
        control_layout.addWidget(self.peak_label)

        self.accel_scale_slider = QSlider(Qt.Horizontal)
        self.accel_scale_slider.setRange(10, 500)
        self.accel_scale_slider.setValue(100)
        self.accel_scale_slider.valueChanged.connect(self.update_accel_scale)

        self.spec_scale_slider = QSlider(Qt.Horizontal)
        self.spec_scale_slider.setRange(10, 500)
        self.spec_scale_slider.setValue(100)
        self.spec_scale_slider.valueChanged.connect(self.update_spec_scale)

        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("Масштаб уск.:"))
        scale_layout.addWidget(self.accel_scale_slider)
        scale_layout.addWidget(QLabel("Масштаб спектра:"))
        scale_layout.addWidget(self.spec_scale_slider)

        layout = QVBoxLayout()
        layout.addWidget(self.graph_accel, stretch=2)
        layout.addWidget(self.graph_spec, stretch=1)
        layout.addLayout(control_layout)
        layout.addLayout(scale_layout)
        self.setLayout(layout)

        self.graph_accel.setYRange(-1, 1)
        self.graph_spec.setYRange(0, 1)

    def on_axis_changed(self, idx):
        self.current_axis = idx
        dev = self.get_current_device()
        if dev is not None:
            self.server_client.send_command(dev, f"AXIS {self.sensor_id} {idx}")
        self.update_graphs()

    def update_rms(self, dev_id, sensor_id, axis, rms_speed, rms_accel, crest_factor, alarm):
        if dev_id != self.get_current_device() or sensor_id != self.sensor_id or axis not in (0,1,2,3):
            return
        self.rms_speed[axis] = rms_speed
        self.rms_accel[axis] = rms_accel
        self.crest_factor[axis] = crest_factor
        if axis == self.current_axis:
            self.rms_label.setText(
                f"RMS уск: {rms_accel:.4f} м/с² | RMS скор: {rms_speed:.3f} мм/с | Пик-фактор: {crest_factor:.2f}"
            )

    def update_spectrum(self, dev_id, sensor_id, axis, spectrum):
        if dev_id != self.get_current_device() or sensor_id != self.sensor_id or axis not in (0,1,2,3):
            return
        self.spectrum_data[axis] = spectrum
        if axis == self.current_axis:
            self.update_spectrum_graph()

    def update_waveform(self, dev_id, sensor_id, axis, accel):
        if dev_id != self.get_current_device() or sensor_id != self.sensor_id or axis not in (0,1,2,3):
            return
        self.accel_data[axis] = accel
        if axis == self.current_axis:
            self.curve_accel.setData(accel)

    def update_spectrum_graph(self):
        axis = self.current_axis
        spec = self.spectrum_data[axis]
        if len(spec) > 1:
            freq_axis = np.linspace(0, SAMPLING_RATE/2, len(spec))
            self.curve_spec.setData(freq_axis[1:], spec[1:])
            peak_idx = np.argmax(spec[1:]) + 1
            self.peak_label.setText(f"Пик: {spec[peak_idx]:.2f} мм/с @ {freq_axis[peak_idx]:.1f} Гц")

    def update_graphs(self):
        axis = self.current_axis
        self.curve_accel.setData(self.accel_data[axis])
        self.update_spectrum_graph()
        self.rms_label.setText(
            f"RMS уск: {self.rms_accel[axis]:.4f} м/с² | RMS скор: {self.rms_speed[axis]:.3f} мм/с | Пик-фактор: {self.crest_factor[axis]:.2f}"
        )

    def update_accel_scale(self):
        data = self.accel_data[self.current_axis]
        if len(data) > 0:
            max_val = max(np.max(np.abs(data)), 1e-9)
            factor = self.accel_scale_slider.value() / 100.0
            self.graph_accel.setYRange(-max_val * factor, max_val * factor)

    def update_spec_scale(self):
        data = self.spectrum_data[self.current_axis]
        if len(data) > 0:
            max_val = max(np.max(data[1:]), 1e-9)
            factor = self.spec_scale_slider.value() / 100.0
            self.graph_spec.setYRange(0, max_val * factor)

    def resize_arrays(self):
        for k in range(4):
            self.accel_data[k] = np.zeros(BUF_SIZE)
            self.spectrum_data[k] = np.zeros(BUF_SIZE // 2)
            self.rms_accel[k] = 0.0
            self.rms_speed[k] = 0.0
            self.crest_factor[k] = 0.0
        self.curve_accel.setData([])
        self.curve_spec.setData([])
        self.rms_label.setText("RMS уск: -- | RMS скор: -- | Пик-фактор: --")
        self.peak_label.setText("Пик: -- мм/с @ -- Гц")


# ==================== HistoricalMonitorWidget ====================

class HistoricalMonitorWidget(QWidget):
    def __init__(self, sensor_id, server_client, get_current_device_func,
                 get_rms_alarm_threshold=None, get_crest_alarm_threshold=None,
                 get_last_alarm_info=None, open_alarm_journal_callback=None):
        super().__init__()
        self.sensor_id = sensor_id
        self.server_client = server_client
        self.get_current_device = get_current_device_func
        self.get_rms_alarm_threshold = get_rms_alarm_threshold
        self.get_crest_alarm_threshold = get_crest_alarm_threshold
        self.get_last_alarm_info = get_last_alarm_info
        self.open_alarm_journal_callback = open_alarm_journal_callback
        self.auto_scale_once = True
        self.last_update_time = 0

        self.live_data = []
        self.cycle_events = []
        self.cycle_active = False
        self.cycle_start_time = None
        self.current_interval_start = None
        self.current_interval_end = None
        self.large_interval = False
        self.is_absolute_interval = False   # True, если выбран абсолютный интервал (календарь)

        # Элементы управления
        self.axis_combo = QComboBox()
        self.axis_combo.addItems(["Модуль", "X", "Y", "Z"])
        self.axis_combo.currentIndexChanged.connect(self.on_axis_changed)

        # Предустановленные интервалы (используются, когда оба чекбокса выключены)
        self.interval_combo = QComboBox()
        self.interval_combo.addItem("15 минут", 15*60)
        self.interval_combo.addItem("1 час", 60*60)
        self.interval_combo.addItem("6 часов", 6*60*60)
        self.interval_combo.addItem("12 часов", 12*60*60)
        self.interval_combo.addItem("24 часа", 24*60*60)
        self.interval_combo.addItem("7 дней", 7*24*60*60)
        self.interval_combo.setCurrentIndex(4)
        self.interval_combo.currentIndexChanged.connect(self.on_interval_changed)

        # Относительный интервал
        self.use_relative_interval = QCheckBox("Относительный")
        self.use_relative_interval.stateChanged.connect(self.on_relative_checkbox_changed)
        self.relative_value = QSpinBox()
        self.relative_value.setRange(1, 100000)
        self.relative_value.setValue(15)
        self.relative_unit = QComboBox()
        self.relative_unit.addItem("минут", 60)
        self.relative_unit.addItem("часов", 3600)
        self.relative_unit.addItem("дней", 86400)
        self.relative_apply_btn = QPushButton("Задать")
        self.relative_apply_btn.clicked.connect(self.apply_relative_interval)

        # Абсолютный интервал
        self.use_absolute_interval = QCheckBox("Абсолютный")
        self.use_absolute_interval.stateChanged.connect(self.on_absolute_checkbox_changed)
        self.date_from_edit = QDateTimeEdit(QDateTime.currentDateTime().addSecs(-3600))
        self.date_from_edit.setCalendarPopup(True)
        self.date_from_edit.setDisplayFormat("dd.MM.yyyy HH:mm:ss")
        self.date_to_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.date_to_edit.setCalendarPopup(True)
        self.date_to_edit.setDisplayFormat("dd.MM.yyyy HH:mm:ss")
        self.absolute_apply_btn = QPushButton("Показать")
        self.absolute_apply_btn.clicked.connect(self.apply_absolute_interval)

        # Начальная видимость
        self.relative_apply_btn.setVisible(False)
        self.relative_value.setVisible(False)
        self.relative_unit.setVisible(False)
        self.absolute_apply_btn.setVisible(False)
        self.date_from_edit.setVisible(False)
        self.date_to_edit.setVisible(False)

        # ... остальные элементы UI (current_speed_label и т.д.) ...
        self.current_speed_label = QLabel("RMS скор: -- мм/с")
        self.current_accel_label = QLabel("RMS уск: -- м/с²")
        self.current_crest_label = QLabel("Пик-фактор: --")
        current_group = QGroupBox("Текущие значения")
        current_layout = QVBoxLayout()
        current_layout.addWidget(self.current_speed_label)
        current_layout.addWidget(self.current_accel_label)
        current_layout.addWidget(self.current_crest_label)
        current_group.setLayout(current_layout)

        self.cycle_total_label = QLabel("Время в цикле (за интервал): --")
        self.current_cycle_label = QLabel("Текущий цикл: --")
        cycle_group = QGroupBox("Цикл станка")
        cycle_layout = QVBoxLayout()
        cycle_layout.addWidget(self.cycle_total_label)
        cycle_layout.addWidget(self.current_cycle_label)
        cycle_group.setLayout(cycle_layout)

        self.last_alarm_label1 = QLabel("Датчик 1: --")
        self.last_alarm_label1.setWordWrap(True)
        self.show_alarm_btn1 = QPushButton("Показать в журнале")
        self.show_alarm_btn1.clicked.connect(lambda: self.on_show_alarm(1))
        self.last_alarm_label2 = QLabel("Датчик 2: --")
        self.last_alarm_label2.setWordWrap(True)
        self.show_alarm_btn2 = QPushButton("Показать в журнале")
        self.show_alarm_btn2.clicked.connect(lambda: self.on_show_alarm(2))

        alarm_group = QGroupBox("Последняя авария")
        alarm_layout = QVBoxLayout()
        alarm_layout.addWidget(self.last_alarm_label1)
        alarm_layout.addWidget(self.show_alarm_btn1)
        alarm_layout.addSpacing(10)
        alarm_layout.addWidget(self.last_alarm_label2)
        alarm_layout.addWidget(self.show_alarm_btn2)
        alarm_group.setLayout(alarm_layout)

        # Графики
        self.speed_plot = pg.PlotWidget(title="СКЗ виброскорости")
        self.speed_plot.setLabel('left', 'мм/с')
        self.speed_plot.showGrid(x=True, y=True, alpha=0.5)
        self.speed_plot.setAxisItems({'bottom': DateAxisItem(orientation='bottom')})
        self.speed_curve = self.speed_plot.plot(pen='y')
        self.speed_curve.setDownsampling(auto=True, method='peak')
        self.speed_plot.getViewBox().setLimits(yMin=0)

        self.accel_plot = pg.PlotWidget(title="СКЗ виброускорения")
        self.accel_plot.setLabel('left', 'м/с²')
        self.accel_plot.showGrid(x=True, y=True, alpha=0.5)
        self.accel_plot.setAxisItems({'bottom': DateAxisItem(orientation='bottom')})
        self.accel_curve = self.accel_plot.plot(pen='c')
        self.accel_curve.setDownsampling(auto=True, method='peak')
        self.accel_plot.getViewBox().setLimits(yMin=0)

        self.accel_plot.setXLink(self.speed_plot)

        self.alarm_regions_speed = []
        self.alarm_regions_accel = []

        # Компоновка
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Ось:"))
        controls_layout.addWidget(self.axis_combo)
        controls_layout.addWidget(QLabel("Интервал:"))
        controls_layout.addWidget(self.interval_combo)
        controls_layout.addWidget(self.use_relative_interval)
        controls_layout.addWidget(self.relative_value)
        controls_layout.addWidget(self.relative_unit)
        controls_layout.addWidget(self.relative_apply_btn)
        controls_layout.addWidget(self.use_absolute_interval)
        controls_layout.addWidget(QLabel("С:"))
        controls_layout.addWidget(self.date_from_edit)
        controls_layout.addWidget(QLabel("По:"))
        controls_layout.addWidget(self.date_to_edit)
        controls_layout.addWidget(self.absolute_apply_btn)
        controls_layout.addStretch()

        right_panel = QVBoxLayout()
        right_panel.addWidget(current_group)
        right_panel.addWidget(cycle_group)
        right_panel.addWidget(alarm_group)
        right_panel.addStretch()

        graphs_layout = QVBoxLayout()
        graphs_layout.addWidget(self.speed_plot, stretch=1)
        graphs_layout.addWidget(self.accel_plot, stretch=1)

        main_layout = QHBoxLayout()
        main_layout.addLayout(graphs_layout, stretch=3)
        main_layout.addLayout(right_panel, stretch=1)

        outer_layout = QVBoxLayout()
        outer_layout.addLayout(controls_layout)
        outer_layout.addLayout(main_layout)
        self.setLayout(outer_layout)

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_plots)
        self.update_timer.start(1000)

        self.cycle_label_timer = QTimer(self)
        self.cycle_label_timer.timeout.connect(self.update_current_cycle_label)
        self.cycle_label_timer.start(1000)

        self.server_client.history_rms_loaded.connect(self.on_history_rms_loaded)
        self.server_client.cycle_events_loaded.connect(self.on_cycle_events_loaded)
        self.server_client.current_cycle_start_loaded.connect(self.on_current_cycle_start_loaded)

        self.load_initial_data()
        self.update_plots()

    # === Обработчики чекбоксов ===
    def on_relative_checkbox_changed(self, state):
        if state == Qt.Checked:
            self.use_absolute_interval.setChecked(False)
            self.relative_value.setVisible(True)
            self.relative_unit.setVisible(True)
            self.relative_apply_btn.setVisible(True)
            self.interval_combo.setVisible(False)
        else:
            self.relative_value.setVisible(False)
            self.relative_unit.setVisible(False)
            self.relative_apply_btn.setVisible(False)
            self.interval_combo.setVisible(True)
            self.load_initial_data()

    def on_absolute_checkbox_changed(self, state):
        if state == Qt.Checked:
            self.use_relative_interval.setChecked(False)
            self.date_from_edit.setVisible(True)
            self.date_to_edit.setVisible(True)
            self.absolute_apply_btn.setVisible(True)
            self.interval_combo.setVisible(False)
        else:
            self.date_from_edit.setVisible(False)
            self.date_to_edit.setVisible(False)
            self.absolute_apply_btn.setVisible(False)
            self.interval_combo.setVisible(True)
            self.load_initial_data()

    def apply_relative_interval(self):
        self.load_initial_data()

    def apply_absolute_interval(self):
        self.load_initial_data()

    def _get_interval(self):
        if self.use_absolute_interval.isChecked():
            dt_from = self.date_from_edit.dateTime().toPyDateTime()
            dt_to = self.date_to_edit.dateTime().toPyDateTime()
            start_ts = dt_from.timestamp()
            end_ts = dt_to.timestamp()
            if start_ts > end_ts:
                start_ts, end_ts = end_ts, start_ts
            return start_ts, end_ts, True
        elif self.use_relative_interval.isChecked():
            value = self.relative_value.value()
            unit_seconds = self.relative_unit.currentData()
            interval_sec = value * unit_seconds
            end_ts = time.time()
            start_ts = end_ts - interval_sec
            return start_ts, end_ts, False
        else:
            interval_sec = self.interval_combo.currentData()
            end_ts = time.time()
            start_ts = end_ts - interval_sec
            return start_ts, end_ts, False

    def load_initial_data(self):
        dev = self.get_current_device()
        if dev is None:
            return
        start_ts, end_ts, is_absolute = self._get_interval()
        self.current_interval_start = start_ts
        self.current_interval_end = end_ts
        self.is_absolute_interval = is_absolute
        self.live_data = []

        # Определяем, большой ли интервал (больше 2 часов)
        self.large_interval = (end_ts - start_ts) > 2 * 60 * 60

        # Настройка осей
        if is_absolute:
            # Абсолютный интервал: ось X фиксирована, но можно двигать вручную
            self.speed_plot.setLimits(xMin=start_ts, xMax=end_ts, yMin=0)
            self.accel_plot.setLimits(xMin=start_ts, xMax=end_ts, yMin=0)
            self.speed_plot.setMouseEnabled(x=True, y=True)
            self.accel_plot.setMouseEnabled(x=True, y=True)
            self.speed_plot.disableAutoRange('x')
            self.accel_plot.disableAutoRange('x')
            self.speed_plot.setXRange(start_ts, end_ts, padding=0)
            self.accel_plot.setXRange(start_ts, end_ts, padding=0)
        else:
            # Относительный или предустановленный: ось X автомасштабируется
            self.speed_plot.setLimits(yMin=0)
            self.accel_plot.setLimits(yMin=0)
            self.speed_plot.setMouseEnabled(x=True, y=True)
            self.accel_plot.setMouseEnabled(x=True, y=True)
            self.speed_plot.enableAutoRange('x', True)
            self.accel_plot.enableAutoRange('x', True)

        self.server_client.get_history_rms_async(dev, self.sensor_id,
                                                 self.axis_combo.currentIndex(),
                                                 start_ts, end_ts)
        self.server_client.get_cycle_events_async(dev, start_ts, end_ts)
        self.server_client.get_current_cycle_start_async(dev)

    def on_history_rms_loaded(self, dev_id, sensor_id, axis, data):
        if dev_id != self.get_current_device() or sensor_id != self.sensor_id or axis != self.axis_combo.currentIndex():
            return
        self.live_data = data
        self._redraw_history(data)

    def add_live_rms(self, axis, rms_speed, rms_accel, crest, alarm, timestamp):
        if self.is_absolute_interval or self.large_interval:
            return
        if axis != self.axis_combo.currentIndex():
            return
        point = {
            'ts': timestamp,
            'rms_speed': rms_speed,
            'rms_accel': rms_accel,
            'crest': crest,
            'alarm': alarm
        }
        self.live_data.append(point)
        if self.current_interval_start is not None:
            self.live_data = [p for p in self.live_data if p['ts'] >= self.current_interval_start]

    def _redraw_history(self, data):
        if not data:
            self.speed_curve.clear()
            self.accel_curve.clear()
            self.current_speed_label.setText("RMS скор: -- мм/с")
            self.current_accel_label.setText("RMS уск: -- м/с²")
            self.current_crest_label.setText("Пик-фактор: --")
            return

        display_data = data
        if len(data) > 5000:
            step = len(data) // 5000
            display_data = data[::step]

        raw_ts = [p['ts'] for p in display_data]
        raw_speeds = [p['rms_speed'] for p in display_data]
        raw_accels = [p['rms_accel'] for p in display_data]
        raw_alarms = [p['alarm'] for p in display_data]

        alarm_intervals = []
        in_alarm = False
        start_alarm_ts = None
        for ts, alarm in zip(raw_ts, raw_alarms):
            if alarm and not in_alarm:
                in_alarm = True
                start_alarm_ts = ts
            elif not alarm and in_alarm:
                in_alarm = False
                alarm_intervals.append((start_alarm_ts, ts))
        if in_alarm:
            alarm_intervals.append((start_alarm_ts, time.time()))

        GAP_THRESHOLD = 5.0
        timestamps = []
        speeds = []
        accels = []
        prev_ts = None
        for ts, sp, acc in zip(raw_ts, raw_speeds, raw_accels):
            if prev_ts is not None and (ts - prev_ts) > GAP_THRESHOLD:
                timestamps.append(np.nan)
                speeds.append(np.nan)
                accels.append(np.nan)
            timestamps.append(ts)
            speeds.append(sp)
            accels.append(acc)
            prev_ts = ts

        self.speed_curve.setData(timestamps, speeds, connect='finite')
        self.accel_curve.setData(timestamps, accels, connect='finite')

        if self.auto_scale_once:
            self.auto_scale_once = False

        last = display_data[-1]
        self.update_current_values(last['rms_speed'], last['rms_accel'], last['crest'])

        for region in self.alarm_regions_speed:
            self.speed_plot.removeItem(region)
        for region in self.alarm_regions_accel:
            self.accel_plot.removeItem(region)
        self.alarm_regions_speed.clear()
        self.alarm_regions_accel.clear()

        rms_thr = self.get_rms_alarm_threshold() if self.get_rms_alarm_threshold else None
        crest_thr = self.get_crest_alarm_threshold() if self.get_crest_alarm_threshold else None

        for s, e in alarm_intervals:
            cause = "Авария"
            for p in display_data:
                if s <= p['ts'] <= e and p['alarm']:
                    reasons = []
                    if rms_thr is not None and p['rms_speed'] > rms_thr:
                        reasons.append("превышение СКЗ виброскорости")
                    if crest_thr is not None and p['crest'] > crest_thr:
                        reasons.append("превышение пик-фактора")
                    if reasons:
                        cause = "Причина: " + ", ".join(reasons)
                    else:
                        cause = "Причина: неизвестна"
                    break

            region_speed = AlarmRegionItem(cause, values=[s, e],
                                           brush=pg.mkBrush(255, 0, 0, 50),
                                           pen=pg.mkPen(None), movable=False)
            self.speed_plot.addItem(region_speed)
            self.alarm_regions_speed.append(region_speed)

            region_accel = AlarmRegionItem(cause, values=[s, e],
                                           brush=pg.mkBrush(255, 0, 0, 50),
                                           pen=pg.mkPen(None), movable=False)
            self.accel_plot.addItem(region_accel)
            self.alarm_regions_accel.append(region_accel)

    def set_cycle_status(self, status_str, timestamp):
        self.cycle_events.append((timestamp, status_str))
        if status_str == "cycle_on":
            self.cycle_active = True
            self.cycle_start_time = timestamp
        elif status_str == "cycle_off":
            self.cycle_active = False
            self.cycle_start_time = None
        self._recalculate_cycle_time()
        self.update_current_cycle_label()

    def _recalculate_cycle_time(self):
        if self.current_interval_start is None or self.current_interval_end is None:
            return
        total = 0.0
        cycle_start = None
        if self.cycle_active and self.cycle_start_time is not None and self.cycle_start_time < self.current_interval_start:
            cycle_start = self.current_interval_start
        else:
            cycle_start = None
        for ts, st in self.cycle_events:
            if st == "cycle_on":
                cycle_start = ts
            elif st == "cycle_off" and cycle_start is not None:
                total += ts - cycle_start
                cycle_start = None
        if cycle_start is not None:
            if self.cycle_active and self.cycle_start_time is not None and self.cycle_start_time >= self.current_interval_start:
                total += time.time() - cycle_start
            else:
                total += self.current_interval_end - cycle_start
        self.cycle_total_label.setText(f"Время в цикле (за интервал): {self.format_duration(total)}")

    def update_current_cycle_label(self):
        if self.cycle_active and self.cycle_start_time is not None:
            duration = time.time() - self.cycle_start_time
            self.current_cycle_label.setText(f"Текущий цикл: {self.format_duration(duration)}")
        else:
            self.current_cycle_label.setText("Текущий цикл: не активен")

    def on_cycle_events_loaded(self, dev_id, events):
        if dev_id != self.get_current_device():
            return
        self.cycle_events = [(e['ts'], e['status']) for e in events]
        self._recalculate_cycle_time()

    def on_current_cycle_start_loaded(self, dev_id, start_ts):
        if dev_id != self.get_current_device():
            return
        if start_ts is not None:
            self.cycle_active = True
            self.cycle_start_time = start_ts
        else:
            self.cycle_active = False
        self.update_current_cycle_label()
        self._recalculate_cycle_time()

    def update_plots(self):
        if not self.server_client.running:
            self.speed_curve.clear()
            self.accel_curve.clear()
            self.current_speed_label.setText("RMS скор: -- мм/с")
            self.current_accel_label.setText("RMS уск: -- м/с²")
            self.current_crest_label.setText("Пик-фактор: --")
            self.cycle_total_label.setText("Время в цикле (за интервал): --")
            self.current_cycle_label.setText("Текущий цикл: --")
            return

        now = time.time()
        if now - self.last_update_time < 1.0:
            return
        self.last_update_time = now

        if not self.is_absolute_interval and not self.large_interval:
            self._redraw_history(self.live_data)

        if self.get_last_alarm_info:
            info1 = self.get_last_alarm_info(1)
            info2 = self.get_last_alarm_info(2)
            self.last_alarm_label1.setText(f"Датчик 1:\n{info1}")
            self.last_alarm_label2.setText(f"Датчик 2:\n{info2}")

    def on_interval_changed(self):
        self.auto_scale_once = True
        self.load_initial_data()
        self.update_plots()

    def on_axis_changed(self):
        self.auto_scale_once = True
        self.load_initial_data()
        self.update_plots()

    def on_show_alarm(self, sensor_id):
        if self.open_alarm_journal_callback:
            self.open_alarm_journal_callback(sensor_id)

    def format_duration(self, seconds):
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def update_current_values(self, rms_speed, rms_accel, crest):
        self.current_speed_label.setText(f"RMS скор: {rms_speed:.3f} мм/с")
        self.current_accel_label.setText(f"RMS уск: {rms_accel:.4f} м/с²")
        self.current_crest_label.setText(f"Пик-фактор: {crest:.2f}")

# ==================== CompareWindow ====================
class CompareWindow(QDialog):
    def __init__(self, test_a, test_b, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сравнение спектров")
        self.resize(1000, 750)
        self.test_a = test_a
        self.test_b = test_b

        layout = QVBoxLayout()
        self.setLayout(layout)

        common_sensors = sorted(set(test_a['sensors'].keys()) & set(test_b['sensors'].keys()))
        if not common_sensors:
            QMessageBox.critical(self, "Ошибка", "Нет общих датчиков для сравнения.")
            self.close()
            return
        self.sensor_combo = QComboBox()
        self.sensor_combo.addItems([f"Датчик {s}" for s in common_sensors])
        self.sensor_combo.currentIndexChanged.connect(self.update_plots)
        layout.addWidget(self.sensor_combo)

        self.plot1 = pg.PlotWidget(title="Первый спектр (type=2)")
        self.plot1.setLabel('left', 'мм/с')
        self.plot1.setLabel('bottom', 'Частота, Гц')
        self.plot1.showGrid(x=True, y=True, alpha=0.5)
        self.plot1.addLegend()
        self.curve1_a = self.plot1.plot(pen='cyan', name='Эталон')
        self.curve1_b = self.plot1.plot(pen='magenta', name='Тест')
        layout.addWidget(self.plot1)

        self.plot2 = pg.PlotWidget(title="Второй спектр (type=3)")
        self.plot2.setLabel('left', 'мм/с')
        self.plot2.setLabel('bottom', 'Частота, Гц')
        self.plot2.showGrid(x=True, y=True, alpha=0.5)
        self.plot2.addLegend()
        self.curve2_a = self.plot2.plot(pen='cyan', name='Эталон')
        self.curve2_b = self.plot2.plot(pen='magenta', name='Тест')
        layout.addWidget(self.plot2)

        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(10, 500)
        self.scale_slider.setValue(100)
        self.scale_slider.valueChanged.connect(self.apply_y_scale)
        layout.addWidget(QLabel("Масштаб Y (%)"))
        layout.addWidget(self.scale_slider)

        self.update_plots()

    def _get_spectrum(self, test_data, sid, spec_type):
        frames = test_data['sensors'].get(str(sid), [])
        for f in frames:
            if f.get('type') == spec_type and f.get('spectrum'):
                return np.array(f['spectrum'])
        return None

    def update_plots(self):
        sid_str = self.sensor_combo.currentText()
        if not sid_str:
            return
        sid = int(sid_str.split()[-1])

        spec1_a = self._get_spectrum(self.test_a, sid, 2)
        spec1_b = self._get_spectrum(self.test_b, sid, 2)
        spec2_a = self._get_spectrum(self.test_a, sid, 3)
        spec2_b = self._get_spectrum(self.test_b, sid, 3)

        freq_axis = np.linspace(0, SAMPLING_RATE/2, BUF_SIZE//2)

        if spec1_a is not None and len(spec1_a) > 1:
            self.curve1_a.setData(freq_axis[1:], spec1_a[1:])
        else:
            self.curve1_a.clear()
        if spec1_b is not None and len(spec1_b) > 1:
            self.curve1_b.setData(freq_axis[1:], spec1_b[1:])
        else:
            self.curve1_b.clear()

        if spec2_a is not None and len(spec2_a) > 1:
            self.curve2_a.setData(freq_axis[1:], spec2_a[1:])
        else:
            self.curve2_a.clear()
        if spec2_b is not None and len(spec2_b) > 1:
            self.curve2_b.setData(freq_axis[1:], spec2_b[1:])
        else:
            self.curve2_b.clear()

        self.apply_y_scale()

    def apply_y_scale(self):
        factor = self.scale_slider.value() / 100.0
        for plot in (self.plot1, self.plot2):
            max_val = 1e-9
            for item in plot.listDataItems():
                if item.yData is not None and len(item.yData) > 0:
                    m = np.max(item.yData[1:]) if len(item.yData) > 1 else 0
                    if m > max_val:
                        max_val = m
            plot.setYRange(0, max_val * factor)


# ==================== TestRecorder ====================
class TestRecorder:
    def __init__(self, device_id):
        self.device_id = device_id
        self.sensors = {1: [], 2: []}
        self.start_time = None
        self.end_time = None
        self.is_active = False
        self.name = ""
        self.is_reference = False
        self.total_alarms = 0

    def add_rms_frame(self, sensor_id, axis, rms_speed, rms_accel, crest, alarm, frame_id, frame_type):
        if not self.start_time:
            self.start_time = time.time()
        self.is_active = True
        if alarm:
            self.total_alarms += 1
        self.sensors[sensor_id].append({
            'type': frame_type,
            'timestamp': time.time(),
            'axis': axis,
            'rms_speed': rms_speed,
            'rms_accel': rms_accel,
            'crest_factor': crest,
            'alarm': alarm,
            'frame_id': frame_id,
            'spectrum': None
        })

    def add_spectrum_frame(self, sensor_id, spectrum, frame_id):
        for frame in reversed(self.sensors[sensor_id]):
            if frame['frame_id'] == frame_id:
                frame['spectrum'] = spectrum.tolist()
                break

    def finish(self):
        self.is_active = False
        self.end_time = time.time()


# ==================== MainWindow ====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Вибромонитор (клиент-сервер)")
        self.resize(1600, 1000)

        SERVER_URL = "ws://192.168.0.131:8000/ws"
        API_URL = "http://192.168.0.131:8000"

        self.server_client = ServerClient(SERVER_URL, API_URL)

        self.alarm_events = []
        self.alarm_detail_dlg = None
        self.alarm_detail_idx = -1
        self.alarm_detail_filtered = []
        self.device_statuses = {}

        self.rms_alarm_spin = QDoubleSpinBox()
        self.rms_alarm_spin.setRange(0.0, 100.0)
        self.rms_alarm_spin.setSingleStep(0.5)
        self.rms_alarm_spin.setValue(6.0)
        self.rms_alarm_spin.setDecimals(2)
        self.crest_alarm_spin = QDoubleSpinBox()
        self.crest_alarm_spin.setRange(0.0, 100.0)
        self.crest_alarm_spin.setSingleStep(0.5)
        self.crest_alarm_spin.setValue(5.0)
        self.crest_alarm_spin.setDecimals(2)
        self.alarm_indicator = AlarmIndicator()

        self.device_combo = QComboBox()
        self.device_combo.addItem("", 0)
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)

        self.start_btn = QPushButton("Подключиться к серверу")
        self.start_btn.clicked.connect(self.start_connection)
        self.stop_btn = QPushButton("Отключиться")
        self.stop_btn.clicked.connect(self.stop_connection)
        self.stop_btn.setEnabled(False)

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Устройство:"))
        top_layout.addWidget(self.device_combo)
        top_layout.addStretch()
        top_layout.addWidget(self.start_btn)
        top_layout.addWidget(self.stop_btn)

        self.main_tabs = QTabWidget()

        # История
        history_tab = QWidget()
        history_layout = QVBoxLayout()
        history_controls_layout = QHBoxLayout()
        self.clear_history_btn = QPushButton("Удалить историю")
        self.clear_history_btn.clicked.connect(self.clear_history_dialog)
        self.load_history_btn = QPushButton("Загрузить историю")
        self.load_history_btn.clicked.connect(self.load_history)
        history_controls_layout.addWidget(self.clear_history_btn)
        history_controls_layout.addWidget(self.load_history_btn)
        history_controls_layout.addStretch()

        history_tabs = QTabWidget()
        self.hist_widget1 = HistoricalMonitorWidget(
            1, self.server_client, self.get_current_device,
            lambda: self.rms_alarm_spin.value(),
            lambda: self.crest_alarm_spin.value(),
            lambda sid: self.get_last_alarm_info_for_sensor(sid),
            lambda sid: self.open_alarm_journal(sid)
        )
        self.hist_widget2 = HistoricalMonitorWidget(
            2, self.server_client, self.get_current_device,
            lambda: self.rms_alarm_spin.value(),
            lambda: self.crest_alarm_spin.value(),
            lambda sid: self.get_last_alarm_info_for_sensor(sid),
            lambda sid: self.open_alarm_journal(sid)
        )
        history_tabs.addTab(self.hist_widget1, "Датчик 1")
        history_tabs.addTab(self.hist_widget2, "Датчик 2")
        history_layout.addLayout(history_controls_layout)
        history_layout.addWidget(history_tabs)
        history_tab.setLayout(history_layout)
        self.main_tabs.addTab(history_tab, "История")

        # Реальное время
        realtime_tab = QWidget()
        realtime_layout = QVBoxLayout()
        realtime_controls_layout = QHBoxLayout()
        self.stream_btn = QPushButton("STREAM")
        self.stream_btn.clicked.connect(self.start_stream)
        self.stream_rms_btn = QPushButton("STREAM RMS")
        self.stream_rms_btn.clicked.connect(self.start_stream_rms)
        self.stop_btn_cmd = QPushButton("STOP")
        self.stop_btn_cmd.clicked.connect(self.stop_all)
        self.alarm_checkbox = QCheckBox("Отправка при аварии")
        self.alarm_checkbox.setChecked(True)
        self.alarm_checkbox.stateChanged.connect(self.toggle_alarm_stream)
        self.apply_alarm_params_btn = QPushButton("Применить")
        self.apply_alarm_params_btn.clicked.connect(self.send_alarm_parameters)

        realtime_controls_layout.addWidget(self.stream_btn)
        realtime_controls_layout.addWidget(self.stream_rms_btn)
        realtime_controls_layout.addWidget(self.stop_btn_cmd)
        realtime_controls_layout.addSpacing(20)
        realtime_controls_layout.addWidget(self.alarm_checkbox)
        realtime_controls_layout.addWidget(QLabel("ALARM:"))
        realtime_controls_layout.addWidget(self.alarm_indicator)
        realtime_controls_layout.addSpacing(20)
        realtime_controls_layout.addWidget(QLabel("RMS_ALARM:"))
        realtime_controls_layout.addWidget(self.rms_alarm_spin)
        realtime_controls_layout.addWidget(QLabel("CREST_ALARM:"))
        realtime_controls_layout.addWidget(self.crest_alarm_spin)
        realtime_controls_layout.addWidget(self.apply_alarm_params_btn)
        realtime_controls_layout.addStretch()

        self.sensor_tab_widget = QTabWidget()
        realtime_layout.addLayout(realtime_controls_layout)
        realtime_layout.addWidget(self.sensor_tab_widget)
        realtime_tab.setLayout(realtime_layout)
        self.main_tabs.addTab(realtime_tab, "Реальное время")

        # Тест
        test_tab = QWidget()
        test_layout = QVBoxLayout()
        test_controls_layout = QHBoxLayout()
        self.test_save_btn = QPushButton("Тест с сохранением")
        self.test_save_btn.clicked.connect(self.start_test_with_save)
        self.test_nosave_btn = QPushButton("Тест без сохранения")
        self.test_nosave_btn.clicked.connect(self.start_test_no_save)
        test_controls_layout.addWidget(self.test_save_btn)
        test_controls_layout.addWidget(self.test_nosave_btn)
        test_controls_layout.addStretch()

        test_params_layout = QHBoxLayout()
        self.test_first_spin = QSpinBox()
        self.test_first_spin.setRange(1, 3600)
        self.test_first_spin.setValue(3)
        self.test_second_spin = QSpinBox()
        self.test_second_spin.setRange(1, 3600)
        self.test_second_spin.setValue(5)
        self.test_dur_spin = QSpinBox()
        self.test_dur_spin.setRange(1, 3600)
        self.test_dur_spin.setValue(10)
        self.apply_test_params_btn = QPushButton("Применить")
        self.apply_test_params_btn.clicked.connect(self.send_test_parameters)

        test_params_layout.addWidget(QLabel("FIRST_TEST(с):"))
        test_params_layout.addWidget(self.test_first_spin)
        test_params_layout.addWidget(QLabel("SECOND_TEST(с):"))
        test_params_layout.addWidget(self.test_second_spin)
        test_params_layout.addWidget(QLabel("DUR_TEST(с):"))
        test_params_layout.addWidget(self.test_dur_spin)
        test_params_layout.addWidget(self.apply_test_params_btn)
        test_params_layout.addStretch()

        self.test_list_widget = QListWidget()
        self.load_ref_btn = QPushButton("Загрузить эталон")
        self.load_ref_btn.clicked.connect(self.load_reference_test)
        self.compare_text_btn = QPushButton("Текстовое сравнение")
        self.compare_text_btn.clicked.connect(self.compare_text_report)
        self.compare_visual_btn = QPushButton("Визуальное сравнение")
        self.compare_visual_btn.clicked.connect(self.compare_visual)
        self.delete_test_btn = QPushButton("Удалить тест")
        self.delete_test_btn.clicked.connect(self.delete_selected_test)

        test_buttons_layout = QHBoxLayout()
        test_buttons_layout.addWidget(self.load_ref_btn)
        test_buttons_layout.addWidget(self.compare_text_btn)
        test_buttons_layout.addWidget(self.compare_visual_btn)
        test_buttons_layout.addWidget(self.delete_test_btn)

        test_layout.addLayout(test_controls_layout)
        test_layout.addLayout(test_params_layout)
        test_layout.addWidget(QLabel("Сохранённые тесты:"))
        test_layout.addWidget(self.test_list_widget)
        test_layout.addLayout(test_buttons_layout)
        test_tab.setLayout(test_layout)
        self.main_tabs.addTab(test_tab, "Тест")

        # Журнал аварий
        alarm_tab = QWidget()
        alarm_layout = QVBoxLayout()
        self.alarm_dev_filter = QComboBox()
        self.alarm_dev_filter.addItem("Все", None)
        self.alarm_sens_filter = QComboBox()
        self.alarm_sens_filter.addItem("Все", None)
        self.alarm_sens_filter.addItem("1", 1)
        self.alarm_sens_filter.addItem("2", 2)
        self.date_from_edit = QDateEdit(QDate.currentDate().addDays(-7))
        self.date_from_edit.setCalendarPopup(True)
        self.time_from_edit = QTimeEdit(QTime(0,0,0))
        self.date_to_edit = QDateEdit(QDate.currentDate())
        self.date_to_edit.setCalendarPopup(True)
        self.time_to_edit = QTimeEdit(QTime(23,59,59))
        self.delete_alarm_btn = QPushButton("Удалить запись")
        self.delete_alarm_btn.clicked.connect(self.delete_selected_alarm)
        self.clear_alarms_btn = QPushButton("Очистить всё")
        self.clear_alarms_btn.clicked.connect(self.clear_all_alarms)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Устр:"))
        filter_layout.addWidget(self.alarm_dev_filter)
        filter_layout.addWidget(QLabel("Датч:"))
        filter_layout.addWidget(self.alarm_sens_filter)
        filter_layout.addWidget(QLabel("С:"))
        filter_layout.addWidget(self.date_from_edit)
        filter_layout.addWidget(self.time_from_edit)
        filter_layout.addWidget(QLabel("По:"))
        filter_layout.addWidget(self.date_to_edit)
        filter_layout.addWidget(self.time_to_edit)
        filter_layout.addWidget(self.delete_alarm_btn)
        filter_layout.addWidget(self.clear_alarms_btn)
        filter_layout.addStretch()

        self.alarm_table = QTableWidget(0, 7)
        self.alarm_table.setHorizontalHeaderLabels(['Дата','Время','Устр.','Датч.','Ось','RMS скор.','Пик-ф.'])
        self.alarm_table.cellDoubleClicked.connect(self.show_alarm_detail)

        alarm_layout.addLayout(filter_layout)
        alarm_layout.addWidget(self.alarm_table)
        alarm_tab.setLayout(alarm_layout)
        self.main_tabs.addTab(alarm_tab, "Журнал аварий")

        self.status_label = QLabel("Статус: Не подключено")
        self.status_label.setStyleSheet("padding: 4px; background-color: #eee;")

        central_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.main_tabs)
        main_layout.addWidget(self.status_label)
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self.alarm_clear_timer = QTimer(self)
        self.alarm_clear_timer.setSingleShot(True)
        self.alarm_clear_timer.timeout.connect(lambda: self.alarm_indicator.set_alarm(False))

        self.current_test = None
        self.reference_test = None
        self.reference_test_id = None
        self.test_sensor_axis = {}
        self.save_test_flag = False

        self.server_client.tests_loaded.connect(self.on_tests_loaded)
        self.server_client.alarms_loaded.connect(self.on_alarms_loaded)
        self.server_client.alarm_loaded.connect(self.on_alarm_loaded)

        self.history_refresh_timer = QTimer(self)
        self.history_refresh_timer.timeout.connect(self.refresh_history_views)
        self.history_refresh_timer.start(1000)

    # ==================== Вспомогательные методы ====================
    def get_current_device(self):
        idx = self.device_combo.currentIndex()
        if idx < 0:
            return None
        data = self.device_combo.itemData(idx)
        return data if isinstance(data, int) else None

    def on_device_changed(self):
        self.refresh_history_views()

    def update_device_list(self, dev_id, status):
        self.device_statuses[dev_id] = status
        found = False
        for i in range(self.device_combo.count()):
            if self.device_combo.itemData(i) == dev_id:
                found = True
                if status == "online":
                    self.device_combo.setItemText(i, f"{dev_id} (online)")
                    self.device_combo.setItemData(i, QBrush(QColor("green")), Qt.ForegroundRole)
                else:
                    self.device_combo.setItemText(i, f"{dev_id} (offline)")
                    self.device_combo.setItemData(i, QBrush(QColor("red")), Qt.ForegroundRole)
                break
        if not found:
            if status == "online":
                self.device_combo.addItem(f"{dev_id} (online)", dev_id)
                self.device_combo.setItemData(self.device_combo.count()-1, QBrush(QColor("green")), Qt.ForegroundRole)
                if self.device_combo.count() == 2:
                    self.device_combo.setCurrentIndex(1)
            else:
                self.device_combo.addItem(f"{dev_id} (offline)", dev_id)
                self.device_combo.setItemData(self.device_combo.count()-1, QBrush(QColor("red")), Qt.ForegroundRole)
        if status == "offline":
            self.refresh_history_views()

    def refresh_history_views(self):
        self.hist_widget1.update_plots()
        self.hist_widget2.update_plots()

    def get_last_alarm_info_for_sensor(self, sensor_id):
        if not self.alarm_events:
            return "Аварий нет"
        events_for_sensor = [ev for ev in self.alarm_events if ev.get('sensor_id') == sensor_id]
        if not events_for_sensor:
            return "Аварий нет"
        last = max(events_for_sensor, key=lambda x: x['timestamp'])
        dt = datetime.fromtimestamp(last['timestamp']).strftime('%d.%m.%Y %H:%M:%S')
        cause = last.get('cause', 'неизвестна')
        rms_speed = last.get('rms_speed', 0.0)
        crest = last.get('crest_factor', 0.0)
        return f"{dt}\nПричина: {cause}\nRMS скор: {rms_speed:.3f} мм/с, Пик-фактор: {crest:.2f}"

    def open_alarm_journal(self, sensor_id):
        self.main_tabs.setCurrentIndex(3)
        idx = self.alarm_sens_filter.findData(sensor_id)
        if idx >= 0:
            self.alarm_sens_filter.setCurrentIndex(idx)
        self.update_alarm_table()
        events_for_sensor = [ev for ev in self.alarm_events if ev.get('sensor_id') == sensor_id]
        if events_for_sensor:
            last = max(events_for_sensor, key=lambda x: x['timestamp'])
            filtered = self.get_filtered_events()
            for row, ev in enumerate(filtered):
                if ev is last:
                    self.alarm_table.selectRow(row)
                    self.show_alarm_detail(row, 0)
                    break

    # ==================== Управление подключением ====================
    def start_connection(self):
        self.server_client.status_updated.connect(self.status_label.setText)
        self.server_client.device_status.connect(self.update_device_list)
        self.server_client.cycle_status_received.connect(self.handle_cycle_status)
        self.server_client.params_received.connect(self.handle_params_received)

        self.server_client.rms_received.connect(self.handle_rms)
        self.server_client.spectrum_received.connect(self.handle_spectrum)
        self.server_client.waveform_received.connect(self.handle_waveform)
        self.server_client.test_rms_received.connect(self.handle_test_rms)
        self.server_client.test_spectrum_received.connect(self.handle_test_spectrum)
        self.server_client.alarm_rms_received.connect(self.handle_alarm_rms)
        self.server_client.alarm_spectrum_received.connect(self.handle_alarm_spectrum)
        self.server_client.alarm_waveform_received.connect(self.handle_alarm_waveform)

        self.server_client.connect()

        if not hasattr(self, 'tab1'):
            self.tab1 = SensorTab(1, self.server_client, self.get_current_device)
            self.tab2 = SensorTab(2, self.server_client, self.get_current_device)
            self.sensor_tab_widget.addTab(self.tab1, "Датчик 1")
            self.sensor_tab_widget.addTab(self.tab2, "Датчик 2")

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.initial_data_load()
        self.refresh_history_views()

    def stop_connection(self):
        self.server_client.disconnect()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Отключено от сервера")
        self.device_combo.clear()
        self.device_combo.addItem("", 0)
        while self.sensor_tab_widget.count() > 0:
            self.sensor_tab_widget.removeTab(0)
        self.tab1 = None
        self.tab2 = None

    # ==================== Обработчики live-данных ====================
    def handle_rms(self, dev_id, sensor_id, axis, rms_speed, rms_accel, crest, alarm, timestamp):
        if alarm:
            self.alarm_indicator.set_alarm(True)
            self.alarm_clear_timer.start(2000)
        if sensor_id == 1 and self.tab1:
            self.tab1.update_rms(dev_id, sensor_id, axis, rms_speed, rms_accel, crest, alarm)
        elif sensor_id == 2 and self.tab2:
            self.tab2.update_rms(dev_id, sensor_id, axis, rms_speed, rms_accel, crest, alarm)
        if dev_id == self.get_current_device():
            if sensor_id == 1:
                self.hist_widget1.update_current_values(rms_speed, rms_accel, crest)
                self.hist_widget1.add_live_rms(axis, rms_speed, rms_accel, crest, alarm, timestamp)
            elif sensor_id == 2:
                self.hist_widget2.update_current_values(rms_speed, rms_accel, crest)
                self.hist_widget2.add_live_rms(axis, rms_speed, rms_accel, crest, alarm, timestamp)

    def handle_spectrum(self, dev_id, sensor_id, axis, spectrum):
        if sensor_id == 1 and self.tab1:
            self.tab1.update_spectrum(dev_id, sensor_id, axis, spectrum)
        elif sensor_id == 2 and self.tab2:
            self.tab2.update_spectrum(dev_id, sensor_id, axis, spectrum)

    def handle_waveform(self, dev_id, sensor_id, axis, accel):
        if sensor_id == 1 and self.tab1:
            self.tab1.update_waveform(dev_id, sensor_id, axis, accel)
        elif sensor_id == 2 and self.tab2:
            self.tab2.update_waveform(dev_id, sensor_id, axis, accel)

    def handle_cycle_status(self, dev_id, status_str, timestamp):
        self.hist_widget1.set_cycle_status(status_str, timestamp)
        self.hist_widget2.set_cycle_status(status_str, timestamp)

    def handle_params_received(self, dev_id, params):
        current = self.get_current_device()
        if current is not None and dev_id == current:
            self.rms_alarm_spin.setValue(params.get('rms_alarm', 6.0))
            self.crest_alarm_spin.setValue(params.get('crest_alarm', 5.0))
            self.test_first_spin.setValue(int(params.get('first_test', 3)))
            self.test_second_spin.setValue(int(params.get('second_test', 5)))
            self.test_dur_spin.setValue(int(params.get('dur_test', 10)))
            self.alarm_checkbox.setChecked(params.get('alarm_stream_enabled', True))

    def handle_test_rms(self, dev_id, sensor_id, axis, rms_speed, rms_accel, crest, alarm, frame_id, frame_type):
        self.test_sensor_axis[(dev_id, sensor_id)] = axis
        if self.current_test is None:
            self.current_test = TestRecorder(dev_id)
            self.status_label.setText("Начата запись теста...")
        if self.current_test.device_id == dev_id:
            self.current_test.add_rms_frame(sensor_id, axis, rms_speed, rms_accel, crest, alarm, frame_id, frame_type)
        if dev_id == self.get_current_device():
            if alarm:
                self.alarm_indicator.set_alarm(True)
                self.alarm_clear_timer.start(2000)
            if sensor_id == 1 and self.tab1:
                self.tab1.update_rms(dev_id, sensor_id, axis, rms_speed, rms_accel, crest, alarm)
            elif sensor_id == 2 and self.tab2:
                self.tab2.update_rms(dev_id, sensor_id, axis, rms_speed, rms_accel, crest, alarm)
        if frame_type == 4:
            self.finish_current_test()

    def handle_test_spectrum(self, dev_id, sensor_id, spectrum, frame_id, spec_type):
        if self.current_test and self.current_test.device_id == dev_id:
            self.current_test.add_spectrum_frame(sensor_id, spectrum, frame_id)
        if dev_id == self.get_current_device():
            axis = self.test_sensor_axis.get((dev_id, sensor_id), -1)
            if axis >= 0:
                if sensor_id == 1 and self.tab1:
                    self.tab1.update_spectrum(dev_id, sensor_id, axis, spectrum)
                elif sensor_id == 2 and self.tab2:
                    self.tab2.update_spectrum(dev_id, sensor_id, axis, spectrum)

    def handle_alarm_rms(self, dev_id, sensor_id, axis, rms_speed, rms_accel, crest, alarm, frame_id, timestamp):
        causes = []
        if rms_speed > self.rms_alarm_spin.value():
            causes.append("превышение СКЗ виброскорости")
        if crest > self.crest_alarm_spin.value():
            causes.append("превышение пик-фактора")
        cause = ", ".join(causes) if causes else "неизвестна"
        event = {
            'id': None,
            'dev_id': dev_id,
            'sensor_id': sensor_id,
            'axis': axis,
            'rms_speed': rms_speed,
            'rms_accel': rms_accel,
            'crest_factor': crest,
            'alarm': alarm,
            'frame_id': frame_id,
            'timestamp': timestamp,
            'spectrum': None,
            'waveform': None,
            'cause': cause
        }
        if not any(e['dev_id'] == dev_id and e['sensor_id'] == sensor_id and e['frame_id'] == frame_id
                   for e in self.alarm_events):
            self.alarm_events.append(event)
            self.update_alarm_table()
            self.refresh_history_views()

    def handle_alarm_spectrum(self, dev_id, sensor_id, spectrum, frame_id):
        for ev in reversed(self.alarm_events):
            if ev['dev_id'] == dev_id and ev['sensor_id'] == sensor_id and ev['frame_id'] == frame_id:
                ev['spectrum'] = spectrum.tolist()
                break

    def handle_alarm_waveform(self, dev_id, sensor_id, waveform, frame_id):
        for ev in reversed(self.alarm_events):
            if ev['dev_id'] == dev_id and ev['sensor_id'] == sensor_id and ev['frame_id'] == frame_id:
                ev['waveform'] = waveform.tolist()
                break

    # ==================== Команды ====================
    def start_stream(self):
        dev = self.get_current_device()
        if dev is None:
            self.status_label.setText("Ошибка: не выбрано устройство")
            return
        self.server_client.send_command(dev, "STREAM")

    def start_stream_rms(self):
        dev = self.get_current_device()
        if dev is None:
            self.status_label.setText("Ошибка: не выбрано устройство")
            return
        self.server_client.send_command(dev, "STREAM_RMS")

    def stop_all(self):
        dev = self.get_current_device()
        if dev is None:
            self.status_label.setText("Ошибка: не выбрано устройство")
            return
        self.server_client.send_command(dev, "STOP")

    def toggle_alarm_stream(self, state):
        dev = self.get_current_device()
        if dev is not None:
            cmd = "ALARM_STREAM ON" if state == Qt.Checked else "ALARM_STREAM OFF"
            self.server_client.send_command(dev, cmd)

    def send_alarm_parameters(self):
        dev = self.get_current_device()
        if dev is None:
            self.status_label.setText("Ошибка: не выбрано устройство")
            return
        commands = [
            f"RMS_ALARM {self.rms_alarm_spin.value():.2f}",
            f"CREST_ALARM {self.crest_alarm_spin.value():.2f}",
        ]
        for cmd in commands:
            self.server_client.send_command(dev, cmd)

    def send_test_parameters(self):
        dev = self.get_current_device()
        if dev is None:
            self.status_label.setText("Ошибка: не выбрано устройство")
            return
        commands = [
            f"FIRST_TEST {self.test_first_spin.value()}",
            f"SECOND_TEST {self.test_second_spin.value()}",
            f"DUR_TEST {self.test_dur_spin.value()}",
        ]
        for cmd in commands:
            self.server_client.send_command(dev, cmd)

    # ==================== Тесты ====================
    def start_test_with_save(self):
        dev = self.get_current_device()
        if dev is None:
            self.status_label.setText("Ошибка: не выбрано устройство")
            return
        self.save_test_flag = True
        self.server_client.send_command(dev, "TEST")
        self.status_label.setText("Тест с сохранением запущен...")

    def start_test_no_save(self):
        dev = self.get_current_device()
        if dev is None:
            self.status_label.setText("Ошибка: не выбрано устройство")
            return
        self.save_test_flag = False
        self.server_client.send_command(dev, "TEST")
        self.status_label.setText("Тест без сохранения запущен...")

    def finish_current_test(self):
        if self.current_test and self.current_test.is_active:
            self.current_test.finish()
            if self.save_test_flag:
                name, ok = QInputDialog.getText(self, "Сохранение теста", "Введите название:")
                if ok and name:
                    self.current_test.name = name
                    test_data = {
                        'device_id': self.current_test.device_id,
                        'name': self.current_test.name,
                        'is_reference': False,
                        'total_alarms': self.current_test.total_alarms,
                        'start_time': self.current_test.start_time,
                        'end_time': self.current_test.end_time,
                        'sensors': {str(sid): self.current_test.sensors[sid] for sid in self.current_test.sensors}
                    }
                    self.server_client.save_test(test_data)
                    self.server_client.get_tests_list_async()
                    self.status_label.setText(f"Тест '{name}' сохранён.")
                else:
                    self.status_label.setText("Сохранение отменено.")
            else:
                self.status_label.setText("Тест завершён (без сохранения).")
            self.current_test = None
            self.save_test_flag = False

    def update_test_list(self):
        self.server_client.get_tests_list_async()

    def on_tests_loaded(self, tests):
        self.test_list_widget.clear()
        for t in tests:
            item = QListWidgetItem(
                f"{t['name']} (устр.{t['dev_id']}, "
                f"{time.strftime('%d.%m.%Y %H:%M', time.localtime(t['start_time']))})"
            )
            item.setData(Qt.UserRole, t['id'])
            self.test_list_widget.addItem(item)

    def load_reference_test(self):
        selected = self.test_list_widget.currentItem()
        if not selected:
            QMessageBox.warning(self, "Ошибка", "Выберите тест из списка.")
            return
        test_id = selected.data(Qt.UserRole)
        test_data = self.server_client.get_test(test_id)
        if test_data:
            self.reference_test = test_data
            self.reference_test_id = test_id
            self.status_label.setText(f"Эталонный тест загружен: {self.reference_test['name']}")
            self.server_client.set_reference_test(test_id)

    def compare_text_report(self):
        if not self.reference_test:
            QMessageBox.warning(self, "Ошибка", "Сначала загрузите эталонный тест.")
            return
        selected = self.test_list_widget.currentItem()
        if not selected:
            QMessageBox.warning(self, "Ошибка", "Выберите тест для сравнения.")
            return
        test_id = selected.data(Qt.UserRole)
        cur_test = self.server_client.get_test(test_id)
        if not cur_test:
            return
        report = self._generate_comparison_report(self.reference_test, cur_test)
        QMessageBox.information(self, "Текстовое сравнение", report)

    def compare_visual(self):
        if not self.reference_test:
            QMessageBox.warning(self, "Ошибка", "Сначала загрузите эталонный тест.")
            return
        selected = self.test_list_widget.currentItem()
        if not selected:
            QMessageBox.warning(self, "Ошибка", "Выберите тест для сравнения.")
            return
        test_id = selected.data(Qt.UserRole)
        cur_test = self.server_client.get_test(test_id)
        if not cur_test:
            return
        self.compare_win = CompareWindow(self.reference_test, cur_test, self)
        self.compare_win.show()

    def delete_selected_test(self):
        selected = self.test_list_widget.currentItem()
        if not selected:
            QMessageBox.warning(self, "Ошибка", "Выберите тест для удаления.")
            return
        test_id = selected.data(Qt.UserRole)
        reply = QMessageBox.question(self, "Подтверждение удаления",
                                     f"Удалить тест '{selected.text()}'?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.server_client.delete_test(test_id)
            self.update_test_list()

    def _generate_comparison_report(self, ref, cur):
        report = []
        report.append(f"Сравнение: '{cur['name']}' с эталоном '{ref['name']}'")
        report.append(f"Устройство: {ref['device_id']}")
        report.append("-" * 70)
        common_sensors = set(ref['sensors'].keys()) & set(cur['sensors'].keys())
        for sid_str in sorted(common_sensors):
            sid = int(sid_str)
            ref_frames = ref['sensors'][sid_str]
            cur_frames = cur['sensors'][sid_str]
            if not ref_frames or not cur_frames:
                continue
            axis = ref_frames[0].get('axis', 0)

            ref_rms_speed_avg = np.mean([f['rms_speed'] for f in ref_frames])
            cur_rms_speed_avg = np.mean([f['rms_speed'] for f in cur_frames])
            ref_rms_accel_avg = np.mean([f['rms_accel'] for f in ref_frames])
            cur_rms_accel_avg = np.mean([f['rms_accel'] for f in cur_frames])
            ref_crest_avg = np.mean([f['crest_factor'] for f in ref_frames])
            cur_crest_avg = np.mean([f['crest_factor'] for f in cur_frames])

            ref_rms_speed_max = max([f['rms_speed'] for f in ref_frames])
            cur_rms_speed_max = max([f['rms_speed'] for f in cur_frames])
            ref_rms_accel_max = max([f['rms_accel'] for f in ref_frames])
            cur_rms_accel_max = max([f['rms_accel'] for f in cur_frames])
            ref_crest_max = max([f['crest_factor'] for f in ref_frames])
            cur_crest_max = max([f['crest_factor'] for f in cur_frames])

            def delta_str(r, c):
                diff = c - r
                pct = (abs(diff)/abs(r)*100) if r else 0.0
                return f"Δ={diff:+.3f} ({pct:.1f}%)"

            report.append(f"Датчик {sid}, ось {axis}")
            report.append(f"  RMS скорости (мм/с):")
            report.append(f"    Среднее: {ref_rms_speed_avg:.3f} → {cur_rms_speed_avg:.3f}  {delta_str(ref_rms_speed_avg, cur_rms_speed_avg)}")
            report.append(f"    Максимум: {ref_rms_speed_max:.3f} → {cur_rms_speed_max:.3f}  {delta_str(ref_rms_speed_max, cur_rms_speed_max)}")
            report.append(f"  RMS ускорения (м/с²):")
            report.append(f"    Среднее: {ref_rms_accel_avg:.4f} → {cur_rms_accel_avg:.4f}  {delta_str(ref_rms_accel_avg, cur_rms_accel_avg)}")
            report.append(f"    Максимум: {ref_rms_accel_max:.4f} → {cur_rms_accel_max:.4f}  {delta_str(ref_rms_accel_max, cur_rms_accel_max)}")
            report.append(f"  Пик-фактор:")
            report.append(f"    Среднее: {ref_crest_avg:.2f} → {cur_crest_avg:.2f}  {delta_str(ref_crest_avg, cur_crest_avg)}")
            report.append(f"    Максимум: {ref_crest_max:.2f} → {cur_crest_max:.2f}  {delta_str(ref_crest_max, cur_crest_max)}")

            for stype, label in [(2, "Первый спектр"), (3, "Второй спектр")]:
                ref_spec = next((np.array(f['spectrum']) for f in ref_frames if f.get('type')==stype and f.get('spectrum')), None)
                cur_spec = next((np.array(f['spectrum']) for f in cur_frames if f.get('type')==stype and f.get('spectrum')), None)
                if ref_spec is not None and cur_spec is not None:
                    min_len = min(len(ref_spec), len(cur_spec))
                    mae = np.mean(np.abs(cur_spec[:min_len] - ref_spec[:min_len]))
                    report.append(f"  {label}: средняя разница спектра = {mae:.3f} мм/с")
                else:
                    report.append(f"  {label}: спектр отсутствует в одном из тестов")
            report.append("")
        return "\n".join(report)

    # ==================== Журнал аварий ====================
    def load_alarm_history(self):
        self.server_client.get_alarms_async()

    def on_alarms_loaded(self, alarms):
        self.alarm_events = alarms
        self.update_alarm_table()
        self.refresh_history_views()

    def on_alarm_loaded(self, alarm):
        self._show_alarm_detail(alarm)

    def get_filtered_events(self):
        dev_filter = self.alarm_dev_filter.currentData()
        sens_filter = self.alarm_sens_filter.currentData()
        date_from = self.date_from_edit.date().toPyDate()
        date_to = self.date_to_edit.date().toPyDate()
        time_from = self.time_from_edit.time().toPyTime()
        time_to = self.time_to_edit.time().toPyTime()

        filtered = []
        for ev in self.alarm_events:
            if dev_filter is not None and ev['dev_id'] != dev_filter:
                continue
            if sens_filter is not None and ev['sensor_id'] != sens_filter:
                continue
            ev_dt = datetime.fromtimestamp(ev['timestamp'])
            ev_date = ev_dt.date()
            ev_time = ev_dt.time()
            if date_from is not None and ev_date < date_from:
                continue
            if date_to is not None and ev_date > date_to:
                continue
            if time_from is not None and ev_time < time_from:
                continue
            if time_to is not None and ev_time > time_to:
                continue
            filtered.append(ev)
        return filtered

    def update_alarm_table(self):
        current_dev = self.alarm_dev_filter.currentData()
        self.alarm_dev_filter.blockSignals(True)
        self.alarm_dev_filter.clear()
        self.alarm_dev_filter.addItem("Все", None)
        dev_ids = sorted(set(ev['dev_id'] for ev in self.alarm_events))
        for d in dev_ids:
            self.alarm_dev_filter.addItem(str(d), d)
        idx = self.alarm_dev_filter.findData(current_dev)
        if idx >= 0:
            self.alarm_dev_filter.setCurrentIndex(idx)
        self.alarm_dev_filter.blockSignals(False)

        filtered = self.get_filtered_events()

        self.alarm_table.setRowCount(0)
        for ev in filtered[-100:]:
            row = self.alarm_table.rowCount()
            self.alarm_table.insertRow(row)
            dt = datetime.fromtimestamp(ev['timestamp'])
            self.alarm_table.setItem(row, 0, QTableWidgetItem(dt.strftime('%d.%m.%Y')))
            self.alarm_table.setItem(row, 1, QTableWidgetItem(dt.strftime('%H:%M:%S')))
            self.alarm_table.setItem(row, 2, QTableWidgetItem(str(ev['dev_id'])))
            self.alarm_table.setItem(row, 3, QTableWidgetItem(str(ev['sensor_id'])))
            self.alarm_table.setItem(row, 4, QTableWidgetItem(str(ev['axis'])))
            self.alarm_table.setItem(row, 5, QTableWidgetItem(f"{ev['rms_speed']:.3f}"))
            self.alarm_table.setItem(row, 6, QTableWidgetItem(f"{ev['crest_factor']:.2f}"))

    def show_alarm_detail(self, row, col):
        filtered = self.get_filtered_events()
        if row < 0 or row >= len(filtered):
            return
        self.alarm_detail_filtered = filtered
        self.alarm_detail_idx = row
        event = filtered[row]
        if event.get('spectrum') is None and event.get('waveform') is None:
            self._create_alarm_detail_dialog()
            self.detail_info_label.setText("Загрузка данных...")
            self.detail_spec_curve.clear()
            self.detail_wf_curve.clear()
            self.alarm_detail_dlg.show()
            self.server_client.get_alarm_by_id_async(event['id'])
        else:
            self._show_alarm_detail(event)

    def _create_alarm_detail_dialog(self):
        if self.alarm_detail_dlg is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Детали аварии")
            dlg.resize(800, 600)
            layout = QVBoxLayout()

            self.detail_info_label = QLabel()
            layout.addWidget(self.detail_info_label)

            self.detail_spec_plot = pg.PlotWidget(title="Спектр виброскорости")
            self.detail_spec_plot.setLabel('left', 'мм/с')
            self.detail_spec_plot.setLabel('bottom', 'Гц')
            self.detail_spec_plot.showGrid(True)
            self.detail_spec_curve = self.detail_spec_plot.plot(pen='lime')
            layout.addWidget(self.detail_spec_plot)

            self.detail_wf_plot = pg.PlotWidget(title="Осциллограмма ускорения")
            self.detail_wf_plot.setLabel('left', 'м/с²')
            self.detail_wf_plot.setLabel('bottom', 'Отсчёт')
            self.detail_wf_plot.showGrid(True)
            self.detail_wf_curve = self.detail_wf_plot.plot(pen='c')
            layout.addWidget(self.detail_wf_plot)

            nav_layout = QHBoxLayout()
            self.prev_btn = QPushButton("◀ Предыдущая")
            self.next_btn = QPushButton("Следующая ▶")
            nav_layout.addStretch()
            nav_layout.addWidget(self.prev_btn)
            nav_layout.addWidget(self.next_btn)
            nav_layout.addStretch()
            layout.addLayout(nav_layout)

            dlg.setLayout(layout)
            self.alarm_detail_dlg = dlg

            self.prev_timer = QTimer(self)
            self.prev_timer.setInterval(240)
            self.prev_timer.timeout.connect(self._prev_alarm_detail)
            self.next_timer = QTimer(self)
            self.next_timer.setInterval(240)
            self.next_timer.timeout.connect(self._next_alarm_detail)

            self.prev_start_timer = QTimer(self)
            self.prev_start_timer.setSingleShot(True)
            self.prev_start_timer.setInterval(300)
            self.prev_start_timer.timeout.connect(self.prev_timer.start)
            self.next_start_timer = QTimer(self)
            self.next_start_timer.setSingleShot(True)
            self.next_start_timer.setInterval(300)
            self.next_start_timer.timeout.connect(self.next_timer.start)

            self.prev_btn.pressed.connect(self._prev_pressed)
            self.prev_btn.released.connect(self._prev_released)
            self.next_btn.pressed.connect(self._next_pressed)
            self.next_btn.released.connect(self._next_released)

            dlg.finished.connect(self._on_alarm_detail_closed)

    def _show_alarm_detail(self, event):
        self._create_alarm_detail_dialog()
        self._update_alarm_detail_content(event)
        if not self.alarm_detail_dlg.isVisible():
            self.alarm_detail_dlg.show()
        else:
            self.alarm_detail_dlg.raise_()

    def _prev_pressed(self):
        self._prev_alarm_detail()
        self.prev_start_timer.start()

    def _prev_released(self):
        self.prev_start_timer.stop()
        self.prev_timer.stop()

    def _next_pressed(self):
        self._next_alarm_detail()
        self.next_start_timer.start()

    def _next_released(self):
        self.next_start_timer.stop()
        self.next_timer.stop()

    def _update_alarm_detail_content(self, event):
        info = (f"Устройство: {event['dev_id']}, Датчик: {event['sensor_id']}, Ось: {event['axis']}\n"
                f"RMS скор: {event['rms_speed']:.3f} мм/с, RMS уск: {event['rms_accel']:.3f} м/с², "
                f"Пик-фактор: {event['crest_factor']:.2f}\n"
                f"Причина: {event.get('cause', 'неизвестна')}")
        self.detail_info_label.setText(info)

        if event.get('spectrum') is not None and len(event['spectrum']) > 1:
            freq = np.linspace(0, SAMPLING_RATE/2, len(event['spectrum']))
            self.detail_spec_curve.setData(freq[1:], event['spectrum'][1:])
        else:
            self.detail_spec_curve.clear()

        if event.get('waveform') is not None and len(event['waveform']) > 0:
            self.detail_wf_curve.setData(event['waveform'])
        else:
            self.detail_wf_curve.clear()

    def _prev_alarm_detail(self):
        if self.alarm_detail_idx > 0:
            self.alarm_detail_idx -= 1
            event = self.alarm_detail_filtered[self.alarm_detail_idx]
            self._show_alarm_detail(event)

    def _next_alarm_detail(self):
        if self.alarm_detail_idx < len(self.alarm_detail_filtered) - 1:
            self.alarm_detail_idx += 1
            event = self.alarm_detail_filtered[self.alarm_detail_idx]
            self._show_alarm_detail(event)

    def _on_alarm_detail_closed(self):
        self.prev_start_timer.stop()
        self.prev_timer.stop()
        self.next_start_timer.stop()
        self.next_timer.stop()
        self.alarm_detail_dlg = None

    def delete_selected_alarm(self):
        filtered = self.get_filtered_events()
        row = self.alarm_table.currentRow()
        if row < 0 or row >= len(filtered):
            QMessageBox.warning(self, "Ошибка", "Выберите запись для удаления.")
            return
        event = filtered[row]
        reply = QMessageBox.question(self, "Подтверждение",
                                     f"Удалить запись аварии от {datetime.fromtimestamp(event['timestamp']).strftime('%d.%m.%Y %H:%M:%S')}?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.server_client.delete_alarm(event['id'])
        self.alarm_events.remove(event)
        self.update_alarm_table()
        self.status_label.setText("Запись аварии удалена.")

    def clear_all_alarms(self):
        reply = QMessageBox.question(self, "Очистка журнала",
                                     "Удалить ВСЕ записи аварий безвозвратно?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.server_client.delete_all_alarms()
        self.alarm_events.clear()
        self.update_alarm_table()
        self.status_label.setText("Журнал аварий полностью очищен.")
        self.refresh_history_views()

    # ==================== Очистка и импорт истории ====================
    def clear_history_dialog(self):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Удаление истории")
        msg_box.setText("Выберите действие:")
        btn_all = msg_box.addButton("Удалить всю историю", QMessageBox.AcceptRole)
        btn_15min = msg_box.addButton("Оставить последние 15 минут", QMessageBox.ActionRole)
        btn_1hour = msg_box.addButton("Оставить последний час", QMessageBox.ActionRole)
        btn_24hours = msg_box.addButton("Оставить последние сутки", QMessageBox.ActionRole)
        btn_cancel = msg_box.addButton("Отмена", QMessageBox.RejectRole)
        msg_box.exec_()

        clicked = msg_box.clickedButton()
        if clicked == btn_all:
            self.server_client.clear_history("all")
        elif clicked == btn_15min:
            self.server_client.clear_history("trim_15min")
        elif clicked == btn_1hour:
            self.server_client.clear_history("trim_1hour")
        elif clicked == btn_24hours:
            self.server_client.clear_history("trim_24hours")

    def load_history(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Загрузить историю",
            "",
            "JSON Files (*.json);;All Files (*)"
        )
        if filename:
            self.server_client.import_history(filename)
            self.refresh_history_views()

    def initial_data_load(self):
        self.server_client.get_tests_list_async()
        self.server_client.get_alarms_async()

    def closeEvent(self, event):
        self.server_client.disconnect()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())