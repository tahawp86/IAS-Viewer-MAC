# -*- coding: utf-8 -*-
"""دیالوگ «پخش ویدیوی NVR در این لحظه»: از دیالوگ گزارش‌ها (reports_dialog.py)
باز می‌شود تا کاربر ویدیوی *واقعیِ ضبط‌شده روی خودِ NVR* حوالی زمان یک رویداد
گزارش‌شده (تشخیص چهره / ورود به محدوده / شمارش نفرات) را ببیند.

چون NVRها (Hikvision/Dahua) برند-به-برند رفتار متفاوتی دارند و از قبل
نمی‌دانیم کدام‌اند (دقیقاً مثل بقیه‌ی این پروژه)، هر دو قالب آدرس RTSP پخش
بازبینی (nvr_playback.build_playback_urls) به‌ترتیب امتحان می‌شوند؛ اولین
موردی که واقعاً فریم بدهد پخش می‌شود.
"""

import cv2
import webbrowser
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
)

from rtsp_utils import open_capture
from nvr_playback import build_playback_urls


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


class NVRPlaybackDialog(QDialog):
    def __init__(self, nvr: dict, channel, start_dt, end_dt, camera_label="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("پخش ویدیوی NVR")
        self.resize(760, 520)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        layout = QVBoxLayout(self)

        info = f"دوربین: {camera_label}" if camera_label else ""
        info += f"   |   بازه: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} تا {end_dt.strftime('%H:%M:%S')}"
        layout.addWidget(QLabel(info))

        self.video_label = QLabel("در حال اتصال به NVR برای پخش بازبینی...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background:#111; color:#ccc;")
        self.video_label.setMinimumHeight(400)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.video_label)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        # (1.2.0) فالبک «باز کردن وب NVR»: اگر پخش داخل‌برنامه‌ای (RTSP
        # بازبینی) ممکن نشد، این دکمه نمایش داده می‌شود تا کاربر پنل وب NVR
        # را در مرورگر باز کند و خودش به بازه‌ی زمانی رویداد برود.
        self._nvr = nvr
        self.web_btn = QPushButton("🌐 باز کردن وب NVR")
        self.web_btn.setToolTip(
            "پخش داخل برنامه ممکن نشد؛ پنل وب NVR در مرورگر باز می‌شود.\n"
            "بعد از ورود، در بخش Playback به بازه‌ی زمانی بالای پنجره بروید.")
        self.web_btn.clicked.connect(self._on_open_nvr_web)
        self.web_btn.hide()
        btn_row.addWidget(self.web_btn)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        candidates = build_playback_urls(nvr, channel, start_dt, end_dt)
        self._thread = _PlaybackThread(nvr, channel, start_dt, end_dt,
                                       candidates, self)
        self._thread.frame_ready.connect(self._on_frame)
        self._thread.connected.connect(self._on_connected)
        self._thread.finished_playing.connect(self._on_finished)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_connected(self, label):
        self.status_label.setText(f"در حال پخش (پروتکل {label})")

    def _on_frame(self, frame):
        pixmap = _bgr_to_pixmap(frame)
        if pixmap is None:
            return
        self.video_label.setPixmap(
            pixmap.scaled(
                self.video_label.width(), self.video_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_finished(self):
        self.status_label.setText("پخش این بازه تمام شد.")

    def _on_error(self, msg):
        self.video_label.setText(msg)
        self.status_label.setText("")
        # (1.2.0) فالبک وب: وقتی پخش RTSP ممکن نیست، راه مرورگر را نشان بده.
        try:
            if nvr_web_url(self._nvr):
                self.web_btn.show()
        except Exception:
            pass

    def _on_open_nvr_web(self):
        """باز کردن پنل وب NVR در مرورگر سیستم (فالبک پخش بازبینی)."""
        url = nvr_web_url(self._nvr)
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _stop_thread(self):
        """(2.0.16-beta - پایداری) توقف امن ترد پخش هنگام بسته شدن دیالوگ.

        نسخه‌ی قبلی فقط wait(2000) می‌کرد؛ اگر run() در cap.read() گیر
        کرده بود، دیالوگ تخریب می‌شد در حالی که ترد هنوز زنده بود و Qt با
        qFatal کل برنامه را می‌کشت. با rw_timeoutِ بالا، read حداکثر ~۵
        ثانیه بلاک می‌ماند؛ ۶ ثانیه صبر می‌کنیم و در بدترین حالت terminate
        (همیشه امن‌تر از کرش qFatal).
        """
        try:
            self._thread.stop()
            if self._thread.isRunning() and not self._thread.wait(6000):
                self._thread.terminate()
                self._thread.wait(3000)
        except Exception:
            pass

    def reject(self):
        self._stop_thread()
        super().reject()

    def closeEvent(self, event):
        self._stop_thread()
        super().closeEvent(event)
