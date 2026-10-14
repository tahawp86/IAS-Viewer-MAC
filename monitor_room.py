# -*- coding: utf-8 -*-
"""monitor_room.py — اتاق مانیتورینگ (multi-monitor) نسخه‌ی مک (1.1.0).

قابلیت «اتاق مانیتورینگ»: تقسیم تصاویر دوربین‌ها روی چند مانیتور متصل به سیستم.
- شناسایی مانیتورها با QApplication.screens()
- دیالوگ تنظیمات: برای هر مانیتور انتخاب دوربین‌ها + حالت تمام‌صفحه
- یک پنجره‌ی نمایش جداگانه برای هر مانیتور، روی همان صفحه (تمام‌صفحه یا پنجره‌ای)
- ذخیره‌ی چیدمان در monitor_layout.json (پوشه‌ی دیتای برنامه)؛ بعد از بستن و باز
  کردن برنامه، چیدمان در دیالوگ بازیابی می‌شود.

نکته‌ی طراحی: تایل‌های این پنجره‌ها فقط «نمایش» می‌دهند — هر تایل استریم
مستقیم خودش را باز می‌کند (CameraStreamThread) ولی هیچ callback رویدادی
(چهره/پلاک/حریق/شخص) وصل نمی‌شود تا رویدادها تکراری ثبت نشوند.
"""

import json
import math
import os
import time

import cv2
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app_paths import get_data_dir
from camera_stream import CameraStreamThread

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


