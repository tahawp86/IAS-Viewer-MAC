# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Taha Arefi (طه عارفی)
# This file is part of IAS Viewer.
# IAS Viewer is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# -*- coding: utf-8 -*-
"""camera_page.py — دیوار دوربین‌ها (camera wall) به‌صورت ویجت قابل‌استفاده‌ی مجدد.

این ماژول «UI مرکزی صفحه‌ی اصلی» برنامه را در یک ویجت واحد گرد هم می‌آورد تا
هم در پنجره‌ی اصلی (MainWindow) و هم در پنجره‌های شناور «نما»ی اتاق مانیتورینگ
(MonitorWindow) از همان استفاده شود — single source of truth:

- نوار ابزار مدیریتی: تعداد نمایش هم‌زمان، شمارش افراد، اتاق مانیتورینگ،
  رسم محدوده‌ی هشدار، مدیریت محدوده‌ها + ردیف دوم رسم (تشخیص خودکار،
  تشخیص هوشمند زمین، تایید/لغو)
- شبکه‌ی دوربین‌ها (CameraGridWidget) با خانه‌های کامل (CameraSlotWidget):
  پخش زنده، رسم محدوده، زوم دیجیتال، صدای زنده، شمارش افراد، تشخیص‌ها

کلاس‌های CameraSlotWidget / CameraGridWidget / RegionManagerDialog /
_NoMinWidthRow از main.py به اینجا منتقل شدند تا بدون وابستگی چرخه‌ای
قابل‌استفاده‌ی مجدد باشند؛ main.py حالا آن‌ها را از اینجا import می‌کند.
"""

import os
import threading
import time
import uuid

import cv2
from PyQt6.QtCore import Qt, QEvent, QMimeData, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import (QDrag, QFontMetrics, QImage,
                         QPixmap)
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox,
                             QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QScrollArea, QSizePolicy, QSlider,
                             QVBoxLayout, QWidget)

from camera_stream import CameraStreamThread, is_low_spec_mode
from face_engine import FaceEngine
from report_store import report_store
from video_display_label import VideoDisplayLabel

try:
    from camera_audio import ListenSession
except Exception:
    ListenSession = None

_FloorDetectThread = None


def _get_floor_detect_thread():
    """دسترسی تنبل به FloorDetectThread (بارگذاری سنگین transformers فقط
    در لحظه‌ی استفاده، نه در زمان بالا آمدن برنامه)."""
    global _FloorDetectThread
    if _FloorDetectThread is None:
        from floor_detector import FloorDetectThread as _FT
        _FloorDetectThread = _FT
    return _FloorDetectThread

_LOW_SPEC_MODE = is_low_spec_mode()

_PROCESS_EVERY_N = 12 if _LOW_SPEC_MODE else (5 if (os.cpu_count() or 4) >= 6 else 8)

GRID_LAYOUTS = {
    1: (1, 1),
    4: (2, 2),
    9: (3, 3),
    16: (4, 4),
    32: (4, 8),
    64: (8, 8),
}

def _bgr_to_pixmap(frame):
    """تبدیل یک فریم OpenCV (BGR، numpy) به QPixmap برای نمایش در UI."""
    if frame is None or frame.size == 0:
        return None
    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb_image.shape
    bytes_per_line = ch * w
    # .copy() الزامی است؛ بدون آن QImage به بافر موقت numpy اشاره می‌کند که ممکن
    # است پیش از رندر شدن، توسط پایتون آزاد/بازچرخانی شود.
    qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qt_img)


def _play_alarm_beep(kind="zone"):
    """پخش تک‌بوق هشدار در ترد جداگانه (رابط کاربری قفل نمی‌شود).
    kind: نوع صدا — "zone" (ورود به محدوده)، "fire" (تشخیص حریق/پنل)،
    "violation" (تخلف طبقاتی) یا "videoloss" (قطع تصویر).
    هر نوع صدا تنظیم مستقل خودش را دارد.
    """
    try:
        from alarm_sound import sound_enabled
        if not sound_enabled(kind):
            return
    except Exception:
        return
    def _run():
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(1500, 220)
                time.sleep(0.08)
        except Exception:
            try:
                QApplication.beep()
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()


class _NoMinWidthRow(QWidget):
    """کانتینر ردیف دوم نوار ابزار (دکمه‌های رسم محدوده).

    ریشه‌ی باگ 2.0.14-beta: وقتی این ردیف داخل همان نوار افقی بود، با نمایان
    شدنش minimumSizeHint نوار ابزار (و از طریق QSplitter که از
    minimumSizeHint استفاده می‌کند، minimumSize کل پنجره) از ~۱۲۴۰ به ~۱۷۹۰
    می‌پرید؛ Qt هم پنجره را خودکار بزرگ می‌کرد و با مخفی شدن دوباره‌ی ردیف،
    پنجره هرگز به اندازه‌ی قبلی برنمی‌گشت - کادرها و اندازه‌ها «به‌هم
    می‌ریخت». با این اورراید، پهنای ردیف هیچ‌وقت در حداقل‌اندازه‌ی پنجره
    حساب نمی‌شود؛ پس ظاهر/مخفی شدنش نه پنجره را بزرگ می‌کند نه کادرها را
    تکان می‌دهد (فقط فضای stretch نوار ابزار را می‌گیرد)."""

    def minimumSizeHint(self):  # noqa: N802 (نام استاندارد Qt)
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())



