# -*- coding: utf-8 -*-
"""monitor_room.py — اتاق مانیتورینگ (multi-monitor) نسخه‌ی مک (1.1.1).

قابلیت «اتاق مانیتورینگ»: تقسیم تصاویر دوربین‌ها روی چند مانیتور متصل به سیستم.
- شناسایی مانیتورها با QApplication.screens() (نمایشی، برای راهنمایی کاربر)
- دیالوگ تنظیمات: افزودن/حذف «پنجره»، انتخاب دوربین‌های هر پنجره
- هر پنجره یک پنجره‌ی مستقل، کاملاً movable و resizable است — کاربر با موس
  می‌گیرد و روی هر مانیتوری که می‌خواهد می‌اندازد؛ F11 یا دابل‌کلیک: تمام‌صفحه
- ذخیره‌ی چیدمان در monitor_layout.json (دوربین‌ها + موقعیت/اندازه + تمام‌صفحه‌ی
  هر پنجره)؛ بعد از بستن و باز کردن برنامه، چیدمان بازیابی می‌شود.
  (چیدمان قدیمی نسخه‌ی 1.1.0 به‌صورت خودکار مهاجرت می‌کند.)

نکته‌ی طراحی: تایل‌های این پنجره‌ها فقط «نمایش» می‌دهند — هر تایل استریم
مستقیم خودش را باز می‌کند (CameraStreamThread) ولی هیچ callback رویدادی
(چهره/پلاک/حریق/شخص) وصل نمی‌شود تا رویدادها تکراری ثبت نشوند.
"""

import json
import math
import os
import time
import uuid
from datetime import datetime, timedelta

import cv2
from PyQt6.QtCore import Qt, QEvent, QPoint, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget, QToolBar, QDockWidget,
    QTabWidget, QComboBox, QInputDialog,
)

from app_paths import get_data_dir
from camera_stream import CameraStreamThread
from video_display_label import VideoDisplayLabel

LAYOUT_FILENAME = "monitor_layout.json"
_DISPLAY_FPS = 15.0


def _bgr_to_pixmap(frame):
    """تبدیل فریم BGR (numpy) به QPixmap — نسخه‌ی سبک مخصوص این ماژول."""
    if frame is None or frame.size == 0:
        return None
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    # .copy() الزامی است تا QImage به بافر موقت numpy اشاره نکند.
    img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(img)


def list_screens():
    """مانیتورهای متصل: لیستی از (نام، QRect، QScreen) به ترتیب QApplication."""
    out = []
    try:
        screens = QApplication.screens()
    except Exception:
        return out
    for scr in screens:
        try:
            out.append((scr.name() or "مانیتور", scr.geometry(), scr))
        except Exception:
            continue
    return out


def _migrate_layout(saved):
    """مهاجرت چیدمان قدیمی (1.1.0، کلید screens) به فرمت پنجره‌محور (1.1.1)."""
    if not isinstance(saved, dict):
        return []
    if isinstance(saved.get("windows"), list):
        return [w for w in saved["windows"] if isinstance(w, dict)]
    wins = []
    for s in saved.get("screens", []) or []:
        if not isinstance(s, dict):
            continue
        cams = [c for c in (s.get("cameras") or []) if c]
        if cams:
            wins.append({"cameras": cams, "geometry": None,
                         "fullscreen": bool(s.get("fullscreen", False))})
    return wins