class MonitorLayoutStore:
    """خواندن/نوشتن چیدمان مانیتورها در monitor_layout.json."""

    def __init__(self, path=None):
        self.path = path or os.path.join(get_data_dir(), LAYOUT_FILENAME)

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            screens = data.get("screens", [])
            if isinstance(screens, list):
                return {"screens": [s for s in screens if isinstance(s, dict)]}
        except Exception:
            pass
        return {"screens": []}

    def save(self, layout):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(layout, fh, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False


class MonitorTileWidget(QWidget):
    """تایل نمایشی سبک اتاق مانیتورینگ: فقط تصویر زنده + وضعیت اتصال."""

    def __init__(self, cam: dict, rtsp_url: str, face_engine, parent=None):
        super().__init__(parent)
        self.cam = cam
        self._last_display_ts = 0.0
        self.stream_thread = None

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

        self.video_label = QLabel("در انتظار تصویر...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background: #000; color: #888;")
        self.video_label.setMinimumSize(160, 120)
        lay.addWidget(self.video_label, 1)

        # مثل تایل‌های اصلی: هر تایل استریم خودش را دارد؛ فقط callbackهای
        # رویداد (چهره/پلاک/حریق/شخص) وصل نمی‌شود — نمایش صرف.
        self.stream_thread = CameraStreamThread(rtsp_url, face_engine)
        self.stream_thread.frame_ready.connect(self._on_frame_ready)
        self.stream_thread.error_signal.connect(self._on_error)
        self.stream_thread.connected_signal.connect(self._on_connected)
        self.stream_thread.stream_status_signal.connect(self._on_stream_status)
        self.stream_thread.start()

    def _on_frame_ready(self, display_frame, raw_frame):
        # محدودیت نرخ نمایش (~۱۵ فریم‌برثانیه) مثل تایل اصلی.
        now = time.monotonic()
        if now - self._last_display_ts < 1.0 / _DISPLAY_FPS:
            return
        self._last_display_ts = now
        frame = display_frame
        try:
            lw, lh = self.video_label.width(), self.video_label.height()
        except Exception:
            lw, lh = 0, 0
        if lw > 0 and lh > 0:
            h, w = frame.shape[:2]
            scale = min(lw / w, lh / h)
            if scale < 1.0:
                frame = cv2.resize(
                    frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_LINEAR)
        pixmap = _bgr_to_pixmap(frame)
        if pixmap is not None:
            self.video_label.setPixmap(pixmap)

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
    """پنجره‌ی نمایش یک مانیتور در اتاق مانیتورینگ."""

    def __init__(self, screen_name, cam_ids, cameras_by_id, build_rtsp_url,
                 face_engine, parent=None):
        super().__init__(parent)
        self.screen_name = screen_name
        self.tiles = []
        self.setWindowTitle(f"🖥 اتاق مانیتورینگ — {screen_name}")
        central = QWidget()
        self.setCentralWidget(central)
        grid = QGridLayout(central)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(6)
        n = len(cam_ids)
        cols = max(1, math.ceil(math.sqrt(n))) if n else 1
        for i, cid in enumerate(cam_ids):
            cam = cameras_by_id.get(cid)
            if cam is None:
                continue
            try:
                rtsp_url = build_rtsp_url(cam)
            except Exception:
                continue
            tile = MonitorTileWidget(cam, rtsp_url, face_engine)
            self.tiles.append(tile)
            grid.addWidget(tile, i // cols, i % cols)
        if not self.tiles:
            lbl = QLabel("دوربینی برای این مانیتور انتخاب نشده است.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(lbl, 0, 0)

    def show_on_screen(self, screen, fullscreen=False):
        """نمایش پنجره روی مانیتور مشخص (تمام‌صفحه یا پنجره‌ای)."""
        # اول پنجره‌ی نیتیو ساخته می‌شود تا setScreen اثر کند.
        self.winId()
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.setScreen(screen)
            except Exception:
                pass
        if fullscreen:
            self.showFullScreen()
        else:
            try:
                geo = screen.geometry()
                self.move(geo.topLeft() + QPoint(40, 40))
                self.resize(min(1280, max(640, geo.width() - 80)),
                            min(800, max(480, geo.height() - 80)))
            except Exception:
                pass
            self.show()

    def closeEvent(self, event):
        for tile in self.tiles:
            try:
                tile.stop()
            except Exception:
                pass
        super().closeEvent(event)


class MonitorRoomDialog(QDialog):
    """دیالوگ تنظیم اتاق مانیتورینگ: انتخاب دوربین‌های هر مانیتور + تمام‌صفحه."""

    def __init__(self, cameras, saved_layout, on_open, on_close_all, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🖥 اتاق مانیتورینگ")
        self.setMinimumSize(560, 480)
        self._on_open = on_open
        self._on_close_all = on_close_all
        self._save_callback = None
        self._screen_rows = []  # (screen_name, QScreen, fullscreen_cb, QListWidget)

        saved_by_screen = {}
        for s in (saved_layout or {}).get("screens", []):
            if isinstance(s, dict) and s.get("screen"):
                saved_by_screen[s.get("screen")] = s

        lay = QVBoxLayout(self)
        info = QLabel("برای هر مانیتور، دوربین‌هایی که باید نمایش داده شوند را تیک بزنید. "
                      "چیدمان با «ذخیره‌ی چیدمان» نگه داشته می‌شود و بعد از بستن و باز کردن "
                      "برنامه در همین دیالوگ بازیابی می‌شود.")
        info.setWordWrap(True)
        lay.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_lay = QVBoxLayout(content)
        screens = list_screens()
        if not screens:
            content_lay.addWidget(QLabel("مانیتوری شناسایی نشد."))
        for idx, (name, geo, scr) in enumerate(screens):
            box = QGroupBox(f"مانیتور {idx + 1} — {name} ({geo.width()}×{geo.height()})")
            box_lay = QVBoxLayout(box)
            saved = saved_by_screen.get(name, {})
            fs_cb = QCheckBox("تمام‌صفحه")
            fs_cb.setChecked(bool(saved.get("fullscreen", True)))
            box_lay.addWidget(fs_cb)
            box_lay.addWidget(QLabel("دوربین‌ها:"))
            lst = QListWidget()
            lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
            saved_cams = set(saved.get("cameras", []) or [])
            for cam in cameras:
                cid = cam.get("id")
                label = cam.get("name") or cam.get("ip", "")
                item = QListWidgetItem(str(label))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked
                                   if cid in saved_cams else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, cid)
                lst.addItem(item)
            lst.setMaximumHeight(min(220, max(90, 28 * max(1, len(cameras)))))
            box_lay.addWidget(lst)
            content_lay.addWidget(box)
            self._screen_rows.append((name, scr, fs_cb, lst))
        content_lay.addStretch()
        scroll.setWidget(content)
        lay.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("💾 ذخیره‌ی چیدمان")
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.open_btn = QPushButton("🖥 باز کردن مانیتورها")
        self.open_btn.clicked.connect(self._on_open_clicked)
        self.close_all_btn = QPushButton("✖ بستن همه")
        self.close_all_btn.clicked.connect(self._on_close_all_clicked)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.close_all_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def set_save_callback(self, cb):
        self._save_callback = cb

    def current_layout(self):
        screens = []
        for name, _scr, fs_cb, lst in self._screen_rows:
            cams = []
            for i in range(lst.count()):
                item = lst.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    cams.append(item.data(Qt.ItemDataRole.UserRole))
            screens.append({"screen": name, "cameras": cams,
                            "fullscreen": bool(fs_cb.isChecked())})
        return {"version": 1, "screens": screens}

    def _on_save_clicked(self):
        if self._save_callback is not None:
            ok = self._save_callback(self.current_layout())
            QMessageBox.information(
                self, "اتاق مانیتورینگ",
                "چیدمان ذخیره شد." if ok else "خطا در ذخیره‌ی چیدمان.")

    def _on_open_clicked(self):
        if self._on_open is not None:
            self._on_open(self.current_layout())

    def _on_close_all_clicked(self):
        if self._on_close_all is not None:
            self._on_close_all()


class MonitorRoomManager:
    """مدیریت پنجره‌های اتاق مانیتورینگ — ساخته و نگه‌داری توسط MainWindow."""

    def __init__(self, camera_store, face_engine):
        self.camera_store = camera_store
        self.face_engine = face_engine
        self.store = MonitorLayoutStore()
        self.windows = []

    def open_setup_dialog(self, parent):
        cameras = self.camera_store.get_cameras()
        saved = self.store.load()
        dlg = MonitorRoomDialog(cameras, saved, self.open_all, self.close_all, parent)
        dlg.set_save_callback(self.store.save)
        dlg.exec()

    def open_all(self, layout):
        self.close_all()
        screens = {name: scr for name, _geo, scr in list_screens()}
        cameras_by_id = {c.get("id"): c for c in self.camera_store.get_cameras()}
        build_rtsp_url = self.camera_store.build_rtsp_url
        for entry in (layout or {}).get("screens", []):
            if not isinstance(entry, dict):
                continue
            scr = screens.get(entry.get("screen"))
            if scr is None:
                continue
            cam_ids = [cid for cid in (entry.get("cameras") or [])
                       if cid in cameras_by_id]
            if not cam_ids:
                continue
            win = MonitorWindow(entry.get("screen"), cam_ids, cameras_by_id,
                                build_rtsp_url, self.face_engine)
            win.show_on_screen(scr, fullscreen=bool(entry.get("fullscreen", True)))
            self.windows.append(win)

    def close_all(self):
        wins, self.windows = self.windows, []
        for win in wins:
            try:
                win.close()
            except Exception:
                pass
