# -*- coding: utf-8 -*-
"""monitor_room.py — اتاق مانیتورینگ (multi-monitor) نسخه‌ی مک (1.2.0).

قابلیت «اتاق مانیتورینگ»: تقسیم تصاویر دوربین‌ها روی چند مانیتور متصل به سیستم.
- شناسایی مانیتورها با QApplication.screens() (نمایشی، برای راهنمایی کاربر)
- دیالوگ تنظیمات: افزودن/حذف «پنجره»، انتخاب دوربین‌های هر پنجره
- هر پنجره یک پنجره‌ی مستقل، کاملاً movable و resizable است — کاربر با موس
  می‌گیرد و روی هر مانیتوری که می‌خواهد می‌اندازد؛ F11 یا دابل‌کلیک: تمام‌صفحه
- ذخیره‌ی چیدمان در monitor_layout.json (دوربین‌ها + موقعیت/اندازه + تمام‌صفحه‌ی
  هر پنجره)؛ بعد از بستن و باز کردن برنامه، چیدمان بازیابی می‌شود.
  (چیدمان قدیمی نسخه‌ی 1.1.0 به‌صورت خودکار مهاجرت می‌کند.)

(1.2.0) بازطراحی از نو به دستور طه: پنجره‌های «نما» کپی کامل صفحه‌ی اصلی‌اند —
از CameraPageWidget مشترک (camera_page.py) استفاده می‌کنند؛ همان ظاهر، چیدمان
و همه‌ی دکمه‌ها (single source of truth). کال‌بک‌های رویداد None هستند تا
رویدادها تکراری ثبت نشوند.
"""

import json
import os
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QEvent, QPoint, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialog, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget, QToolBar,
)

from app_paths import get_data_dir
from main_page import MainPageWidget

LAYOUT_FILENAME = "monitor_layout.json"


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