class CameraSlotWidget(QWidget):
    """یک خانه (slot) در شبکه‌ی نمایش هم‌زمان دوربین‌ها. می‌تواند خالی باشد یا
    یک دوربین را پخش کند. با کلیک انتخاب (highlight) می‌شود تا فریم زنده‌اش
    برای «ثبت چهره از تصویر زنده» در دسترس باشد."""

    # رفع باگ: دکمه‌های نوار ابزار محدوده‌ی هشدار در MainWindow قبلاً فقط
    # هنگام عوض‌شدن خانه‌ی انتخاب‌شده (selection_changed) به‌روزرسانی
    # می‌شدند - نه وقتی خودِ محدوده داخل همین خانه رسم/تایید/حذف می‌شد.
    # نتیجه این بود که بعد از رسم محدوده، دکمه‌ی «تایید» غیرفعال (خاکستری)
    # باقی می‌ماند و کلیک روی آن هیچ اثری نداشت - و چون تایید هرگز واقعاً
    # اجرا نمی‌شد، محدوده هم هرگز روی ترد پخش برای تشخیص ورود فعال نمی‌شد
    # (پس آلارم هم هرگز رخ نمی‌داد). این سیگنال با هر تغییر وضعیت محدوده‌ها
    # (رسم/تایید/حذف/بارگذاری از دیسک/بستن دوربین) ارسال می‌شود تا
    # MainWindow._refresh_line_buttons همیشه با وضعیت واقعی هم‌گام بماند.
    tripwire_changed = pyqtSignal()
    # تغییر وضعیت موتور تشخیص شخص (برای بنر صفحه‌ی «ردیابی اشخاص»).
    detector_status_changed = pyqtSignal()
    # (2.0.61-beta) هشدار قطع تصویر: (cam dict, "lost"|"recovered")
    video_loss_signal = pyqtSignal(object, str)

    # هشدارهای وضعیت پلاک‌خوان فقط یک‌بار در کل برنامه نمایش داده می‌شوند
    # (کلید: متن پیام) تا با چند دوربین، دیالوگ تکراری باز نشود.
    _plate_status_warned = set()

    def __init__(self, on_clicked, on_close_requested, on_double_clicked=None,
                 on_slot_drag_swap=None, on_camera_drag_drop=None, on_region_alert=None,
                 on_fire_event=None, on_plate_event=None, on_person_event=None,
                 on_audio_url_found=None, on_audio_creds_save=None,
                 parent=None):
        super().__init__(parent)
        # (2.0.106-beta) کال‌بک ذخیره‌ی مسیر صوتی موفق (cam_id, url)
        self._on_audio_url_found_cb = on_audio_url_found
        # (2.0.110-beta) کال‌بک ذخیره‌ی یوزر/پس جدید صدا (cam_id, user, pass)
        self._on_audio_creds_save_cb = on_audio_creds_save
        self.cam = None
        self.stream_thread = None
        self.latest_raw_frame = None
        # رفع باگ «یک فریم از دوربین قبلی در کادر می‌ماند» هنگام جابه‌جایی با
        # درگ‌انددراپ: سیگنال‌های ترد پخش با اتصال صف‌شده (queued) به ترد GUI
        # می‌رسند؛ بعد از stop() (که ترد را کاملاً متوقف می‌کند) ممکن است
        # هنوز چند رویداد قدیمی در صف رویداد GUI مانده باشد و بعد از start()
        # دوربین جدید، دیرتر تحویل شوند - مثلاً آخرین فریم دوربین قبلی روی
        # خانه‌ای که حالا دوربین دیگری را نشان می‌دهد نقاشی می‌شد. هر start/
        # stop یک شماره‌ی نسل (generation) تازه می‌سازد و همه‌ی اتصال‌های
        # سیگنال فقط رویدادهای هم‌نسل با استریم جاری را قبول می‌کنند.
        self._stream_seq = 0
        # رفع درخواست «هر کادر قابلیت Zoom in/out داشته باشه»: بزرگ‌نمایی
        # دیجیتالِ مستقلِ هر خانه.
        #   _zoom: ضریب بزرگ‌نمایی (1.0 = کل فریم)؛ سقف 8 برابر
        #   _zoom_cx/_zoom_cy: مرکز ناحیه‌ی نمایشی، نرمال 0..1 نسبت به فریم اصلی
        #   _last_zoom_crop: آخرین کراپ اعمال‌شده (x0,y0,cw,ch) به پیکسل فریم -
        #       برای نگاشت موقعیت نشانگر ماوس به مختصات فریم (زوم حول نشانگر + پن)
        self._zoom = 1.0
        self._zoom_cx = 0.5
        self._zoom_cy = 0.5
        self._last_zoom_crop = None
        # بهینه‌سازی سرعت: زمان آخرین به‌روزرسانی تصویر این تایل (برای
        # محدود کردن نرخ نمایش به ~۱۵ فریم‌برثانیه - رجوع کنید به
        # on_frame_ready).
        self._last_display_ts = 0.0
        self._panning = False
        self._pan_button = None
        self._pan_start_label_pos = None
        self._pan_start_center = None
        self._selected = False
        self._on_clicked = on_clicked
        self._on_close_requested = on_close_requested
        # رفع درخواست: با ورود شخصی به یکی از محدوده‌های هشدار این خانه، این
        # callback (در MainWindow) صدا زده می‌شود تا رویداد در پنل تشخیص
        # چهره هم به‌صورت متنی ثبت شود.
        self._on_region_alert = on_region_alert
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: با هر تشخیص تصویری
        # آتش/دود روی این خانه، این callback (در MainWindow) صدا زده می‌شود
        # تا رویداد در پنل «هشدارهای حریق و دود» ثبت و گزارش شود. نکته: نام
        # این ویژگی عمداً با متد واکنشیِ سیگنال (_on_fire_event پایین‌تر)
        # متفاوت است تا با آن تداخل (override) نکند - دقیقاً مثل تمایز
        # self._on_region_alert (callback) از _on_region_entered (متد).
        self._fire_event_cb = on_fire_event
        # رفع درخواست «سیستم پلاک‌خوان»: با هر پلاک تازه‌تأییدشده روی این خانه،
        # این callback (در MainWindow) صدا زده می‌شود تا عبور (تعریف‌شده/
        # تعریف‌نشده) ثبت و در گزارش عبور نمایش داده شود. نام عمداً با متد
        # واکنشی سیگنال (_on_plate_event پایین‌تر) متفاوت است تا override نشود.
        self._plate_event_cb = on_plate_event
        # رفع درخواست «ردیابی اشخاص»: با هر رد تازه‌تأییدشده/تمام‌شده روی این
        # خانه، این callback (در MainWindow) صدا زده می‌شود تا تطبیق بین
        # دوربینی و ثبت «حضور» در person_store انجام شود.
        self._person_event_cb = on_person_event
        # رفع درخواست: وضعیت روشن/خاموش بودن «شمارش افراد Real Time» برای این
        # خانه؛ چون خانه‌ها هنگام عوض شدن تعداد شبکه (set_grid_size) از نو
        # ساخته می‌شوند، این وضعیت فقط تا وقتی همین خانه/دوربین برقرار است
        # حفظ می‌شود.
        self._people_counting_enabled = False
        # رفع درخواست: با دابل‌کلیک روی تصویر دوربین، این خانه بزرگ‌نمایی
        # می‌شود و با دابل‌کلیک دوباره به اندازه‌ی قبل (چیدمان شبکه‌ای) برمی‌گردد.
        self._on_double_clicked = on_double_clicked
        # رفع درخواست: امکان جابه‌جایی محل نمایش دوربین‌ها با درگ (Drag & Drop) -
        # هم بین دو خانه‌ی شبکه (جابه‌جایی) و هم از لیست دوربین‌ها روی یک خانه
        # (افزودن/جایگزینی). index این خانه در CameraGridWidget.set_grid_size
        # مقداردهی می‌شود.
        self._on_slot_drag_swap = on_slot_drag_swap
        self._on_camera_drag_drop = on_camera_drag_drop
        self.slot_index = None
        self._drag_start_pos = None
        self.setAcceptDrops(True)

        # رفع درخواست: محدوده‌های هشدار (Zone) برای این خانه - جایگزین خط
        # فرضی عبور قبلی. کاربر می‌تواند به تعداد دلخواه محدوده‌ی چندضلعی
        # (نقاط دلخواه روی زمین، نه فقط مستطیل) رسم و نام‌گذاری کند (مثلاً
        # «محدوده ۱ / اتاق سرور»)؛ با ورود هرکسی به هرکدام، کادر این خانه
        # قرمز می‌شود و آلارم صوتی پخش می‌شود.
        #   pending_points: نقاطِ محدوده‌ای که تازه با کلیک‌های متوالی بسته
        #       شده ولی هنوز کاربر نامش را تایید نکرده - با خط‌چین زرد روی
        #       تصویر دیده می‌شود. None یعنی چیزی در انتظار تایید نیست.
        #   regions: لیست محدوده‌های نهایی/فعال؛ هر کدام
        #       {"id","number","name","points"} - همیشه روی تصویر دیده می‌شوند.
        self.pending_points = None
        self.regions = []
        # رفع درخواست: وقتی کاربر از دیالوگ «مدیریت محدوده‌ها» یکی از
        # محدوده‌های قبلاً تایید‌شده را برای «ویرایش شکل/اندازه» باز کرده،
        # id همان محدوده اینجا نگه داشته می‌شود تا با «ذخیره ویرایش» روی
        # همان محدوده به‌جای ساختن یک محدوده‌ی تازه اعمال شود (رجوع کنید به
        # start_edit_region/save_region_edit/cancel_region_edit پایین‌تر).
        # None یعنی الان در حال ویرایش هیچ محدوده‌ی از قبل تایید‌شده‌ای
        # نیستیم (حالت عادی رسم محدوده‌ی تازه).
        self._editing_region_id = None
        self._alarm_active = False
        self._alarm_timer = QTimer(self)
        self._alarm_timer.setSingleShot(True)
        self._alarm_timer.timeout.connect(self._clear_alarm)
        # رفع درخواست: «محدوده رسم می‌شود ولی هشدار نمی‌دهد» بی‌هیچ توضیحی.
        # علتش این بود که کل زنجیره‌ی هشدار به بارگذاری موفق مدل تشخیص شخص
        # (YOLOv8، در person_detector.py) وابسته است و قبلاً وقتی آن مدل
        # بارگذاری نمی‌شد، هیچ نشانه‌ای روی UI دیده نمی‌شد (فقط یک print()
        # که در exe نهایی اصلاً قابل‌دیدن نیست - رجوع کنید به
        # CameraStreamThread.person_detector_status_signal). None یعنی
        # هنوز وضعیت واقعی مشخص نیست (اولین تلاش بارگذاری هنوز انجام
        # نشده)، True/False یعنی نتیجه‌ی همان اولین تلاش.
        self._detector_available = None
        self._detector_error = ""

        # رفع درخواست: ثبت دائمیِ تاریخچه‌ی شمارش نفرات (report_store.py).
        # فقط وقتی عدد نسبت به آخرین باری که ثبت شد تغییر کند لاگ می‌شود
        # (نه هر فریم/هر چند فریم که people_count_signal شلیک می‌شود) تا
        # حجم گزارش منطقی بماند؛ None یعنی هنوز هیچ عددی برای این خانه لاگ
        # نشده (بعد از هر start() تازه دوباره None می‌شود - رجوع کنید به
        # start() پایین‌تر).
        self._last_logged_count = None

        self.setMinimumSize(140, 110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(3, 3, 3, 3)
        outer.setSpacing(2)

        header = QHBoxLayout()
        self.name_label = QLabel("خالی")
        self.name_label.setStyleSheet("color:#dddddd; font-size:11px; font-weight:bold;")
        # رفع باگ: وقتی اسم دوربین طولانی است (مثلاً اسم + IP)، در خانه‌های
        # کوچک (چیدمان‌های شلوغ مثل 9/16/32 دوربین) sizeHint کامل متن باعث
        # می‌شد کل عرض هدر را اشغال کند و برچسب تعداد نفرات/دکمه‌ی بستن را از
        # فضای قابل‌مشاهده بیرون براند (بدون خطا، فقط دیده نمی‌شدند). با
        # Ignored از layout می‌خواهیم sizeHint این لیبل را نادیده بگیرد و
        # فضای باقی‌مانده (بعد از رزرو فضای ثابت برای بقیه‌ی ویجت‌های هدر) را
        # به آن بدهد؛ متن هم با «...» بر اساس همان فضای واقعی کوتاه می‌شود
        # (رجوع کنید به _refresh_name_label/resizeEvent) تا چیزی از هدر گم
        # نشود.
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._name_full_text = "خالی"
        # رفع درخواست: نمایش Real Time تعداد افراد شناسایی‌شده، بالای همان
        # پنجره‌ی دوربین (کنار نام دوربین). فقط وقتی شمارش افراد به‌صورت
        # سراسری (یک دکمه‌ی واحد بالای همه‌ی پنجره‌های دوربین، در نوار ابزار
        # وسط - رجوع کنید به MainWindow.people_toggle_btn) روشن باشد مقداری
        # دارد (رجوع کنید به set_people_counting/on_people_count).
        self.people_count_label = QLabel("")
        self.people_count_label.setStyleSheet("color:#f39c12; font-size:11px; font-weight:bold;")
        # (2.0.15-beta) نشانگر زنده‌ی وضعیت شبکه/پهنای باند این دوربین؛ از
        # stream_stats_signal/stream_status_signal ترد استریم به‌روزرسانی
        # می‌شود (رجوع کنید به on_stream_stats/on_stream_status).
        self.net_label = QLabel("")
        self.net_label.setStyleSheet("color:#2ecc71; font-size:11px; font-weight:bold;")
        self.net_label.setToolTip("وضعیت شبکه و پهنای باند این دوربین")
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setStyleSheet("QPushButton{color:#ccc; background:#333; border-radius:9px; padding:0px;}")
        self.close_btn.setVisible(False)
        self.close_btn.clicked.connect(lambda: self._on_close_requested(self))
        # رفع درخواست «هر کادر قابلیت Zoom in/out داشته باشه»: دکمه‌های
        # بزرگ‌نمایی/کوچک‌نمایی/بازنشانی هر خانه (علاوه بر زوم با اسکرول ماوس
        # روی تصویر و پن با Shift+درگ یا درگ با دکمه‌ی وسط ماوس).
        _zoom_style = ("QPushButton{color:#ccc; background:#333; border-radius:9px; font-size:11px; padding:0px;}"
                       "QPushButton:disabled{color:#666; background:#2a2a2a;}")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedSize(18, 18)
        self.zoom_in_btn.setStyleSheet(_zoom_style)
        self.zoom_in_btn.setToolTip("بزرگ‌نمایی تصویر (می‌توانید با اسکرول ماوس روی تصویر هم زوم کنید)")
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedSize(18, 18)
        self.zoom_out_btn.setStyleSheet(_zoom_style)
        self.zoom_out_btn.setToolTip("کوچک‌نمایی تصویر")
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.zoom_reset_btn = QPushButton("1:1")
        self.zoom_reset_btn.setFixedSize(26, 18)
        self.zoom_reset_btn.setStyleSheet(_zoom_style)
        self.zoom_reset_btn.setToolTip("بازنشانی بزرگ‌نمایی (نمایش کل فریم)")
        self.zoom_reset_btn.clicked.connect(lambda: self._reset_zoom())
        header.addWidget(self.name_label, 1)
        header.addWidget(self.people_count_label)
        header.addWidget(self.net_label)
        # (2.0.106-beta) دکمه‌ی صدا و اسلایدر ولوم روی کادر دوربین —
        # بدون نیاز به راست‌کلیک و دیالوگ جدا.
        self.audio_btn = QPushButton("🔊")
        self.audio_btn.setFixedSize(18, 18)
        self.audio_btn.setStyleSheet(_zoom_style)
        self.audio_btn.setToolTip("شنیدن صدای دوربین")
        self.audio_btn.setVisible(False)
        self.audio_btn.clicked.connect(self._toggle_audio)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(60)
        self.volume_slider.setToolTip("ولوم صدا")
        self.volume_slider.setVisible(False)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        # نشست شنیدن صدا (بدون دیالوگ)
        self._listen_session = None
        self._on_audio_url_found = None
        header.addWidget(self.audio_btn)
        header.addWidget(self.volume_slider)
        header.addWidget(self.zoom_in_btn)
        header.addWidget(self.zoom_out_btn)
        header.addWidget(self.zoom_reset_btn)
        header.addWidget(self.close_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#888888; font-size:9px;")

        # رفع درخواست: وقتی محدوده‌ی هشدار تعریف شده ولی موتور تشخیص شخص
        # (YOLOv8) بارگذاری نشده - پس هیچ هشداری هرگز صادر نخواهد شد - این
        # پیام به‌جای سکوت کامل، همین‌جا زیر نام دوربین نشان داده می‌شود.
        # رجوع کنید به _refresh_detector_warning.
        self.detector_warn_label = QLabel("")
        self.detector_warn_label.setStyleSheet("color:#e67e22; font-size:9px; font-weight:bold;")
        self.detector_warn_label.setWordWrap(True)
        self.detector_warn_label.setVisible(False)

        self.video_label = VideoDisplayLabel("خالی — برای افزودن دوربین،\nدر لیست سمت چپ دابل‌کلیک کنید")
        self.video_label.setWordWrap(True)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color:#1e1e1e; color:#888888; border-radius:6px; font-size:10px;")
        self.video_label.setMinimumSize(100, 80)
        # رفع باگ: وقتی یک QLabel با setPixmap() یک فریم بزرگ (مثلاً حالت
        # بزرگ‌نمایی‌شده با دابل‌کلیک) نمایش می‌دهد، sizeHint/minimumSizeHint
        # داخلی آن برابر همان اندازه‌ی بزرگ باقی می‌ماند و چیدمان (QGridLayout)
        # دیگر اجازه نمی‌دهد این خانه پس از بازگشت به حالت شبکه‌ای، کوچک شود -
        # همان مشکل «تصویر دوربین بعد از دابل‌کلیک دوم به اندازه‌ی قبل برنمی‌گردد».
        # با Ignored، چیدمان این sizeHint را نادیده می‌گیرد و صرفاً فضای واقعی
        # داده‌شده به خانه را ملاک قرار می‌دهد.
        self.video_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.video_label.region_drawn.connect(self._on_region_drawn)
        # (2.0.38-beta) پیشرفت رسمِ نقطه‌به‌نقطه هم نوار ابزار را به‌روز
        # می‌کند تا «لغو رسم» حین رسم هم دیده/فعال شود.
        self.video_label.draw_progress_changed.connect(
            self.tripwire_changed.emit)
        # برای زوم با اسکرول و پن (Shift+درگ / درگ با دکمه‌ی وسط) روی تصویر،
        # رویدادهای لیبل نمایش از همین‌جا رهگیری می‌شوند - رجوع کنید به eventFilter.
        self.video_label.installEventFilter(self)
        self._refresh_zoom_buttons()

        outer.addLayout(header)
        outer.addWidget(self.status_label)
        outer.addWidget(self.detector_warn_label)
        outer.addWidget(self.video_label, 1)
        self._apply_frame_style()

    # ---------------------------------------------------------- selection --

    def _apply_frame_style(self):
        # رفع درخواست: وقتی آلارم عبور از خط فرضی فعال است، کادر دور خانه‌ی
        # دوربین قرمز و ضخیم‌تر می‌شود - با اولویت بالاتر از رنگ انتخاب‌شدن.
        if self._alarm_active:
            border = "3px solid #e74c3c"
        elif self._selected:
            border = "2px solid #3498db"
        else:
            border = "1px solid #3a3a3a"
        self.setStyleSheet(f"CameraSlotWidget {{ border: {border}; border-radius: 8px; background-color: #262626; }}")

    # ----------------------------------------------------- محدوده‌ی هشدار --

    def set_draw_mode(self, enabled: bool):
        # مختصات رسم محدوده نرمالِ کل فریم است؛ اگر زوم فعال باشد ابتدا به
        # حالت عادی برمی‌گردیم تا محدوده سر جای درستش رسم شود.
        if enabled:
            self._reset_zoom()
        self.video_label.set_draw_mode(enabled)
        self._refresh_zoom_buttons()

    def is_draw_mode(self) -> bool:
        return self.video_label.draw_mode

    def has_pending_region(self) -> bool:
        return self.pending_points is not None

    def has_draw_points(self) -> bool:
        """(2.0.38-beta) آیا نقطه‌ای «در حال رسم» (هنوز بسته‌نشده) هست؟"""
        try:
            return bool(self.video_label._draw_points)
        except Exception:
            return False

    def cancel_draw_points(self):
        """(2.0.38-beta) لغو نقاطِ در حال رسم (قبل از بسته‌شدن چندضلعی)."""
        try:
            self.video_label._draw_points = []
            self.video_label._hover_norm = None
            self.video_label.update()
            self.video_label.draw_progress_changed.emit()
        except Exception:
            pass

    def has_any_region(self) -> bool:
        return bool(self.regions) or self.pending_points is not None

    def _on_region_drawn(self, norm_points):
        self.pending_points = list(norm_points)
        self.video_label.set_pending_points_norm(self.pending_points)
        self.tripwire_changed.emit()

    def confirm_region(self, name: str):
        """رفع درخواست: تایید و نام‌گذاری محدوده‌ی تازه‌بسته‌شده. محدوده به
        لیست محدوده‌های فعال این خانه اضافه و بلافاصله روی ترد پخش برای
        تشخیص ورود فعال می‌شود. دیکشنری محدوده‌ی تازه (برای ذخیره در
        camera_store) یا None برمی‌گرداند اگر نقاطی در انتظار تایید نبود.

        نکته‌ی مهم (رفع باگ 2.0.26): نقاط از video_label خوانده می‌شوند، نه
        از self.pending_points؛ چون کاربر ممکن است بعد از قرارگرفتن شکل
        (مثلاً خروجی «تشخیص هوشمند زمین») گوشه‌ها را با ماوس جابه‌جا کرده
        باشد و آن ویرایش فقط روی کپیِ داخل video_label اعمال می‌شود
        (رجوع کنید به VideoDisplayLabel.mouseMoveEvent). خواندن از
        self.pending_points باعث می‌شد ویرایش کاربر نادیده گرفته شود و
        شکلِ خامِ تشخیص ذخیره شود."""
        points = self.video_label.pending_points_norm()
        if not points or len(points) < 3:
            return None
        region = {
            "id": str(uuid.uuid4()),
            "number": len(self.regions) + 1,
            "name": (name or "").strip(),
            "points": list(points),
        }
        self.regions.append(region)
        self.pending_points = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_confirmed_regions(self.regions)
        self.video_label.set_draw_mode(False)
        if self.stream_thread is not None:
            self.stream_thread.set_regions(self.regions)
        self._refresh_detector_warning()
        self.tripwire_changed.emit()
        return region

    def cancel_pending_region(self):
        """رفع درخواست: لغو نقاط در حال رسم/در انتظار نام‌گذاری، بدون هیچ
        تاثیری روی محدوده‌های قبلاً تایید‌شده."""
        self.pending_points = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_draw_mode(False)
        self.tripwire_changed.emit()

    def is_editing_region(self) -> bool:
        """آیا همین الان در حال ویرایش شکل/اندازه‌ی یک محدوده‌ی قبلاً
        تایید‌شده هستیم؟ (رجوع کنید به start_edit_region)."""
        return self._editing_region_id is not None

    def start_edit_region(self, region_id) -> bool:
        """رفع درخواست «بتونه اندازه و شکل محدوده تغییر بده»: محدوده‌ی
        تایید‌شده با شناسه‌ی region_id را به‌عنوان پیش‌نمایشِ سبزِ قابل‌کشیدن
        روی تصویر می‌گذارد (موقتاً از فهرست محدوده‌های ثابت/آبی بیرون
        می‌آید تا دوبار دیده نشود) تا کاربر گوشه‌هایش را با ماوس جابه‌جا/
        اضافه/حذف کند؛ نتیجه با save_region_edit ذخیره یا با
        cancel_region_edit لغو می‌شود. اگر چیزی در انتظار رسم/ویرایشِ
        دیگری باشد یا محدوده پیدا نشود، False برمی‌گرداند."""
        if self.pending_points is not None:
            return False
        region = next((r for r in self.regions if r["id"] == region_id), None)
        if region is None:
            return False
        self._editing_region_id = region_id
        self.pending_points = list(region["points"])
        visible = [r for r in self.regions if r["id"] != region_id]
        self.video_label.set_confirmed_regions(visible)
        self.video_label.set_pending_points_norm(self.pending_points, editing_existing=True)
        self.video_label.set_draw_mode(False)
        self.tripwire_changed.emit()
        return True

    def save_region_edit(self):
        """رفع درخواست: شکل/اندازه‌ی ویرایش‌شده (نقاطِ فعلیِ روی video_label،
        شاملِ هر جابه‌جایی/افزودن/حذف گوشه‌ای که کاربر انجام داده) را روی
        همان محدوده‌ی قبلاً تایید‌شده می‌نویسد و بلافاصله روی ترد پخش هم
        برای تشخیص ورود به‌روز می‌کند. دیکشنری محدوده‌ی به‌روزشده (برای
        ذخیره در camera_store) یا None برمی‌گرداند (اگر در حال ویرایش
        نبودیم یا کمتر از ۳ گوشه باقی مانده بود - عملاً غیرممکن چون خودِ
        VideoDisplayLabel اجازه‌ی حذف گوشه‌ی چهارم به بعد را فقط تا حداقل ۳
        گوشه می‌دهد)."""
        if self._editing_region_id is None:
            return None
        points = self.video_label.pending_points_norm()
        if not points or len(points) < 3:
            return None
        region = None
        for r in self.regions:
            if r["id"] == self._editing_region_id:
                r["points"] = list(points)
                region = r
                break
        self._editing_region_id = None
        self.pending_points = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_confirmed_regions(self.regions)
        if self.stream_thread is not None:
            self.stream_thread.set_regions(self.regions)
        self.tripwire_changed.emit()
        return region

    def cancel_region_edit(self):
        """رفع درخواست: انصراف از ویرایشِ در حال انجام - محدوده به شکل/
        اندازه‌ی قبلی (قبل از شروع ویرایش) دست‌نخورده برمی‌گردد، چون
        start_edit_region هیچ تغییری روی خودِ self.regions اعمال نکرده
        بود (فقط یک کپی برای پیش‌نمایش ساخته بود)."""
        self._editing_region_id = None
        self.pending_points = None
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_confirmed_regions(self.regions)
        self.tripwire_changed.emit()

    def start_auto_full_frame_region(self) -> bool:
        """رفع درخواست «یک حالت جدید که خودش سطح زمین رو تشخیص بده و کلش
        رو محدوده محسوب کنه»: چون این برنامه به مدلی برای تشخیص دقیقِ کفِ
        زمین (Ground/Floor Segmentation - جدا از تشخیص شخص) دسترسی ندارد،
        این حالت کل کادر تصویر زنده‌ی همین دوربین را - که در عمل تقریباً
        معادل «کل زمینی است که دوربین می‌بیند» - به‌عنوان یک محدوده‌ی تازه و
        در انتظار تایید می‌گذارد؛ دقیقاً مثل یک محدوده‌ی دستی‌رسم‌شده، کاملاً
        قابل ویرایش است (کاربر می‌تواند مثلاً گوشه‌ای را عقب بکشد تا یک در
        ورودی یا راهرو را از محدوده کنار بگذارد) و در پایان با همان دکمه‌ی
        «تایید و نام‌گذاری» یا «لغو رسم» ذخیره/لغو می‌شود. اگر چیزی در حال
        رسم/ویرایش دیگری باشد، False برمی‌گرداند."""
        if self.pending_points is not None or self._editing_region_id is not None:
            return False
        # کمی فاصله از لبه‌ی دقیق کادر (۰ و ۱) تا دسته‌های چهارگوشه کاملاً
        # داخل تصویر و به‌راحتی با ماوس قابل‌گرفتن باشند.
        inset = 0.015
        points = [
            (inset, inset), (1 - inset, inset),
            (1 - inset, 1 - inset), (inset, 1 - inset),
        ]
        self.pending_points = points
        self.video_label.set_pending_points_norm(points, editing_existing=False)
        self.video_label.set_draw_mode(False)
        self.tripwire_changed.emit()
        return True

    def start_ai_floor_region(self, points_norm) -> bool:
        """رفع درخواست «یک حالت جدید که خودش سطح زمین رو تشخیص بده و کلش
        رو محدوده محسوب کنه ولی بازم قابل ادیت باشه، دقیق‌تر با مدل هوش
        مصنوعی (Segmentation)»: برخلاف start_auto_full_frame_region (که
        همیشه ۴ گوشه‌ی ثابتِ کل کادر را می‌گذاشت چون مدلی در دسترس نبود)،
        اینجا points_norm همان چندضلعیِ واقعاً تشخیص‌داده‌شده‌ی سطح زمین است
        (رجوع کنید به floor_detector.FloorDetector.detect_polygon، صدا
        زده‌شده از MainWindow._on_ai_floor_detect_finished). دقیقاً مثل هر
        محدوده‌ی دیگر - چه دستی‌رسم‌شده چه خودکارِ کل تصویر - قبل از تایید
        کاملاً با ماوس قابل ویرایش (جابه‌جایی/افزودن/حذف گوشه) است؛ کاربر
        می‌تواند مثلاً گوشه‌ای را که اشتباهی روی یک فرش/سایه رفته عقب بکشد.
        اگر چیزی در حال رسم/ویرایش دیگری باشد یا کمتر از ۳ نقطه داده شده
        باشد، False برمی‌گرداند."""
        if self.pending_points is not None or self._editing_region_id is not None:
            return False
        if not points_norm or len(points_norm) < 3:
            return False
        points = [tuple(p) for p in points_norm]
        self.pending_points = points
        self.video_label.set_pending_points_norm(points, editing_existing=False)
        self.video_label.set_draw_mode(False)
        self.tripwire_changed.emit()
        return True

    def remove_region(self, region_id):
        """حذف یک محدوده‌ی مشخص با شناسه‌اش (از پنل مدیریت محدوده‌ها) و
        شماره‌گذاری مجدد بقیه. لیست محدوده‌های باقی‌مانده را برمی‌گرداند."""
        self.regions = [r for r in self.regions if r["id"] != region_id]
        for i, r in enumerate(self.regions, start=1):
            r["number"] = i
        self.video_label.set_confirmed_regions(self.regions)
        if self.stream_thread is not None:
            self.stream_thread.set_regions(self.regions)
        self._refresh_detector_warning()
        self.tripwire_changed.emit()
        return list(self.regions)

    def set_regions_silent(self, regions):
        """بارگذاری محدوده‌های از قبل ذخیره‌شده (هنگام باز شدن دوربین) - از
        همان لحظه هم روی تصویر دیده می‌شوند و هم روی ترد پخش برای تشخیص
        ورود فعال می‌شوند."""
        self.regions = [dict(r) for r in (regions or [])]
        self.video_label.set_confirmed_regions(self.regions)
        if self.stream_thread is not None and self.regions:
            self.stream_thread.set_regions(self.regions)
        self._refresh_detector_warning()
        self.tripwire_changed.emit()

    def _on_region_entered(self, number, name):
        # رفع درخواست: با هر ورود، کادر قرمز می‌شود، آلارم صوتی پخش می‌شود
        # و پیام «ورود به محدوده شماره N / نام» زیر نام دوربین نمایش داده
        # می‌شود؛ تایمر با هر ورود تازه ریست می‌شود تا قرمزی/پیام حداقل چند
        # ثانیه بماند (نه فقط یک لحظه‌ی محو).
        label = f"شماره {number}" + (f" / {name}" if name else "")
        self._alarm_active = True
        self._apply_frame_style()
        self.status_label.setText(f"⚠ ورود به محدوده {label}")
        _play_alarm_beep("zone")
        self._alarm_timer.start(4000)
        if self._on_region_alert is not None and self.cam is not None:
            # رفع درخواست «گزارش‌ها روی NVR ضبط بشه»: کل cam پاس داده می‌شود
            # تا nvr_id/channel هم در on_region_alert در دسترس باشد.
            self._on_region_alert(self.cam, number, name)

    def _on_fire_event(self, kind: str, crop_frame, confidence: float):
        """رفع درخواست «سیستم تشخیص دود و اعلام حریق»: دقیقاً همان الگوی
        _on_region_entered بالا - کادر خانه قرمز و ضخیم می‌شود، آلارم صوتی
        پخش می‌شود و پیام روی همین خانه نمایش داده می‌شود؛ به‌علاوه رویداد به
        MainWindow (پنل «هشدارهای حریق و دود» + ثبت دائمی در گزارش‌ها) هم
        اطلاع داده می‌شود."""
        label = "🔥 آتش" if kind == "fire" else "💨 دود"
        self._alarm_active = True
        self._apply_frame_style()
        self.status_label.setText(f"⚠ تشخیص {label} ({confidence * 100:.0f}%)")
        _play_alarm_beep("fire")
        self._alarm_timer.start(4000)
        if self._fire_event_cb is not None and self.cam is not None:
            self._fire_event_cb(self.cam, kind, crop_frame, confidence)

    def _on_detector_status(self, available: bool, error_msg: str):
        """رفع درخواست: بعد از اولین تلاش (موفق یا ناموفق) برای بارگذاری
        مدل تشخیص شخص، این متد صدا زده می‌شود (رجوع کنید به
        CameraStreamThread.person_detector_status_signal). فقط وقتی این
        خانه محدوده‌ی هشدار هم داشته باشد پیام نشان داده می‌شود - چون تا
        وقتی محدوده‌ای تعریف نشده، در دسترس نبودن تشخیص شخص برای کاربر
        این خانه بی‌اهمیت است."""
        self._detector_available = bool(available)
        self._detector_error = error_msg or ""
        self._refresh_detector_warning()
        # بنر صفحه‌ی «ردیابی اشخاص» هم باید از این وضعیت باخبر شود.
        try:
            self.detector_status_changed.emit()
        except Exception:
            pass

    def _refresh_detector_warning(self):
        if self._detector_available is False and self.regions:
            # رفع باگ «هشدار ورود به محدوده اصلاً کار نمی‌کند»: وقتی مدل سنگین
            # شخص بارگذاری نشده، زنجیره‌ی هشدار با فالبک «تشخیص حرکت» ادامه
            # پیدا می‌کند (رجوع کنید به _MotionRegionDetector)؛ پس پیام دیگر
            # «کار نمی‌کند» نیست، بلکه «حالت جایگزین» را اعلام می‌کند.
            msg = "⚠ مدل تشخیص شخص بارگذاری نشد؛ هشدار ورود به محدوده با «تشخیص حرکت» (حالت جایگزین) کار می‌کند"
            if self._detector_error:
                msg += f" ({self._detector_error})"
            self.detector_warn_label.setText(msg)
            self.detector_warn_label.setToolTip(
                "برای فعال شدن تشخیص دقیق شخص (به‌جای تشخیص حرکت): مطمئن شوید "
                "کتابخانه‌ی ultralytics نصب است و فایل وزن مدل (yolov8n.pt) در "
                "دسترس است. اگر از نسخه‌ی exe استفاده می‌کنید، بررسی کنید مرحله‌ی "
                "دانلود/بسته‌بندی این فایل در بیلد گیت‌هاب موفق بوده است."
            )
            self.detector_warn_label.setVisible(True)
        else:
            self.detector_warn_label.setVisible(False)

    def _clear_alarm(self):
        self._alarm_active = False
        self._apply_frame_style()
        # بعد از پایان قرمزی، اگر وضعیت هنوز پیام هشدار را نشان می‌دهد،
        # دوباره به متن وضعیت عادی (متصل/خالی) برمی‌گردد.
        if self.status_label.text().startswith("⚠"):
            if self.stream_thread is not None and self.stream_thread.isRunning():
                self.status_label.setText("متصل - پخش زنده")
            else:
                self.status_label.setText("")

    def _set_name_text(self, full_text: str):
        """متن کامل اسم دوربین را نگه می‌دارد و نسخه‌ی کوتاه‌شده (بر اساس
        عرض واقعی فعلیِ لیبل) را نمایش می‌دهد. تولتیپ همیشه متن کامل را
        نشان می‌دهد تا حتی وقتی کوتاه شده، اطلاعات کامل (اسم + IP) در دسترس
        بماند."""
        self._name_full_text = full_text
        self.name_label.setToolTip(full_text)
        self._refresh_name_label()

    def _refresh_name_label(self):
        fm = QFontMetrics(self.name_label.font())
        avail_w = max(20, self.name_label.width())
        elided = fm.elidedText(self._name_full_text, Qt.TextElideMode.ElideRight, avail_w)
        self.name_label.setText(elided)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_name_label()

    def set_selected(self, value: bool):
        self._selected = value
        self._apply_frame_style()

    # ------------------------------------------------------ people count --

    def set_people_counting(self, enabled: bool):
        """رفع درخواست: شمارش افراد دیگر دکمه‌ی جداگانه برای هر خانه ندارد؛
        این متد از بیرون (CameraGridWidget، بر اساس همان یک دکمه‌ی سراسری
        بالای شبکه‌ی دوربین‌ها) صدا زده می‌شود تا شمارش برای این خانه هم
        روشن/خاموش شود."""
        self._people_counting_enabled = enabled
        if self.stream_thread is not None:
            self.stream_thread.set_people_counting(enabled)
        if enabled:
            # رفع باگ «روشن کردم ولی چیزی نمایش داده نشد»: قبلاً بعد از روشن
            # کردن، تا رسیدن اولین چرخه‌ی واقعی شمارش برچسب کاملاً خالی
            # می‌ماند. حالا بلافاصله یک وضعیت موقت نمایش داده می‌شود تا
            # مشخص باشد شمارش واقعاً فعال شده و فقط منتظر اولین نتیجه هستیم
            # (که چون از همان تشخیص چهره‌ی همیشه‌فعال می‌آید، معمولاً خیلی
            # سریع می‌رسد).
            self.people_count_label.setText("👤 در حال شمارش...")
        else:
            self.people_count_label.setText("")

    def set_plate_detection(self, enabled: bool):
        """رفع درخواست «سیستم پلاک‌خوان»: روشن/خاموش کردن زنده‌ی پلاک‌خوان
        برای دوربینِ همین خانه (از صفحه‌ی «پلاک‌خوان» تب «تعریف پلاک‌ها»)؛
        روی ترد پخشِ جاری اعمال می‌شود و در cam ذخیره نمی‌شود (ذخیره با
        camera_store.update_camera در همان صفحه انجام شده)."""
        if self.stream_thread is not None:
            self.stream_thread.set_plate_detection(enabled)

    def set_fire_detection(self, enabled: bool):
        """روشن/خاموش کردن زنده‌ی تشخیص حریق برای دوربینِ همین خانه
        (از صفحه‌ی «اعلام حریق»)؛ روی ترد پخشِ جاری اعمال می‌شود."""
        if self.stream_thread is not None:
            self.stream_thread.set_fire_detection(enabled)

    def set_person_tracking(self, enabled: bool):
        """رفع درخواست «ردیابی اشخاص»: روشن/خاموش کردن زنده‌ی ردیابی اشخاص
        برای دوربینِ همین خانه (از صفحه‌ی «ردیابی اشخاص» تب «اشخاص»)؛
        روی ترد پخشِ جاری اعمال می‌شود و در cam ذخیره نمی‌شود (ذخیره با
        camera_store.update_camera در همان صفحه انجام شده)."""
        if self.stream_thread is not None:
            self.stream_thread.set_person_tracking(enabled)

    def on_people_count(self, count):
        # رفع باگ «کسی تو اتاقه ولی صفر نشون می‌داد»: این عدد از تشخیص‌دهنده‌ی
        # شخص/بدن کامل (PersonDetector در person_detector.py) می‌آید که همه‌ی
        # حالت‌های بدن (ایستاده، نشسته، نیم‌خیز، پشت به دوربین و ...) را
        # پوشش می‌دهد و نیازی به دیدن چهره ندارد؛ فقط اگر آن تشخیص‌دهنده در
        # دسترس نباشد، به شمارش بر پایه‌ی چهره برمی‌گردد - رجوع کنید به
        # CameraStreamThread.run().
        self.people_count_label.setText(f"👤 {count} نفر")

        # رفع درخواست: ثبت دائمی تاریخچه‌ی شمارش نفرات با ساعت/تاریخ روی
        # سیستمی که برنامه رویش اجراست (report_store.py) - فقط وقتی عدد
        # واقعاً نسبت به آخرین ثبت تغییر کرده باشد (وگرنه با نرخ فریم/چند
        # فریمِ people_count_signal، دیتابیس بی‌دلیل غرق می‌شد).
        if self.cam is not None and count != self._last_logged_count:
            self._last_logged_count = count
            report_store.log_person_count(
                self.cam.get("name", ""), count,
                nvr_id=self.cam.get("nvr_id"), channel=self.cam.get("channel"),
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        self._on_clicked(self)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # رفع درخواست: با گرفتن و کشیدن (درگ) یک خانه‌ی دارای دوربین، محل
        # نمایش آن با خانه‌ی مقصد جابه‌جا می‌شود (رجوع کنید به dropEvent و
        # CameraGridWidget.swap_slots).
        if (
            self.cam is not None
            and self._drag_start_pos is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
            and (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData("application/x-ias-slot-index", str(self.slot_index).encode("utf-8"))
            drag.setMimeData(mime)
            pixmap = self.video_label.pixmap()
            if pixmap is not None and not pixmap.isNull():
                drag.setPixmap(pixmap.scaled(96, 72, Qt.AspectRatioMode.KeepAspectRatio))
            self._drag_start_pos = None
            drag.exec(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        # رفع درخواست: دابل‌کلیک روی تصویر دوربین، بزرگ/کوچک‌نمایی (toggle) را
        # فعال می‌کند - منطق واقعیِ چیدمان در CameraGridWidget.toggle_maximize است.
        if self._on_double_clicked is not None:
            self._on_double_clicked(self)
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------- drag & drop --

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasFormat("application/x-ias-slot-index") or md.hasFormat("application/x-ias-camera-id"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        md = event.mimeData()
        if md.hasFormat("application/x-ias-slot-index"):
            try:
                src_index = int(bytes(md.data("application/x-ias-slot-index")).decode("utf-8"))
            except (TypeError, ValueError):
                return
            if self._on_slot_drag_swap is not None:
                self._on_slot_drag_swap(src_index, self.slot_index)
            event.acceptProposedAction()
        elif md.hasFormat("application/x-ias-camera-id"):
            cam_id = bytes(md.data("application/x-ias-camera-id")).decode("utf-8")
            if self._on_camera_drag_drop is not None:
                self._on_camera_drag_drop(cam_id, self.slot_index)
            event.acceptProposedAction()

    # --------------------------------------------------------------- start -

    def _guarded_connect(self, seq):
        """سازنده‌ی اتصال سیگنالِ محافظت‌شده با شماره‌ی نسل استریم: هندلر فقط
        وقتی صدا زده می‌شود که seq برابر نسل جاری همین خانه باشد؛ رویدادهای
        جامانده‌ی استریم قبلی (که در صف GUI گیر کرده‌اند) بی‌صدا نادیده گرفته
        می‌شوند - رجوع کنید به توضیح self._stream_seq در __init__."""
        def connect(signal, handler):
            signal.connect(
                lambda *args, _s=seq, _h=handler: _h(*args) if _s == self._stream_seq else None
            )
        return connect

    def start(self, cam: dict, rtsp_url: str, face_engine: FaceEngine, face_event_cb,
              plate_event_cb=None, person_event_cb=None):
        self._stream_seq += 1
        _seq = self._stream_seq
        _conn = self._guarded_connect(_seq)
        self._reset_zoom()
        self.cam = cam
        # نمایش اسم دوربین همراه با IP (کنار هم، جلوی شمارش افراد در همین
        # هدر). اگر کاربر برای دوربین اسمی وارد نکرده باشد، cam["name"] از
        # قبل برابر همان IP است (رجوع کنید به camera_store.py) که در این حالت
        # از تکرار IP در پرانتز جلوگیری می‌شود.
        cam_name = cam.get("name") or cam.get("ip", "")
        cam_ip = cam.get("ip", "")
        if cam_ip and cam_ip != cam_name:
            self._set_name_text(f"{cam_name} ({cam_ip})")
        else:
            self._set_name_text(cam_name)
        self.close_btn.setVisible(True)
        # (2.0.106-beta) دکمه‌ی صدا با شروع پخش نمایش داده می‌شود.
        self.audio_btn.setVisible(True)
        self.status_label.setText("در حال اتصال...")
        self.video_label.setText("در انتظار تصویر...")
        # (2.0.61-beta) ریست ردیاب قطع تصویر برای استریم جدید
        self._last_stream_state = ""

        self.stream_thread = CameraStreamThread(rtsp_url, face_engine, process_every_n=_PROCESS_EVERY_N)
        # همه‌ی اتصال‌ها محافظت‌شده با نسل استریم‌اند تا رویدادهای جامانده‌ی
        # استریم قبلی همین خانه (در صف GUI) به اشتباه روی دوربین جدید اعمال
        # نشوند - رجوع کنید به _guarded_connect/_stream_seq.
        _conn(self.stream_thread.frame_ready, self.on_frame_ready)
        _conn(self.stream_thread.error_signal, self.on_error)
        _conn(self.stream_thread.connected_signal, self.on_connected)
        _conn(self.stream_thread.people_count_signal, self.on_people_count)
        # (2.0.15-beta) وضعیت زنده‌ی شبکه/پهنای باند روی تایل
        _conn(self.stream_thread.stream_stats_signal, self.on_stream_stats)
        _conn(self.stream_thread.stream_status_signal, self.on_stream_status)
        _conn(self.stream_thread.region_entered, self._on_region_entered)
        _conn(self.stream_thread.person_detector_status_signal, self._on_detector_status)
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»
        _conn(self.stream_thread.fire_event_signal, self._on_fire_event)
        self._last_logged_count = None
        # با هر بازِ جدید (دوربین تازه در همین خانه)، وضعیت تشخیص شخص قبلی
        # (اگر مربوط به دوربین قبلی این خانه بوده) پاک می‌شود تا وضعیت
        # واقعی دوربین جدید (بعد از اولین دور تشخیصش) دوباره از صفر تعیین
        # شود - نه اینکه هشدار نادرست از پخش قبلی روی صفحه بماند.
        self._detector_available = None
        self._detector_error = ""
        self._refresh_detector_warning()
        # رفع درخواست «گزارش‌ها روی NVR ضبط بشه»: cam (کل دیکشنری دوربین، نه
        # فقط اسمش) پاس داده می‌شود تا on_face_event بتواند nvr_id/channel را
        # هم برای لینک «پخش ویدیوی NVR» در دیالوگ گزارش‌ها ثبت کند.
        # مثل بقیه‌ی سیگنال‌ها با محافظ نسل وصل می‌شود تا رویداد چهره‌ی ترد
        # قبلی بعد از جابه‌جایی دوربین به کادر جدید نرسد.
        _conn(
            self.stream_thread.face_event_signal,
            lambda person, crop: face_event_cb(cam, person, crop),
        )
        # رفع درخواست «سیستم پلاک‌خوان»: مثل face_event با محافظ نسل وصل
        # می‌شود تا رویداد پلاکِ ترد قبلی به دوربین جدید نرسد؛ cam (کل
        # دیکشنری دوربین، نه فقط اسمش) پاس داده می‌شود تا نام دوربین و
        # nvr_id/channel هم در گزارش عبور ثبت شود.
        if plate_event_cb is not None:
            _conn(
                self.stream_thread.plate_event_signal,
                lambda data: plate_event_cb(cam, data),
            )
        # وضعیت پلاک‌خوان (مثلاً «موتور OCR نصب نیست»): یک‌بار به کاربر
        # اطلاع داده می‌شود تا باکس خالیِ بی‌صدا نماند.
        _conn(
            self.stream_thread.plate_detector_status_signal,
            lambda available, msg: self._on_plate_detector_status(cam, available, msg),
        )
        # پلاک‌خوان این دوربین از همان تنظیم ذخیره‌شده (cam["plate_detection"])
        # فعال می‌شود - رجوع کنید به صفحه‌ی «پلاک‌خوان» تب «تعریف پلاک‌ها».
        self.stream_thread.set_plate_detection(bool(cam.get("plate_detection")))
        # تشخیص تصویری آتش/دود این دوربین از تنظیم ذخیره‌شده
        # (cam["fire_detection"]) فعال می‌شود - صفحه‌ی «اعلام حریق».
        self.stream_thread.set_fire_detection(bool(cam.get("fire_detection")))
        # رفع درخواست «ردیابی اشخاص»: مثل plate_event با محافظ نسل وصل
        # می‌شود تا رویداد ردِ ترد قبلی به دوربین جدید نرسد؛ cam (کل
        # دیکشنری دوربین) پاس داده می‌شود تا نام دوربین و nvr_id/channel در
        # گزارش مسیر حرکت ثبت شود.
        if person_event_cb is not None:
            _conn(
                self.stream_thread.person_event_signal,
                lambda data: person_event_cb(cam, data),
            )
        # ردیابی اشخاص این دوربین از همان تنظیم ذخیره‌شده
        # (cam["person_tracking"]) فعال می‌شود - رجوع کنید به صفحه‌ی
        # «ردیابی اشخاص» تب «اشخاص ردیابی‌شده».
        self.stream_thread.set_person_tracking(bool(cam.get("person_tracking")))
        self.stream_thread.start()
        # بنر صفحه‌ی «ردیابی اشخاص» را هم تازه کن (می‌شود «در حال آماده‌سازی»).
        try:
            self.detector_status_changed.emit()
        except Exception:
            pass
        # رفع درخواست: اگر برای این دوربین قبلاً محدوده‌های هشدار رسم و
        # ذخیره شده باشند (cameras.json)، همان لحظه‌ی اتصال دوباره روی ترد
        # پخش تازه فعال می‌شوند.
        saved_regions = cam.get("regions")
        if saved_regions:
            self.set_regions_silent(saved_regions)
        # اگر شمارش افراد به‌صورت سراسری (دکمه‌ی بالای شبکه‌ی دوربین‌ها) از قبل
        # روشن بوده، روی ترد پخش جدید هم بلافاصله اعمال می‌شود (رجوع کنید به
        # CameraGridWidget که بلافاصله بعد از start() هم set_people_counting
        # را صدا می‌زند؛ این خط فقط برای اطمینان از سازگاری با وضعیت فعلیِ
        # همین خانه است).
        if self._people_counting_enabled:
            self.stream_thread.set_people_counting(True)
        self._refresh_zoom_buttons()

    def on_connected(self):
        self.status_label.setText("متصل - پخش زنده")

    def on_error(self, msg):
        self.status_label.setText(f"خطا: {msg}")

    # (2.0.15-beta) نمایش زنده‌ی وضعیت شبکه/پهنای باند روی تایل.
    def on_stream_stats(self, stats):
        try:
            state = (stats or {}).get("state", "")
            fps = (stats or {}).get("fps", 0)
            kbps = (stats or {}).get("kbps", 0)
            # (2.0.52-beta) تغذیه‌ی مانیتور سراسری پهنای باند
            try:
                cam = getattr(self, "cam", None) or {}
                if cam.get("id"):
                    from bandwidth import get_monitor
                    get_monitor().update(cam.get("id"), kbps,
                                         cam.get("name") or cam.get("ip") or "")
            except Exception:
                pass
            if state == "weak":
                self.net_label.setText(f"📶 ضعیف ({fps} فریم/ث)")
                self.net_label.setStyleSheet(
                    "color:#f39c12; font-size:11px; font-weight:bold;")
                self.net_label.setToolTip(
                    f"پهنای باند کم (~{kbps} کیلوبیت/ثانیه)؛ پردازش تشخیص "
                    f"خودکار کم‌تواتر شد تا تصویر پایدار بماند.")
            elif state == "down":
                self.net_label.setText("📶 قطع")
                self.net_label.setStyleSheet(
                    "color:#e74c3c; font-size:11px; font-weight:bold;")
            else:
                self.net_label.setText("📶")
                self.net_label.setStyleSheet(
                    "color:#2ecc71; font-size:11px; font-weight:bold;")
                self.net_label.setToolTip(
                    f"شبکه پایدار (~{kbps} کیلوبیت/ثانیه، {fps} فریم/ثانیه)")
        except Exception:
            pass

    def on_stream_status(self, payload):
        try:
            state = (payload or {}).get("state", "")
            # (2.0.61-beta) هشدار قطع تصویر: گذار از «متصل» به «تلاش مجدد»
            # یعنی تصویری که وصل بود واقعاً قطع شده؛ بازگشت به «متصل» یعنی
            # وصل مجدد. تلاش اولیه‌ی ناموفق (بدون اتصال قبلی) هشدار ندارد.
            # منطق خالص در video_loss.py است تا بدون Qt هم تست شود.
            prev = getattr(self, "_last_stream_state", "")
            _vl_kind = _video_loss_transition(prev, state)
            if _vl_kind in ("lost", "recovered") and self.cam is not None:
                self.video_loss_signal.emit(dict(self.cam), _vl_kind)
            self._last_stream_state = state
            if state == "reconnecting":
                n = (payload or {}).get("reconnects", 0)
                self.net_label.setText(f"📶 اتصال مجدد ({n})")
                self.net_label.setStyleSheet(
                    "color:#f39c12; font-size:11px; font-weight:bold;")
                self.net_label.setToolTip(
                    "اتصال استریم قطع شد؛ تلاش خودکار برای وصل مجدد…")
        except Exception:
            pass

    def _on_plate_detector_status(self, cam, available, msg):
        """نمایش یک‌باره‌ی هشدار وضعیت پلاک‌خوان (مثلاً «موتور OCR نصب نیست»)."""
        if not msg or msg in CameraSlotWidget._plate_status_warned:
            return
        CameraSlotWidget._plate_status_warned.add(msg)
        cam_name = ""
        if isinstance(cam, dict):
            cam_name = cam.get("name") or cam.get("ip", "")
        QMessageBox.warning(
            self, "پلاک‌خوان",
            f"دوربین «{cam_name}»: {msg}" if cam_name else msg)

    def on_frame_ready(self, display_frame, raw_frame):
        self.latest_raw_frame = raw_frame
        # بهینه‌سازی سرعت (۱): هر تایل حداکثر ~۱۵ فریم‌برثانیه به‌روز می‌شود.
        # چشم انسان در کاشی‌های کوچک نظارتی فرق ۱۵ با ۲۵ فریم را حس نمی‌کند،
        # ولی تبدیل/مقیاس هر فریم در ترد UI پرهزینه است.
        now = time.monotonic()
        if now - self._last_display_ts < 1.0 / 15.0:
            return
        self._last_display_ts = now
        show_frame = display_frame
        if self._zoom > 1.0:
            show_frame = self._zoom_crop(display_frame)
        elif self._last_zoom_crop is not None:
            # زوم به ۱ برگشته ولی کراپِ قبلی هنوز ست است - پاکش می‌کنیم تا
            # محدوده‌ها دوباره نسبت به کل فریم رسم شوند.
            self._last_zoom_crop = None
            self.video_label.set_zoom_crop_norm(None)
        # بهینه‌سازی سرعت (۲): به‌جای تبدیل BGR→RGB روی فریم کامل ۱۰۸۰p و
        # بعد مقیاس نرم‌افزاری پرهزینه در Qt، اول با OpenCV (سریع) به اندازه‌ی
        # خودِ لیبل کوچک می‌کنیم و بعد تبدیل می‌کنیم - حدود ۱۰ برابر ارزان‌تر.
        try:
            lw, lh = self.video_label.width(), self.video_label.height()
        except Exception:
            lw, lh = 0, 0
        if lw > 0 and lh > 0:
            h, w = show_frame.shape[:2]
            _scale = min(lw / w, lh / h)
            if _scale < 1.0:
                _nw, _nh = max(1, int(w * _scale)), max(1, int(h * _scale))
                show_frame = cv2.resize(show_frame, (_nw, _nh),
                                        interpolation=cv2.INTER_LINEAR)
        pixmap = _bgr_to_pixmap(show_frame)
        if pixmap is None:
            return
        # چون فریم از قبل به اندازه‌ی لیبل (با حفظ نسبت) کوچک شده، دیگر
        # scaled() لازم نیست - مستقیم نمایش داده می‌شود.
        self.video_label.setPixmap(pixmap)

    # ------------------------------------------------- zoom in/out (هر کادر) --
    _ZOOM_MIN = 1.0
    _ZOOM_MAX = 8.0
    _ZOOM_STEP = 1.25

    def _reset_zoom(self):
        self._zoom = 1.0
        self._zoom_cx = 0.5
        self._zoom_cy = 0.5
        self._last_zoom_crop = None
        self.video_label.set_zoom_crop_norm(None)
        self._panning = False
        self._pan_button = None
        self._refresh_zoom_buttons()

    def _clamp_zoom_center(self):
        half = 0.5 / self._zoom
        self._zoom_cx = min(max(self._zoom_cx, half), 1.0 - half)
        self._zoom_cy = min(max(self._zoom_cy, half), 1.0 - half)

    def _zoom_crop(self, frame):
        """کراپ ناحیه‌ی نمایشی از فریم بر اساس ضریب و مرکز زوم؛ ابعاد کراپ هم
        در _last_zoom_crop نگه داشته می‌شود تا نگاشت ماوس→فریم دقیق باشد."""
        h, w = frame.shape[:2]
        cw, ch = w / self._zoom, h / self._zoom
        cx = min(max(self._zoom_cx * w, cw / 2.0), w - cw / 2.0)
        cy = min(max(self._zoom_cy * h, ch / 2.0), h - ch / 2.0)
        x0, y0 = int(cx - cw / 2.0), int(cy - ch / 2.0)
        cw_i, ch_i = max(1, int(cw)), max(1, int(ch))
        self._last_zoom_crop = (x0, y0, cw_i, ch_i)
        # لیبل هم باید کراپ را بداند تا محدوده‌ها (نرمالِ کل فریم) سر جایشان
        # رسم شوند و با زوم جابه‌جا نشوند.
        if w > 0 and h > 0:
            self.video_label.set_zoom_crop_norm((x0 / w, y0 / h, cw_i / w, ch_i / h))
        return frame[y0:y0 + ch_i, x0:x0 + cw_i]

    def _label_pos_to_frame_norm(self, label_pos):
        """موقعیت نشانگر (پیکسل لیبل) به مختصات نرمال 0..1 فریم اصلی - با در
        نظر گرفتن کراپِ زومِ جاری."""
        rect = self.video_label._frame_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        if self.latest_raw_frame is None:
            return None
        h, w = self.latest_raw_frame.shape[:2]
        px = (label_pos.x() - rect.x()) / rect.width()
        py = (label_pos.y() - rect.y()) / rect.height()
        if self._last_zoom_crop is None:
            return (min(max(px, 0.0), 1.0), min(max(py, 0.0), 1.0))
        x0, y0, cw, ch = self._last_zoom_crop
        fx = (x0 + px * cw) / w
        fy = (y0 + py * ch) / h
        return (min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0))

    def _zoom_at(self, focus_norm, factor):
        """زوم حول یک نقطه‌ی ثابت از فریم (نرمال 0..1) - نقطه‌ی زیر نشانگر
        سر جایش می‌ماند."""
        if self.cam is None or self.is_draw_mode():
            return
        new_zoom = min(self._ZOOM_MAX, max(self._ZOOM_MIN, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            return
        fx, fy = focus_norm
        r = self._zoom / new_zoom
        self._zoom_cx = fx - (fx - self._zoom_cx) * r
        self._zoom_cy = fy - (fy - self._zoom_cy) * r
        self._zoom = new_zoom
        self._clamp_zoom_center()
        self._refresh_zoom_buttons()

    def zoom_in(self):
        self._zoom_at((self._zoom_cx, self._zoom_cy), self._ZOOM_STEP)

    def zoom_out(self):
        self._zoom_at((self._zoom_cx, self._zoom_cy), 1.0 / self._ZOOM_STEP)

    def _refresh_zoom_buttons(self):
        has_cam = self.cam is not None
        drawing = self.is_draw_mode()
        self.zoom_in_btn.setEnabled(has_cam and not drawing and self._zoom < self._ZOOM_MAX)
        self.zoom_out_btn.setEnabled(has_cam and not drawing and self._zoom > self._ZOOM_MIN)
        self.zoom_reset_btn.setEnabled(has_cam and not drawing and self._zoom > self._ZOOM_MIN)
        self.zoom_reset_btn.setToolTip(
            f"بازنشانی بزرگ‌نمایی (فعلی: {self._zoom:.1f}×)"
            if self._zoom > self._ZOOM_MIN else "بازنشانی بزرگ‌نمایی (نمایش کل فریم)"
        )

    def _pan_to(self, label_pos):
        """جابه‌جایی مرکز زوم بر اساس درگ ماوس (پن) - محتوا دنبال نشانگر می‌آید."""
        rect = self.video_label._frame_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return
        if self.latest_raw_frame is None or self._last_zoom_crop is None:
            return
        if self._pan_start_label_pos is None or self._pan_start_center is None:
            return
        h, w = self.latest_raw_frame.shape[:2]
        _, _, cw, ch = self._last_zoom_crop
        dx_px = label_pos.x() - self._pan_start_label_pos.x()
        dy_px = label_pos.y() - self._pan_start_label_pos.y()
        dnx = dx_px / rect.width() * (cw / w)
        dny = dy_px / rect.height() * (ch / h)
        sx, sy = self._pan_start_center
        self._zoom_cx, self._zoom_cy = sx - dnx, sy - dny
        self._clamp_zoom_center()

    def eventFilter(self, obj, event):
        # زوم با اسکرول + پن (Shift+درگ چپ یا درگ با دکمه‌ی وسط) روی تصویر هر
        # خانه - بدون تداخل با رسم محدوده (draw_mode) و درگ‌انددراپ جابه‌جایی
        # خانه‌ها (درگ چپِ ساده همچنان مال جابه‌جایی خانه است).
        if obj is self.video_label:
            etype = event.type()
            if etype == QEvent.Type.Wheel:
                if self.cam is not None and not self.is_draw_mode():
                    delta = event.angleDelta().y()
                    if delta:
                        focus = self._label_pos_to_frame_norm(event.position())
                        self._zoom_at(focus or (0.5, 0.5),
                                      self._ZOOM_STEP if delta > 0 else 1.0 / self._ZOOM_STEP)
                        return True
            elif etype == QEvent.Type.MouseButtonPress:
                btn = event.button()
                if (self._zoom > 1.0 and not self.is_draw_mode() and not self._panning
                        and (btn == Qt.MouseButton.MiddleButton
                             or (btn == Qt.MouseButton.LeftButton
                                 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier))):
                    self._panning = True
                    self._pan_button = btn
                    self._pan_start_label_pos = event.position()
                    self._pan_start_center = (self._zoom_cx, self._zoom_cy)
                    self.video_label.setCursor(Qt.CursorShape.ClosedHandCursor)
                    return True
            elif etype == QEvent.Type.MouseMove:
                if self._panning:
                    self._pan_to(event.position())
                    return True
            elif etype == QEvent.Type.MouseButtonRelease:
                if self._panning and event.button() == self._pan_button:
                    self._panning = False
                    self._pan_button = None
                    self._pan_start_label_pos = None
                    self._pan_start_center = None
                    self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
                    return True
        return super().eventFilter(obj, event)

    def stop(self):
        # رفع درخواست «ردیابی اشخاص»: قبل از بالا بردن نسل استریم (که
        # رویدادهای بعدی را با _guarded_connect نادیده می‌گیرد)، ردهای باز
        # را می‌بندیم تا «حضور»هایشان در دیتابیس بسته شوند.
        if self.stream_thread is not None:
            try:
                self.stream_thread.flush_person_tracks()
            except Exception:
                pass
        # نسل استریم را همین‌جا بالا می‌بریم تا هر رویداد جامانده‌ی ترد قبلی
        # که هنوز در صف GUI است، با _guarded_connect نادیده گرفته شود (رفع
        # باگ «یک فریم از دوربین قبلی در کادر می‌ماند»).
        self._stream_seq += 1
        # با توقف پخش، بزرگ‌نمایی هم به حالت عادی برمی‌گردد.
        self._reset_zoom()
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.stop()
        self.stream_thread = None
        self.cam = None
        # (2.0.106-beta) توقف صدا و مخفی کردن دکمه/ولوم.
        self._stop_audio()
        self.audio_btn.setVisible(False)
        self.volume_slider.setVisible(False)
        self.latest_raw_frame = None
        # (2.0.61-beta) ریست ردیاب قطع تصویر
        self._last_stream_state = ""
        self._set_name_text("خالی")
        # بنر صفحه‌ی «ردیابی اشخاص» را هم تازه کن.
        try:
            self.detector_status_changed.emit()
        except Exception:
            pass
        self.close_btn.setVisible(False)
        self.status_label.setText("")
        self.video_label.clear()
        self.video_label.setText("خالی — برای افزودن دوربین،\nدر لیست سمت چپ دابل‌کلیک کنید")
        self.set_selected(False)
        # رفع درخواست: با بسته‌شدن/خالی‌شدن خانه، شمارش افراد هم خاموش و
        # برچسب تعداد پاک می‌شود.
        self._people_counting_enabled = False
        self.people_count_label.setText("")
        # پاک‌کردن کامل وضعیت محدوده‌های هشدار/آلارم این خانه (دوربین بعدی
        # که در این خانه باز شود، محدوده‌های خودش را - اگر داشته باشد -
        # جداگانه از cameras.json بارگذاری می‌کند).
        self.pending_points = None
        self.regions = []
        self._editing_region_id = None

    # -- صدای دوربین روی کادر (2.0.106-beta) ---------------------------------
    def _toggle_audio(self):
        """روشن/خاموش کردن صدای دوربین بدون دیالوگ."""
        if self._listen_session is not None:
            self._stop_audio()
            return
        if not self.cam:
            return
        try:
            from camera_audio import ListenSession
            session = ListenSession()
            # نمایش وضعیت/خطا روی همین کادر
            try:
                session.state_changed = self._on_audio_state
                session.error_occurred = self._on_audio_error
            except Exception:
                pass
            # ولوم اولیه از اسلایدر
            try:
                session.set_volume(self.volume_slider.value() / 100.0)
            except Exception:
                pass
            cam = dict(self.cam)
            ok = session.start(cam, on_url_found=self._on_audio_url_found)
            if ok:
                self._listen_session = session
                self.audio_btn.setText("🎧")
                self.audio_btn.setToolTip("قطع صدای دوربین")
                self.volume_slider.setVisible(True)
                self.status_label.setText("در حال اتصال صدا...")
            else:
                self.status_label.setText("خطا در شروع صدا")
        except Exception as e:
            try:
                self.status_label.setText(f"خطای صدا: {e}"[:60])
            except Exception:
                pass

    def _on_audio_state(self, state: str):
        try:
            if state == "playing":
                self.status_label.setText("🔊 صدا وصل است")
            elif state.startswith("connecting"):
                self.status_label.setText("در حال اتصال صدا...")
            elif state == "error":
                pass  # خطا از _on_audio_error می‌آید
        except Exception:
            pass

    def _on_audio_error(self, msg: str):
        # (2.0.110-beta) اگر خطا احرازهویت باشد، مثل VLC پنجره‌ی
        # یوزرنیم/پسورد باز می‌کنیم تا کاربر رمز درست را بدهد و دوباره
        # تلاش می‌کنیم.
        is_auth_error = False
        try:
            low = (msg or "").lower()
            is_auth_error = ("احرازهویت" in (msg or "") or "401" in low
                             or "unauthorized" in low)
        except Exception:
            pass
        if is_auth_error and not getattr(self, "_audio_auth_asked", False):
            self._audio_auth_asked = True
            try:
                self._stop_audio()
            except Exception:
                pass
            creds = self._ask_audio_credentials()
            # فلگ را ریست می‌کنیم تا دفعه‌ی بعد دوباره بپرسد
            self._audio_auth_asked = False
            if creds:
                user, pwd = creds
                try:
                    if self.cam is not None:
                        self.cam["user"] = user
                        self.cam["pass"] = pwd
                    # رمز جدید را در store هم ذخیره می‌کنیم تا دفعه‌ی بعد
                    # لازم نباشد دوباره پرسیده شود.
                    save_cb = getattr(self, "_on_audio_creds_save_cb", None)
                    if save_cb and self.cam:
                        try:
                            save_cb(self.cam.get("id"), user, pwd)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    self.status_label.setText("تلاش مجدد با رمز جدید...")
                except Exception:
                    pass
                self._toggle_audio()
                return
        try:
            # فقط خط اول پیام را نشان می‌دهیم
            line = (msg or "").split("\n")[0][:80]
            self.status_label.setText(f"⚠ صدا: {line}")
        except Exception:
            pass
        # نشست ناموفق را تمیز می‌کنیم تا دکمه به حالت اول برگردد
        try:
            self._stop_audio()
        except Exception:
            pass

    def _ask_audio_credentials(self):
        """دیالوگ یوزرنیم/پسورد به سبک VLC برای اتصال صدا.
        خروجی: (user, password) یا None اگر انصراف."""
        try:
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, \
                QLabel, QLineEdit, QDialogButtonBox
            from PyQt6.QtCore import Qt
            cam_name = ""
            try:
                cam_name = (self.cam or {}).get("name", "")
            except Exception:
                pass
            dlg = QDialog(self)
            dlg.setWindowTitle("🔐 احرازهویت صدای دوربین")
            dlg.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            dlg.setMinimumWidth(320)
            lay = QVBoxLayout(dlg)
            lay.addWidget(QLabel(
                f"برای اتصال به صدای «{cam_name}» نام کاربری و رمز را وارد کنید:"))
            user_edit = QLineEdit()
            user_edit.setPlaceholderText("نام کاربری")
            try:
                user_edit.setText((self.cam or {}).get("user", "") or "")
            except Exception:
                pass
            pass_edit = QLineEdit()
            pass_edit.setPlaceholderText("رمز عبور")
            pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
            lay.addWidget(QLabel("نام کاربری:"))
            lay.addWidget(user_edit)
            lay.addWidget(QLabel("رمز عبور:"))
            lay.addWidget(pass_edit)
            btns = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok |
                QDialogButtonBox.StandardButton.Cancel)
            btns.button(QDialogButtonBox.StandardButton.Ok).setText("اتصال")
            btns.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            lay.addWidget(btns)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                return (user_edit.text().strip(), pass_edit.text())
            return None
        except Exception:
            return None

    def _stop_audio(self):
        try:
            if self._listen_session is not None:
                self._listen_session.stop()
        except Exception:
            pass
        self._listen_session = None
        try:
            self.audio_btn.setText("🔊")
            self.audio_btn.setToolTip("شنیدن صدای دوربین")
            self.volume_slider.setVisible(False)
        except Exception:
            pass

    def _on_volume_changed(self, v: int):
        if self._listen_session is not None:
            try:
                self._listen_session.set_volume(v / 100.0)
            except Exception:
                pass

    def _on_audio_url_found(self, url: str):
        """مسیر صوتی موفق پیدا شد — برای کش کردن به MainWindow اطلاع می‌دهیم."""
        # (2.0.112-beta) خودِ self.cam را هم به‌روز می‌کنیم تا دفعه‌ی بعد
        # کش استفاده شود (قبلاً فقط در store ذخیره می‌شد).
        try:
            if self.cam is not None:
                self.cam["audio_url"] = url
        except Exception:
            pass
        cb = getattr(self, "_on_audio_url_found_cb", None)
        if cb and self.cam:
            try:
                cb(self.cam.get("id"), url)
            except Exception:
                pass
        self.video_label.set_pending_points_norm(None)
        self.video_label.set_confirmed_regions([])
        self.video_label.set_draw_mode(False)
        self._clear_alarm()
        self._detector_available = None
        self._detector_error = ""
        self._refresh_detector_warning()
        self._refresh_zoom_buttons()
        self.tripwire_changed.emit()



class RegionManagerDialog(QDialog):
    """رفع درخواست: مدیریت (مشاهده/حذف) محدوده‌های هشدار تعریف‌شده برای
    دوربین انتخاب‌شده‌ی فعلی - چون هر دوربین می‌تواند هم‌زمان چند محدوده‌ی
    نام‌دار داشته باشد و راهی برای حذف تک‌تک آن‌ها لازم است."""

    def __init__(self, slot, on_changed, parent=None):
        super().__init__(parent)
        self.setWindowTitle("مدیریت محدوده‌های هشدار")
        self.resize(340, 320)
        self.slot = slot
        self.on_changed = on_changed

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("محدوده‌های تعریف‌شده برای این دوربین:"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        # رفع درخواست «بتونه اندازه و شکل محدوده تغییر بده»: این دکمه همین
        # دیالوگ را می‌بندد و روی تصویر زنده‌ی همان دوربین، محدوده‌ی
        # انتخاب‌شده را به‌شکل یک پیش‌نمایش سبزِ قابل‌کشیدن درمی‌آورد
        # (رجوع کنید به CameraSlotWidget.start_edit_region) - از همان
        # دکمه‌های «✅ تایید و نام‌گذاری»/«❌ لغو رسم» نوار ابزار (که در حالت
        # ویرایش به «💾 ذخیره ویرایش شکل»/«↩ لغو ویرایش» تغییر متن می‌دهند)
        # برای ذخیره یا انصراف استفاده می‌شود.
        self.edit_btn = QPushButton("✏ ویرایش شکل/اندازه")
        self.edit_btn.setToolTip(
            "این دیالوگ را می‌بندد و امکان کشیدن گوشه‌های این محدوده را روی تصویر دوربین فعال می‌کند."
        )
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        layout.addWidget(self.edit_btn)

        self.remove_btn = QPushButton("🗑 حذف محدوده‌ی انتخاب‌شده")
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        layout.addWidget(self.remove_btn)

        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._reload()

    def _reload(self):
        self.list_widget.clear()
        for region in self.slot.regions:
            label = f"شماره {region['number']}"
            if region.get("name"):
                label += f" / {region['name']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, region["id"])
            self.list_widget.addItem(item)

    def _on_remove_clicked(self):
        item = self.list_widget.currentItem()
        if item is None:
            return
        region_id = item.data(Qt.ItemDataRole.UserRole)
        self.slot.remove_region(region_id)
        if self.on_changed is not None:
            self.on_changed()
        self._reload()

    def _on_edit_clicked(self):
        item = self.list_widget.currentItem()
        if item is None:
            QMessageBox.information(self, "ویرایش شکل/اندازه", "ابتدا یک محدوده را از فهرست انتخاب کنید.")
            return
        region_id = item.data(Qt.ItemDataRole.UserRole)
        if not self.slot.start_edit_region(region_id):
            QMessageBox.information(
                self, "ویرایش شکل/اندازه",
                "الان امکان شروع ویرایش نیست (یک رسم/ویرایش دیگر در حال انجام است)."
            )
            return
        QMessageBox.information(
            self, "ویرایش شکل/اندازه",
            "گوشه‌های سبزِ روی تصویر را با کلیک-و-درگ جابه‌جا کنید. برای افزودن گوشه‌ی "
            "تازه روی وسط یک ضلع کلیک کنید؛ برای حذف یک گوشه، روی آن کلیک راست کنید "
            "(حداقل ۳ گوشه لازم است). در پایان از نوار ابزار «💾 ذخیره ویرایش شکل» یا "
            "«↩ لغو ویرایش» را بزنید."
        )
        self.accept()



class CameraGridWidget(QWidget):
    """شبکه‌ی نمایش هم‌زمان دوربین‌ها با تعداد خانه‌ی قابل انتخاب
    (1، 4، 9، 16، 32 یا 64). با تغییر تعداد، دوربین‌های از قبل باز تا حد
    امکان در چیدمان جدید حفظ می‌شوند."""

    # رفع درخواست: هر بار خانه‌ی انتخاب‌شده عوض شود (یا با -1 خالی شود)،
    # این سیگنال ارسال می‌شود تا نوار ابزار «محدوده‌ی هشدار» در MainWindow
    # وضعیت دکمه‌های تایید/لغو/مدیریت را برای همان خانه به‌روزرسانی کند.
    selection_changed = pyqtSignal(int)
    # رفع باگ: علاوه بر عوض‌شدن انتخاب، با هر تغییر واقعی در وضعیت
    # محدوده‌های هر خانه (رسم/تایید/حذف - رجوع کنید به
    # CameraSlotWidget.tripwire_changed) هم باید نوار ابزار به‌روز شود،
    # وگرنه دکمه‌ها با وضعیت واقعی هم‌گام نمی‌مانند.
    tripwire_changed = pyqtSignal()
    # تغییر وضعیت موتور تشخیص شخص در هر خانه (CameraSlotWidget.
    # detector_status_changed)؛ به صورت تجمیعی به MainWindow می‌رسد تا بنر
    # صفحه‌ی «ردیابی اشخاص» با وضعیت واقعی به‌روز شود.
    detector_status_changed = pyqtSignal()
    # (2.0.61-beta) هشدار قطع تصویر هر خانه (CameraSlotWidget.
    # video_loss_signal)؛ به صورت تجمیعی به MainWindow می‌رسد.
    video_loss = pyqtSignal(object, str)

    def __init__(self, face_engine: FaceEngine, on_face_event, on_external_camera_drop=None,
                 on_region_alert=None, on_fire_event=None, on_plate_event=None,
                 on_person_event=None, on_audio_url_found=None,
                 on_audio_creds_save=None, parent=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.on_face_event = on_face_event
        # رفع درخواست: با ورود شخصی به یکی از محدوده‌های هشدار هر خانه، این
        # callback (در MainWindow) به هر خانه‌ی تازه‌ساخته‌شده هم پاس داده
        # می‌شود تا رویداد در پنل رویدادها هم ثبت شود.
        self.on_region_alert = on_region_alert
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: مشابه on_region_alert،
        # به هر خانه‌ی تازه‌ساخته‌شده پاس داده می‌شود.
        self.on_fire_event = on_fire_event
        # رفع درخواست «سیستم پلاک‌خوان»: callback رویداد پلاک (در MainWindow)
        # به هر خانه‌ی تازه‌ساخته‌شده پاس داده می‌شود تا عبور پلاک‌ها
        # (تعریف‌شده/تعریف‌نشده) ثبت و گزارش شود.
        self.on_plate_event = on_plate_event
        # رفع درخواست «ردیابی اشخاص»: callback رویداد رد (در MainWindow) به
        # هر خانه‌ی تازه‌ساخته‌شده پاس داده می‌شود تا تطبیق بین دوربینی و
        # ثبت «حضور» در person_store انجام شود.
        self.on_person_event = on_person_event
        # رفع درخواست: وقتی موردی از لیست دوربین‌ها (خارج از شبکه‌ی نمایش) روی
        # یک خانه رها (drop) شود، این callback (در MainWindow) صدا زده می‌شود
        # تا رمز عبور را در صورت نیاز بپرسد و آدرس RTSP را بسازد.
        self.on_external_camera_drop = on_external_camera_drop
        # (2.0.106-beta) کال‌بک ذخیره‌ی مسیر صوتی موفق هر خانه.
        self.on_audio_url_found = on_audio_url_found
        # (2.0.110-beta) کال‌بک ذخیره‌ی یوزر/پس جدید صدا.
        self.on_audio_creds_save = on_audio_creds_save
        self.slots = []
        self.selected_index = None
        # کادرهای دوربین «سیال»‌اند: اندازه‌ی هر کادر از چیدمان (QGridLayout)
        # می‌آید و فضای در دسترس شبکه را پر می‌کند؛ با کوچک/بزرگ شدن
        # پنل‌ها یا پنجره، کادرها هم کوچک/بزرگ می‌شوند (رفتار استاندارد
        # VMS). هیچ setFixedSizeای روی کادرها گذاشته نمی‌شود تا موقع
        # تغییر اندازه‌ی پنجره، پنل کناری له نشود و اسکرول‌بار بی‌مورد
        # ظاهر نشود.
        # رفع درخواست: با دابل‌کلیک روی یک خانه، آن خانه تمام فضای شبکه را
        # اشغال می‌کند (بزرگ‌نمایی) و بقیه‌ی خانه‌ها مخفی می‌شوند؛ برای بازگشت
        # به حالت قبل، موقعیت اصلی (ردیف/ستون) هر خانه را نگه می‌داریم.
        self._slot_positions = []  # index -> (row, col)
        self._rows = 0
        self._cols = 0
        self._maximized_index = None
        # رفع درخواست: به‌جای یک دکمه‌ی جداگانه‌ی شمارش افراد برای هر خانه،
        # فقط یک دکمه‌ی سراسری بالای کل شبکه‌ی دوربین‌ها وجود دارد (رجوع کنید
        # به MainWindow.people_toggle_btn و set_people_counting_all). این
        # فلگ وضعیت همان دکمه‌ی سراسری را نگه می‌دارد تا خانه‌های تازه‌ساز
        # (تغییر چیدمان) یا دوربین‌های تازه‌باز هم همان وضعیت را بگیرند.
        self._people_counting_enabled = False

        self._layout = QGridLayout(self)
        self._layout.setSpacing(4)
        self._layout.setContentsMargins(2, 2, 2, 2)

        self.set_grid_size(4)

    # ------------------------------------------------------------- layout --

    def set_grid_size(self, count: int):
        rows, cols = GRID_LAYOUTS.get(count, (2, 2))

        # حفظ دوربین‌های در حال پخش (تا حد ظرفیت چیدمان جدید).
        previous = [(slot.cam, slot.stream_thread.rtsp_url if slot.stream_thread else None)
                    for slot in self.slots if slot.cam is not None]

        for slot in self.slots:
            slot.stop()
            self._layout.removeWidget(slot)
            slot.setParent(None)
            slot.deleteLater()
        self.slots = []
        self.selected_index = None
        self.selection_changed.emit(-1)
        self._slot_positions = []
        self._rows = rows
        self._cols = cols
        self._maximized_index = None

        total = rows * cols
        for r in range(rows):
            for c in range(cols):
                slot = CameraSlotWidget(
                    self._on_slot_clicked, self._on_slot_close_requested, self._on_slot_double_clicked,
                    on_slot_drag_swap=self._on_slot_drag_swap,
                    on_camera_drag_drop=self._on_camera_drag_drop,
                    on_region_alert=self.on_region_alert,
                    on_fire_event=self.on_fire_event,
                    on_plate_event=self.on_plate_event,
                    on_person_event=self.on_person_event,
                    on_audio_url_found=self.on_audio_url_found,
                    on_audio_creds_save=self.on_audio_creds_save,
                )
                slot.slot_index = len(self.slots)
                slot.tripwire_changed.connect(self.tripwire_changed.emit)
                slot.detector_status_changed.connect(
                    self.detector_status_changed.emit)
                # (2.0.61-beta) هشدار قطع تصویر هر خانه به سیگنال تجمیعی گرید
                slot.video_loss_signal.connect(self.video_loss.emit)
                self._layout.addWidget(slot, r, c)
                self.slots.append(slot)
                self._slot_positions.append((r, c))

        for cam, rtsp_url in previous[:total]:
            if rtsp_url:
                self.assign_camera(cam, rtsp_url)

    def set_people_counting_all(self, enabled: bool):
        """رفع درخواست: یک گزینه‌ی واحد بالای تمام پنجره‌های دوربین‌ها که
        شمارش افراد Real Time را برای همه‌ی دوربین‌های باز، هم‌زمان روشن/
        خاموش می‌کند. تعداد نفرات هم‌چنان جداگانه، بالای پنجره‌ی هر دوربین
        نمایش داده می‌شود (رجوع کنید به CameraSlotWidget.on_people_count)."""
        self._people_counting_enabled = bool(enabled)
        for slot in self.slots:
            if slot.cam is not None:
                slot.set_people_counting(self._people_counting_enabled)

    # -------------------------------------------------------- assignment --

    def assign_camera(self, cam: dict, rtsp_url: str) -> bool:
        """دوربین را در اولین خانه‌ی خالی باز می‌کند. اگر همان دوربین از قبل
        باز است، فقط آن خانه را انتخاب می‌کند. اگر خانه‌ی خالی نباشد، False
        برمی‌گرداند تا پیام مناسب به کاربر نمایش داده شود."""
        for i, slot in enumerate(self.slots):
            if slot.cam is not None and slot.cam["id"] == cam["id"]:
                self._select_index(i)
                return True

        for i, slot in enumerate(self.slots):
            if slot.cam is None:
                slot.start(cam, rtsp_url, self.face_engine, self.on_face_event, self.on_plate_event, self.on_person_event)
                # اعمال وضعیت فعلیِ دکمه‌ی سراسریِ شمارش افراد روی دوربین
                # تازه‌باز.
                slot.set_people_counting(self._people_counting_enabled)
                self._select_index(i)
                return True

        return False

    def is_camera_open(self, cam_id) -> bool:
        return any(slot.cam is not None and slot.cam["id"] == cam_id for slot in self.slots)

    def assign_camera_to_slot(self, cam: dict, rtsp_url: str, slot_index: int):
        """رفع درخواست: دوربین را دقیقاً در خانه‌ی مشخص‌شده (مثلاً همان خانه‌ای
        که کاربر آیتم را روی آن رها/drop کرده) باز می‌کند. اگر همان دوربین از
        قبل در خانه‌ی دیگری باز است، به‌جای باز کردن یک اتصال تکراری، فقط
        محل نمایش آن به خانه‌ی مقصد منتقل (جابه‌جا) می‌شود."""
        if not (0 <= slot_index < len(self.slots)):
            return
        for i, slot in enumerate(self.slots):
            if slot.cam is not None and slot.cam["id"] == cam["id"]:
                if i != slot_index:
                    self.swap_slots(i, slot_index)
                self._select_index(slot_index)
                return
        target = self.slots[slot_index]
        target.stop()
        target.start(cam, rtsp_url, self.face_engine, self.on_face_event, self.on_plate_event, self.on_person_event)
        target.set_people_counting(self._people_counting_enabled)
        self._select_index(slot_index)

    def swap_slots(self, idx_a: int, idx_b: int):
        """رفع درخواست: جابه‌جا کردن محل نمایش دو خانه با درگ‌کردن. چون هر ترد
        پخش (CameraStreamThread) مستقیماً به سیگنال‌های همان خانه وصل است، به
        جای جابه‌جایی فیزیکی ویجت‌ها در چیدمان (که منطق بزرگ‌نمایی/بازگشت را
        پیچیده می‌کرد)، محتوای دو خانه (دوربین + اتصال) با هم جابه‌جا می‌شود -
        از دید کاربر دقیقاً همان «عوض شدن جای پنجره‌ی نمایش» است."""
        if idx_a == idx_b or not (0 <= idx_a < len(self.slots)) or not (0 <= idx_b < len(self.slots)):
            return
        slot_a, slot_b = self.slots[idx_a], self.slots[idx_b]
        if slot_a.cam is None and slot_b.cam is None:
            return

        cam_a = slot_a.cam
        url_a = slot_a.stream_thread.rtsp_url if slot_a.stream_thread else None
        cam_b = slot_b.cam
        url_b = slot_b.stream_thread.rtsp_url if slot_b.stream_thread else None

        slot_a.stop()
        slot_b.stop()
        if cam_b is not None:
            slot_a.start(cam_b, url_b, self.face_engine, self.on_face_event, self.on_plate_event, self.on_person_event)
            slot_a.set_people_counting(self._people_counting_enabled)
        if cam_a is not None:
            slot_b.start(cam_a, url_a, self.face_engine, self.on_face_event, self.on_plate_event, self.on_person_event)
            slot_b.set_people_counting(self._people_counting_enabled)

        if self.selected_index == idx_a:
            self._select_index(idx_b)
        elif self.selected_index == idx_b:
            self._select_index(idx_a)

    def _on_slot_drag_swap(self, src_index, dst_index):
        self.swap_slots(src_index, dst_index)

    def _on_camera_drag_drop(self, cam_id, dst_index):
        if self.on_external_camera_drop is not None:
            self.on_external_camera_drop(cam_id, dst_index)

    # --------------------------------------------------------- selection --

    def _on_slot_clicked(self, slot):
        self._select_index(self.slots.index(slot))

    def _select_index(self, idx):
        if self.selected_index is not None and 0 <= self.selected_index < len(self.slots):
            self.slots[self.selected_index].set_selected(False)
        self.selected_index = idx
        self.slots[idx].set_selected(True)
        self.selection_changed.emit(idx)

    def _on_slot_close_requested(self, slot):
        idx = self.slots.index(slot)
        slot.stop()
        if idx == self.selected_index:
            self.selected_index = None
            self.selection_changed.emit(-1)
        if idx == self._maximized_index:
            self.toggle_maximize(idx)

    # ----------------------------------------------------------- maximize --

    def _on_slot_double_clicked(self, slot):
        self.toggle_maximize(self.slots.index(slot))

    def toggle_maximize(self, idx):
        """رفع درخواست: با دابل‌کلیک روی تصویر یک دوربین، آن خانه بزرگ می‌شود
        (کل فضای شبکه را می‌گیرد و بقیه‌ی خانه‌ها مخفی می‌شوند) و با دابل‌کلیک
        دوباره روی همان خانه، به اندازه و چیدمان قبلی (شبکه‌ای) برمی‌گردد.
        کادرها سیال‌اند، پس بزرگ‌نمایی فقط یعنی اشغال کل خانه‌های شبکه؛
        چیدمان (QGridLayout) خودش اندازه را پر می‌کند."""
        if self._maximized_index == idx:
            # بازگشت به چیدمان عادی شبکه‌ای.
            for i, s in enumerate(self.slots):
                self._layout.removeWidget(s)
                r, c = self._slot_positions[i]
                self._layout.addWidget(s, r, c)
                s.setVisible(True)
            self._maximized_index = None
        else:
            for i, s in enumerate(self.slots):
                self._layout.removeWidget(s)
                if i == idx:
                    self._layout.addWidget(s, 0, 0, self._rows, self._cols)
                    s.setVisible(True)
                else:
                    s.setVisible(False)
            self._maximized_index = idx
        self._select_index(idx)

    def get_selected_frame(self):
        if self.selected_index is not None:
            return self.slots[self.selected_index].latest_raw_frame
        return None

    def stop_all(self):
        for slot in self.slots:
            slot.stop()




class CameraPageWidget(QWidget):
    """دیوار دوربین‌ها — UI مرکزی صفحه‌ی اصلی به‌صورت ویجت قابل‌استفاده‌ی مجدد.

    شامل نوار ابزار مدیریتی (تعداد نمایش، شمارش افراد، اتاق مانیتورینگ،
    رسم/مدیریت محدوده‌ی هشدار + ردیف دوم رسم) و شبکه‌ی دوربین‌ها با
    خانه‌های کامل (CameraSlotWidget).

    در MainWindow و پنجره‌های شناور «نما»ی اتاق مانیتورینگ استفاده می‌شود —
    single source of truth: هر تغییری در دکمه‌ها/رفتار اینجا انجام می‌شود و
    هر دو جا همان را می‌بینند.
    """

    # فوروارد سیگنال‌های CameraGridWidget برای میزبان
    selection_changed = pyqtSignal(int)
    tripwire_changed = pyqtSignal()
    detector_status_changed = pyqtSignal()
    video_loss = pyqtSignal(object, str)

    def __init__(self, face_engine, camera_store,
                 on_face_event=None, on_external_camera_drop=None,
                 on_region_alert=None, on_fire_event=None, on_plate_event=None,
                 on_person_event=None, on_audio_url_found=None,
                 on_audio_creds_save=None,
                 on_open_monitor_room=None,
                 on_people_toggle_all=None,
                 show_sidebar_toggle=False, on_toggle_sidebar=None,
                 grid_size=4, parent=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.camera_store = camera_store
        self._on_people_toggle_all_cb = on_people_toggle_all
        self._floor_detect_thread = None
        self._build_rtsp_url = None

        wall_layout = QVBoxLayout(self)
        wall_layout.setContentsMargins(0, 0, 0, 0)
        grid_toolbar = QHBoxLayout()

        if show_sidebar_toggle:
            # دکمه‌ی نمایش/مخفی‌کردن پنل کناری سمت چپ (فقط پنجره‌ی اصلی).
            self.sidebar_toggle_btn = QPushButton("☰")
            self.sidebar_toggle_btn.setFixedWidth(32)
            self.sidebar_toggle_btn.setToolTip("نمایش / مخفی کردن پنل کناری")
            if on_toggle_sidebar is not None:
                self.sidebar_toggle_btn.clicked.connect(on_toggle_sidebar)
            grid_toolbar.addWidget(self.sidebar_toggle_btn)
        else:
            self.sidebar_toggle_btn = None

        grid_toolbar_label = QLabel("تعداد نمایش هم‌زمان دوربین‌ها:")
        grid_toolbar_label.setStyleSheet("font-size: 11px;")
        self.grid_size_combo = QComboBox()
        for n in (1, 4, 9, 16, 32, 64):
            self.grid_size_combo.addItem(str(n), n)
        try:
            self.grid_size_combo.setCurrentIndex((1, 4, 9, 16, 32, 64).index(grid_size))
        except ValueError:
            self.grid_size_combo.setCurrentIndex(1)  # پیش‌فرض: 4
        self.grid_size_combo.currentIndexChanged.connect(self._on_grid_size_changed)
        grid_toolbar.addWidget(grid_toolbar_label)
        grid_toolbar.addWidget(self.grid_size_combo)

        # دکمه‌ی واحد شمارش افراد برای همه‌ی دوربین‌های باز (رجوع کنید به
        # CameraSlotWidget.people_count_label برای نمایش تعداد هر خانه).
        self.people_toggle_btn = QPushButton("👥 شمارش افراد")
        self.people_toggle_btn.setCheckable(True)
        self.people_toggle_btn.setToolTip("روشن/خاموش کردن شمارش افراد Real Time برای تمام دوربین‌های باز")
        self.people_toggle_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#e67e22; color:#fff;}"
        )
        self.people_toggle_btn.toggled.connect(self._on_people_toggle_all)
        grid_toolbar.addWidget(self.people_toggle_btn)

        if on_open_monitor_room is not None:
            # (1.1.0) اتاق مانیتورینگ (multi-monitor).
            self.monitor_room_btn = QPushButton("🖥 اتاق مانیتورینگ")
            self.monitor_room_btn.setToolTip("تقسیم نمایش دوربین‌ها روی چند مانیتور (اتاق مانیتورینگ)")
            self.monitor_room_btn.setStyleSheet(
                "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            )
            self.monitor_room_btn.clicked.connect(on_open_monitor_room)
            grid_toolbar.addWidget(self.monitor_room_btn)
        else:
            self.monitor_room_btn = None

        # دکمه‌های رسم محدوده‌ی هشدار: «نوع رسم» فقط در حالت رسم، و
        # «تایید»/«لغو» فقط وقتی محدوده‌ای رسم شده یا در حال ویرایش است
        # دیده می‌شوند؛ بقیه‌ی وقت‌ها مخفی‌اند تا نوار ابزار خلوت بماند.
        # کاربر ابتدا یک دوربین را از شبکه انتخاب می‌کند (کلیک روی خانه‌اش)،
        # سپس رسم را می‌زند و با کلیک‌های متوالی روی تصویر همان دوربین یک
        # محدوده‌ی چندضلعی می‌سازد.
        self.draw_line_btn = QPushButton("🖊 رسم محدوده هشدار")
        self.draw_line_btn.setCheckable(True)
        self.draw_line_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید\n"
            "۳) روی تصویر همان دوربین، به‌ترتیب روی نقاط زمین/اتاق کلیک کنید "
            "(برنامه نقاط را به هم وصل می‌کند)\n"
            "۴) برای بستن محدوده: روی نقطه‌ی اول (دایره‌ی بزرگ‌تر) کلیک کنید، "
            "یا دابل‌کلیک کنید (حداقل ۳ نقطه لازم است)\n"
            "کلیک راست: لغو رسمِ در حال انجام"
        )
        self.draw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#9b59b6; color:#fff;}"
        )
        self.draw_line_btn.toggled.connect(self._on_draw_line_toggled)
        grid_toolbar.addWidget(self.draw_line_btn)

        # کانتینر دکمه‌های حالت رسم - فقط در حالت رسم نمایان می‌شود.
        # این ردیف، «سطر دوم» نوار ابزار است (زیر نوار اصلی) نه عضوی از همان
        # نوار افقی؛ تا ظاهر/مخفی شدنش ارتفاع نوار اصلی و اندازه‌ی کادرها را
        # تکان ندهد. _NoMinWidthRow هم جلوی بزرگ شدن خودکار پنجره را می‌گیرد.
        self.region_draw_row = _NoMinWidthRow()
        region_draw_layout = QHBoxLayout()
        region_draw_layout.setContentsMargins(0, 0, 0, 0)
        self.region_draw_row.setLayout(region_draw_layout)
        self.region_draw_row.setVisible(False)

        # «خودش سطح زمین را تشخیص بده و کلش را محدوده محسوب کنه»: کل کادر
        # تصویر زنده‌ی دوربین انتخاب‌شده را به‌عنوان یک محدوده‌ی تازه و در
        # انتظار تایید می‌گذارد (دقیقاً مثل رسم دستی، با همان دکمه‌های
        # تایید/لغو).
        self.auto_region_btn = QPushButton("🌐 تشخیص خودکار محدوده (کل تصویر)")
        self.auto_region_btn.setToolTip(
            "کل تصویر زنده‌ی دوربین انتخاب‌شده را به‌عنوان محدوده‌ی در انتظار تایید می‌گذارد."
        )
        self.auto_region_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.auto_region_btn.clicked.connect(self._on_auto_region_clicked)
        region_draw_layout.addWidget(self.auto_region_btn)

        self.ai_floor_btn = QPushButton("🧭 تشخیص هوشمند زمین (AI)")
        self.ai_floor_btn.setToolTip(
            "روی آخرین فریم زنده‌ی دوربین یک مدل واقعی اجرا می‌کند و سطح زمین "
            "را به‌عنوان محدوده‌ی در انتظار و قابل‌ویرایش قرار می‌دهد."
        )
        self.ai_floor_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.ai_floor_btn.clicked.connect(self._on_ai_floor_region_clicked)
        region_draw_layout.addWidget(self.ai_floor_btn)

        self.confirm_line_btn = QPushButton("✅ تایید و نام‌گذاری")
        self.confirm_line_btn.setEnabled(False)
        self.confirm_line_btn.setToolTip(
            "محدوده‌ی رسم‌شده را با یک نام (مثلاً نام اتاق) نهایی می‌کند؛ از این پس با ورود هرکسی به آن، کادر دوربین قرمز و آلارم پخش می‌شود."
        )
        self.confirm_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#27ae60; color:#fff;}"
        )
        self.confirm_line_btn.clicked.connect(self._on_confirm_line_clicked)
        region_draw_layout.addWidget(self.confirm_line_btn)

        self.redraw_line_btn = QPushButton("❌ لغو رسم")
        self.redraw_line_btn.setEnabled(False)
        self.redraw_line_btn.setToolTip("نقاط در انتظار نام‌گذاری را پاک می‌کند.")
        self.redraw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#c0392b; color:#fff;}"
        )
        self.redraw_line_btn.clicked.connect(self._on_redraw_line_clicked)
        region_draw_layout.addWidget(self.redraw_line_btn)

        self.manage_regions_btn = QPushButton("📋 مدیریت محدوده‌ها")
        self.manage_regions_btn.setEnabled(False)
        self.manage_regions_btn.setToolTip("مشاهده و حذف محدوده‌های هشدار تعریف‌شده برای دوربین انتخاب‌شده.")
        self.manage_regions_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#2980b9; color:#fff;}"
        )
        self.manage_regions_btn.clicked.connect(self._on_manage_regions_clicked)
        grid_toolbar.addWidget(self.manage_regions_btn)
        grid_toolbar.addStretch()

        # پیش‌فرضِ ذخیره‌ی مسیر صوتی/کریدنشنال صدا روی همان camera_store.
        if on_audio_url_found is None:
            on_audio_url_found = self._default_audio_url_found
        if on_audio_creds_save is None:
            on_audio_creds_save = self._default_audio_creds_save

        self.camera_grid = CameraGridWidget(
            face_engine, on_face_event,
            on_external_camera_drop=on_external_camera_drop,
            on_region_alert=on_region_alert,
            on_fire_event=on_fire_event,
            on_plate_event=on_plate_event,
            on_person_event=on_person_event,
            on_audio_url_found=on_audio_url_found,
            on_audio_creds_save=on_audio_creds_save,
            parent=self,
        )
        self.camera_grid.selection_changed.connect(
            lambda _idx: self._refresh_line_buttons())
        self.camera_grid.tripwire_changed.connect(self._refresh_line_buttons)
        # فوروارد به میزبان (سیگنال به سیگنال).
        self.camera_grid.detector_status_changed.connect(self.detector_status_changed)
        self.camera_grid.video_loss.connect(self.video_loss)

        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self.camera_grid)

        wall_layout.addLayout(grid_toolbar)
        # سطر دوم نوار ابزار: دکمه‌های رسم محدوده (مخفی پیش‌فرض).
        wall_layout.addWidget(self.region_draw_row)
        wall_layout.addWidget(grid_scroll, 1)

    # ------------------------------------------------- پیش‌فرض‌های صدا --
    def _default_audio_url_found(self, cam_id, url):
        try:
            if self.camera_store is not None:
                self.camera_store.update_camera(cam_id, audio_url=url)
        except Exception:
            pass

    def _default_audio_creds_save(self, cam_id, user, pwd):
        try:
            if self.camera_store is not None:
                self.camera_store.update_camera(cam_id, **{"user": user, "pass": pwd})
        except Exception:
            pass

    # ------------------------------------------------------------ API --
    def selected_slot(self):
        """خانه‌ی فعلاً انتخاب‌شده (یا None)."""
        return self._selected_slot()

    def refresh_line_buttons(self):
        """به‌روزرسانی دستی وضعیت دکمه‌های رسم (alias سازگار)."""
        self._refresh_line_buttons()

    def set_cameras(self, cams, build_rtsp_url):
        """اختصاص لیست دوربین‌ها به خانه‌ها (برای پنجره‌های نما).

        اندازه‌ی شبکه به کوچک‌ترین چیدمانِ جا‌دهنده تنظیم می‌شود و هر
        دوربین در یک خانه باز می‌شود.
        """
        cams = list(cams or [])
        self._build_rtsp_url = build_rtsp_url
        # کوچک‌ترین چیدمان جا‌دهنده از (1, 4, 9, 16, 32, 64)
        size = 1
        for n in (1, 4, 9, 16, 32, 64):
            if n >= len(cams):
                size = n
                break
        else:
            size = 64
        try:
            self.grid_size_combo.blockSignals(True)
            self.grid_size_combo.setCurrentIndex((1, 4, 9, 16, 32, 64).index(size))
        finally:
            try:
                self.grid_size_combo.blockSignals(False)
            except Exception:
                pass
        self.camera_grid.set_grid_size(size)
        for i, cam in enumerate(cams):
            try:
                rtsp_url = build_rtsp_url(cam)
            except Exception:
                continue
            try:
                self.camera_grid.assign_camera_to_slot(cam, rtsp_url, i)
            except Exception:
                continue

    def _on_grid_size_changed(self, _index):
        count = self.grid_size_combo.currentData()
        self.camera_grid.set_grid_size(count)

    def _on_people_toggle_all(self, checked):
        """رفع درخواست: با این یک دکمه (بالای کل شبکه‌ی دوربین‌ها)، شمارش
        افراد Real Time برای همه‌ی دوربین‌های باز هم‌زمان روشن/خاموش می‌شود؛
        وضعیت روی دوربین‌هایی که بعداً باز شوند هم اعمال می‌ماند (رجوع کنید
        به CameraGridWidget.set_people_counting_all)."""
        if checked:
            try:
                from admin_quota import require_feature
                if not require_feature("people_counting", self):
                    self.people_toggle_btn.blockSignals(True)
                    self.people_toggle_btn.setChecked(False)
                    self.people_toggle_btn.blockSignals(False)
                    return
            except Exception:
                pass
        self.camera_grid.set_people_counting_all(checked)
        if self._on_people_toggle_all_cb is not None:
            try:
                self._on_people_toggle_all_cb(bool(checked))
            except Exception:
                pass

    def _selected_slot(self):
        idx = self.camera_grid.selected_index
        if idx is None or not (0 <= idx < len(self.camera_grid.slots)):
            return None
        return self.camera_grid.slots[idx]

    def _on_draw_line_toggled(self, checked):
        # گیت لایسنس: رسم محدوده‌ی هشدار فقط وقتی مجاز است که قابلیت
        # «هشدار ورود به محدوده» در لایسنس فعال باشد.
        if checked:
            try:
                from admin_quota import require_feature
                if not require_feature("zone_alerts", self):
                    self.draw_line_btn.blockSignals(True)
                    self.draw_line_btn.setChecked(False)
                    self.draw_line_btn.blockSignals(False)
                    return
            except Exception:
                pass
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            if checked:
                self.draw_line_btn.blockSignals(True)
                self.draw_line_btn.setChecked(False)
                self.draw_line_btn.blockSignals(False)
                QMessageBox.information(
                    self, "رسم محدوده هشدار",
                    "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)، سپس دوباره این دکمه را بزنید."
                )
            return
        slot.set_draw_mode(checked)
        self._refresh_line_buttons()

    def _on_auto_region_clicked(self):
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            QMessageBox.information(
                self, "تشخیص خودکار محدوده",
                "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)."
            )
            return
        if slot.is_draw_mode():
            self.draw_line_btn.setChecked(False)
        if not slot.start_auto_full_frame_region():
            QMessageBox.information(
                self, "تشخیص خودکار محدوده",
                "ابتدا محدوده‌ی در انتظار/در حال ویرایشِ فعلی را با «✅ تایید» یا «❌ لغو» تمام کنید."
            )
            return
        self._refresh_line_buttons()

    def _on_ai_floor_region_clicked(self):
        """رفع درخواست «حالت جدید که خودش سطح زمین رو تشخیص بده»: برخلاف
        _on_auto_region_clicked (که فوری و همزمان کل کادر را می‌گذارد)،
        اینجا باید روی آخرین فریمِ زنده‌ی دوربین یک مدل واقعی اجرا شود -
        این کار در یک ترد جدا (FloorDetectThread) انجام می‌شود تا در طول
        چند ثانیه‌ی پردازش (به‌خصوص بار اول، وقتی مدل هنوز دانلود/بارگذاری
        نشده)، بقیه‌ی برنامه (از جمله پخش زنده‌ی سایر دوربین‌های باز) فریز
        نشود."""
        if self._floor_detect_thread is not None:
            return  # یک تشخیص از قبل در حال اجراست؛ از کلیک تکراری صرف‌نظر می‌شود
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)."
            )
            return
        if slot.has_pending_region() or slot.is_editing_region():
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "ابتدا محدوده‌ی در انتظار/در حال ویرایشِ فعلی را با «✅ تایید» یا «❌ لغو» تمام کنید."
            )
            return
        frame = slot.latest_raw_frame
        if frame is None:
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "هنوز هیچ فریمی از این دوربین دریافت نشده - چند لحظه صبر کنید تا تصویر زنده بیاید و دوباره امتحان کنید."
            )
            return
        if slot.is_draw_mode():
            self.draw_line_btn.setChecked(False)
        self.ai_floor_btn.setEnabled(False)
        self.ai_floor_btn.setText("⏳ در حال تحلیل تصویر...")
        thread = _get_floor_detect_thread()(frame.copy(), parent=self)
        thread.finished_signal.connect(
            lambda points, message, _slot=slot: self._on_ai_floor_detect_finished(_slot, points, message)
        )
        thread.finished.connect(self._on_ai_floor_detect_thread_done)
        self._floor_detect_thread = thread
        thread.start()

    def _on_ai_floor_detect_thread_done(self):
        self._floor_detect_thread = None
        self.ai_floor_btn.setText("🧭 تشخیص هوشمند زمین (AI)")
        self._refresh_line_buttons()

    def _on_ai_floor_detect_finished(self, slot, points, message):
        """رفع درخواست: نتیجه‌ی FloorDetectThread را روی تصویر همان دوربین
        (slot) به‌عنوان یک محدوده‌ی «در انتظار» و کاملاً قابل‌ویرایش قرار
        می‌دهد - دقیقاً مثل خروجی رسم دستی یا تشخیص خودکار کل تصویر. اگر
        مدل چیزی پیدا نکرد یا اصلاً در دسترس نبود، به‌جای گذاشتن یک محدوده‌ی
        نادرست/حدسی، فقط دلیل را روشن به کاربر می‌گوید و پیشنهاد می‌دهد از
        «تشخیص خودکار محدوده (کل تصویر)» یا رسم دستی استفاده کند."""
        if points is None:
            QMessageBox.warning(self, "تشخیص هوشمند زمین ناموفق بود", message)
            return
        # ممکن است در طول چند ثانیه‌ی پردازش، کاربر دوربین دیگری انتخاب
        # کرده یا محدوده‌ی دیگری روی همین دوربین شروع کرده باشد؛ در این
        # حالت نتیجه‌ی دیرکرد‌شده را بی‌سروصدا نادیده می‌گیریم تا چیزی
        # غیرمنتظره روی تصویر ظاهر نشود.
        if slot.has_pending_region() or slot.is_editing_region():
            return
        if not slot.start_ai_floor_region(points):
            return
        if self._selected_slot() is slot:
            self._refresh_line_buttons()

    def _on_confirm_line_clicked(self):
        slot = self._selected_slot()
        if slot is None or not slot.has_pending_region():
            return
        # رفع درخواست «قابلیت ادیت‌کردن»: اگر در حال ویرایشِ شکل/اندازه‌ی یک
        # محدوده‌ی از قبل تایید‌شده هستیم (رجوع کنید به
        # CameraSlotWidget.start_edit_region)، همین دکمه به‌جای پرسیدن یک
        # نام تازه، فقط نقاط جدید را روی همان محدوده ذخیره می‌کند - نامش
        # دست‌نخورده می‌ماند.
        if slot.is_editing_region():
            region = slot.save_region_edit()
            if region is not None and slot.cam is not None:
                self.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
            self._refresh_line_buttons()
            return
        name, ok = QInputDialog.getText(
            self, "نام‌گذاری محدوده",
            "نام این محدوده را وارد کنید (مثلاً اسم اتاق) - اختیاری:"
        )
        if not ok:
            return
        region = slot.confirm_region(name)
        if region is not None and slot.cam is not None:
            self.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
        # رفع درخواست: قبلاً اگر تشخیص شخص (YOLOv8) بارگذاری نشده بود، کاربر
        # محدوده را تایید می‌کرد و فکر می‌کرد همه‌چیز فعال شده، در حالی که
        # هشدار هرگز صادر نمی‌شد و هیچ توضیحی هم نمی‌دید. حالا هشدار با فالبک
        # «تشخیص حرکت» کار می‌کند؛ فقط به کاربر اطلاع می‌دهیم که حالت جایگزین
        # فعال است (نه تشخیص دقیق شخص).
        if region is not None and slot._detector_available is False:
            QMessageBox.information(
                self, "محدوده ذخیره شد (حالت جایگزین)",
                "این محدوده ذخیره شد و هشدار ورود به آن فعال است، اما مدل "
                "تشخیص دقیق شخص (YOLOv8) در این اجرا بارگذاری نشد؛ فعلاً با "
                "«تشخیص حرکت» کار می‌کند (حساس‌تر به حرکت‌های غیرانسانی).\n\n"
                "کتابخانه‌ی لازم (ultralytics + وزن مدل yolov8n.pt) داخل "
                "نسخه‌ی رسمی برنامه بسته‌بندی شده و نیازی به نصب هیچ‌چیز "
                "روی ویندوز نیست. اگر این پیام را در نسخه‌ی رسمی "
                "(ساخته‌شده با GitHub Actions) می‌بینید، آخرین نسخه‌ی "
                "ساخته‌شده را دانلود و جایگزین کنید."
            )
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(False)
        self.draw_line_btn.blockSignals(False)
        self._refresh_line_buttons()

    def _on_redraw_line_clicked(self):
        slot = self._selected_slot()
        if slot is None:
            return
        # رفع درخواست «قابلیت ادیت‌کردن»: در حالت ویرایشِ یک محدوده‌ی
        # قبلاً تایید‌شده، این دکمه فقط ویرایش را لغو می‌کند (خودِ محدوده با
        # شکل/اندازه‌ی قبلی دست‌نخورده می‌ماند) - نه اینکه محدوده حذف شود.
        # (2.0.38-beta) اگر چندضلعی هنوز بسته نشده و فقط نقطه‌هایی «در حال
        # رسم»‌اند، همان نقاطِ نیمه‌کاره لغو می‌شوند.
        if slot.is_editing_region():
            slot.cancel_region_edit()
        elif slot.has_pending_region():
            slot.cancel_pending_region()
        else:
            slot.cancel_draw_points()
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(False)
        self.draw_line_btn.blockSignals(False)
        self._refresh_line_buttons()

    def _on_manage_regions_clicked(self):
        slot = self._selected_slot()
        if slot is None:
            return

        def _on_changed():
            if slot.cam is not None:
                self.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
            self._refresh_line_buttons()

        dialog = RegionManagerDialog(slot, _on_changed, self)
        dialog.exec()
    def _refresh_line_buttons(self):
        """دکمه‌های «تایید و نام‌گذاری»/«لغو رسم»/«مدیریت محدوده‌ها» و
        وضعیت تیک‌خورده‌ی «رسم محدوده هشدار» را بر اساس خانه‌ی فعلاً
        انتخاب‌شده به‌روز می‌کند - چون هر خانه محدوده‌های مستقل خودش را
        دارد."""
        slot = self._selected_slot()
        has_pending = slot.has_pending_region() if slot is not None else False
        has_confirmed = bool(slot.regions) if slot is not None else False
        is_editing = slot.is_editing_region() if slot is not None else False
        # (فعال/غیرفعال دکمه‌های تایید/لغو در انتهای همین تابع تنظیم می‌شود)
        self.manage_regions_btn.setEnabled(bool(has_confirmed))
        # (2.0.12-beta به دستور کاربر): دکمه‌های مربوط به رسم فقط وقتی دیده
        # می‌شوند که لازم باشند (رجوع کنید به انتهای همین تابع)؛ بقیه‌ی
        # وقت‌ها مخفی‌اند تا نوار ابزار خلوت بماند. ردیف رسم سطر دوم نوار
        # ابزار است و _NoMinWidthRow جلوی اثرش روی اندازه‌ی پنجره/کادرها را
        # می‌گیرد (باگ 2.0.14-beta).
        # رفع درخواست «قابلیت ادیت‌کردن»: وقتی یک محدوده‌ی «در انتظار» (چه
        # تازه رسم‌شده، چه محدوده‌ی خودکارِ کل تصویر، چه در حال ویرایش شکلِ
        # یک محدوده‌ی قبلی) روی تصویر هست، رسم/تشخیص خودکارِ تازه غیرفعال
        # می‌شود تا دو جریان هم‌زمان با هم قاطی نشوند؛ متن دکمه‌های
        # تایید/لغو هم بسته به این‌که «ویرایش یک محدوده‌ی موجود» است یا
        # «محدوده‌ی تازه»، عوض می‌شود.
        self.draw_line_btn.setEnabled(not has_pending)
        self.auto_region_btn.setEnabled(not has_pending)
        # رفع درخواست: دکمه‌ی «تشخیص هوشمند زمین» هم مثل «تشخیص خودکار
        # (کل تصویر)» وقتی محدوده‌ای در انتظار/در حال ویرایش است غیرفعال
        # می‌شود؛ علاوه بر آن، اگر همین الان یک تشخیص در حال اجرا باشد
        # (self._floor_detect_thread) هم غیرفعال می‌ماند تا این تابع (که از
        # چند رویداد دیگر - مثل تغییر انتخاب دوربین - هم صدا زده می‌شود)
        # وسط پردازش دوباره فعالش نکند.
        self.ai_floor_btn.setEnabled(not has_pending and self._floor_detect_thread is None)
        if is_editing:
            self.confirm_line_btn.setText("💾 ذخیره ویرایش شکل")
            self.redraw_line_btn.setText("↩ لغو ویرایش")
        else:
            self.confirm_line_btn.setText("✅ تایید و نام‌گذاری")
            self.redraw_line_btn.setText("❌ لغو رسم")
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(bool(slot.is_draw_mode()) if slot is not None else False)
        self.draw_line_btn.blockSignals(False)
        # (2.0.14-beta به دستور کاربر): «تایید» و «لغو» فقط وقتی دیده می‌شوند
        # که محدوده‌ای «رسم شده» باشد (در انتظار تایید/نام‌گذاری) یا در حال
        # ویرایش شکل یک محدوده‌ی قبلی باشیم؛ در حالتِ خالصِ رسم (هنوز چیزی
        # رسم نشده) فقط دکمه‌های «نوع رسم» (خودکار/هوشمند) دیده می‌شوند.
        # خود ردیف هم فقط وقتی نمایان است که حداقل یکی از این دو گروه نمایان
        # باشد. ردیف، سطر دوم نوار ابزار است (_NoMinWidthRow) و ظاهر/مخفی
        # شدنش اندازه‌ی پنجره و کادرها را عوض نمی‌کند.
        in_draw_mode = bool(slot.is_draw_mode()) if slot is not None else False
        # (2.0.38-beta) «لغو رسم» علاوه بر محدوده‌ی در انتظار/در حال ویرایش،
        # وقتی هم که نقطه‌هایی «در حال رسم»‌اند (هنوز چندضلعی بسته نشده)
        # دیده و فعال می‌شود — تا کاربر هیچ‌وقت با رسمِ نیمه‌کاره‌ی
        # بی‌دکمه مواجه نشود. «تایید و نام‌گذاری» همچنان فقط برای محدوده‌ی
        # بسته‌شده (در انتظار) یا در حال ویرایش است.
        has_drawing = bool(slot.has_draw_points()) if slot is not None else False
        show_types = bool(in_draw_mode and not has_pending and not is_editing)
        show_confirm = bool(has_pending or is_editing)
        show_cancel = bool(has_pending or is_editing or (in_draw_mode and has_drawing))
        self.auto_region_btn.setVisible(show_types)
        self.ai_floor_btn.setVisible(show_types)
        self.confirm_line_btn.setVisible(show_confirm)
        self.confirm_line_btn.setEnabled(bool(has_pending or is_editing))
        self.redraw_line_btn.setVisible(show_cancel)
        self.redraw_line_btn.setEnabled(bool(has_pending or is_editing or has_drawing))
        self.region_draw_row.setVisible(bool(show_types or show_confirm or show_cancel))
