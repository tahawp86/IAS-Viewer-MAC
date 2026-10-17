# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Taha Arefi
"""کنسول مدیریت دوربین (1.2.0) — توسعه‌ی «گزارش ویدیویی» NVR.

از دیالوگ گزارش‌ها باز می‌شود. علاوه بر پخش بازبینی ضبط‌شده‌ی NVR حوالی
زمان رویداد، این ابزارهای مدیریتی را هم در همان پنجره دارد:
  ۱) رسم محدوده (زون) روی تصویر — با VideoDisplayLabel مشترک با پنجره‌ی اصلی
  ۲) شمارش/ردیابی افراد (فعال/غیرفعال + تنظیمات کامل)
  ۳) مدیریت محدوده‌ها (لیست/ویرایش شکل/حذف)
  ۴) صدای زنده‌ی دوربین (ListenSession مشترک با پنجره‌ی اصلی)
  ۵) زوم دیجیتال روی تصویر (چرخ ماوس + اسلایدر نوار ابزار)
  ۶) انتخاب تعداد نمایش (۱ یا ۴ — کانال‌های همان NVR)
  ۷) پنل رویدادهای مرتبط (دابل‌کلیک = پرش به آن رویداد)

بخش پخش بازبینی (nvr_web_url ،_PlaybackThread) از nvr_playback_dialog به
اینجا منتقل شده تا همه‌چیز یکجا باشد؛ آن ماژول فقط یک shim سازگاری است.
"""

import uuid
import webbrowser
from datetime import datetime, timedelta

import cv2
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QEvent, QPointF
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSizePolicy, QTabWidget, QWidget, QListWidget, QListWidgetItem,
    QCheckBox, QComboBox, QSlider, QInputDialog, QMessageBox, QSplitter,
)

from rtsp_utils import open_capture
from nvr_playback import build_playback_urls
from video_display_label import VideoDisplayLabel


def nvr_web_url(nvr: dict) -> str | None:
    """آدرس پنل وب NVR برای فالبک «باز کردن وب NVR».

    فقط scheme+host است (بدون پورت سفارشی، بدون هیچ کریدنشنالی) — کاربر در
    مرورگر خودش لاگین می‌کند و به بازه‌ی زمانی رویداد می‌رود.
    """
    try:
        ip = (nvr or {}).get("ip", "").strip()
        if not ip:
            return None
        scheme = "https" if (nvr or {}).get("use_https") else "http"
        return f"{scheme}://{ip}"
    except Exception:
        return None


# همان گزینه‌های کم‌تاخیر پخش زنده (rtsp_utils.STREAM_FFMPEG_OPTS) برای پخش
# بازبینی هم مناسب است؛ فقط stimeout کمی بیشتر است چون خیلی از NVRها برای
# شروع پخش یک بازه‌ی بایگانی‌شده (به‌خصوص از هارد پر/کند) کندتر از پخش زنده
# پاسخ می‌دهند.
# (2.0.16-beta - پایداری) rw_timeout اضافه شد: بدون آن، cap.read() وسط
# استریم می‌توانست برای همیشه بلاک بماند و بستن دیالوگ در آن حالت، ترد را
# زنده رها می‌کرد و به کرش qFatal («Close Program») می‌رسید.
PLAYBACK_FFMPEG_OPTS = (
    "rtsp_transport;tcp|stimeout;8000000|max_delay;300000|"
    "buffer_size;102400|fflags;nobuffer|flags;low_delay|"
    "rw_timeout;5000000"
)


def _bgr_to_pixmap(frame):
    if frame is None or frame.size == 0:
        return None
    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb_image.shape
    bytes_per_line = ch * w
    qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qt_img)


