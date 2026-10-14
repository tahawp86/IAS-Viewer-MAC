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

import cv2
from PyQt6.QtCore import Qt, QEvent, QPoint
from PyQt6.QtGui import QImage, QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QGroupBox, QHBoxLayout,
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
    """پنجره‌ی نمایش مستقل اتاق مانیتورینگ — کاملاً movable و resizable.

    کاربر با موس می‌گیرد و روی هر مانیتوری می‌اندازد؛ F11 یا دابل‌کلیک:
    تمام‌صفحه.
    """

    def __init__(self, title, cam_ids, cameras_by_id, build_rtsp_url,
                 face_engine, on_closed=None, parent=None):
        super().__init__(parent)
        self.tiles = []
        self._on_closed = on_closed
        self.setWindowTitle(title)
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
            lbl = QLabel("دوربینی برای این پنجره انتخاب نشده است.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(lbl, 0, 0)
        # F11: تمام‌صفحه؛ دابل‌کلیک روی هرجای پنجره هم همین کار را می‌کند.
        self._fs_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self._fs_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._fs_shortcut.activated.connect(self.toggle_fullscreen)
        self.installEventFilter(self)

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

    def __init__(self, camera_store, face_engine):
        self.camera_store = camera_store
        self.face_engine = face_engine
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
            on_closed=self._on_window_closed)
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