class MonitorLayoutStore:
    """خواندن/نوشتن چیدمان اتاق مانیتورینگ در monitor_layout.json."""

    def __init__(self, path=None):
        self.path = path or os.path.join(get_data_dir(), LAYOUT_FILENAME)

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return {"version": 2, "windows": _migrate_layout(data)}
        except Exception:
            pass
        return {"version": 2, "windows": []}

    def save(self, layout):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(layout, fh, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False


class MonitorTileWidget(QWidget):
    """تایل نمایشی اتاق مانیتورینگ: تصویر زنده + وضعیت اتصال + ابزارهای مدیریتی.

    (1.2.0) برای دکمه‌های مدیریتی پنجره‌ی نما: رسم محدوده (VideoDisplayLabel
    مشترک)، زوم دیجیتال و انتخاب تایل. استریم نمایشی می‌ماند — هیچ callback
    رویدادی وصل نمی‌شود تا رویدادها تکراری ثبت نشوند.
    """

    selected = pyqtSignal(object)

    _ZOOM_MIN, _ZOOM_MAX = 1.0, 8.0

    def __init__(self, cam: dict, rtsp_url: str, face_engine, parent=None):
        super().__init__(parent)
        self.cam = cam
        self._last_display_ts = 0.0
        self.stream_thread = None
        self._zoom = 1.0
        self._zoom_center = (0.5, 0.5)
        self._is_selected = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)

        header = QHBoxLayout()
        self.name_label = QLabel(cam.get("name") or cam.get("ip", ""))
        self.name_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        self.status_label = QLabel("در حال اتصال...")
        self.status_label.setStyleSheet("color: #aaa; font-size: 11px;")
        header.addWidget(self.name_label)
        header.addStretch()
        header.addWidget(self.status_label)
        lay.addLayout(header)

        self.video_label = VideoDisplayLabel("در انتظار تصویر...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background: #000; color: #888;")
        self.video_label.setMinimumSize(160, 120)
        lay.addWidget(self.video_label, 1)
        try:
            self.video_label.set_confirmed_regions(cam.get("regions") or [])
        except Exception:
            pass
        # کلیک روی لیبل هم تایل را انتخاب کند (بدون بلاک کردن رسم محدوده).
        self.video_label.installEventFilter(self)
        self.name_label.installEventFilter(self)

        # مثل تایل‌های اصلی: هر تایل استریم خودش را دارد؛ فقط callbackهای
        # رویداد (چهره/پلاک/حریق/شخص) وصل نمی‌شود — نمایش صرف.
        self.stream_thread = CameraStreamThread(rtsp_url, face_engine)
        try:
            self.stream_thread.set_person_tracking(bool(cam.get("person_tracking")))
        except Exception:
            pass
        self.stream_thread.frame_ready.connect(self._on_frame_ready)
        self.stream_thread.error_signal.connect(self._on_error)
        self.stream_thread.connected_signal.connect(self._on_connected)
        self.stream_thread.stream_status_signal.connect(self._on_stream_status)
        self.stream_thread.start()

    # ------------------------------------------------------------- انتخاب --
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            self.selected.emit(self)
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        self.selected.emit(self)
        super().mousePressEvent(event)

    def set_selected(self, on: bool):
        self._is_selected = bool(on)
        if on:
            self.setStyleSheet("QWidget { border: 2px solid #4da3ff; border-radius: 4px; }")
        else:
            self.setStyleSheet("QWidget { border: 2px solid transparent; border-radius: 4px; }")

    # ---------------------------------------------------------------- زوم --
    def set_zoom(self, zoom: float):
        self._zoom = min(self._ZOOM_MAX, max(self._ZOOM_MIN, float(zoom)))
        if self._zoom <= 1.0:
            self._zoom_center = (0.5, 0.5)

    def zoom_step(self, direction: int):
        self.set_zoom(self._zoom * (1.25 if direction > 0 else 0.8))

    def _zoom_crop_origin(self):
        zw = 1.0 / self._zoom
        cx, cy = self._zoom_center
        x0 = min(max(cx - zw / 2.0, 0.0), 1.0 - zw)
        y0 = min(max(cy - zw / 2.0, 0.0), 1.0 - zw)
        return (x0, y0, zw, zw)

    def _on_frame_ready(self, display_frame, raw_frame):
        # محدودیت نرخ نمایش (~۱۵ فریم‌برثانیه) مثل تایل اصلی.
        now = time.monotonic()
        if now - self._last_display_ts < 1.0 / _DISPLAY_FPS:
            return
        self._last_display_ts = now
        frame = display_frame
        try:
            if self._zoom > 1.0:
                h, w = frame.shape[:2]
                x0, y0, zw, zh = self._zoom_crop_origin()
                px0, py0 = int(x0 * w), int(y0 * h)
                px1 = min(int((x0 + zw) * w), w)
                py1 = min(int((y0 + zh) * h), h)
                if px1 > px0 and py1 > py0:
                    frame = frame[py0:py1, px0:px1]
                self.video_label.set_zoom_crop_norm((x0, y0, zw, zh))
            else:
                self.video_label.set_zoom_crop_norm(None)
            pixmap = _bgr_to_pixmap(frame)
            if pixmap is not None:
                self.video_label.setPixmap(pixmap.scaled(
                    self.video_label.width(), self.video_label.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        except Exception:
            pass

    def _on_error(self, msg):
        self.status_label.setText(f"خطا: {(msg or '')[:60]}")
        self.status_label.setStyleSheet("color: #e74c3c; font-size: 11px;")

    def _on_connected(self):
        self.status_label.setText("متصل")
        self.status_label.setStyleSheet("color: #2ecc71; font-size: 11px;")

    def _on_stream_status(self, info):
        try:
            state = (info or {}).get("state", "")
        except Exception:
            state = ""
        mapping = {
            "connected": ("متصل", "#2ecc71"),
            "reconnecting": ("در حال تلاش مجدد...", "#f39c12"),
            "weak": ("سیگنال ضعیف", "#f39c12"),
            "down": ("قطع", "#e74c3c"),
        }
        if state in mapping:
            text, color = mapping[state]
            self.status_label.setText(text)
            self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")

    def stop(self):
        th, self.stream_thread = self.stream_thread, None
        if th is not None:
            try:
                th.stop()
            except Exception:
                pass


class MonitorWindow(QMainWindow):
    """پنجره‌ی نمایش مستقل اتاق مانیتورینگ — کاملاً movable و resizable.

    کاربر با موس می‌گیرد و روی هر مانیتوری می‌اندازد؛ F11 یا دابل‌کلیک:
    تمام‌صفحه.
    """

    def __init__(self, title, cam_ids, cameras_by_id, build_rtsp_url,
                 face_engine, on_closed=None, parent=None,
                 camera_store=None, report_store=None, on_person_toggle=None):
        super().__init__(parent)
        self.tiles = []
        self._on_closed = on_closed
        self._cam_ids = list(cam_ids or [])
        self._cameras_by_id = dict(cameras_by_id or {})
        self._build_rtsp_url = build_rtsp_url
        self._face_engine = face_engine
        self.camera_store = camera_store
        self.report_store = report_store
        self._on_person_toggle = on_person_toggle
        self._selected_tile = None
        self._display_count = 0  # 0 = خودکار (همه)
        self._listen_session = None
        self._drawing = False
        self.setWindowTitle(title)
        central = QWidget()
        self.setCentralWidget(central)
        self.grid = QGridLayout(central)
        self.grid.setContentsMargins(6, 6, 6, 6)
        self.grid.setSpacing(6)
        self._build_toolbar()
        self._build_dock()
        self._rebuild_tiles()
        self._select_tile(self.tiles[0] if self.tiles else None)
        # F11: تمام‌صفحه؛ دابل‌کلیک روی هرجای پنجره هم همین کار را می‌کند.
        self._fs_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self._fs_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._fs_shortcut.activated.connect(self.toggle_fullscreen)
        self.installEventFilter(self)

    # ------------------------------------------------- نوار ابزار مدیریتی --
    def _build_toolbar(self):
        tb = QToolBar("مدیریت دوربین")
        tb.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        self.draw_btn = QPushButton("✏ رسم محدوده")
        self.draw_btn.setToolTip("رسم محدوده‌ی هشدار روی تصویر دوربین انتخاب‌شده")
        self.draw_btn.setCheckable(True)
        self.draw_btn.clicked.connect(self._on_draw_toggle)
        tb.addWidget(self.draw_btn)

        self.zones_btn = QPushButton("🗂 محدوده‌ها")
        self.zones_btn.setToolTip("مدیریت محدوده‌های دوربین انتخاب‌شده (لیست/ویرایش/حذف)")
        self.zones_btn.clicked.connect(lambda: self._toggle_dock(0))
        tb.addWidget(self.zones_btn)

        self.people_btn = QPushButton("👥 شمارش")
        self.people_btn.setToolTip("فعال/غیرفعال کردن ردیابی و شمارش افراد برای دوربین انتخاب‌شده")
        self.people_btn.setCheckable(True)
        self.people_btn.clicked.connect(self._on_people_toggle)
        tb.addWidget(self.people_btn)

        self.audio_btn = QPushButton("🎧 صدا")
        self.audio_btn.setToolTip("پخش/قطع صدای زنده‌ی دوربین انتخاب‌شده")
        self.audio_btn.setCheckable(True)
        self.audio_btn.clicked.connect(self._toggle_audio)
        tb.addWidget(self.audio_btn)

        tb.addWidget(QLabel("زوم:"))
        zoom_out = QPushButton("−")
        zoom_out.setFixedWidth(30)
        zoom_out.clicked.connect(lambda: self._on_zoom_step(-1))
        tb.addWidget(zoom_out)
        zoom_in = QPushButton("+")
        zoom_in.setFixedWidth(30)
        zoom_in.clicked.connect(lambda: self._on_zoom_step(1))
        tb.addWidget(zoom_in)

        tb.addWidget(QLabel("چیدمان:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("خودکار", 0)
        self.layout_combo.addItem("۱ دوربین", 1)
        self.layout_combo.addItem("۴ دوربین", 4)
        self.layout_combo.addItem("۹ دوربین", 9)
        self.layout_combo.addItem("۱۶ دوربین", 16)
        self.layout_combo.setToolTip("تعداد دوربین‌های نمایشی این پنجره")
        self.layout_combo.currentIndexChanged.connect(self._on_display_count_changed)
        tb.addWidget(self.layout_combo)

        self.events_btn = QPushButton("📋 رویدادها")
        self.events_btn.setToolTip("پنل رویدادهای مرتبط با دوربین انتخاب‌شده")
        self.events_btn.clicked.connect(lambda: self._toggle_dock(1))
        tb.addWidget(self.events_btn)

        self.console_btn = QPushButton("🎛 کنسول")
        self.console_btn.setToolTip(
            "باز کردن کنسول کامل مدیریت دوربین برای دوربین انتخاب‌شده\n(پخش بازبینی + همه‌ی ابزارها) — فقط برای کانال‌های NVR")
        self.console_btn.clicked.connect(self._open_full_console)
        tb.addWidget(self.console_btn)

    # ------------------------------------------------------------ داک --
    def _build_dock(self):
        self.dock = QDockWidget("پنل مدیریت", self)
        self.dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea |
                                  Qt.DockWidgetArea.LeftDockWidgetArea)
        tabs = QTabWidget()
        tabs.addTab(self._build_zones_tab(), "محدوده‌ها")
        tabs.addTab(self._build_events_tab(), "رویدادها")
        self.dock_tabs = tabs
        self.dock.setWidget(tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide()

    def _toggle_dock(self, tab_index):
        try:
            if self.dock.isVisible():
                if self.dock_tabs.currentIndex() == tab_index:
                    self.dock.hide()
                    return
            self.dock_tabs.setCurrentIndex(tab_index)
            if tab_index == 0:
                self._refresh_zone_list()
            else:
                self._refresh_events()
            self.dock.show()
        except Exception:
            pass

    def _build_zones_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.zone_list = QListWidget()
        layout.addWidget(self.zone_list, 1)
        row = QHBoxLayout()
        edit_btn = QPushButton("ویرایش شکل")
        edit_btn.clicked.connect(self._on_zone_edit)
        row.addWidget(edit_btn)
        del_btn = QPushButton("🗑 حذف")
        del_btn.clicked.connect(self._on_zone_delete)
        row.addWidget(del_btn)
        layout.addLayout(row)
        self.zone_edit_save_btn = QPushButton("💾 ذخیره‌ی ویرایش")
        self.zone_edit_save_btn.clicked.connect(self._on_zone_edit_save)
        self.zone_edit_save_btn.hide()
        layout.addWidget(self.zone_edit_save_btn)
        self.zone_edit_cancel_btn = QPushButton("↩ لغو")
        self.zone_edit_cancel_btn.clicked.connect(self._on_zone_edit_cancel)
        self.zone_edit_cancel_btn.hide()
        layout.addWidget(self.zone_edit_cancel_btn)
        return w

    def _build_events_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("رویدادهای مرتبط (دابل‌کلیک = باز کردن کنسول در آن لحظه):"))
        self.event_list = QListWidget()
        self.event_list.itemDoubleClicked.connect(self._on_event_double_clicked)
        layout.addWidget(self.event_list, 1)
        return w

    # -------------------------------------------------------------- تایل‌ها --
    def _rebuild_tiles(self):
        for t in self.tiles:
            try:
                t.stop()
            except Exception:
                pass
        while self.grid.count():
            item = self.grid.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.deleteLater()
        self.tiles = []
        self._selected_tile = None

        cam_ids = self._cam_ids
        if self._display_count > 0:
            cam_ids = cam_ids[:self._display_count]
        n = len(cam_ids)
        cols = max(1, math.ceil(math.sqrt(n))) if n else 1
        for i, cid in enumerate(cam_ids):
            cam = self._cameras_by_id.get(cid)
            if cam is None:
                continue
            try:
                rtsp_url = self._build_rtsp_url(cam)
            except Exception:
                continue
            tile = MonitorTileWidget(cam, rtsp_url, self._face_engine)
            tile.selected.connect(self._select_tile)
            self.tiles.append(tile)
            self.grid.addWidget(tile, i // cols, i % cols)
        if not self.tiles:
            lbl = QLabel("دوربینی برای این پنجره انتخاب نشده است.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(lbl, 0, 0)

    def _on_display_count_changed(self, _idx):
        try:
            self._display_count = int(self.layout_combo.currentData() or 0)
        except Exception:
            self._display_count = 0
        self._cancel_draw_mode()
        self._stop_audio()
        self._rebuild_tiles()
        self._select_tile(self.tiles[0] if self.tiles else None)

    def _select_tile(self, tile):
        if tile is None or tile not in self.tiles:
            return
        self._cancel_draw_mode()
        for t in self.tiles:
            t.set_selected(t is tile)
        self._selected_tile = tile
        self._refresh_zone_list()
        self._refresh_people_btn()
        if self.dock.isVisible() and self.dock_tabs.currentIndex() == 1:
            self._refresh_events()

    def _selected_cam(self):
        t = self._selected_tile
        return t.cam if t is not None else None

    # ------------------------------------------------------------ رسم محدوده --
    def _on_draw_toggle(self, checked):
        tile = self._selected_tile
        if tile is None or tile.cam is None:
            self.draw_btn.setChecked(False)
            return
        self._drawing = bool(checked)
        tile.video_label.set_draw_mode(bool(checked))
        if checked:
            tile.video_label.region_drawn.connect(self._on_region_drawn)

    def _cancel_draw_mode(self):
        if not self._drawing:
            return
        self._drawing = False
        try:
            self.draw_btn.setChecked(False)
        except Exception:
            pass
        for t in self.tiles:
            try:
                t.video_label.set_draw_mode(False)
                t.video_label.region_drawn.disconnect(self._on_region_drawn)
            except Exception:
                pass

    def _on_region_drawn(self, points):
        tile = self._selected_tile
        cam = self._selected_cam()
        if tile is None or cam is None or not points or len(points) < 3:
            return
        regions = list(cam.get("regions") or [])
        name, ok = QInputDialog.getText(
            self, "نام محدوده", "نام این محدوده را وارد کنید:",
            text=f"محدوده‌ی {len(regions) + 1}")
        if not ok:
            tile.video_label.set_pending_points_norm(None)
            return
        region = {
            "id": str(uuid.uuid4()),
            "number": len(regions) + 1,
            "name": (name or "").strip() or f"محدوده‌ی {len(regions) + 1}",
            "points": [tuple(p) for p in points],
        }
        regions.append(region)
        self._save_regions(cam, regions)
        tile.video_label.set_confirmed_regions(regions)
        self._cancel_draw_mode()
        self._refresh_zone_list()

    def _save_regions(self, cam, regions):
        try:
            cam["regions"] = list(regions)
            if self.camera_store is not None:
                self.camera_store.update_camera(cam["id"], regions=list(regions))
        except Exception:
            pass

    def _refresh_zone_list(self):
        try:
            self.zone_list.clear()
        except Exception:
            return
        cam = self._selected_cam()
        if cam is None:
            return
        for r in cam.get("regions") or []:
            item = QListWidgetItem(f"{r.get('number', '?')} — {r.get('name', '')}")
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.zone_list.addItem(item)

    def _selected_region(self):
        try:
            item = self.zone_list.currentItem()
            return item.data(Qt.ItemDataRole.UserRole) if item else None
        except Exception:
            return None

    def _on_zone_delete(self):
        cam = self._selected_cam()
        region = self._selected_region()
        if cam is None or region is None:
            return
        regions = [r for r in (cam.get("regions") or [])
                   if r.get("id") != region.get("id")]
        for i, r in enumerate(regions, 1):
            r["number"] = i
        self._save_regions(cam, regions)
        tile = self._selected_tile
        if tile is not None:
            tile.video_label.set_confirmed_regions(regions)
        self._refresh_zone_list()

    def _on_zone_edit(self):
        tile = self._selected_tile
        region = self._selected_region()
        if tile is None or region is None:
            QMessageBox.information(self, "ویرایش", "اول یک محدوده را از لیست انتخاب کنید.")
            return
        self._cancel_draw_mode()
        tile.video_label.set_pending_points_norm(region.get("points") or [],
                                                 editing_existing=True)
        self.zone_edit_save_btn.show()
        self.zone_edit_cancel_btn.show()

    def _on_zone_edit_save(self):
        tile = self._selected_tile
        cam = self._selected_cam()
        region = self._selected_region()
        if tile is None or cam is None or region is None:
            return
        pts = tile.video_label.pending_points_norm()
        if not pts or len(pts) < 3:
            QMessageBox.warning(self, "خطا", "شکل معتبر نیست (حداقل ۳ نقطه).")
            return
        for r in cam.get("regions") or []:
            if r.get("id") == region.get("id"):
                r["points"] = [tuple(p) for p in pts]
                break
        self._save_regions(cam, list(cam.get("regions") or []))
        tile.video_label.set_pending_points_norm(None)
        tile.video_label.set_confirmed_regions(cam.get("regions") or [])
        self.zone_edit_save_btn.hide()
        self.zone_edit_cancel_btn.hide()
        self._refresh_zone_list()

    def _on_zone_edit_cancel(self):
        tile = self._selected_tile
        if tile is not None:
            tile.video_label.set_pending_points_norm(None)
        self.zone_edit_save_btn.hide()
        self.zone_edit_cancel_btn.hide()

    # ---------------------------------------------------------- شمارش افراد --
    def _refresh_people_btn(self):
        cam = self._selected_cam()
        try:
            self.people_btn.blockSignals(True)
            self.people_btn.setChecked(bool((cam or {}).get("person_tracking")))
            self.people_btn.setEnabled(cam is not None)
        except Exception:
            pass
        finally:
            try:
                self.people_btn.blockSignals(False)
            except Exception:
                pass

    def _on_people_toggle(self, checked):
        cam = self._selected_cam()
        tile = self._selected_tile
        if cam is None:
            self.people_btn.setChecked(not checked)
            return
        try:
            cam["person_tracking"] = bool(checked)
            if self.camera_store is not None:
                self.camera_store.update_camera(cam["id"], person_tracking=bool(checked))
            if tile is not None and tile.stream_thread is not None:
                tile.stream_thread.set_person_tracking(bool(checked))
            if self._on_person_toggle is not None:
                try:
                    self._on_person_toggle(cam["id"], bool(checked))
                except Exception:
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------ صدا --
    def _toggle_audio(self):
        if self._listen_session is not None:
            self._stop_audio()
            return
        cam = self._selected_cam()
        if not cam:
            self.audio_btn.setChecked(False)
            return
        try:
            from camera_audio import ListenSession
            session = ListenSession()
            if session.start(dict(cam)):
                self._listen_session = session
                self.audio_btn.setText("🔊 وصل است")
            else:
                self.audio_btn.setChecked(False)
        except Exception:
            self.audio_btn.setChecked(False)

    def _stop_audio(self):
        try:
            if self._listen_session is not None:
                try:
                    self._listen_session.stop()
                except Exception:
                    pass
                self._listen_session = None
        except Exception:
            pass
        try:
            self.audio_btn.setChecked(False)
            self.audio_btn.setText("🎧 صدا")
        except Exception:
            pass

    # ------------------------------------------------------------------ زوم --
    def _on_zoom_step(self, direction):
        tile = self._selected_tile
        if tile is not None:
            tile.zoom_step(direction)

    # --------------------------------------------------------------- رویدادها --
    def _refresh_events(self):
        try:
            self.event_list.clear()
        except Exception:
            return
        if self.report_store is None:
            return
        cam = self._selected_cam()
        cam_name = (cam or {}).get("name")
        if not cam_name:
            return
        try:
            now = datetime.now()
            start_s = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            end_s = now.strftime("%Y-%m-%d %H:%M:%S")
            rows = self.report_store.query(start=start_s, end=end_s,
                                           camera_name=cam_name, limit=100)
        except Exception:
            rows = []
        for row in rows or []:
            try:
                ts, event_type = row[0], row[1]
                item = QListWidgetItem(f"{str(ts)[11:19]} — {event_type}")
                item.setData(Qt.ItemDataRole.UserRole, str(ts))
                self.event_list.addItem(item)
            except Exception:
                continue

    def _open_full_console(self):
        """دکمه‌ی «کنسول کامل»: باز کردن NVRConsoleDialog برای دوربین
        انتخاب‌شده در لبه‌ی زنده (۴۰ ثانیه‌ی اخیر) — فقط کانال‌های NVR."""
        cam = self._selected_cam()
        nvr_id = (cam or {}).get("nvr_id")
        channel = (cam or {}).get("channel")
        if not nvr_id or channel is None or self.camera_store is None:
            QMessageBox.information(
                self, "کنسول",
                "کنسول کامل فقط برای کانال‌های NVR است؛ این دوربین مستقل است.")
            return
        try:
            nvr = self.camera_store.get_nvr(nvr_id)
        except Exception:
            nvr = None
        if not nvr:
            return
        try:
            from nvr_console_dialog import NVRConsoleDialog
            now = datetime.now()
            dlg = NVRConsoleDialog(
                nvr, channel, now - timedelta(seconds=40), now,
                camera_label=(cam or {}).get("name", ""), parent=self,
                camera_store=self.camera_store, report_store=self.report_store,
                on_person_toggle=self._on_person_toggle)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن کنسول ممکن نشد:\n{e}")

    def _on_event_double_clicked(self, item):
        """دابل‌کلیک روی رویداد = باز کردن کنسول مدیریت دوربین در آن لحظه
        (اگر دوربین کانال NVR باشد)."""
        ts = item.data(Qt.ItemDataRole.UserRole)
        cam = self._selected_cam()
        if cam is None:
            return
        try:
            event_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return
        nvr_id = cam.get("nvr_id")
        channel = cam.get("channel")
        if not nvr_id or channel is None or self.camera_store is None:
            QMessageBox.information(
                self, "رویداد",
                "این دوربین کانال NVR نیست؛ کنسول پخش بازبینی فقط برای کانال‌های NVR است.")
            return
        try:
            nvr = self.camera_store.get_nvr(nvr_id)
        except Exception:
            nvr = None
        if not nvr:
            return
        try:
            from nvr_console_dialog import NVRConsoleDialog
            dlg = NVRConsoleDialog(
                nvr, channel,
                event_dt - timedelta(seconds=10), event_dt + timedelta(seconds=30),
                camera_label=cam.get("name", ""), parent=self,
                camera_store=self.camera_store, report_store=self.report_store,
                on_person_toggle=self._on_person_toggle)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن کنسول ممکن نشد:\n{e}")

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonDblClick:
            self.toggle_fullscreen()
            return True
        return super().eventFilter(obj, event)

    def toggle_fullscreen(self):
        try:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        except Exception:
            pass

    def show_initial(self, geometry=None, fullscreen=False):
        """نمایش اولیه: با موقعیت/اندازه‌ی ذخیره‌شده، یا اندازه‌ی پیش‌فرض."""
        if geometry and len(geometry) == 4:
            try:
                x, y, w, h = [int(v) for v in geometry]
                self.setGeometry(x, y, max(320, w), max(240, h))
            except Exception:
                pass
        if fullscreen:
            # نیتیو باید ساخته شده باشد تا تمام‌صفحه درست اعمال شود.
            self.winId()
            self.showFullScreen()
        else:
            self.show()

    def window_state(self):
        """وضعیت قابل ذخیره‌سازی پنجره: موقعیت/اندازه + تمام‌صفحه."""
        try:
            if self.isFullScreen():
                ng = self.normalGeometry()
                geo = [ng.x(), ng.y(), ng.width(), ng.height()]
            else:
                g = self.geometry()
                geo = [g.x(), g.y(), g.width(), g.height()]
        except Exception:
            geo = None
        return {"geometry": geo, "fullscreen": bool(self.isFullScreen())}

    def closeEvent(self, event):
        try:
            self._cancel_draw_mode()
        except Exception:
            pass
        self._stop_audio()
        for tile in self.tiles:
            try:
                tile.stop()
            except Exception:
                pass
        try:
            if self._on_closed is not None:
                self._on_closed(self)
        except Exception:
            pass
        super().closeEvent(event)


class MonitorRoomDialog(QDialog):
    """دیالوگ تنظیم اتاق مانیتورینگ: مدیریت پنجره‌ها و دوربین‌های هر پنجره."""

    def __init__(self, cameras, saved_windows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🖥 اتاق مانیتورینگ")
        self.setMinimumSize(560, 480)
        self._cameras = cameras
        self._rows = []  # dict: {box, list}
        # callbackها را مدیر ست می‌کند
        self.on_open_window = None    # (cam_ids) -> None
        self.on_open_all = None       # (configs) -> None
        self.on_close_all = None      # () -> None
        self._save_callback = None    # () -> bool

        lay = QVBoxLayout(self)
        n_screens = len(list_screens())
        info = QLabel(
            f"{n_screens} مانیتور شناسایی شد. برای هر پنجره دوربین‌ها را تیک بزنید؛ "
            "پنجره‌ها کاملاً قابل جابه‌جایی و تغییر اندازه‌اند — با موس بگیرید و روی "
            "هر مانیتوری که می‌خواهید بیندازید. تمام‌صفحه: F11 یا دابل‌کلیک روی پنجره.")
        info.setWordWrap(True)
        lay.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.addStretch()
        scroll.setWidget(self._content)
        lay.addWidget(scroll, 1)

        add_row = QHBoxLayout()
        self.add_btn = QPushButton("➕ افزودن پنجره‌ی جدید")
        self.add_btn.clicked.connect(lambda: self.add_window_row())
        add_row.addWidget(self.add_btn)
        add_row.addStretch()
        lay.addLayout(add_row)

        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("💾 ذخیره‌ی چیدمان")
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.open_all_btn = QPushButton("🖥 باز کردن همه")
        self.open_all_btn.clicked.connect(self._on_open_all_clicked)
        self.close_all_btn = QPushButton("✖ بستن همه")
        self.close_all_btn.clicked.connect(self._on_close_all_clicked)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.open_all_btn)
        btn_row.addWidget(self.close_all_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        if saved_windows:
            for w in saved_windows:
                self.add_window_row((w or {}).get("cameras") or [])
        else:
            self.add_window_row()

    def set_save_callback(self, cb):
        self._save_callback = cb

    def add_window_row(self, checked_ids=None):
        checked = set(checked_ids or [])
        idx = len(self._rows) + 1
        box = QGroupBox(f"پنجره‌ی {idx}")
        box_lay = QVBoxLayout(box)
        lst = QListWidget()
        lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for cam in self._cameras:
            cid = cam.get("id")
            label = cam.get("name") or cam.get("ip", "")
            item = QListWidgetItem(str(label))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if cid in checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            lst.addItem(item)
        lst.setMaximumHeight(min(200, max(80, 28 * max(1, len(self._cameras)))))
        box_lay.addWidget(QLabel("دوربین‌های این پنجره:"))
        box_lay.addWidget(lst)
        row_btns = QHBoxLayout()
        open_btn = QPushButton("🖥 باز کردن این پنجره")
        remove_btn = QPushButton("🗑 حذف")
        row_btns.addWidget(open_btn)
        row_btns.addWidget(remove_btn)
        row_btns.addStretch()
        box_lay.addLayout(row_btns)
        # درج قبل از addStretch انتهایی
        self._content_lay.insertWidget(self._content_lay.count() - 1, box)
        row = {"box": box, "list": lst}
        self._rows.append(row)
        open_btn.clicked.connect(lambda: self._open_row(row))
        remove_btn.clicked.connect(lambda: self._remove_row(row))
        self._renumber()
        return row

    def _renumber(self):
        for i, row in enumerate(self._rows, 1):
            row["box"].setTitle(f"پنجره‌ی {i}")

    def _remove_row(self, row):
        if row in self._rows:
            self._rows.remove(row)
            row["box"].setParent(None)
            row["box"].deleteLater()
            self._renumber()

    def _row_cameras(self, row):
        cams = []
        lst = row["list"]
        for i in range(lst.count()):
            item = lst.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                cams.append(item.data(Qt.ItemDataRole.UserRole))
        return cams

    def window_configs(self):
        """پیکربندی پنجره‌ها: فقط ردیف‌هایی که حداقل یک دوربین دارند."""
        out = []
        for row in self._rows:
            cams = self._row_cameras(row)
            if cams:
                out.append({"cameras": cams})
        return out

    def _open_row(self, row):
        cams = self._row_cameras(row)
        if not cams:
            QMessageBox.information(self, "اتاق مانیتورینگ",
                                    "برای این پنجره هیچ دوربینی انتخاب نشده است.")
            return
        if self.on_open_window is not None:
            self.on_open_window(cams)

    def _on_open_all_clicked(self):
        if self.on_open_all is not None:
            self.on_open_all(self.window_configs())

    def _on_close_all_clicked(self):
        if self.on_close_all is not None:
            self.on_close_all()

    def _on_save_clicked(self):
        if self._save_callback is not None:
            ok = self._save_callback()
            QMessageBox.information(
                self, "اتاق مانیتورینگ",
                "چیدمان ذخیره شد." if ok else "خطا در ذخیره‌ی چیدمان.")


class MonitorRoomManager:
    """مدیریت پنجره‌های اتاق مانیتورینگ — ساخته و نگه‌داری توسط MainWindow."""

    def __init__(self, camera_store, face_engine, report_store=None,
                 on_person_toggle=None):
        self.camera_store = camera_store
        self.face_engine = face_engine
        self.report_store = report_store
        self._on_person_toggle = on_person_toggle
        self.store = MonitorLayoutStore()
        self.windows = []  # list of {"window": MonitorWindow, "cameras": [...]}
        self._win_counter = 0
        self._cascade = 0

    def open_setup_dialog(self, parent):
        cameras = self.camera_store.get_cameras()
        saved = self.store.load().get("windows", [])
        dlg = MonitorRoomDialog(cameras, saved, parent)
        dlg.on_open_window = self.open_window
        dlg.on_open_all = self.open_all_configs
        dlg.on_close_all = self.close_all
        dlg.set_save_callback(self.save_current_layout)
        dlg.exec()

    def _next_title(self):
        self._win_counter += 1
        return f"🖥 اتاق مانیتورینگ — پنجره‌ی {self._win_counter}"

    def _on_window_closed(self, win):
        self.windows = [e for e in self.windows if e["window"] is not win]

    def open_window(self, cam_ids, geometry=None, fullscreen=False):
        """باز کردن یک پنجره‌ی جدید (movable/resizable) با دوربین‌های داده‌شده."""
        cameras_by_id = {c.get("id"): c for c in self.camera_store.get_cameras()}
        cam_ids = [cid for cid in (cam_ids or []) if cid in cameras_by_id]
        if not cam_ids:
            return None
        win = MonitorWindow(
            self._next_title(), cam_ids, cameras_by_id,
            self.camera_store.build_rtsp_url, self.face_engine,
            on_closed=self._on_window_closed,
            camera_store=self.camera_store, report_store=self.report_store,
            on_person_toggle=self._on_person_toggle)
        if geometry is None:
            # آبشاری روی صفحه‌ی اصلی تا پنجره‌ها روی هم نیفتند
            try:
                primary = QApplication.primaryScreen()
                if primary is not None:
                    top_left = primary.geometry().topLeft()
                    off = (self._cascade % 8) * 36
                    win.move(top_left + QPoint(60 + off, 60 + off))
                    self._cascade += 1
            except Exception:
                pass
        win.show_initial(geometry=geometry, fullscreen=fullscreen)
        self.windows.append({"window": win, "cameras": list(cam_ids)})
        return win

    def open_all_configs(self, configs):
        self.close_all()
        for cfg in configs or []:
            if isinstance(cfg, dict):
                self.open_window((cfg or {}).get("cameras") or [])

    def close_all(self):
        wins, self.windows = self.windows, []
        for entry in wins:
            try:
                entry["window"].close()
            except Exception:
                pass

    def save_current_layout(self):
        """ذخیره‌ی چیدمان: وضعیت واقعی پنجره‌های باز (دوربین‌ها + موقعیت/اندازه
        + تمام‌صفحه). اگر پنجره‌ای باز نیست، چیزی برای ذخیره نیست."""
        windows = []
        for entry in self.windows:
            try:
                win = entry["window"]
                st = win.window_state()
                windows.append({"cameras": list(entry["cameras"]),
                                "geometry": st.get("geometry"),
                                "fullscreen": bool(st.get("fullscreen"))})
            except Exception:
                continue
        return self.store.save({"version": 2, "windows": windows})