class _PlaybackThread(QThread):
    frame_ready = pyqtSignal(object)
    connected = pyqtSignal(str)  # برچسب برند/آدرسی که واقعاً وصل شد
    finished_playing = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, nvr, channel, start_dt, end_dt, candidates, parent=None):
        super().__init__(parent)
        self._nvr = nvr
        self._channel = channel
        self._start_dt = start_dt
        # (2.0.52-beta) آدرس‌های Replay ممکن است تا لبه‌ی زنده ادامه پیدا
        # کنند؛ بعد از این مدت (طول بازه + کمی حاشیه) حتماً توقف کن.
        try:
            self._max_seconds = max(10.0, (end_dt - start_dt).total_seconds() + 10.0)
        except Exception:
            self._max_seconds = 300.0
        self._candidates = candidates  # [(برچسب, آدرس), ...]
        self._stop_flag = False
        self._tried_labels = []

    def stop(self):
        self._stop_flag = True

    def run(self):
        import time as _time
        from nvr_playback import try_onvif_replay_url
        # (2.0.52-beta) اول: ONVIF Replay استاندارد (مستقل از برند)؛ بعد قالب‌ها
        onvif_hit = try_onvif_replay_url(self._nvr, self._channel, self._start_dt,
                                         timeout=10)
        candidates = ([onvif_hit] if onvif_hit else []) + list(self._candidates)
        for label, url in candidates:
            if self._stop_flag:
                return
            self._tried_labels.append(label)
            cap = open_capture(url, PLAYBACK_FFMPEG_OPTS)
            try:
                if not cap.isOpened():
                    continue
                got_any_frame = False
                t0 = _time.monotonic()
                # چند تلاش اول ممکن است قبل از رسیدن به اولین کی‌فریم بایگانی
                # شکست بخورد (دقیقاً همان دلیل PROBE_READ_ATTEMPTS در rtsp_utils).
                empty_reads = 0
                while not self._stop_flag:
                    if _time.monotonic() - t0 > self._max_seconds:
                        break  # بازه تمام شد (مخصوص استریم‌های Replay تا لبه‌ی زنده)
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        empty_reads += 1
                        if not got_any_frame and empty_reads > 8:
                            break  # این کاندید اصلاً پاسخ نداد - برو سراغ بعدی
                        if got_any_frame and empty_reads > 15:
                            break  # بازه‌ی ضبط‌شده تمام شد
                        continue
                    empty_reads = 0
                    if not got_any_frame:
                        got_any_frame = True
                        self.connected.emit(label)
                    self.frame_ready.emit(frame)
                if got_any_frame:
                    self.finished_playing.emit()
                    return
            finally:
                cap.release()
        if not self._stop_flag:
            tried = "، ".join(self._tried_labels) if self._tried_labels else "هیچ"
            self.error.emit(
                "امکان پخش این بازه از NVR وجود نداشت.\n"
                f"روش‌های امتحان‌شده: {tried}.\n\n"
                "دلایل ممکن:\n"
                "• در این بازه‌ی زمانی چیزی روی هارد NVR ضبط نشده است؛\n"
                "• آدرس پخش بازبینی این دستگاه با قالب‌های استاندارد فرق دارد.\n\n"
                "راه‌حل: روی NVR در لیست دوربین‌ها راست‌کلیک کنید ← «⚙ قالب آدرس "
                "پخش بازبینی» و الگوی دقیق دستگاه‌تان را وارد کنید (از دفترچه‌ی "
                "راهنما یا پنل وب NVR)."
            )


_ZOOM_MIN, _ZOOM_MAX = 1.0, 8.0