class MonitorWindow(QMainWindow):
    """پنجره‌ی نمایش مستقل اتاق مانیتورینگ — کپی کامل صفحه‌ی اصلی.

    (1.2.0) بازطراحی به دستور طه: کل لایه‌ی صفحه‌ی اصلی (چپ + وسط + راست)
    از MainPageWidget مشترک (main_page.py) ساخته می‌شود — همان ظاهر، چیدمان
    و همه‌ی دکمه‌ها و قابلیت‌ها (single source of truth). داده‌ها مشترک‌اند
    (همان camera_store و report_store).
    کال‌بک‌های رویداد None هستند تا رویدادها تکراری ثبت نشوند؛ پنل رویدادها
    از report_store مشترک خوانده می‌شود.
    افزونه‌ی مخصوص نما: دکمه‌ی «🎛 کنسول» برای باز کردن کنسول کامل دوربین.

    کاملاً movable و resizable؛ F11 یا دابل‌کلیک: تمام‌صفحه.
    """

    def __init__(self, title, cam_ids, cameras_by_id, build_rtsp_url,
                 face_engine, on_closed=None, parent=None,
                 camera_store=None, report_store=None, on_person_toggle=None,
                 on_open_monitor_room=None, alarm_player=None, building_fire=None,
                 on_building_fire_log=None):
        super().__init__(parent)
        self._on_closed = on_closed
        self.camera_store = camera_store
        self.report_store = report_store
        self._on_person_toggle = on_person_toggle
        self._cam_ids = [cid for cid in (cam_ids or [])
                         if cid in (cameras_by_id or {})]
        self.setWindowTitle(title)

        # کل صفحه‌ی اصلی — همان کامپوننت‌های مشترک.
        self.page = MainPageWidget(
            face_engine, camera_store,
            on_open_monitor_room=on_open_monitor_room,
            on_people_toggle_all=self._on_wall_people_toggle_all,
            alarm_player=alarm_player, building_fire=building_fire,
            on_building_fire_log=on_building_fire_log,
            report_store=report_store,
            parent=self,
        )
        self.setCentralWidget(self.page)
        cams = [(cameras_by_id or {})[cid] for cid in self._cam_ids]
        self.page.camera_page.set_cameras(cams, build_rtsp_url)

        # سازگاری: wall به صفحه‌ی مرکزی اشاره می‌کند.
        self.wall = self.page.camera_page

        # نوار افزونه‌ی نما: دکمه‌ی کنسول کامل.
        extras = QToolBar("افزونه‌های نما")
        extras.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, extras)
        self.console_btn = QPushButton("🎛 کنسول")
        self.console_btn.setToolTip(
            "باز کردن کنسول کامل مدیریت دوربین برای دوربین انتخاب‌شده\n"
            "(پخش بازبینی + همه‌ی ابزارها) — فقط برای کانال‌های NVR")
        self.console_btn.clicked.connect(self._open_full_console)
        extras.addWidget(self.console_btn)

        # دابل‌کلیک روی رویداد = باز کردن کنسول در آن لحظه.
        try:
            self.page.events_panel.events_panel_list.itemDoubleClicked.connect(
                self._on_event_double_clicked)
        except Exception:
            pass

        # تازه‌سازی دوره‌ای پنل رویدادها از report_store مشترک.
        self._events_timer = QTimer(self)
        self._events_timer.timeout.connect(self._refresh_events_panel)
        self._events_timer.start(30000)
        self._refresh_events_panel()

        # F11: تمام‌صفحه؛ دابل‌کلیک روی هرجای پنجره هم همین کار را می‌کند.
        self._fs_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self._fs_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._fs_shortcut.activated.connect(self.toggle_fullscreen)
        self.installEventFilter(self)

    # ------------------------------------------------- همگام‌سازی شمارش --
    def _on_wall_people_toggle_all(self, enabled):
        """تاگل شمارش افراد دیوار نما -> اعمال روی دوربین‌های پنجره‌ی اصلی هم."""
        if self._on_person_toggle is None:
            return
        for cid in self._cam_ids:
            try:
                self._on_person_toggle(cid, bool(enabled))
            except Exception:
                pass

    def _selected_cam(self):
        try:
            slot = self.wall.selected_slot()
            return slot.cam if slot is not None else None
        except Exception:
            return None

    # ------------------------------------------------------------ رویدادها --
    def _refresh_events_panel(self):
        try:
            self.page.events_panel.refresh_from_report_store(limit=100)
        except Exception:
            pass

    def _on_event_double_clicked(self, item):
        """دابل‌کلیک روی رویداد = باز کردن کنسول مدیریت دوربین در آن لحظه."""
        try:
            ts = item.data(Qt.ItemDataRole.UserRole)
            text = item.text()
        except Exception:
            return
        cam = self._find_cam_for_event(text)
        if cam is None:
            cam = self._selected_cam()
        if cam is None:
            return
        try:
            event_dt = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
        except Exception:
            event_dt = None
        self._open_console_for_cam(cam, event_dt)

    def _find_cam_for_event(self, text):
        try:
            for cid in self._cam_ids:
                c = (self.camera_store.get_camera(cid) if self.camera_store else None)
                if c and c.get("name") and c["name"] in str(text):
                    return c
        except Exception:
            pass
        return None


    def _open_console_for_cam(self, cam, event_dt=None):
        """باز کردن کنسول NVR برای دوربین؛ event_dt لحظه‌ی رویداد (اختیاری)."""
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
            if event_dt is None:
                now = datetime.now()
                start_dt, end_dt = now - timedelta(seconds=40), now
            else:
                start_dt = event_dt - timedelta(seconds=10)
                end_dt = event_dt + timedelta(seconds=30)
            dlg = NVRConsoleDialog(
                nvr, channel, start_dt, end_dt,
                camera_label=(cam or {}).get("name", ""), parent=self,
                camera_store=self.camera_store, report_store=self.report_store,
                on_person_toggle=self._on_person_toggle)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن کنسول ممکن نشد:\n{e}")

    def _open_full_console(self):
        """دکمه‌ی «کنسول کامل»: باز کردن NVRConsoleDialog برای دوربین
        انتخاب‌شده در لبه‌ی زنده (۴۰ ثانیه‌ی اخیر) — فقط کانال‌های NVR."""
        self._open_console_for_cam(self._selected_cam())

    # ------------------------------------------------- پنجره (مثل قبل) --
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
            self.wall.camera_grid.stop_all()
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
                 on_person_toggle=None, alarm_player=None, building_fire=None,
                 on_building_fire_log=None):
        self.camera_store = camera_store
        self.face_engine = face_engine
        self.report_store = report_store
        self._on_person_toggle = on_person_toggle
        self._alarm_player = alarm_player
        self._building_fire = building_fire
        self._on_building_fire_log = on_building_fire_log
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

    def _open_setup_for_window(self):
        """کال‌بک دکمه‌ی «🖥 اتاق مانیتورینگ» داخل پنجره‌های نما."""
        try:
            parent = self.windows[-1]["window"] if self.windows else None
            self.open_setup_dialog(parent)
        except Exception:
            pass

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
            on_person_toggle=self._on_person_toggle,
            on_open_monitor_room=self._open_setup_for_window,
            alarm_player=self._alarm_player, building_fire=self._building_fire,
            on_building_fire_log=self._on_building_fire_log)
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