class _ConsoleTile(QWidget):
    """یک خانه‌ی ویدیوی کنسول: پخش بازبینی + زوم دیجیتال + رسم محدوده.

    cam: دیکشنری دوربین از camera_store (یا None برای خانه‌ی خالی).
    """

    selected = pyqtSignal(object)   # کلیک روی خانه
    zoom_changed = pyqtSignal(float)  # برای همگام‌سازی اسلایدر نوار ابزار
    playback_error = pyqtSignal()  # خطای پخش این خانه (برای فالبک وب)

    def __init__(self, nvr, channel, cam, label_text, parent=None):
        super().__init__(parent)
        self.nvr = nvr
        self.channel = channel
        self.cam = cam
        self._base_name = label_text or (f"کانال {channel}" if channel else "—")
        self._zoom = 1.0
        self._zoom_center = (0.5, 0.5)
        self._thread = None
        self._state = "idle"  # idle/connecting/playing/finished/error

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.name_label = QLabel(self._base_name)
        self.name_label.setStyleSheet("font-weight: bold; padding: 2px;")
        layout.addWidget(self.name_label)
        self.video_label = VideoDisplayLabel("در حال اتصال به NVR...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background:#111; color:#ccc;")
        self.video_label.setMinimumSize(300, 220)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Expanding)
        layout.addWidget(self.video_label, 1)
        # کلیک روی لیبل ویدیو هم خانه را انتخاب کند (رویداد به خود لیبل
        # می‌رسد، نه به ویجت والد) — بدون بلاک کردن رسم محدوده.
        self.video_label.installEventFilter(self)
        self.name_label.installEventFilter(self)
        self.set_selected(False)

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

    # --------------------------------------------------------------- زوم --
    def set_zoom(self, zoom: float, center=None):
        zoom = min(_ZOOM_MAX, max(_ZOOM_MIN, float(zoom)))
        if center is not None:
            cx, cy = center
            self._zoom_center = (min(max(cx, 0.0), 1.0), min(max(cy, 0.0), 1.0))
        if zoom <= 1.0:
            self._zoom = 1.0
            self._zoom_center = (0.5, 0.5)
        else:
            self._zoom = zoom
        self.zoom_changed.emit(self._zoom)

    def zoom_in(self, center=None):
        self.set_zoom(self._zoom * 1.25, center)

    def zoom_out(self, center=None):
        self.set_zoom(self._zoom / 1.25, center)

    def _label_pos_to_full_norm(self, label_pos):
        """نگاشت نقطه‌ی روی لیبل به مختصات نرمال کل فریم (برای زوم مرکزنشانگر)."""
        pix = self.video_label.pixmap()
        if pix is None or pix.isNull():
            return None
        lw, lh = self.video_label.width(), self.video_label.height()
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            return None
        rx = (lw - pw) / 2.0
        ry = (lh - ph) / 2.0
        u = (label_pos.x() - rx) / pw
        v = (label_pos.y() - ry) / ph
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return None
        if self._zoom > 1.0:
            zw = 1.0 / self._zoom
            zh = 1.0 / self._zoom
            x0, y0 = self._zoom_crop_origin()
            return (x0 + u * zw, y0 + v * zh)
        return (u, v)

    def _zoom_crop_origin(self):
        zw = 1.0 / self._zoom
        zh = 1.0 / self._zoom
        cx, cy = self._zoom_center
        x0 = min(max(cx - zw / 2.0, 0.0), 1.0 - zw)
        y0 = min(max(cy - zh / 2.0, 0.0), 1.0 - zh)
        return (x0, y0)

    def wheelEvent(self, event):
        try:
            label_pos = self.video_label.mapFromParent(event.position().toPoint())
            norm = self._label_pos_to_full_norm(QPointF(label_pos))
        except Exception:
            norm = None
        if event.angleDelta().y() > 0:
            self.zoom_in(center=norm)
        else:
            self.zoom_out(center=norm)
        event.accept()

    # --------------------------------------------------------------- پخش --
    def start_playback(self, start_dt, end_dt):
        self.stop_thread()
        self._set_state("connecting")
        candidates = build_playback_urls(self.nvr, self.channel, start_dt, end_dt)
        self._thread = _PlaybackThread(self.nvr, self.channel, start_dt, end_dt,
                                       candidates, self)
        self._thread.frame_ready.connect(self._on_frame)
        self._thread.connected.connect(lambda _lbl: self._set_state("playing"))
        self._thread.finished_playing.connect(lambda: self._set_state("finished"))
        self._thread.error.connect(self._on_thread_error)
        self._thread.start()

    def _on_thread_error(self, msg):
        self._set_state("error")
        self.video_label.setText(msg)
        try:
            self.playback_error.emit()
        except Exception:
            pass

    def _set_state(self, state):
        self._state = state
        mark = {"idle": "", "connecting": "⏳ ", "playing": "▶ ",
                "finished": "⏹ ", "error": "⚠ "}.get(state, "")
        self.name_label.setText(f"{mark}{self._base_name}")

    def _on_frame(self, frame):
        try:
            h, w = frame.shape[:2]
            if self._zoom > 1.0:
                x0, y0 = self._zoom_crop_origin()
                zw = 1.0 / self._zoom
                zh = 1.0 / self._zoom
                px0, py0 = int(x0 * w), int(y0 * h)
                px1 = min(int((x0 + zw) * w), w)
                py1 = min(int((y0 + zh) * h), h)
                if px1 > px0 and py1 > py0:
                    frame = frame[py0:py1, px0:px1]
                self.video_label.set_zoom_crop_norm((x0, y0, zw, zh))
            else:
                self.video_label.set_zoom_crop_norm(None)
            pixmap = _bgr_to_pixmap(frame)
            if pixmap is None:
                return
            self.video_label.setPixmap(pixmap.scaled(
                self.video_label.width(), self.video_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        except Exception:
            pass

    def stop_thread(self):
        """توقف امن ترد پخش (همان منطق _stop_thread دیالوگ قبلی)."""
        try:
            t = self._thread
            self._thread = None
            if t is not None:
                t.stop()
                if t.isRunning() and not t.wait(6000):
                    t.terminate()
                    t.wait(3000)
        except Exception:
            pass


# حاشیه‌ی بازه‌ی پخش حوالی هر رویداد (همان مقادیر reports_dialog).
_PLAYBACK_MARGIN_BEFORE = timedelta(seconds=10)
_PLAYBACK_MARGIN_AFTER = timedelta(seconds=30)


class NVRConsoleDialog(QDialog):
    """کنسول مدیریت دوربین: پخش بازبینی NVR + ابزارهای مدیریتی یکجا."""

    def __init__(self, nvr: dict, channel, start_dt, end_dt, camera_label="",
                 parent=None, camera_store=None, report_store=None,
                 on_person_toggle=None):
        super().__init__(parent)
        self.setWindowTitle(f"🎛 کنسول مدیریت دوربین — {camera_label or 'پخش NVR'}")
        self.resize(1200, 740)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        self._nvr = nvr or {}
        self._channel = channel
        self._event_channel = channel
        self._camera_label = camera_label or ""
        self._start_dt = start_dt
        self._end_dt = end_dt
        self._event_dt = start_dt + _PLAYBACK_MARGIN_BEFORE
        self.camera_store = camera_store
        self.report_store = report_store
        self._on_person_toggle = on_person_toggle

        self._tiles = []
        self._selected_tile = None
        self._listen_session = None
        self._drawing = False

        # در RTL اولین ویجت سمت راست می‌نشیند: اول پنل تب‌ها، بعد ناحیه‌ی ویدیو.
        root = QHBoxLayout(self)
        root.addWidget(self._build_side_panel(), 0)
        root.addWidget(self._build_video_area(), 1)

        self._build_tiles()
        self._select_tile(self._tiles[0] if self._tiles else None)
        self._refresh_events()

    # ------------------------------------------------------- ناحیه‌ی ویدیو --
    def _build_video_area(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.info_label = QLabel()
        self.info_label.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(self.info_label)

        self.grid = QGridLayout()
        self.grid.setSpacing(4)
        layout.addLayout(self.grid, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#888; padding: 2px;")
        layout.addWidget(self.status_label)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("تعداد نمایش:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("۱ دوربین", 1)
        self.layout_combo.addItem("۴ دوربین", 4)
        self.layout_combo.setToolTip(
            "در حالت ۴ دوربین، کانال‌های دیگر همین NVR هم در همین بازه‌ی زمانی پخش می‌شوند.")
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        toolbar.addWidget(self.layout_combo)

        toolbar.addWidget(QLabel("زوم:"))
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedWidth(32)
        self.zoom_out_btn.setToolTip("کوچک‌نمایی تصویر دوربین انتخاب‌شده")
        self.zoom_out_btn.clicked.connect(self._on_zoom_out)
        toolbar.addWidget(self.zoom_out_btn)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(100, 800)  # 1.0x تا 8.0x
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.setToolTip("زوم دیجیتال روی تصویر دوربین انتخاب‌شده (چرخ ماوس هم کار می‌کند)")
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        toolbar.addWidget(self.zoom_slider)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(32)
        self.zoom_in_btn.setToolTip("بزرگ‌نمایی تصویر دوربین انتخاب‌شده")
        self.zoom_in_btn.clicked.connect(self._on_zoom_in)
        toolbar.addWidget(self.zoom_in_btn)

        self.audio_btn = QPushButton("🎧 صدا")
        self.audio_btn.setToolTip("پخش/قطع صدای زنده‌ی دوربین انتخاب‌شده")
        self.audio_btn.setCheckable(True)
        self.audio_btn.clicked.connect(self._toggle_audio)
        toolbar.addWidget(self.audio_btn)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setToolTip("بلندی صدا")
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.hide()
        toolbar.addWidget(self.volume_slider)

        toolbar.addStretch()
        # فالبک وب (1.2.0): اگر پخش RTSP ممکن نشد نمایش داده می‌شود.
        self.web_btn = QPushButton("🌐 باز کردن وب NVR")
        self.web_btn.setToolTip(
            "پخش داخل برنامه ممکن نشد؛ پنل وب NVR در مرورگر باز می‌شود.\n"
            "بعد از ورود، در بخش Playback به بازه‌ی زمانی رویداد بروید.")
        self.web_btn.clicked.connect(self._on_open_nvr_web)
        self.web_btn.hide()
        toolbar.addWidget(self.web_btn)
        close_btn = QPushButton("✕ بستن")
        close_btn.clicked.connect(self.reject)
        toolbar.addWidget(close_btn)
        layout.addLayout(toolbar)
        self._refresh_info()
        return container

    def _refresh_info(self):
        try:
            s = self._start_dt.strftime("%Y-%m-%d %H:%M:%S")
            e = self._end_dt.strftime("%H:%M:%S")
            self.info_label.setText(f"دوربین: {self._camera_label}   |   بازه: {s} تا {e}")
        except Exception:
            pass

    # -------------------------------------------------------------- تایل‌ها --
    def _event_cam(self):
        """دیکت دوربین کانال رویداد از camera_store (یا None)."""
        try:
            if self.camera_store is None:
                return None
            for c in self.camera_store.cameras:
                if (c.get("nvr_id") == self._nvr.get("id")
                        and c.get("channel") == self._channel):
                    return c
        except Exception:
            pass
        return None

    def _sibling_cams(self, limit=3):
        try:
            if self.camera_store is None:
                return []
            out = []
            for c in self.camera_store.cameras:
                if (c.get("nvr_id") == self._nvr.get("id")
                        and c.get("channel") != self._channel):
                    out.append(c)
                    if len(out) >= limit:
                        break
            return out
        except Exception:
            return []

    def _build_tiles(self):
        # تردهای قبلی را امن متوقف کن و گرید را خالی کن
        for t in self._tiles:
            try:
                t.stop_thread()
            except Exception:
                pass
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._tiles = []
        self._selected_tile = None

        count = self.layout_combo.currentData() if hasattr(self, "layout_combo") else 1
        specs = [(self._channel, self._event_cam(), self._camera_label)]
        if count == 4:
            for c in self._sibling_cams(3):
                specs.append((c.get("channel"), c, c.get("name", "")))
            while len(specs) < 4:
                specs.append((None, None, "—"))

        for i, (ch, cam, label) in enumerate(specs):
            tile = _ConsoleTile(self._nvr, ch, cam, label, self)
            tile.selected.connect(self._select_tile)
            tile.zoom_changed.connect(self._on_tile_zoom_changed)
            tile.playback_error.connect(self._maybe_show_web_btn)
            if ch is not None:
                try:
                    tile.video_label.set_confirmed_regions((cam or {}).get("regions") or [])
                except Exception:
                    pass
                tile.start_playback(self._start_dt, self._end_dt)
            else:
                tile.video_label.setText("کانال دیگری در این NVR ثبت نشده است.")
            r, c_ = divmod(i, 2 if count == 4 else 1)
            self.grid.addWidget(tile, r, c_)
            self._tiles.append(tile)
        for r in range(2 if count == 4 else 1):
            self.grid.setRowStretch(r, 1)
        for c_ in range(2 if count == 4 else 1):
            self.grid.setColumnStretch(c_, 1)

    def _on_layout_changed(self, _idx):
        self._stop_audio()
        self._cancel_draw_mode()
        self._build_tiles()
        self._select_tile(self._tiles[0] if self._tiles else None)

    def _select_tile(self, tile):
        if tile is None or tile not in self._tiles:
            return
        self._cancel_draw_mode()
        for t in self._tiles:
            t.set_selected(t is tile)
        self._selected_tile = tile
        try:
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(int(tile._zoom * 100))
        finally:
            self.zoom_slider.blockSignals(False)
        self._refresh_zone_list()
        self._refresh_people_tab()
        self._refresh_events()

    def _selected_cam(self):
        t = self._selected_tile
        return t.cam if t is not None else None

    # ----------------------------------------------------------------- زوم --
    def _on_zoom_slider(self, value):
        tile = self._selected_tile
        if tile is not None:
            tile.set_zoom(value / 100.0)

    def _on_tile_zoom_changed(self, zoom):
        if self._selected_tile is not None:
            try:
                self.zoom_slider.blockSignals(True)
                self.zoom_slider.setValue(int(zoom * 100))
            finally:
                self.zoom_slider.blockSignals(False)

    def _on_zoom_in(self):
        tile = self._selected_tile
        if tile is not None:
            tile.zoom_in()

    def _on_zoom_out(self):
        tile = self._selected_tile
        if tile is not None:
            tile.zoom_out()

    # ----------------------------------------------------------------- صدا --
    def _toggle_audio(self):
        if self._listen_session is not None:
            self._stop_audio()
            return
        cam = self._selected_cam()
        if not cam:
            QMessageBox.information(self, "صدا", "برای این خانه دوربینی ثبت نشده است.")
            self.audio_btn.setChecked(False)
            return
        try:
            from camera_audio import ListenSession
            session = ListenSession()
            try:
                session.state_changed = self._on_audio_state
                session.error_occurred = self._on_audio_error
            except Exception:
                pass
            try:
                session.set_volume(self.volume_slider.value() / 100.0)
            except Exception:
                pass
            if session.start(dict(cam)):
                self._listen_session = session
                self.audio_btn.setText("🔊 وصل است")
                self.volume_slider.show()
                self.status_label.setText("در حال اتصال صدا...")
            else:
                self.status_label.setText("خطا در شروع صدا")
                self.audio_btn.setChecked(False)
        except Exception as e:
            self.status_label.setText(f"خطای صدا: {e}"[:80])
            self.audio_btn.setChecked(False)

    def _on_audio_state(self, state: str):
        try:
            if state == "playing":
                self.status_label.setText("🔊 صدای زنده وصل است")
            elif state.startswith("connecting"):
                self.status_label.setText("در حال اتصال صدا...")
        except Exception:
            pass

    def _on_audio_error(self, msg: str):
        try:
            self.status_label.setText(f"خطای صدا: {msg}"[:80])
        except Exception:
            pass
        self._stop_audio()

    def _on_volume_changed(self, value):
        try:
            if self._listen_session is not None:
                self._listen_session.set_volume(value / 100.0)
        except Exception:
            pass

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
            self.volume_slider.hide()
        except Exception:
            pass

    # ------------------------------------------------------------ فالبک وب --
    def _on_open_nvr_web(self):
        url = nvr_web_url(self._nvr)
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _maybe_show_web_btn(self):
        """اگر پخش هیچ خانه‌ای موفق نبود، فالبک وب را نشان بده."""
        try:
            if any(t._state == "error" for t in self._tiles):
                if nvr_web_url(self._nvr):
                    self.web_btn.show()
        except Exception:
            pass

    # ------------------------------------------------------------ پنل کناری --
    def _build_side_panel(self):
        tabs = QTabWidget()
        tabs.setFixedWidth(300)
        tabs.addTab(self._build_zones_tab(), "محدوده‌ها")
        tabs.addTab(self._build_people_tab(), "شمارش افراد")
        tabs.addTab(self._build_events_tab(), "رویدادها")
        return tabs

    # ------------------------------------------------------ تب محدوده‌ها --
    def _build_zones_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("رسم و مدیریت محدوده‌های هشدار دوربین انتخاب‌شده:"))
        self.draw_btn = QPushButton("✏ رسم محدوده‌ی جدید")
        self.draw_btn.setToolTip(
            "روی تصویر دوربین انتخاب‌شده با کلیک‌های متوالی چندضلعی بکشید؛\n"
            "برای بستن، نزدیک نقطه‌ی اول کلیک کنید.")
        self.draw_btn.setCheckable(True)
        self.draw_btn.clicked.connect(self._on_draw_toggle)
        layout.addWidget(self.draw_btn)

        self.zone_list = QListWidget()
        self.zone_list.setToolTip("محدوده‌های ذخیره‌شده‌ی این دوربین")
        layout.addWidget(self.zone_list, 1)

        row = QHBoxLayout()
        self.zone_edit_btn = QPushButton("ویرایش شکل")
        self.zone_edit_btn.setToolTip("شکل محدوده‌ی انتخاب‌شده را روی تصویر با درگ گوشه‌ها عوض کنید")
        self.zone_edit_btn.clicked.connect(self._on_zone_edit)
        row.addWidget(self.zone_edit_btn)
        self.zone_del_btn = QPushButton("🗑 حذف")
        self.zone_del_btn.clicked.connect(self._on_zone_delete)
        row.addWidget(self.zone_del_btn)
        layout.addLayout(row)

        self.zone_edit_save_btn = QPushButton("💾 ذخیره‌ی ویرایش شکل")
        self.zone_edit_save_btn.clicked.connect(self._on_zone_edit_save)
        self.zone_edit_save_btn.hide()
        layout.addWidget(self.zone_edit_save_btn)
        self.zone_edit_cancel_btn = QPushButton("↩ لغو ویرایش")
        self.zone_edit_cancel_btn.clicked.connect(self._on_zone_edit_cancel)
        self.zone_edit_cancel_btn.hide()
        layout.addWidget(self.zone_edit_cancel_btn)
        layout.addStretch()
        return w

    def _on_draw_toggle(self, checked):
        tile = self._selected_tile
        if tile is None or tile.cam is None:
            self.draw_btn.setChecked(False)
            return
        self._drawing = bool(checked)
        tile.video_label.set_draw_mode(bool(checked))
        if checked:
            tile.video_label.region_drawn.connect(self._on_region_drawn)
            self.status_label.setText("حالت رسم: با کلیک چندضلعی بکشید؛ نزدیک نقطه‌ی اول کلیک کنید تا بسته شود.")
        else:
            try:
                tile.video_label.region_drawn.disconnect(self._on_region_drawn)
            except Exception:
                pass
            self.status_label.setText("")

    def _cancel_draw_mode(self):
        if not self._drawing:
            return
        self._drawing = False
        try:
            self.draw_btn.setChecked(False)
        except Exception:
            pass
        for t in self._tiles:
            try:
                t.video_label.set_draw_mode(False)
                t.video_label.region_drawn.disconnect(self._on_region_drawn)
            except Exception:
                pass

    def _on_region_drawn(self, points):
        """کاربر چندضلعی را بست — نام بگیر و در cam ذخیره کن."""
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
        self.status_label.setText(f"محدوده‌ی «{region['name']}» ذخیره شد.")

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
            label = f"{r.get('number', '?')} — {r.get('name', '')}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.zone_list.addItem(item)

    def _selected_region(self):
        item = self.zone_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_zone_delete(self):
        cam = self._selected_cam()
        region = self._selected_region()
        if cam is None or region is None:
            return
        regions = [r for r in (cam.get("regions") or [])
                   if r.get("id") != region.get("id")]
        # شماره‌گذاری مجدد
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
        self.status_label.setText("گوشه‌ها را با ماوس بکشید؛ بعد «ذخیره‌ی ویرایش شکل» را بزنید.")

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
        self.status_label.setText("ویرایش شکل ذخیره شد.")

    def _on_zone_edit_cancel(self):
        tile = self._selected_tile
        if tile is not None:
            tile.video_label.set_pending_points_norm(None)
        self.zone_edit_save_btn.hide()
        self.zone_edit_cancel_btn.hide()
        self.status_label.setText("")

    # ------------------------------------------------- تب شمارش افراد --
    def _build_people_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.people_cam_label = QLabel("")
        self.people_cam_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.people_cam_label)
        self.people_check = QCheckBox("ردیابی و شمارش افراد فعال باشد")
        self.people_check.setToolTip(
            "با فعال شدن، روی پخش زنده‌ی این دوربین در پنجره‌ی اصلی\n"
            "ردیابی اشخاص انجام می‌شود.")
        self.people_check.toggled.connect(self._on_people_toggle)
        layout.addWidget(self.people_check)
        layout.addWidget(QLabel(
            "این تنظیم در مشخصات دوربین ذخیره می‌شود و روی پخش زنده اعمال می‌گردد."))
        self.people_settings_btn = QPushButton("⚙ تنظیمات کامل ردیابی اشخاص")
        self.people_settings_btn.setToolTip("باز کردن صفحه‌ی کامل ردیابی اشخاص")
        self.people_settings_btn.clicked.connect(self._open_person_settings)
        layout.addWidget(self.people_settings_btn)
        layout.addStretch()
        return w

    def _refresh_people_tab(self):
        cam = self._selected_cam()
        try:
            name = (cam or {}).get("name", "—")
            self.people_cam_label.setText(f"دوربین: {name}")
            self.people_check.blockSignals(True)
            self.people_check.setChecked(bool((cam or {}).get("person_tracking")))
            self.people_check.setEnabled(cam is not None)
            self.people_settings_btn.setEnabled(self.camera_store is not None)
        except Exception:
            pass
        finally:
            try:
                self.people_check.blockSignals(False)
            except Exception:
                pass

    def _on_people_toggle(self, checked):
        cam = self._selected_cam()
        if cam is None or self.camera_store is None:
            return
        try:
            cam["person_tracking"] = bool(checked)
            self.camera_store.update_camera(cam["id"], person_tracking=bool(checked))
            if self._on_person_toggle is not None:
                try:
                    self._on_person_toggle(cam["id"], bool(checked))
                except Exception:
                    pass
            self.status_label.setText(
                f"ردیابی اشخاص برای «{cam.get('name', '')}» {'فعال' if checked else 'غیرفعال'} شد.")
        except Exception as e:
            self.status_label.setText(f"خطا در ذخیره: {e}"[:80])

    def _open_person_settings(self):
        if self.camera_store is None:
            return
        try:
            from person_track_dialog import PersonTrackPage
            dlg = QDialog(self)
            dlg.setWindowTitle("تنظیمات ردیابی اشخاص")
            dlg.resize(950, 620)
            lay = QVBoxLayout(dlg)
            page = PersonTrackPage(self.camera_store,
                                   on_person_toggle=self._on_person_toggle,
                                   parent=dlg)
            lay.addWidget(page)
            dlg.exec()
            self._refresh_people_tab()
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن تنظیمات ممکن نشد:\n{e}")

    # ------------------------------------------------------ تب رویدادها --
    def _build_events_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.events_cam_label = QLabel("")
        self.events_cam_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.events_cam_label)
        layout.addWidget(QLabel("رویدادهای مرتبط (دابل‌کلیک = پرش به آن لحظه):"))
        self.event_list = QListWidget()
        self.event_list.itemDoubleClicked.connect(self._on_event_double_clicked)
        layout.addWidget(self.event_list, 1)
        return w

    def _refresh_events(self):
        try:
            self.event_list.clear()
        except Exception:
            return
        if self.report_store is None:
            return
        tile = self._selected_tile
        cam = self._selected_cam()
        cam_name = (cam or {}).get("name") or self._camera_label
        try:
            self.events_cam_label.setText(f"دوربین: {cam_name}")
        except Exception:
            pass
        if not cam_name:
            return
        try:
            start_s = (self._event_dt - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            end_s = (self._event_dt + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            rows = self.report_store.query(start=start_s, end=end_s,
                                           camera_name=cam_name, limit=200)
        except Exception:
            rows = []
        for row in rows or []:
            try:
                ts, event_type = row[0], row[1]
                detail = row[12] if len(row) > 12 else ""
                label = f"{str(ts)[11:19]} — {event_type}"
                if detail:
                    label += f" — {str(detail)[:40]}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, str(ts))
                self.event_list.addItem(item)
            except Exception:
                continue

    def _on_event_double_clicked(self, item):
        ts = item.data(Qt.ItemDataRole.UserRole)
        try:
            event_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return
        self._seek_to_event(event_dt)

    def _seek_to_event(self, event_dt):
        """پرش همه‌ی خانه‌ها به بازه‌ی رویداد انتخاب‌شده."""
        self._event_dt = event_dt
        self._start_dt = event_dt - _PLAYBACK_MARGIN_BEFORE
        self._end_dt = event_dt + _PLAYBACK_MARGIN_AFTER
        self._stop_audio()
        self._cancel_draw_mode()
        for tile in self._tiles:
            if tile.channel is not None:
                tile.start_playback(self._start_dt, self._end_dt)
        self._refresh_info()
        self._refresh_events()
        self.status_label.setText(
            f"پرش به رویداد {event_dt.strftime('%H:%M:%S')} انجام شد.")

    # ------------------------------------------------------------- بستن --
    def _stop_all(self):
        self._stop_audio()
        for t in self._tiles:
            try:
                t.stop_thread()
            except Exception:
                pass

    def reject(self):
        self._stop_all()
        super().reject()

    def closeEvent(self, event):
        self._stop_all()
        super().closeEvent(event)
