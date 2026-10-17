import os
import sys
import time
import uuid
import threading

# مرحله‌ی دوم آپدیت (CCTV_CMS_upd.exe --apply-update ...): موتور خالص پایتون
# بدون GUI و بدون Qt؛ باید قبل از هر import سنگین (cv2، PyQt و…) اجرا شود.
if "--apply-update" in sys.argv:
    from update_apply import main as _update_apply_main
    sys.exit(_update_apply_main(sys.argv))

import cv2

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QGroupBox, QMenu, QTreeWidget, QTreeWidgetItem, QInputDialog, QDialog,
    QGridLayout, QComboBox, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QSlider
)
from PyQt6.QtGui import QImage, QPixmap, QAction, QIcon, QDrag, QFontMetrics, QPainter, QPen, QColor, QPolygonF
from PyQt6.QtCore import Qt, QSize, QMimeData, QPointF, QRectF, QTimer, QEvent, pyqtSignal

from face_engine import FaceEngine
from scanner import NetworkScanThread, parse_ip_range
from camera_store import CameraStore
from camera_stream import CameraStreamThread, region_to_polygon, is_low_spec_mode
from video_display_label import VideoDisplayLabel
# (2.0.61-beta) منطق خالص تشخیص قطع/وصل مجدد تصویر (بدون وابستگی به Qt)
from video_loss import transition as _video_loss_transition, cooldown_ok as _video_loss_cooldown_ok
# نکته: floor_detector عمداً در بالای فایل import نمی‌شود؛ transformers و
# SegFormer چند صد مگابایت رم می‌گیرند. فقط وقتی کاربر واقعاً دکمه‌ی «تشخیص
# هوشمند زمین» را بزند، در همان لحظه بارگذاری می‌شود - رجوع کنید به
# _get_floor_detect_thread پایین‌تر.
_FloorDetectThread = None


def _get_floor_detect_thread():
    """دسترسی تنبل به FloorDetectThread (بارگذاری سنگین transformers فقط
    در لحظه‌ی استفاده، نه در زمان بالا آمدن برنامه)."""
    global _FloorDetectThread
    if _FloorDetectThread is None:
        from floor_detector import FloorDetectThread as _FT
        _FloorDetectThread = _FT
    return _FloorDetectThread
from add_camera_dialog import AddCameraDialog
from add_nvr_dialog import AddNVRDialog
from nvr_scanner import DirectCameraProbeThread
from nvr_channel_select_dialog import NVRChannelSelectDialog
try:
    from nvr_webview_dialog import NVRWebViewDialog, _WEBENGINE_AVAILABLE
except ImportError:
    NVRWebViewDialog, _WEBENGINE_AVAILABLE = None, False
from face_library_dialog import FaceLibraryPage, DetectedFacesDialog
from report_store import report_store
from reports_dialog import ReportsPage
# رفع درخواست «سیستم پلاک‌خوان»: صفحه‌ی جدا (مثل Face Library) با دو تب
# «تعریف پلاک‌ها» و «گزارش عبور» + دیتابیس SQLite پلاک‌ها/رویدادها.
from plate_store import plate_store
from plate_library_dialog import PlateLibraryPage
# رفع درخواست «ردیابی اشخاص بین دوربین‌ها»: صفحه‌ی جدا (مثل پلاک‌خوان) با دو
# تب «اشخاص ردیابی‌شده» و «گزارش مسیر حرکت» + دیتابیس SQLite اشخاص/حضورها +
# تطبیق ظاهری بدون چهره (person_reid).
from person_store import person_store
from person_track_dialog import PersonTrackPage
from person_reid import GlobalPersonMatcher
# رفع درخواست «نقشه‌ی تعاملی ساختمان»: صفحه‌ی جدا با نقشه‌ی هر طبقه (DXF
# اتوکد/تصویر)، جای‌گذاری دوربین/NVR/رک/سوئیچ، پخش زنده با کلیک روی دوربین
# نقشه و نمایش مسیر تردد شخص روی نقشه‌ها.
try:
    from building_map_dialog import BuildingMapPage
    _MAP_PAGE_AVAILABLE = True
except Exception:
    BuildingMapPage = None
    _MAP_PAGE_AVAILABLE = False
from nvr_storage_dialog import NVRStorageDialog
from device_detect import DeviceDetectThread
from fire_alarm_store import FireAlarmStore
from fire_alarm_io import FireAlarmMonitorThread
from fire_alarm_dialog import FireAlarmPage
try:
    # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
    # این سه فایل در پکیج ias-cms-fire-building کنار main.py قرار می‌گیرند.
    # نبودشان باعث کرش نمی‌شود؛ فقط این قابلیت غیرفعال می‌ماند.
    from building_fire_output import BuildingFireOutput
    from alarm_sound import AlarmSoundPlayer
    from building_fire_settings_dialog import BuildingFireSettingsDialog
    _BUILDING_FIRE_AVAILABLE = True
except ImportError:
    BuildingFireOutput = None
    AlarmSoundPlayer = None
    BuildingFireSettingsDialog = None
    _BUILDING_FIRE_AVAILABLE = False
from settings_page import SettingsPage
from theme import (
    apply_theme, LOGO_SHIELD, APP_NAME_FA, APP_NAME_EN, LOGO_BLUE, TEXT_MUTED,
)

# بهینه‌سازی برای سیستم‌های ضعیف (رم کم / بدون کارت گرافیک):
# OpenCV به‌صورت پیش‌فرض برای عملیات داخلی (resize، cvtColor و ...) روی *تمام*
# هسته‌های CPU ترد باز می‌کند. وقتی چند دوربین هم‌زمان پخش می‌شوند (هر کدام با
# ترد پخش + ترد تشخیص چهره‌ی خودشان)، این تردهای داخلی OpenCV با تردهای خود
# برنامه بر سر CPU رقابت می‌کنند و روی سیستم‌های 2 تا 4 هسته‌ای (بدون GPU) کل
# رابط کاربری کند/تکه‌تکه می‌شود. محدود کردن آن به نصف هسته‌ها این رقابت را
# کم می‌کند بدون افت محسوس در سرعت پردازش هر فریم.
cv2.setNumThreads(max(1, (os.cpu_count() or 4) // 2))

# روی سیستم‌های کم‌هسته، تشخیص چهره روی هر ۵ فریم هنوز نسبتاً سنگین است؛ فاصله
# را کمی بیشتر می‌کنیم تا CPU بیشتری برای خود پخش زنده (decode ویدیو) بماند.
# حالت سبک (۴ گیگ رم / بدون GPU): خودکار از روی رم سیستم تشخیص داده می‌شود
# (کمتر از ۶ گیگ = سبک)؛ با IAS_LITE_MODE=1 اجباری و با IAS_LITE_MODE=0
# غیرفعال می‌شود. در حالت سبک فاصله‌ی تشخیص بیشتر می‌شود تا CPU و رم برای
# پخش زنده بماند.
_LOW_SPEC_MODE = is_low_spec_mode()
if _LOW_SPEC_MODE:
    print("حالت سبک فعال شد (رم کم / بدون GPU): تشخیص‌ها با فاصله‌ی بیشتر و حداکثر یک تشخیص هم‌زمان.")
_PROCESS_EVERY_N = 12 if _LOW_SPEC_MODE else (5 if (os.cpu_count() or 4) >= 6 else 8)

# نگاشت تعداد نمایش هم‌زمان دوربین‌ها به چیدمان (ردیف, ستون) شبکه‌ی نمایش.
# اعداد دقیقاً همان مقادیر درخواستی هستند: 1، 4، 9، 16، 32، 64.
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


def _play_violation_beep():
    """تک‌بوق تخلف طبقاتی — کاملاً مستقل از صدای حریق و ورود به محدوده."""
    try:
        from alarm_sound import sound_enabled
        if not sound_enabled("violation"):
            return
    except Exception:
        return
    def _run():
        try:
            import winsound
            winsound.Beep(880, 300)
            time.sleep(0.12)
            winsound.Beep(660, 300)
        except Exception:
            try:
                QApplication.beep()
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()




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


class CameraPreviewDialog(QDialog):
    """رفع درخواست: با «دابل‌کلیک» روی دوربین در صفحه‌ی «نقشه ساختمان»،
    به‌جای رفتن به صفحه‌ی اصلی، همین پنجره‌ی شناور باز می‌شود و پخش زنده‌ی
    همان دوربین را نشان می‌دهد (بدون ترک نقشه). کلیک ساده فقط دوربین را
    انتخاب می‌کند (پنل تنظیم زاویه/پهنای دید). بستن پنجره، استریم را متوقف
    می‌کند. دکمه‌ی «نمایش در صفحه اصلی» همان رفتار قبلی (باز کردن در
    شبکه‌ی نمایش) را انجام می‌دهد."""

    def __init__(self, cam: dict, rtsp_url: str, face_engine, parent=None):
        super().__init__(parent)
        self.cam = cam
        self._last_display_ts = 0.0
        cam_name = cam.get("name") or cam.get("ip", "")
        self.setWindowTitle(f"🎥 پخش زنده — {cam_name}")
        self.resize(680, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        self.video_label = QLabel("در حال اتصال...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(480, 320)
        self.video_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_label.setStyleSheet(
            "background:#111; color:#aaa; border-radius:6px;")
        layout.addWidget(self.video_label, 1)

        bottom = QHBoxLayout()
        self.status_label = QLabel("در حال اتصال...")
        self.status_label.setStyleSheet("color:#9b978c;")
        bottom.addWidget(self.status_label, 1)
        self.grid_btn = QPushButton("📌 نمایش در صفحه اصلی")
        self.grid_btn.clicked.connect(self._open_in_grid)
        bottom.addWidget(self.grid_btn)
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self.stream_thread = CameraStreamThread(
            rtsp_url, face_engine, process_every_n=_PROCESS_EVERY_N)
        self.stream_thread.frame_ready.connect(self._on_frame_ready)
        self.stream_thread.error_signal.connect(self._on_error)
        self.stream_thread.connected_signal.connect(self._on_connected)
        self.stream_thread.start()

    def _open_in_grid(self):
        parent = self.parent()
        open_fn = getattr(parent, "open_live_view", None)
        self.close()
        if callable(open_fn):
            open_fn(self.cam)

    def _on_frame_ready(self, display_frame, raw_frame):
        # مثل CameraSlotWidget.on_frame_ready: حداکثر ~۱۵ فریم‌برثانیه و
        # کوچک‌سازی اولیه با OpenCV برای سبکی روی CPU.
        now = time.monotonic()
        if now - self._last_display_ts < 1.0 / 15.0:
            return
        self._last_display_ts = now
        try:
            lw, lh = self.video_label.width(), self.video_label.height()
        except Exception:
            lw, lh = 0, 0
        show_frame = display_frame
        if lw > 0 and lh > 0:
            h, w = show_frame.shape[:2]
            _scale = min(lw / w, lh / h)
            if _scale < 1.0:
                _nw, _nh = max(1, int(w * _scale)), max(1, int(h * _scale))
                show_frame = cv2.resize(show_frame, (_nw, _nh),
                                        interpolation=cv2.INTER_LINEAR)
        pixmap = _bgr_to_pixmap(show_frame)
        if pixmap is not None:
            self.video_label.setPixmap(pixmap)

    def _on_connected(self):
        self.status_label.setText("متصل شد ✓")

    def _on_error(self, msg):
        self.status_label.setText(f"خطا: {msg}")
        if "در انتظار تصویر" in self.video_label.text() or \
                "در حال اتصال" in self.video_label.text():
            self.video_label.setText(f"عدم اتصال\n{msg}")

    def closeEvent(self, event):
        try:
            if self.stream_thread is not None:
                self.stream_thread.stop()
        except Exception:
            pass
        super().closeEvent(event)


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


class CameraTreeWidget(QTreeWidget):
    """درخت «دوربین‌ها و NVRهای من» با پشتیبانی از Drag: رفع درخواست - کاربر
    می‌تواند یک دوربین را از این لیست گرفته و روی خانه‌ی موردنظر در شبکه‌ی
    نمایش رها (drop) کند تا همان‌جا باز شود. فقط آیتم‌های «دوربین» قابل درگ
    هستند (نه گره‌های NVR که خودشان قابل پخش مستقیم نیستند)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "camera":
            return
        mime = QMimeData()
        mime.setData("application/x-ias-camera-id", str(data["id"]).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class MainWindow(QMainWindow):
    # (2.0.72-beta) نتیجه‌ی بررسی آپدیت از ترد پس‌زمینه به ترد UI
    update_check_done = pyqtSignal(object)

    def __init__(self, current_user=None):
        # current_user=None یعنی حالت توسعه/تست: دسترسی کامل، بدون لاگین.
        self.current_user = current_user
        super().__init__()
        from updater import get_app_version
        self.app_version = get_app_version()
        self.setWindowTitle(f"{APP_NAME_FA} v{self.app_version}")
        # بررسی لایسنس در شروع برنامه (license.py) — بدون لایسنس معتبر،
        # حالت محدود فعال می‌شود (نمایش تصویر همه‌ی دوربین‌ها، فقط شمارش
        # افراد؛ بدون پلاک‌خوان/حریق/ردیابی اشخاص/چهره‌خوان/هشدار محدوده).
        try:
            from license import load_license
            self.license_state = load_license()
        except Exception:
            from license import LicenseState
            self.license_state = LicenseState(error="خطا در بررسی لایسنس.")
        self._apply_license_title()
        # اعلان‌های شروع برنامه: هشدار لایسنس (در صورت نیاز) و بعد پنجره‌ی
        # «تغییرات آپدیت» اگر تازه به‌روزرسانی اعمال شده باشد.
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(900, self._show_startup_notices)
        except Exception:
            pass
        # آیکون پنجره: سپر لوگوی شرکت (هم در اجرای عادی، هم داخل exe).
        _logo_icon = QIcon(LOGO_SHIELD)
        if not _logo_icon.isNull():
            self.setWindowIcon(_logo_icon)
        self.setGeometry(100, 100, 1500, 780)

        self.face_engine = FaceEngine()
        self.camera_store = CameraStore()
        # (2.0.15-beta) موتور قوانین جهت تردد پلاک‌خوان: نقش ورود/خروج هر
        # دوربین + جهت مجاز هر مسیر؛ هوک در on_plate_event.
        try:
            from plate_direction import PlateDirectionEngine
            self.plate_direction = PlateDirectionEngine(
                plate_store, self.camera_store)
            self.plate_direction.set_violation_beep(_play_violation_beep)
        except Exception:
            self.plate_direction = None
        # رفع درخواست «ردیابی اشخاص بین دوربین‌ها»: تطبیق‌دهنده‌ی سراسری
        # ظاهری (یک نمونه برای کل برنامه، در ترد اصلی) + نگاشت ردهای فعال
        # هر دوربین: (cam_id, local_id) -> {person_id, sighting_id, slot}
        # تا «حضور»ها با ورود/خروج دقیق ثبت و کد شخص روی تصویر به‌روز شود.
        self._person_matcher = GlobalPersonMatcher(
            threshold=person_store.match_threshold,
            window_s=person_store.link_window_min * 60.0)
        self._active_person_tracks = {}
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: لیست پنل‌ها/سنسورهای
        # فیزیکی اعلام حریق کاربر + تردهای پس‌زمینه‌ی مانیتور هرکدام
        # (panel_id -> FireAlarmMonitorThread) - رجوع کنید به
        # fire_alarm_io.py/fire_alarm_store.py.
        self.fire_alarm_store = FireAlarmStore()
        self._fire_alarm_threads = {}
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # وقتی آتش/دود تشخیص داده شود، آژیر ممتد پخش و سیگنال به پنل
        # ساختمان ارسال می‌شود (تنظیمات در building_fire_config.json).
        if _BUILDING_FIRE_AVAILABLE:
            self.building_fire = BuildingFireOutput(logger=self._on_building_fire_log)
            self.alarm_player = AlarmSoundPlayer()
        else:
            self.building_fire = None
            self.alarm_player = None
        # مهاجرت یک‌باره‌ی تنظیم قدیمی «floor_violation_sound» به کلید جدید
        # و مستقل «violation_enabled» در alarm_sound_config.json
        try:
            from alarm_sound import migrate_legacy_violation
            migrate_legacy_violation(person_store.get_setting)
        except Exception:
            pass
        self.network_scan_thread = None
        self.detect_thread = None
        self._scan_ports_by_ip = {}  # ip -> [ports...] از آخرین اسکن شبکه
        self._detect_queue = []  # صف IPهایی که با انتخاب چندتایی باید پشت‌سرهم تشخیص داده شوند

        self.init_ui()
        self.reload_camera_list()
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: پنل‌های ذخیره‌شده از
        # اجراهای قبلی، مانیتور پس‌زمینه‌ی هرکدام همین لحظه شروع می‌شود -
        # دقیقاً مثل بارگذاری خودکار دوربین‌ها/NVRها. نمایش خودِ لیست حالا در
        # صفحه‌ی جداگانه‌ی FireAlarmPage انجام می‌شود (هنگام باز شدن آن از
        # هدر، متد refresh صفحه صدا زده می‌شود).
        for panel in self.fire_alarm_store.panels:
            self._start_fire_alarm_monitor(panel)

    def _apply_license_title(self):
        """به‌روزرسانی عنوان پنجره بر اساس وضعیت فعلی لایسنس."""
        try:
            if self.license_state.valid:
                _lic_cust = self.license_state.customer or ""
                self.setWindowTitle(
                    f"{APP_NAME_FA} v{self.app_version} — 🔑 {_lic_cust}")
            else:
                self.setWindowTitle(
                    f"{APP_NAME_FA} v{self.app_version} — ⛔ حالت محدود")
        except Exception:
            pass

    def refresh_license_state(self):
        """بارخوانی لایسنس از فایل (مثلاً بعد از آپلود) و به‌روزرسانی عنوان.

        گیت‌های قابلیت‌ها فایل را هر بار تازه می‌خوانند، پس با این فراخوانی
        قابلیت‌های لایسنس جدید بلافاصله و بدون ری‌استارت فعال می‌شوند.
        """
        try:
            from license import load_license
            self.license_state = load_license()
        except Exception:
            pass
        self._apply_license_title()

    def _show_startup_notices(self):
        """اعلان‌های شروع: هشدار لایسنس (در صورت نیاز) + تغییرات آپدیت."""
        try:
            if not self.license_state.valid:
                from PyQt6.QtWidgets import QMessageBox
                _lic_err = self.license_state.error or "فایل لایسنس یافت نشد."
                QMessageBox.warning(
                    self, "لایسنس معتبر نیست",
                    f"{_lic_err}\n\nبرنامه در حالت محدود اجرا می‌شود "
                    "(نمایش تصویر همه‌ی دوربین‌ها؛ فقط شمارش افراد فعال است).\n"
                    "از صفحه‌ی تنظیمات، فایل لایسنس (.lic) را بارگذاری کنید.")
            else:
                # اگر فایل لایسنس بیرون از برنامه عوض شده و سقفی کم شده،
                # تیک قابلیت از روی دوربین‌های اضافی برداشته می‌شود
                try:
                    from admin_quota import enforce_quotas
                    from PyQt6.QtWidgets import QMessageBox
                    cam_store = getattr(self, "camera_store", None)
                    lines = enforce_quotas(cam_store) if cam_store else []
                    if lines:
                        QMessageBox.warning(
                            self, "سقف لایسنس تغییر کرد",
                            "سقف برخی قابلیت‌ها در لایسنس فعلی کمتر از مصرف "
                            "فعلی است؛ به‌صورت خودکار اصلاح شد:\n\n"
                            + "\n".join(lines)
                            + "\n\nبرای فعال‌سازی دوباره، تیک قابلیت را در "
                            "تنظیمات دوربین بزنید.")
                except Exception:
                    pass
        except Exception:
            pass
        self._maybe_show_update_notice()

    def _maybe_show_update_notice(self):
        """اگر موتور آپدیت تازه نسخه‌ی جدید را اعمال کرده، پنجره‌ی
        «تغییرات این نسخه» را از روی CHANGELOG_FA.md نشان بده."""
        try:
            from updater import (get_update_notice, clear_update_notice,
                                 changelog_between)
            notice = get_update_notice()
            if not notice:
                return
            clear_update_notice()
            # (1.0.0) بعد از اعمال آپدیت، نسخه‌ی هشدار داده‌شده را پاک می‌کنیم
            # تا آپدیت‌های بعدی دوباره هشدار بدهند.
            try:
                import app_settings
                app_settings.set_notified_update_version("")
            except Exception:
                pass
            new_v = str(notice.get("new_version") or "")
            prev_v = str(notice.get("prev_version") or "")
            entries = changelog_between(prev_v, new_v) if new_v else []
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
            from PyQt6.QtWidgets import QTextEdit
            from PyQt6.QtCore import Qt
            dlg = QDialog(self)
            dlg.setWindowTitle("به‌روزرسانی انجام شد")
            dlg.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            dlg.resize(540, 430)
            lay = QVBoxLayout(dlg)
            lay.setSpacing(10)
            lay.addWidget(QLabel(
                f"🎉 <b>برنامه با موفقیت به نسخه‌ی {new_v} به‌روزرسانی شد.</b>"))
            if entries:
                lay.addWidget(QLabel("<b>تغییرات این نسخه‌ها:</b>"))
                te = QTextEdit()
                te.setReadOnly(True)
                parts = []
                for ver, date, body in entries:
                    head = f"## نسخه‌ی {ver}" + (f" ({date})" if date else "")
                    parts.append(head + "\n" + body)
                te.setMarkdown("\n\n".join(parts))
                lay.addWidget(te, 1)
            else:
                lay.addWidget(QLabel(
                    "تغییرات جزئی و رفع اشکال در این نسخه اعمال شد."))
            ok = QPushButton("باشه")
            ok.setDefault(True)
            ok.clicked.connect(dlg.accept)
            lay.addWidget(ok)
            dlg.exec()
        except Exception:
            pass

    # ---------------------------------------------------------------- UI ---

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        left_panel = QVBoxLayout()

        # رفع درخواست: کادر یوزرنیم/پسورد بالای پنل اسکن شبکه. این مقادیر فقط
        # در حافظه نگه‌داشته می‌شوند (هیچ‌جا روی دیسک ذخیره نمی‌شوند) و برای
        # اتصال به دستگاه‌های یافت‌شده در اسکن شبکه (چه برای تشخیص خودکار نوع
        # دستگاه، چه برای پرشدن خودکار فیلد یوزرنیم/پسورد دیالوگ افزودن
        # دوربین/NVR) استفاده می‌شوند. با بستن برنامه (closeEvent) پاک می‌شوند.
        scan_cred_group = QGroupBox("اطلاعات ورود برای اتصال به دوربین‌ها")
        scan_cred_layout = QVBoxLayout()
        self.scan_user_input = QLineEdit("admin")
        self.scan_user_input.setPlaceholderText("نام کاربری")
        self.scan_pass_input = QLineEdit()
        self.scan_pass_input.setPlaceholderText("رمز عبور")
        self.scan_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        scan_cred_layout.addWidget(self.scan_user_input)
        scan_cred_layout.addWidget(self.scan_pass_input)
        scan_cred_group.setLayout(scan_cred_layout)
        left_panel.addWidget(scan_cred_group)

        # بخش اسکن شبکه
        # نکته: قبلاً این بخش «اسکن دستگاه‌های مداربسته» نام داشت و صرفاً پورت‌های
        # باز را نشان می‌داد؛ چون این اسکن صرفاً یک اسکن عمومی شبکه (پورت‌های باز
        # روی هر IP) است - نه اسکن اختصاصی دوربین - عنوان و متن دکمه به «اسکن
        # شبکه» تغییر کرد تا با واقعیت عملکرد آن هم‌خوانی داشته باشد. همچنین حالا
        # با دابل‌کلیک روی هر نتیجه، کاربر مشخص می‌کند دستگاه یک دوربین تکی است یا
        # یک NVR؛ در صورت انتخاب NVR، مستقیماً دیالوگ افزودن NVR با IP از پیش
        # پرشده باز می‌شود و کانال‌ها/دوربین‌های متصل به آن پس از افزودن، به‌صورت
        # زیرمنو (زیرشاخه‌ی درختی) زیر همان NVR در پنل «دوربین‌ها و NVRهای من»
        # نمایش داده می‌شوند (رجوع کنید به reload_camera_list).
        scan_group = QGroupBox("اسکن شبکه (Network Scan)")
        scan_layout = QVBoxLayout()
        self.subnet_input = QLineEdit("192.168.1")
        self.subnet_input.setPlaceholderText(
            "رنج IP؛ مثلاً: 192.168.1 یا 192.168.1.20-192.168.1.80 یا 192.168.2.0/24")
        self.subnet_input.setToolTip(
            "رنج IP برای اسکن شبکه.\n"
            "قالب‌ها: «192.168.1» (کل ساب‌نت)، «192.168.1.20-192.168.1.80»،\n"
            "«192.168.1.20-80»، «192.168.2.0/24»؛ چند رنج با ویرگول جدا شود.")
        self.scan_btn = QPushButton("اسکن شبکه")
        self.scan_btn.clicked.connect(self.run_network_scan)
        self.scan_result_list = QListWidget()
        # رفع درخواست: به‌جای نگه‌داشتن Ctrl/Shift هنگام کلیک برای انتخاب چندتایی،
        # کنار هر دستگاه یک چک‌باکس نمایش داده می‌شود (هنگام افزودن آیتم‌ها در
        # _on_network_scan_finished تنظیم می‌شود) و کاربر با تیک زدن آن‌ها،
        # دستگاه‌های موردنظر برای اتصال را مشخص می‌کند. دابل‌کلیک همچنان برای
        # افزودن سریع یک دستگاه تکی کار می‌کند.
        self.scan_result_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.scan_result_list.itemDoubleClicked.connect(self.on_scan_result_selected)
        self.add_selected_scan_btn = QPushButton("+ افزودن دستگاه‌های انتخاب‌شده")
        self.add_selected_scan_btn.clicked.connect(self.on_add_selected_scan_results)
        self.detect_status_label = QLabel("")
        self.detect_status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        scan_layout.addWidget(self.subnet_input)
        scan_layout.addWidget(self.scan_btn)
        # نکته: دیگر از کاربر پرسیده نمی‌شود دستگاه دوربین تکی است یا NVR؛ با
        # دابل‌کلیک، نوع دستگاه به‌صورت خودکار تشخیص داده می‌شود (device_detect.py).
        scan_layout.addWidget(self.scan_result_list)
        scan_layout.addWidget(self.add_selected_scan_btn)
        scan_layout.addWidget(self.detect_status_label)
        scan_group.setLayout(scan_layout)
        left_panel.addWidget(scan_group)

        # بخش لیست دوربین‌ها و NVRهای من (با نام دلخواه)
        cam_group = QGroupBox("دوربین‌ها و NVRهای من")
        cam_layout = QVBoxLayout()
        add_btn_row = QHBoxLayout()
        self.add_camera_btn = QPushButton("افزودن دوربین")
        self.add_camera_btn.clicked.connect(lambda: self.open_add_camera_dialog())
        self.add_nvr_btn = QPushButton("افزودن NVR")
        # نکته: چون open_add_nvr_dialog اکنون یک آرگومان اختیاری (prefill_ip)
        # دارد، باید مثل add_camera_btn از طریق lambda وصل شود؛ در غیر این
        # صورت PyQt مقدار bool سیگنال clicked(checked) را به‌جای None به
        # prefill_ip پاس می‌دهد و IP به‌اشتباه با True/False پر می‌شود.
        self.add_nvr_btn.clicked.connect(lambda: self.open_add_nvr_dialog())
        add_btn_row.addWidget(self.add_camera_btn, 1)
        add_btn_row.addWidget(self.add_nvr_btn, 1)
        # (2.0.58-beta به دستور کاربر) دکمه‌ی «تست پایداری شبکه» از ردیف
        # دکمه‌های افزودن جدا شد و زیر پنل «دوربین‌ها و NVRهای من» قرار گرفت،
        # تا دو دکمه‌ی افزودن دوربین/NVR کنار هم با برچسب کامل جا شوند.
        self.net_probe_btn = QPushButton("📶 تست پایداری شبکه")
        self.net_probe_btn.setToolTip(
            "اسکن پهنای باند و تاخیر شبکه‌ی همه‌ی دوربین‌ها (چند ثانیه طول می‌کشد)")
        self.net_probe_btn.clicked.connect(self._on_network_probe_clicked)
        # دوربین‌های متصل به یک NVR به‌صورت زیرمجموعه‌ی همان NVR نمایش داده می‌شوند.
        self.camera_list = CameraTreeWidget()
        self.camera_list.setHeaderHidden(True)
        self.camera_list.itemDoubleClicked.connect(self.on_camera_item_activated)
        self.camera_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.camera_list.customContextMenuRequested.connect(self.show_camera_context_menu)
        connect_hint = QLabel("برای پخش زنده روی یک دوربین/کانال دابل‌کلیک کنید. کلیک راست: ویرایش/حذف/بازخوانی کانال‌ها")
        connect_hint.setStyleSheet("color: #888; font-size: 10px;")
        cam_layout.addLayout(add_btn_row)
        cam_layout.addWidget(self.camera_list)
        cam_layout.addWidget(connect_hint)
        cam_group.setLayout(cam_layout)
        left_panel.addWidget(cam_group)
        left_panel.addWidget(self.net_probe_btn)

        # «🔥 اعلام حریق»، «👤 چهره‌ها» و «📊 گزارش‌ها» دیگر در پنل چپ
        # نیستند؛ هرکدام صفحه‌ی جداگانه‌ی خودشان را دارند و از هدر بالای
        # برنامه (دکمه‌های ناوبری) قابل دسترسی‌اند - رجوع کنید به انتهای
        # init_ui (QStackedWidget) و متد show_page.

        # ------------------------------------------------ ستون میانی: شبکه‌ی
        # نمایش هم‌زمان دوربین‌ها با تعداد خانه‌ی قابل انتخاب.
        grid_column = QVBoxLayout()
        grid_toolbar = QHBoxLayout()

        # دکمه‌ی نمایش/مخفی‌کردن پنل کناری سمت چپ (اسکن شبکه، دوربین‌ها و
        # NVRهای من). با کلیک روی این دکمه، عرض پنل چپ صفر یا
        # به اندازه‌ی قبلی‌اش برمی‌گردد تا کاربر در صورت نیاز فضای بیشتری
        # برای تصویر دوربین‌ها در وسط داشته باشد.
        self.sidebar_toggle_btn = QPushButton("☰")
        self.sidebar_toggle_btn.setFixedWidth(32)
        self.sidebar_toggle_btn.setToolTip("نمایش / مخفی کردن پنل کناری")
        self.sidebar_toggle_btn.clicked.connect(self.toggle_sidebar)
        grid_toolbar.addWidget(self.sidebar_toggle_btn)

        grid_toolbar_label = QLabel("تعداد نمایش هم‌زمان دوربین‌ها:")
        grid_toolbar_label.setStyleSheet("font-size: 11px;")
        self.grid_size_combo = QComboBox()
        for n in (1, 4, 9, 16, 32, 64):
            self.grid_size_combo.addItem(str(n), n)
        self.grid_size_combo.setCurrentIndex(1)  # پیش‌فرض: 4
        self.grid_size_combo.currentIndexChanged.connect(self._on_grid_size_changed)
        grid_toolbar.addWidget(grid_toolbar_label)
        grid_toolbar.addWidget(self.grid_size_combo)

        # رفع درخواست: به‌جای یک دکمه‌ی روشن/خاموش شمارش افراد برای هر
        # دوربین جداگانه، فقط یک دکمه‌ی واحد بالای تمام پنجره‌های دوربین‌ها
        # قرار دارد که شمارش را برای همه‌ی دوربین‌های باز هم‌زمان روشن/خاموش
        # می‌کند. تعداد نفرات هم‌چنان جداگانه بالای پنجره‌ی هر دوربین نوشته
        # می‌شود (رجوع کنید به CameraSlotWidget.people_count_label).
        self.people_toggle_btn = QPushButton("👥 شمارش افراد")
        self.people_toggle_btn.setCheckable(True)
        self.people_toggle_btn.setToolTip("روشن/خاموش کردن شمارش افراد Real Time برای تمام دوربین‌های باز")
        self.people_toggle_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#e67e22; color:#fff;}"
        )
        self.people_toggle_btn.toggled.connect(self._on_people_toggle_all)
        grid_toolbar.addWidget(self.people_toggle_btn)

        # (1.1.0) اتاق مانیتورینگ (multi-monitor): تقسیم نمایش دوربین‌ها روی
        # چند مانیتور — دیالوگ تنظیم + پنجره‌ی نمایش جداگانه برای هر مانیتور.
        self.monitor_room_btn = QPushButton("🖥 اتاق مانیتورینگ")
        self.monitor_room_btn.setToolTip("تقسیم نمایش دوربین‌ها روی چند مانیتور (اتاق مانیتورینگ)")
        self.monitor_room_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.monitor_room_btn.clicked.connect(self._on_monitor_room_clicked)
        grid_toolbar.addWidget(self.monitor_room_btn)

        # (2.0.12-beta به دستور کاربر، اصلاح‌شده در 2.0.14-beta): دکمه‌های
        # مربوط به رسم - «نوع رسم» (تشخیص خودکار/AI) فقط در حالت رسم، و
        # «تایید»/«لغو» فقط وقتی محدوده‌ای رسم شده (در انتظار) یا در حال
        # ویرایش است دیده می‌شوند؛ بقیه‌ی وقت‌ها مخفی‌اند تا نوار ابزار خلوت
        # بماند. این دکمه‌ها در سطر دوم نوار ابزار (_NoMinWidthRow) هستند تا
        # ظاهر/مخفی شدنشان اندازه‌ی پنجره و کادرها را تکان ندهد.
        # رفع درخواست: محدوده‌ی هشدار (Zone) - جایگزین خط فرضی عبور قبلی.
        # کاربر ابتدا یک دوربین را از شبکه انتخاب می‌کند (کلیک روی خانه‌اش)،
        # سپس این دکمه را می‌زند تا بتواند با کلیک‌های متوالی روی نقاط دلخواه
        # زمین/تصویر همان دوربین یک محدوده‌ی چندضلعی (نه لزوماً مستطیل) بسازد.
        # «تایید و نام‌گذاری» یک نام (مثلاً اسم اتاق) از کاربر می‌پرسد و
        # محدوده را به لیست محدوده‌های فعال آن دوربین اضافه می‌کند (از آن پس
        # با ورود هرکسی به آن، کادر دوربین قرمز و آلارم پخش می‌شود)؛ «لغو
        # رسم» فقط نقاط در انتظار نام‌گذاری را پاک می‌کند و «مدیریت
        # محدوده‌ها» امکان مشاهده/حذف محدوده‌های از قبل تایید‌شده را می‌دهد.
        # هر دوربین می‌تواند هم‌زمان چند محدوده‌ی نام‌دار داشته باشد.
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

        # کانتینر دکمه‌های حالت رسم - فقط در حالت رسم نمایان می‌شود
        # (رجوع کنید به _refresh_line_buttons). این ردیف، «سطر دوم» نوار
        # ابزار است (داخل grid_column، زیر نوار اصلی) نه عضوی از همان نوار
        # افقی؛ تا ظاهر/مخفی شدنش ارتفاع نوار اصلی و اندازه‌ی کادرها را تکان
        # ندهد. _NoMinWidthRow هم جلوی بزرگ شدن خودکار پنجره را می‌گیرد
        # (ریشه‌ی باگ 2.0.14-beta).
        self.region_draw_row = _NoMinWidthRow()
        region_draw_layout = QHBoxLayout()
        region_draw_layout.setContentsMargins(0, 0, 0, 0)
        self.region_draw_row.setLayout(region_draw_layout)
        self.region_draw_row.setVisible(False)

        # رفع درخواست «یک حالت جدید که خودش سطح زمین رو تشخیص بده و کلش رو
        # محدوده محسوب کنه»: به‌جای کلیک‌های متوالی دستی، این دکمه بلافاصله
        # کل کادر تصویر زنده‌ی دوربین انتخاب‌شده را - در عمل معادل «کل زمینی
        # که دوربین می‌بیند» - به‌عنوان یک محدوده‌ی تازه و در انتظار تایید
        # می‌گذارد (دقیقاً مثل رسم دستی، با همان دکمه‌های تایید/لغوِ پایین).
        # این محدوده هم مثل هر محدوده‌ی دیگری کاملاً قابل ویرایش است - قبل
        # از تایید (با کشیدن گوشه‌ها) یا بعداً از «مدیریت محدوده‌ها».
        self.auto_region_btn = QPushButton("🌐 تشخیص خودکار محدوده (کل تصویر)")
        self.auto_region_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید - کل تصویر دوربین به‌عنوان محدوده در نظر گرفته می‌شود\n"
            "۳) در صورت نیاز، گوشه‌های آن را با ماوس بکشید تا شکل/اندازه‌اش را دقیق‌تر کنید\n"
            "   (مثلاً گوشه‌ای را عقب بکشید تا یک در ورودی از محدوده کنار برود)\n"
            "۴) «✅ تایید و نام‌گذاری» را بزنید\n"
            "توجه: این برنامه مدل جداگانه‌ای برای تشخیص دقیقِ کفِ زمین ندارد؛ «تشخیص خودکار» "
            "یعنی کل کادر تصویر دوربین به‌عنوان محدوده گرفته می‌شود، نه شناسایی هوشمند مرز زمین."
        )
        self.auto_region_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.auto_region_btn.clicked.connect(self._on_auto_region_clicked)
        region_draw_layout.addWidget(self.auto_region_btn)

        # رفع درخواست «یک حالت جدید که خودش سطح زمین رو تشخیص بده و کلش رو
        # محدوده محسوب کنه، ولی بازم قابل ادیت باشه، دقیق‌تر با مدل هوش
        # مصنوعی (Segmentation)»: برخلاف دکمه‌ی بالا (که فقط کل کادر تصویر
        # را می‌گذارد چون مدل جداگانه‌ای نداشت)، این دکمه واقعاً از یک مدل
        # Segmentation (SegFormer/ADE20K - رجوع کنید به floor_detector.py)
        # روی آخرین فریمِ زنده‌ی همین دوربین استفاده می‌کند تا فقط سطح
        # زمین/کفِ واقعی را (نه کل کادر شامل دیوار/سقف/اثاثیه) به‌عنوان
        # محدوده‌ی پیشنهادی بگذارد. نتیجه، دقیقاً مثل هر محدوده‌ی دیگر،
        # کاملاً با ماوس قابل ویرایش است. چون بارگذاری/اجرای مدل چند ثانیه
        # طول می‌کشد، در یک ترد جدا (FloorDetectThread) اجرا می‌شود تا UI
        # فریز نشود - رجوع کنید به _on_ai_floor_region_clicked.
        self.ai_floor_btn = QPushButton("🧭 تشخیص هوشمند زمین (AI)")
        self.ai_floor_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید - با مدل هوش مصنوعی (Segmentation)، فقط "
            "سطح زمین/کفِ واقعیِ تصویر تشخیص داده و به‌عنوان محدوده پیشنهاد "
            "می‌شود (نه کل کادر تصویر)\n"
            "۳) در صورت نیاز، گوشه‌ها را با ماوس بکشید/اضافه/حذف کنید تا "
            "دقیق‌تر شود\n"
            "۴) «✅ تایید و نام‌گذاری» را بزنید\n"
            "نکته: اولین استفاده ممکن است به‌خاطر دانلود یک‌بارِ مدل (حدود "
            "۱۴ مگابایت) چند ثانیه بیشتر طول بکشد. اگر این قابلیت روی سیستم "
            "شما در دسترس نباشد، به‌جایش می‌توانید از «تشخیص خودکار محدوده "
            "(کل تصویر)» یا رسم دستی استفاده کنید."
        )
        self.ai_floor_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.ai_floor_btn.clicked.connect(self._on_ai_floor_region_clicked)
        region_draw_layout.addWidget(self.ai_floor_btn)
        # نگه‌داشتن ارجاع به تردِ در حال اجرا (اگر باشد) - هم برای جلوگیری
        # از garbage-collect شدنِ زودهنگام QThread در حال اجرا، هم برای
        # اینکه بدانیم همین الان یک تشخیص در جریان است (رجوع کنید به
        # _on_ai_floor_region_clicked).
        self._floor_detect_thread = None

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
        self.redraw_line_btn.setToolTip("نقاط در حال رسم/در انتظار نام‌گذاری را لغو می‌کند؛ محدوده‌های قبلاً تایید‌شده حذف نمی‌شوند.")
        self.redraw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
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

        self.camera_grid = CameraGridWidget(
            self.face_engine, self.on_face_event, on_external_camera_drop=self.on_camera_dropped_on_grid,
            on_region_alert=self.on_region_alert,
            on_fire_event=self.on_fire_event,
            on_plate_event=self.on_plate_event,
            on_person_event=self.on_person_event,
            on_audio_url_found=lambda cam_id, url: self.camera_store.update_camera(
                cam_id, audio_url=url),
            on_audio_creds_save=self._save_audio_creds,
        )
        self.camera_grid.selection_changed.connect(lambda _idx: self._refresh_line_buttons())
        self.camera_grid.tripwire_changed.connect(self._refresh_line_buttons)
        self.camera_grid.detector_status_changed.connect(
            self._refresh_person_detector_status)
        # (2.0.61-beta) هشدار قطع تصویر هر خانه به MainWindow وصل می‌شود
        # تا در «پنل رویدادها» ثبت و بوق هشدار پخش شود.
        self.camera_grid.video_loss.connect(self._on_video_loss)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self.camera_grid)

        grid_column.addLayout(grid_toolbar)
        # سطر دوم نوار ابزار: دکمه‌های رسم محدوده (مخفی پیش‌فرض) - رجوع کنید
        # به کامنت _NoMinWidthRow برای چرایی جدا بودنش از نوار اصلی.
        grid_column.addWidget(self.region_draw_row)
        grid_column.addWidget(grid_scroll, 1)

        # ------------------------------------------------ ستون راست: پنل
        # رویدادها (2.0.56-beta به دستور کاربر: تبدیل «پنل تشخیص چهره» به
        # «پنل رویدادها»). این پنل رویدادهای مهم سیستم را نشان می‌دهد:
        # تخلفات پلاک‌خوان (از جمله «⛔ ورود غیرمجاز» برای پلاک‌های لیست
        # سیاه) و هشدارهای «ورود به محدوده». رویدادهای شناسایی چهره دیگر
        # این‌جا نمایش داده نمی‌شوند (در صفحه‌ی «چهره‌ها» و گزارش‌ها هستند)
        # ولی برای گالری «دیدن تصاویر» در حافظه نگه داشته می‌شوند.
        events_panel_group = QGroupBox("🚨 پنل رویدادها")
        events_panel_layout = QVBoxLayout()
        # دکمه‌ی «🖼 دیدن تصاویر»: گالری چهره‌های تشخیص‌داده‌شده (تاریخچه‌ی
        # همین نشست، از حافظه — نه از لیست پنل).
        gallery_btn = QPushButton("🖼 دیدن تصاویر")
        gallery_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gallery_btn.clicked.connect(self.open_face_gallery)
        events_panel_layout.addWidget(gallery_btn)
        self.events_panel_list = QListWidget()
        self.events_panel_list.setIconSize(QSize(64, 64))
        self.events_panel_list.setWordWrap(True)
        events_panel_layout.addWidget(self.events_panel_list)
        events_panel_group.setLayout(events_panel_layout)
        # تاریخچه‌ی چهره‌های این نشست برای گالری + شناسه‌ی تخلفات پلاکی که
        # قبلاً در پنل نمایش داده شده‌اند (جلوگیری از تکرار).
        self._recent_face_events = []
        self._shown_violation_ids = set()
        # (2.0.61-beta) هشدار قطع تصویر: آخرین زمان هشدار هر دوربین
        # (کول‌داون ضداسپم) + مجموعه‌ی دوربین‌هایی که الان قطع‌اند.
        self._video_loss_alerted = {}
        self._video_loss_down = set()

        # پنل «هشدارهای حریق و دود» - رفع درخواست «سیستم تشخیص دود و اعلام
        # حریق»: هم رویدادهای تشخیص تصویری (fire_smoke_detector.py روی هر
        # دوربین) و هم هشدارهای دریافتی از پنل‌های فیزیکی (fire_alarm_io.py)
        # در همین‌جا (و هم‌زمان در «گزارش‌ها») ثبت می‌شوند - جدا از پنل
        # تشخیص چهره‌ی بالا تا با آن قاطی نشود.
        fire_panel_group = QGroupBox("🔥 هشدارهای حریق و دود")
        self.fire_panel_group = fire_panel_group  # برای نمایش/مخفی‌شدن خودکار
        fire_panel_layout = QVBoxLayout()
        self.fire_panel_list = QListWidget()
        self.fire_panel_list.setIconSize(QSize(64, 64))
        self.fire_panel_list.setWordWrap(True)
        fire_panel_layout.addWidget(self.fire_panel_list)
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # دکمه‌ی قطع صدای آژیر + دکمه‌ی تنظیمات اتصال ساختمان زیر لیست حریق.
        fire_btn_row = QHBoxLayout()
        self.btn_stop_alarm = QPushButton("🔇 قطع صدای هشدار")
        self.btn_stop_alarm.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop_alarm.clicked.connect(self.stop_fire_alarm_sound)
        self.btn_building_fire = QPushButton("🏢 اتصال به سیستم حریق ساختمان")
        self.btn_building_fire.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_building_fire.clicked.connect(self.open_building_fire_settings)
        fire_btn_row.addWidget(self.btn_stop_alarm)
        fire_btn_row.addWidget(self.btn_building_fire)
        fire_panel_layout.addLayout(fire_btn_row)
        fire_panel_group.setLayout(fire_panel_layout)

        # رفع درخواست «تنظیم اندازه پنل‌های سمت راست»: به‌جای QVBoxLayout با
        # ضریب کشش ثابت (که با توجه به حداقل‌اندازه‌ی متفاوت دو QGroupBox
        # همیشه دقیقاً به همان نسبت ۳:۲ در نمی‌آمد و کاربر هم راهی برای
        # تغییرش نداشت)، از یک QSplitter عمودی استفاده می‌شود: هم نسبت
        # اولیه‌ی مشخص (۶۰٪/۴۰٪) دارد، هم کاربر می‌تواند با کشیدن لبه‌ی بین
        # دو پنل، اندازه‌ی هرکدام را دستی تنظیم کند، و هم حداقل ارتفاعی
        # (setMinimumHeight) دارند که هیچ‌کدام کاملاً جمع نشوند.
        events_panel_group.setMinimumHeight(120)
        fire_panel_group.setMinimumHeight(120)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(events_panel_group)
        self.right_splitter.addWidget(fire_panel_group)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 2)

        # رفع درخواست: عرض پنل سمت راست (پنل رویدادها) باید دقیقاً هم‌اندازه‌ی
        # پنل سمت چپ باشد تا فضای بیشتری به تصویر دوربین‌ها در وسط برسد. قبلاً
        # با addLayout/addWidget + ضریب کشش (stretch factor) این کار انجام
        # می‌شد، اما چون QVBoxLayout سمت چپ و QGroupBox سمت راست حداقل‌اندازه‌ی
        # (minimumSizeHint) متفاوتی داشتند، ضریب کشش یکسان همیشه به عرض واقعاً
        # یکسان منجر نمی‌شد. با QSplitter و تنظیم صریح اندازه‌ی اولیه، عرض دو
        # ستون کناری همیشه یکسان شروع می‌شود (کاربر همچنان می‌تواند با کشیدن
        # لبه‌ی splitter عرض را دستی تغییر دهد).
        # رفع درخواست: پنل سمت چپ کوچکتر شده (عرض ثابت و محدودتر به‌جای سهم
        # کشیدنی از splitter) و یک دکمه‌ی sidebar اضافه شده که با کلیک روی آن
        # کل پنل چپ باز/بسته می‌شود (toggle_sidebar).
        self.left_widget = QWidget()
        self.left_widget.setLayout(left_panel)
        self.left_widget.setMaximumWidth(230)
        grid_widget = QWidget()
        grid_widget.setLayout(grid_column)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.left_widget)
        self.splitter.addWidget(grid_widget)
        self.splitter.addWidget(self.right_splitter)
        # پنل چپ اکنون کوچکتر و با عرض محدود (ثابت‌تر) است، پنل وسط (شبکه‌ی
        # دوربین‌ها) بیشترین سهم را می‌گیرد و پنل راست (تشخیص چهره) بدون تغییر
        # باقی می‌ماند.
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 7)
        self.splitter.setStretchFactor(2, 2)
        total_w = max(self.width(), 1500)
        right_w = total_w * 2 // 9        # عرض قبلی/بدون‌تغییرِ پنل راست
        left_w = 200                       # عرض کوچک و ثابت پنل چپ
        self.splitter.setSizes([left_w, total_w - left_w - right_w, right_w])
        # اندازه‌ی اولیه‌ی صریح برای دو پنل داخل ستون راست (۶۰٪ تشخیص چهره،
        # ۴۰٪ هشدار حریق/دود) - کاربر همچنان می‌تواند با کشیدن لبه‌ی بینشان
        # این نسبت را تغییر دهد.
        right_h = max(self.height(), 780)
        self.right_splitter.setSizes([right_h * 6 // 10, right_h * 4 // 10])
        # عرضی که پنل چپ قبل از مخفی‌شدن داشت، برای بازگرداندن آن هنگام کلیک
        # مجدد روی دکمه‌ی sidebar نگه‌داشته می‌شود.
        # (برگشت به رفتار action #207 به دستور کاربر، 2.0.12-beta: هیچ تنظیم
        # اضافه‌ای روی splitterها نیست - نه setCollapsible، نه ذخیره/بازیابی
        # اندازه. این بخش را دیگر دست نزن مگر در صورت ضرورت.)
        self._left_panel_width = left_w

        # -------------------------------------------- صفحه‌ها + هدر بالای برنامه --
        # محتوای قبلی پنجره (پنل چپ + شبکه‌ی دوربین‌ها + پنل راست) حالا «صفحه‌ی
        # اصلی» است؛ «اعلام حریق»، «چهره‌ها» و «گزارش‌ها» هرکدام صفحه‌ی
        # جداگانه‌ی خودشان را دارند و از هدر بالای برنامه (دکمه‌های ناوبری)
        # قابل دسترسی‌اند.
        home_widget = QWidget()
        home_layout = QHBoxLayout(home_widget)
        home_layout.setContentsMargins(0, 0, 0, 0)
        home_layout.addWidget(self.splitter)

        self.pages = QStackedWidget()
        self.pages.addWidget(home_widget)
        self.fire_page = FireAlarmPage(
            self.fire_alarm_store, self._start_fire_alarm_monitor,
            self._stop_fire_alarm_monitor,
            camera_store=self.camera_store,
            on_fire_toggle=self._on_camera_fire_toggle,
        )
        self.pages.addWidget(self.fire_page)
        self.face_page = FaceLibraryPage(self.face_engine, self.get_active_camera_frame)
        self.pages.addWidget(self.face_page)
        # camera_store به صفحه‌ی گزارش‌ها پاس داده می‌شود تا برای دکمه‌ی
        # «پخش ویدیوی NVR»، اطلاعات اتصال NVR مربوط به هر رویداد را پیدا کند.
        self.reports_page = ReportsPage(report_store, self.camera_store)
        self.pages.addWidget(self.reports_page)
        # رفع درخواست «سیستم پلاک‌خوان»: صفحه‌ی جدا (مثل Face Library) با دو
        # تب «تعریف پلاک‌ها» و «گزارش عبور»؛ on_plate_toggle برای اعمال زنده‌ی
        # فعال/غیرفعال شدن پلاک‌خوان روی دوربینی که همین حالا باز است.
        self.plate_page = PlateLibraryPage(
            self.get_active_camera_frame, self.camera_store,
            on_plate_toggle=self._on_camera_plate_toggle,
            get_plate_diag_callback=self._get_plate_live_diag)
        self.pages.addWidget(self.plate_page)
        # رفع درخواست «ردیابی اشخاص بین دوربین‌ها»: صفحه‌ی جدا (مثل
        # پلاک‌خوان) با دو تب «اشخاص ردیابی‌شده» و «گزارش مسیر حرکت»؛
        # on_person_toggle برای اعمال زنده‌ی فعال/غیرفعال شدن ردیابی روی
        # دوربینی که همین حالا باز است.
        self.person_page = PersonTrackPage(
            self.camera_store,
            on_person_toggle=self._on_camera_person_toggle,
            on_show_on_map=self._on_person_show_on_map,
            face_engine=self.face_engine)
        self.pages.addWidget(self.person_page)
        # رفع درخواست «نقشه‌ی تعاملی ساختمان»: صفحه‌ی هفتم؛ اگر فایل
        # building_map_dialog.py کنار برنامه نباشد، بدون کرش رد می‌شود.
        self.map_page = None
        if _MAP_PAGE_AVAILABLE:
            self.map_page = BuildingMapPage(
                self.camera_store,
                on_camera_click=self._on_map_camera_click)
            self.pages.addWidget(self.map_page)
        # صفحه‌ی «⚙️ تنظیمات»: تم (تاریک/روشن/سیستم)، زبان (فارسی/English)
        # و «اعمال آپدیت» (از هدر به اینجا منتقل شد).
        self.settings_page = SettingsPage(
            on_apply_update=self._on_apply_update,
            on_sound_changed=self._on_alarm_sound_changed,
            on_password_save_changed=self._on_password_save_changed,
            on_check_updates_now=self.check_updates_now,
            is_admin=self._is_admin(),
            camera_store=self.camera_store)
        self.pages.addWidget(self.settings_page)

        main_layout.addWidget(self._build_header())
        main_layout.addWidget(self.pages, 1)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        self.show_page("home")
        # پنل «هشدارهای حریق و دود» فقط وقتی دیده می‌شود که حداقل یک دوربین
        # تشخیص حریق فعال داشته باشد (وضعیت اولیه هنگام بالا آمدن برنامه).
        self._refresh_fire_panel_visibility()
        # (2.0.72-beta) کاربران/سطح دسترسی + بررسی خودکار آپدیت جدید
        self._update_notified_version = None
        self._update_toast = None
        try:
            self.update_check_done.connect(self._on_update_check_result)
        except Exception:
            pass
        self.apply_user_permissions()
        self._schedule_update_checks()

    def _on_password_save_changed(self, enabled: bool):
        """(2.0.15-beta) وقتی کاربر ذخیره‌ی امن رمزها را در تنظیمات عوض می‌کند:
        - خاموش -> رمزهای ذخیره‌شده (دیسک + حافظه) کاملاً پاک می‌شوند و از
          این به بعد مثل قبل، رمز هرگز روی دیسک نمی‌ماند.
        - روشن -> رمزهایی که الان در حافظه‌اند بلافاصله رمزنگاری و ذخیره
          می‌شوند تا در اجرای بعدی پرسیده نشوند."""
        try:
            if not enabled:
                self.camera_store.wipe_saved_passwords()
            else:
                self.camera_store.save()
                self.camera_store.save_nvrs()
        except Exception:
            pass

    def _refresh_fire_panel_visibility(self):
        """رفع درخواست: پنل «هشدارهای حریق و دود» در صفحه‌ی اصلی فقط وقتی
        دیده می‌شود که حداقل یک دوربین «تشخیص حریق» فعال داشته باشد؛ با
        فعال/غیرفعال شدن تیک حریق هر دوربین یا افزودن/حذف دوربین، پنل خودکار
        ظاهر/مخفی می‌شود."""
        try:
            store = getattr(self, "camera_store", None)
            cams = list(getattr(store, "cameras", None) or [])
            any_fire = any(bool(c.get("fire_detection")) for c in cams)
        except Exception:
            any_fire = False
        panel = getattr(self, "fire_panel_group", None)
        if panel is None:
            return
        want_visible = bool(any_fire)
        # نکته: isVisible() زنجیره‌ی والدها را هم چک می‌کند و قبل از
        # show() شدن پنجره همیشه False است؛ برای مقایسه‌ی «وضعیتِ خواسته‌شده»
        # باید پرچم صریح خودِ ویجت (isHidden) را خواند، وگرنه در startup
        # هیچ‌وقت مخفی نمی‌شود.
        if panel.isHidden() == (not want_visible):
            return
        # (2.0.12-beta: مثل action #207 - فقط مخفی/نمایان ساده، بدون
        # ذخیره/بازیابی اندازه‌ی splitter.)
        panel.setVisible(want_visible)

    def toggle_sidebar(self):
        """رفع درخواست: نمایش/مخفی‌کردن پنل کناری سمت چپ با کلیک روی دکمه‌ی
        sidebar. وقتی پنل باز است با کلیک بسته می‌شود (عرض صفر) و وقتی بسته
        است با کلیک، به آخرین عرضی که داشت باز می‌گردد.
        (2.0.12-beta: برگشت به رفتار action #207.)"""
        sizes = self.splitter.sizes()
        if sizes[0] > 0:
            self._left_panel_width = sizes[0]
            sizes[1] += sizes[0]
            sizes[0] = 0
        else:
            restore_w = getattr(self, "_left_panel_width", 200) or 200
            sizes[1] = max(0, sizes[1] - restore_w)
            sizes[0] = restore_w
        self.splitter.setSizes(sizes)

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

    def _on_monitor_room_clicked(self):
        """(1.1.0) اتاق مانیتورینگ: دیالوگ تنظیم تقسیم دوربین‌ها روی چند مانیتور."""
        try:
            from monitor_room import MonitorRoomManager
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"بارگذاری اتاق مانیتورینگ ممکن نشد:\n{e}")
            return
        if getattr(self, "_monitor_room_mgr", None) is None:
            self._monitor_room_mgr = MonitorRoomManager(
                self.camera_store, self.face_engine, report_store=report_store,
                on_person_toggle=self._on_camera_person_toggle)
        try:
            self._monitor_room_mgr.open_setup_dialog(self)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن اتاق مانیتورینگ ممکن نشد:\n{e}")

    # ----------------------------------------------------- محدوده‌ی هشدار --

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

    def on_region_alert(self, cam, number, name):
        """رفع درخواست: با ورود شخصی به یکی از محدوده‌های هشدار هر دوربین
        (از CameraSlotWidget._on_region_entered)، یک ردیف متنی قرمز هم در
        پنل رویدادها (سمت راست) ثبت می‌شود تا سابقه‌ی هشدارها هم در دسترس
        باشد. ``cam``: کل دیکشنری دوربین (نه فقط اسم) تا nvr_id/channel هم
        برای لینک «پخش ویدیوی NVR» در دیالوگ گزارش‌ها ذخیره شود."""
        # گیت لایسنس: اگر قابلیت «هشدار ورود به محدوده» فعال نباشد،
        # هشداری ثبت و پخش نمی‌شود.
        try:
            from license import is_feature_enabled
            if not is_feature_enabled("zone_alerts"):
                return
        except Exception:
            pass
        camera_name = cam.get("name", "")
        timestamp = time.strftime("%H:%M:%S")
        label = f"شماره {number}" + (f" / {name}" if name else "")
        text = f"[{timestamp}] {camera_name}\n⚠ ورود به محدوده {label}"
        item = QListWidgetItem(text)
        item.setForeground(QColor("#e74c3c"))
        self.events_panel_list.insertItem(0, item)
        # رفع درخواست: علاوه بر نمایش موقت در همین پنل، ثبت دائمی روی سیستم
        # (report_store.py) - قابل جست‌وجو/خروجی از دیالوگ «گزارش‌ها».
        report_store.log_region_alert(camera_name, number, name,
                                       nvr_id=cam.get("nvr_id"), channel=cam.get("channel"))
        while self.events_panel_list.count() > 300:
            self.events_panel_list.takeItem(self.events_panel_list.count() - 1)

    def _push_simple_event(self, text, color="#c0392b"):
        """(2.0.61-beta) افزودن یک ردیف متنی ساده به «پنل رویدادها»
        (بدون ثبت دائمی در گزارش‌ها)؛ سقف ۳۰۰ ردیف مثل بقیه."""
        try:
            timestamp = time.strftime("%H:%M:%S")
            item = QListWidgetItem(f"[{timestamp}] {text}")
            item.setForeground(QColor(color))
            self.events_panel_list.insertItem(0, item)
            while self.events_panel_list.count() > 300:
                self.events_panel_list.takeItem(self.events_panel_list.count() - 1)
        except Exception:
            pass

    def _on_video_loss(self, cam, kind):
        """(2.0.61-beta) هشدار قطع/وصل مجدد تصویر دوربین (از
        CameraSlotWidget.video_loss_signal): ثبت در «پنل رویدادها» + بوق.
        کول‌داون ۵ دقیقه‌ای برای هر دوربین تا در قطعی‌های پشت‌سرهم
        پنل اسپم نشود."""
        try:
            cam = cam or {}
            cam_id = str(cam.get("id") or "")
            if not cam_id:
                return
            cam_name = cam.get("name") or cam.get("ip") or "دوربین"
            now = time.time()
            if kind == "lost":
                # کول‌داون ضداسپم (منطق خالص در video_loss.py)
                if not _video_loss_cooldown_ok(self._video_loss_alerted.get(cam_id, 0), now):
                    return
                self._video_loss_alerted[cam_id] = now
                self._video_loss_down.add(cam_id)
                self._push_simple_event(
                    f"📡 قطع تصویر دوربین «{cam_name}»", color="#c0392b")
                _play_alarm_beep("videoloss")
            elif kind == "recovered":
                if cam_id not in self._video_loss_down:
                    return
                self._video_loss_down.discard(cam_id)
                self._push_simple_event(
                    f"✅ وصل مجدد تصویر دوربین «{cam_name}»", color="#27ae60")
        except Exception as e:
            print(f"خطا در هشدار قطع تصویر: {e}")

    def on_fire_event(self, cam, kind: str, crop_frame, confidence: float):
        """رفع درخواست «سیستم تشخیص دود و اعلام حریق»: با هر تشخیص تصویری
        آتش/دود روی یکی از دوربین‌ها (از CameraSlotWidget._on_fire_event)،
        یک ردیف با تصویر برش‌خورده در پنل «هشدارهای حریق و دود» ثبت و در
        گزارش‌ها ذخیره می‌شود - دقیقاً همان الگوی on_region_alert بالا."""
        camera_name = cam.get("name", "")
        timestamp = time.strftime("%H:%M:%S")
        label = "🔥 آتش" if kind == "fire" else "💨 دود"
        text = f"[{timestamp}] {camera_name}\n⚠ {label} شناسایی شد ({confidence * 100:.0f}%)"
        item = QListWidgetItem(text)
        item.setForeground(QColor("#e74c3c"))
        pixmap = _bgr_to_pixmap(crop_frame) if crop_frame is not None else None
        if pixmap is not None:
            item.setIcon(QIcon(pixmap))
        self.fire_panel_list.insertItem(0, item)
        report_store.log_fire_smoke_visual(camera_name, kind, crop_frame=crop_frame,
                                            confidence=confidence,
                                            nvr_id=cam.get("nvr_id"), channel=cam.get("channel"))
        while self.fire_panel_list.count() > 300:
            self.fire_panel_list.takeItem(self.fire_panel_list.count() - 1)
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # با هر تشخیص آتش/دود: آژیر ممتد + ارسال سیگنال به پنل ساختمان.
        if self.alarm_player is not None:
            self.alarm_player.start()
        if self.building_fire is not None:
            self.building_fire.trigger(camera_name, kind, confidence)

    # ---------------------------------------------- پنل‌های فیزیکی اعلام حریق -

    def _start_fire_alarm_monitor(self, panel):
        """رفع درخواست «سیستم تشخیص دود و اعلام حریق»: برای این پنل یک ترد
        مانیتور پس‌زمینه (رجوع کنید به fire_alarm_io.FireAlarmMonitorThread)
        شروع می‌کند که با تغییر واقعی وضعیت، سیگنال می‌فرستد."""
        self._stop_fire_alarm_monitor(panel["id"])  # جلوگیری از دو ترد هم‌زمان روی یک پنل
        thread = FireAlarmMonitorThread(panel, self)
        thread.alarm_triggered.connect(lambda p=panel: self.on_fire_alarm_panel_triggered(p))
        thread.alarm_cleared.connect(lambda p=panel: self.on_fire_alarm_panel_cleared(p))
        thread.start()
        self._fire_alarm_threads[panel["id"]] = thread

    def _stop_fire_alarm_monitor(self, panel_id):
        thread = self._fire_alarm_threads.pop(panel_id, None)
        if thread is not None:
            thread.stop()

    def on_fire_alarm_panel_triggered(self, panel):
        """آلارم یک پنل/سنسور فیزیکی اعلام حریق همین الان فعال شده - رفع
        درخواست «سیستم تشخیص دود و اعلام حریق»: بوق هشدار + ردیف قرمز در
        پنل + ثبت دائمی در گزارش‌ها، دقیقاً مثل تشخیص تصویری."""
        timestamp = time.strftime("%H:%M:%S")
        text = f"[{timestamp}] {panel['name']}\n🔥 آلارم پنل اعلام حریق فعال شد!"
        item = QListWidgetItem(text)
        item.setForeground(QColor("#e74c3c"))
        self.fire_panel_list.insertItem(0, item)
        _play_alarm_beep("fire")
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # آلارم پنل فیزیکی هم آژیر ممتد + سیگنال به پنل ساختمان را فعال می‌کند.
        if self.alarm_player is not None:
            self.alarm_player.start()
        if self.building_fire is not None:
            self.building_fire.trigger(panel["name"], "fire", 1.0)
        report_store.log_fire_alarm_panel(panel["name"], active=True)
        while self.fire_panel_list.count() > 300:
            self.fire_panel_list.takeItem(self.fire_panel_list.count() - 1)

    def on_fire_alarm_panel_cleared(self, panel):
        timestamp = time.strftime("%H:%M:%S")
        text = f"[{timestamp}] {panel['name']}\n✅ وضعیت پنل اعلام حریق به حالت عادی برگشت"
        item = QListWidgetItem(text)
        item.setForeground(QColor("#2ecc71"))
        self.fire_panel_list.insertItem(0, item)
        # با عادی‌شدن پنل، آژیر ممتد هم قطع می‌شود.
        self.stop_fire_alarm_sound()
        report_store.log_fire_alarm_panel(panel["name"], active=False)
        while self.fire_panel_list.count() > 300:
            self.fire_panel_list.takeItem(self.fire_panel_list.count() - 1)

    def _on_building_fire_log(self, msg):
        """نمایش پیام فارسی نتیجه‌ی ارسال سیگنال به پنل ساختمان
        (از ترد پس‌زمینه‌ی BuildingFireOutput) در لیست رویدادهای حریق."""
        try:
            item = QListWidgetItem(f"🏢 {msg}")
            item.setWordWrap(True)
            self.fire_panel_list.insertItem(0, item)
            while self.fire_panel_list.count() > 300:
                self.fire_panel_list.takeItem(self.fire_panel_list.count() - 1)
        except Exception:
            pass

    def stop_fire_alarm_sound(self):
        """قطع صدای آژیر حریق (دکمه‌ی 🔇 قطع صدای هشدار)."""
        if self.alarm_player is not None:
            try:
                self.alarm_player.stop()
            except Exception:
                pass

    def open_building_fire_settings(self):
        """باز کردن دیالوگ تنظیمات اتصال به سیستم حریق ساختمان + صدای هشدار."""
        if not _BUILDING_FIRE_AVAILABLE or BuildingFireSettingsDialog is None:
            QMessageBox.warning(
                self, "اتصال به سیستم حریق ساختمان",
                "فایل‌های اتصال ساختمان (building_fire_output.py، alarm_sound.py و\n"
                "building_fire_settings_dialog.py) کنار main.py پیدا نشد.\n"
                "آن‌ها را از پکیج ias-cms-fire-building کپی کنید.")
            return
        dlg = BuildingFireSettingsDialog(self, log_callback=self._on_building_fire_log)
        if dlg.exec():
            self.building_fire.reload()
            self.alarm_player.reload()

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

    # ------------------------------------------------------- camera list ---

        # (2.0.15-beta) نشان پایداری شبکه روی هر دوربین (از آخرین اسکن)
    def _net_badge(self, cam):
        q = (cam or {}).get("net_quality", "")
        kbps = (cam or {}).get("net_kbps")
        rtt = (cam or {}).get("net_rtt_ms")
        if not q:
            return "", None
        icon = {"خوب": "📶", "متوسط": "📶", "ضعیف": "⚠️", "قطع": "⛔",
                "نامشخص": "❓"}.get(q, "📶")
        color = {"خوب": "#2ecc71", "متوسط": "#f1c40f", "ضعیف": "#e67e22",
                 "قطع": "#e74c3c", "نامشخص": "#95a5a6"}.get(q)
        tip = "آخرین اسکن شبکه: %s" % q
        if isinstance(kbps, (int, float)):
            tip += f" (~{kbps:.0f} کیلوبیت/ثانیه)"
        if isinstance(rtt, (int, float)):
            tip += f" (تاخیر {rtt:.0f}ms)"
        return f"  {icon}{q}", (color, tip)

    # (2.0.15-beta) اسکن پهنای باند/پایداری شبکه‌ی دوربین‌ها
    def _on_network_probe_clicked(self):
        """شروع اسکن پایداری شبکه‌ی همه‌ی دوربین‌ها در ترد پس‌زمینه (رابط
        قفل نمی‌شود)."""
        if getattr(self, "_net_probe_thread", None) is not None:
            return  # اسکن در حال اجراست
        cams = list(self.camera_store.cameras)
        if not cams:
            QMessageBox.information(self, "تست پایداری شبکه",
                                    "هنوز دوربینی ثبت نشده است.")
            return
        # نکته: اگر رمز دوربینی در حافظه نباشد (ذخیره‌ی امن خاموش و هنوز
        # وارد نشده)، نمونه‌گیری استریمش «نامشخص» می‌شود ولی تاخیر TCP
        # همچنان اندازه‌گیری می‌شود.
        from network_probe import NetworkProbeThread
        self._net_probe_thread = NetworkProbeThread(cams, self)
        self._net_probe_thread.probe_done.connect(self._on_probe_done)
        self._net_probe_thread.finished.connect(self._on_probe_finished)
        self.net_probe_btn.setEnabled(False)
        self.net_probe_btn.setText("📶 در حال اسکن...")
        self._net_probe_thread.start()

    def _on_probe_done(self, cam_id, res):
        cam = self.camera_store.get_camera(cam_id)
        if cam is None or not isinstance(res, dict):
            return
        cam["net_rtt_ms"] = res.get("rtt_ms")
        kbps = res.get("kbps")
        cam["net_kbps"] = round(kbps, 1) if isinstance(kbps, (int, float)) else None
        cam["net_quality"] = res.get("quality") or "نامشخص"
        cam["net_checked_at"] = res.get("checked_at")

    def _on_probe_finished(self):
        try:
            self.camera_store.save()
        except Exception:
            pass
        self._net_probe_thread = None
        self.net_probe_btn.setEnabled(True)
        self.net_probe_btn.setText("📶 تست پایداری شبکه")
        self.reload_camera_list()
        try:
            quals = [c.get("net_quality", "?") for c in self.camera_store.cameras]
            summary = ", ".join(f"{q}: {quals.count(q)}" for q in
                                ("خوب", "متوسط", "ضعیف", "قطع", "نامشخص")
                                if quals.count(q))
            QMessageBox.information(self, "تست پایداری شبکه",
                                    "اسکن تمام شد.\n" + (summary or "نتیجه‌ای ثبت نشد."))
        except Exception:
            pass

    def _make_camera_tree_item(self, cam):
        """(2.0.61-beta) ساخت آیتم درختی یک دوربین (مستقل یا کانال NVR) —
        بدون افزودن به والد؛ برای استفاده‌ی مشترک در حالت گروه‌دار/بدون‌گروه."""
        # رفع درخواست: در صورت شناسایی IP واقعی دوربین شبکه‌ای پشت این
        # کانال (متفاوت از IP خود NVR)، جلوی نام کانال هم نمایش داده می‌شود.
        cam_label = cam["name"]
        if cam.get("camera_ip"):
            cam_label += f"  ({cam['camera_ip']})"
        badge, style = self._net_badge(cam)
        cam_item = QTreeWidgetItem([cam_label + badge])
        if style:
            cam_item.setForeground(0, QColor(style[0]))
            cam_item.setToolTip(0, style[1])
        cam_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "camera", "id": cam["id"]})
        return cam_item

    def _make_nvr_tree_item(self, nvr):
        """(2.0.61-beta) ساخت آیتم درختی یک NVR همراه با کانال‌هایش."""
        channel_count = len(self.camera_store.cameras_for_nvr(nvr["id"]))
        nvr_item = QTreeWidgetItem([f"🖥 {nvr['name']}  ({nvr['ip']}) — {channel_count} کانال"])
        nvr_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "nvr", "id": nvr["id"]})
        for cam in self.camera_store.cameras_for_nvr(nvr["id"]):
            nvr_item.addChild(self._make_camera_tree_item(cam))
        nvr_item.setExpanded(True)
        return nvr_item

    def reload_camera_list(self):
        self.camera_list.clear()
        # پنل «هشدارهای حریق و دود» فقط وقتی دیده می‌شود که حداقل یک دوربین
        # تشخیص حریق فعال داشته باشد (با افزودن/حذف/ویرایش دوربین تازه می‌شود).
        self._refresh_fire_panel_visibility()

        def _group_of(x):
            return (x.get("group") or "").strip()

        def _add_item(item, parent):
            if parent is None:
                self.camera_list.addTopLevelItem(item)
            else:
                parent.addChild(item)

        # (2.0.61-beta) گروه‌بندی: هر گروه یک پوشه‌ی 📁 در سطح بالا می‌گیرد؛
        # کانال‌های NVR مثل قبل زیر NVR خودشان می‌مانند. آیتم‌های بدون گروه
        # دقیقاً مثل قبل در سطح بالا نمایش داده می‌شوند.
        groups = self.camera_store.get_groups()
        for g in groups:
            folder = QTreeWidgetItem([f"📁 {g}"])
            folder.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "name": g})
            self.camera_list.addTopLevelItem(folder)
            for nvr in self.camera_store.nvrs:
                if _group_of(nvr) == g:
                    _add_item(self._make_nvr_tree_item(nvr), folder)
            for cam in self.camera_store.standalone_cameras():
                if _group_of(cam) == g:
                    _add_item(self._make_camera_tree_item(cam), folder)
            folder.setExpanded(True)

        # NVRهای بدون گروه — مثل قبل در سطح بالا.
        for nvr in self.camera_store.nvrs:
            if not _group_of(nvr):
                _add_item(self._make_nvr_tree_item(nvr), None)

        # دوربین‌های مستقل (بدون NVR) و بدون گروه — مثل قبل در سطح بالا.
        for cam in self.camera_store.standalone_cameras():
            if not _group_of(cam):
                _add_item(self._make_camera_tree_item(cam), None)

    def _scan_credentials(self):
        """رفع درخواست: نام‌کاربری/رمز کادر بالای پنل اسکن شبکه را برمی‌گرداند
        (این مقادیر هرگز روی دیسک ذخیره نمی‌شوند - فقط در حافظه‌ی همین کادر)."""
        user = self.scan_user_input.text().strip() or "admin"
        pwd = self.scan_pass_input.text()
        return user, pwd

    def _auto_display_camera(self, cam) -> bool:
        """رفع درخواست: بعد از افزوده‌شدن هر دوربین، به‌صورت خودکار در اولین
        خانه‌ی خالی شبکه‌ی نمایش باز می‌شود. اگر رمز عبور لازم و نامعلوم باشد
        از کاربر پرسیده می‌شود؛ صرف‌نظر کردن از رمز به‌معنای «جای خالی نبود»
        نیست، پس True برمی‌گرداند (خطای واقعی فقط پر بودن شبکه‌ی نمایش است)."""
        if not self._ensure_password(cam):
            return True
        rtsp_url = self.camera_store.build_rtsp_url(cam)
        return self.camera_grid.assign_camera(cam, rtsp_url)

    def open_add_camera_dialog(self, prefill_ip=None, detected_path=None, detected_full_url=None,
                                prefill_user=None, prefill_pass=None):
        # گیت سهمیه‌ی «تعداد کل دوربین‌ها»
        try:
            from admin_quota import check_quota, quota_denied_message
            allowed, used, quota = check_quota("cameras", self.camera_store)
            if not allowed:
                QMessageBox.warning(
                    self, "قابلیت غیرفعال" if quota == -1 else "سهمیه تکمیل است",
                    quota_denied_message("cameras", used, quota))
                return
        except Exception:
            pass
        dialog = AddCameraDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user is not None:
            dialog.user_input.setText(prefill_user)
        if prefill_pass is not None:
            dialog.pass_input.setText(prefill_pass)
        if detected_path is not None or detected_full_url is not None:
            dialog.set_detected_stream(path=detected_path, full_url=detected_full_url)
        if dialog.exec():
            data = dialog.get_camera_data()
            cam = self.camera_store.add_camera(
                data["name"], data["ip"], data["port"], data["user"], data["pass"], data["path"],
                full_url=data.get("full_url"),
                floor_id=data.get("floor_id", ""),
                group=data.get("group", ""),
                bitrate_kbps=data.get("bitrate_kbps", 0),
            )
            self.reload_camera_list()
            # (2.0.68-beta) شناسایی خودکار PTZ/لنز موتورایزد در پس‌زمینه
            self._autodetect_ptz_async(cam["id"])
            # رفع درخواست: دوربین تازه‌اضافه‌شده اتوماتیک به پنجره‌ی نمایش اضافه شود.
            self.open_live_view(cam)

    def open_add_nvr_dialog(self, prefill_ip=None, detected_brand=None, detected_onvif_port=None,
                             prefill_user=None, prefill_pass=None):
        dialog = AddNVRDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user is not None:
            dialog.user_input.setText(prefill_user)
        if prefill_pass is not None:
            dialog.pass_input.setText(prefill_pass)
        if detected_brand:
            dialog.set_detected_brand(brand=detected_brand, onvif_port=detected_onvif_port)
        if dialog.exec():
            data = dialog.get_nvr_data()
            # گیت سهمیه‌ی «تعداد کل دوربین‌ها» برای کانال‌های انتخاب‌شده‌ی NVR
            try:
                from admin_quota import check_quota, quota_denied_message
                n_channels = len(dialog.get_selected_channels())
                allowed, used, quota = check_quota("cameras", self.camera_store,
                                                   count=n_channels)
                if not allowed:
                    QMessageBox.warning(
                        self,
                        "قابلیت غیرفعال" if quota == -1 else "سهمیه تکمیل است",
                        quota_denied_message("cameras", used, quota)
                        + f"\nکانال‌های انتخاب‌شده: {n_channels}")
                    return
            except Exception:
                pass
            nvr = self.camera_store.add_nvr(
                name=data["name"], ip=data["ip"], rtsp_port=data["rtsp_port"],
                onvif_port=data["onvif_port"], user=data["user"],
                pwd=data["pass"], brand=data["brand"],
                camera_brand=data["camera_brand"],
                group=data.get("group", ""),
                use_https=data.get("use_https", False),
            )
            added_cams = [
                self._add_channel_from_entry(nvr, entry, default_name)
                for entry, default_name in dialog.get_selected_channels()
            ]
            self.reload_camera_list()
            # رفع درخواست: هر کانال تازه‌اضافه‌شده اتوماتیک به پنجره‌ی نمایش اضافه شود.
            not_shown = sum(1 for cam in added_cams if not self._auto_display_camera(cam))
            msg = f"NVR «{nvr['name']}» با {len(added_cams)} کانال اضافه شد."
            if not_shown:
                msg += (
                    f"\n{not_shown} کانال به دلیل پر بودن شبکه‌ی نمایش به‌صورت خودکار باز "
                    "نشدند؛ برای باز کردن آن‌ها، تعداد نمایش هم‌زمان را افزایش دهید یا "
                    "روی آن‌ها در لیست دابل‌کلیک کنید."
                )
            QMessageBox.information(self, "NVR اضافه شد", msg)

    def _add_channel_from_entry(self, nvr, entry, default_name):
        camera_ip = entry.get("camera_ip") or ""
        if entry["is_full_url"]:
            # ONVIF: خودِ URL کشف‌شده معمولاً از قبل مستقیماً به IP دوربین
            # اشاره می‌کند (رجوع کنید به nvr_scanner._extract_camera_ip_from_uri).
            return self.camera_store.add_channel_camera(
                nvr, entry["channel"], default_name, path="", full_url=entry["path_or_url"],
                camera_ip=camera_ip,
            )
        if camera_ip and entry.get("direct"):
            # رفع درخواست: این کانال با یک مسیر واقعاً تست‌شده روی خودِ
            # دوربین تایید شده (نه پروکسی NVR که خطا می‌داد)؛ دقیقاً مثل
            # افزودن یک دوربین تکی، مستقیماً به همان IP/پورت ۵۵۴ دوربین وصل
            # می‌شویم - در حالی که کانال همچنان زیر همین NVR در لیست می‌ماند.
            return self.camera_store.add_channel_camera(
                nvr, entry["channel"], default_name, path=entry["path_or_url"],
                camera_ip=camera_ip, connect_ip=camera_ip, connect_port="554",
            )
        return self.camera_store.add_channel_camera(
            nvr, entry["channel"], default_name, path=entry["path_or_url"], camera_ip=camera_ip,
        )

    def rescan_nvr(self, nvr_id):
        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        dialog = AddNVRDialog(self)
        dialog.setWindowTitle(f"بازخوانی کانال‌های «{nvr['name']}»")
        dialog.name_input.setText(nvr["name"])
        dialog.ip_input.setText(nvr["ip"])
        dialog.rtsp_port_input.setText(str(nvr.get("rtsp_port", "554")))
        dialog.onvif_port_input.setText(str(nvr.get("onvif_port", "") or ""))
        dialog.user_input.setText(nvr.get("user", ""))
        dialog.pass_input.setText(nvr.get("pass", ""))
        idx = dialog.brand_combo.findData(nvr.get("brand", "auto"))
        if idx >= 0:
            dialog.brand_combo.setCurrentIndex(idx)
        cam_brand_idx = dialog.camera_brand_combo.findData(nvr.get("camera_brand", "auto"))
        if cam_brand_idx >= 0:
            dialog.camera_brand_combo.setCurrentIndex(cam_brand_idx)

        if dialog.exec():
            data = dialog.get_nvr_data()
            self.camera_store.update_nvr(nvr_id, **data)
            existing_channels = {c.get("channel") for c in self.camera_store.cameras_for_nvr(nvr_id)}
            added_cams = []
            for entry, default_name in dialog.get_selected_channels():
                if entry["channel"] in existing_channels:
                    continue  # این کانال قبلاً اضافه شده است
                added_cams.append(self._add_channel_from_entry(nvr, entry, default_name))
            self.reload_camera_list()
            # رفع درخواست: کانال‌های تازه‌کشف‌شده هم اتوماتیک به پنجره‌ی نمایش اضافه شوند.
            for cam in added_cams:
                self._auto_display_camera(cam)
            QMessageBox.information(self, "بازخوانی کامل شد", f"{len(added_cams)} کانال جدید اضافه شد.")

    def _save_audio_creds(self, cam_id, user, pwd):
        """(2.0.110-beta) ذخیره‌ی یوزر/پس جدیدی که کاربر برای صدا وارد کرد."""
        try:
            self.camera_store.update_camera(cam_id, **{"user": user, "pass": pwd})
        except Exception:
            pass

    def delete_nvr(self, nvr_id):
        confirm = QMessageBox.question(
            self, "تأیید حذف", "آیا از حذف این NVR و همه‌ی کانال‌های ثبت‌شده‌ی آن مطمئن هستید؟"
        )
        if confirm == QMessageBox.StandardButton.Yes:
            # (2.0.105-beta) قبل از حذف، شناسه‌ی دوربین‌های این NVR را نگه
            # می‌داریم تا خانه‌های لایوی که آن‌ها را نشان می‌دهند متوقف شوند.
            cam_ids = {c.get("id") for c in
                       self.camera_store.cameras_for_nvr(nvr_id)}
            self.camera_store.remove_nvr(nvr_id, cascade=True)
            # توقف خانه‌های لایو که دوربین‌های حذف‌شده را نشان می‌دادند.
            try:
                for slot in self.camera_grid.slots:
                    if slot.cam and slot.cam.get("id") in cam_ids:
                        slot.stop()
            except Exception:
                pass
            self.reload_camera_list()

    def edit_nvr_playback_template(self, nvr_id):
        """(2.0.52-beta) تنظیم قالب دلخواه آدرس RTSP «پخش بازبینی» برای NVRهایی
        که قالب‌های استاندارد Hikvision/Dahua را پشتیبانی نمی‌کنند."""
        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, \
            QDialogButtonBox
        from PyQt6.QtCore import Qt
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙ قالب آدرس پخش بازبینی")
        dlg.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        dlg.resize(560, 260)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"NVR: {nvr.get('name') or nvr.get('ip')}"))
        help_lbl = QLabel(
            "اگر پخش بازبینی (از صفحه‌ی گزارش‌ها) با خطا مواجه می‌شود، الگوی "
            "دقیق آدرس دستگاه‌تان را اینجا وارد کنید. متغیرهای مجاز:\n"
            "{ip} آدرس NVR، {port} پورت RTSP، {user} نام کاربری، {pass} رمز، "
            "{channel} شماره کانال،\n"
            "{start} و {end} زمان شروع/پایان (قالب Hikvision)، "
            "{start_dahua} و {end_dahua} (قالب Dahua).\n\n"
            "مثال Hikvision:\n"
            "rtsp://{ip}:{port}/Streaming/tracks/{channel}01?starttime={start}&endtime={end}\n\n"
            "خالی بگذارید تا فقط قالب‌های استاندارد امتحان شوند.")
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        lay.addWidget(help_lbl)
        edit = QLineEdit()
        edit.setText(nvr.get("playback_template") or "")
        edit.setPlaceholderText("rtsp://{ip}:{port}/...")
        lay.addWidget(edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("ذخیره")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec():
            self.camera_store.update_nvr(nvr_id, playback_template=edit.text().strip())
            QMessageBox.information(self, "ذخیره شد",
                                    "قالب آدرس پخش بازبینی ذخیره شد.")

    def open_nvr_storage(self, nvr_id):
        """رفع درخواست: وضعیت هارددیسک NVR و جست‌وجوی بازه‌های واقعاً
        ضبط‌شده روی آن را نشان می‌دهد (رجوع کنید به nvr_storage_dialog.py /
        nvr_storage_api.py). قبل از باز کردن دیالوگ، رمز NVR باید در حافظه
        موجود باشد (دقیقاً همان جریان _ensure_password که برای پخش زنده
        استفاده می‌شود، چون این قابلیت هم به یوزرنیم/رمز NVR نیاز دارد)."""
        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        if not nvr.get("pass"):
            pwd, ok = QInputDialog.getText(
                self, "رمز عبور مورد نیاز",
                f"برای بررسی هارد/ضبط‌های NVR «{nvr['name']}»، رمز عبور آن را وارد کنید:",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return
            nvr["pass"] = pwd
            for sibling in self.camera_store.cameras_for_nvr(nvr["id"]):
                sibling["pass"] = pwd

        channels = sorted(
            ((c.get("channel"), c.get("name", "")) for c in self.camera_store.cameras_for_nvr(nvr_id)
             if c.get("channel") is not None),
            key=lambda x: x[0],
        )
        dialog = NVRStorageDialog(nvr, channels, self)
        dialog.exec()

    def open_nvr_webview(self, nvr_id):
        """رفع درخواست: باز کردن پنل وب واقعی NVR داخل برنامه (با موتور
        Chromium از طریق PyQt6-WebEngine)، برای دستگاه‌هایی که سرویس RTSP
        آن‌ها به‌دلیل باگ فریمور با کلاینت‌های استاندارد (این برنامه، VLC،
        live555) کار نمی‌کند. این پنل هم پخش زنده‌ی سالم را ممکن می‌کند و
        هم لیست واقعی کانال‌های متصل به NVR را (با دکمه‌ی «دریافت لیست
        کانال‌ها») مستقیماً از خودِ NVR می‌گیرد."""
        if not _WEBENGINE_AVAILABLE:
            QMessageBox.warning(
                self, "بسته‌ی موردنیاز نصب نیست",
                "برای این قابلیت باید بسته‌ی PyQt6-WebEngine نصب باشد:\n\n"
                "pip install PyQt6-WebEngine\n\n"
                "بعد از نصب، برنامه را دوباره اجرا کنید."
            )
            return
        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        dialog = NVRWebViewDialog(nvr, self)
        dialog.channels_fetched.connect(lambda ch_list: self._on_nvr_channels_fetched(nvr, ch_list))
        dialog.exec()

    def _on_nvr_channels_fetched(self, nvr, ch_list):
        """کانال‌هایی که از پنل وب NVR دریافت شده‌اند (via g_deviceList) را،
        در صورت تأیید کاربر، به لیست دوربین‌های زیر همین NVR اضافه می‌کند.

        رفع درخواست: هر کانالی که IP واقعی دوربین شبکه‌ای پشت آن هم از پنل وب
        NVR شناسایی شده باشد (نه فقط شماره کانال)، دقیقاً مثل افزودن یک
        دوربین تکی مستقیماً به همان IP دوربین (نه IP خودِ NVR) و با همان
        یوزرنیم/رمزی که برای این NVR در برنامه ثبت شده وصل می‌شود؛ کانال
        همچنان زیر همین NVR در لیست گروه‌بندی می‌ماند. برای کانال‌هایی که
        IP دوربینشان مشخص نشده (مثلاً کانال آنالوگ)، مثل قبل از طریق خودِ
        NVR اضافه می‌شوند.
        """
        if not ch_list:
            return
        existing_channels = {c.get("channel") for c in self.camera_store.cameras_for_nvr(nvr["id"])}
        new_entries = []
        for i, dev in enumerate(ch_list):
            # ساختار دقیق آیتم‌های g_deviceList ممکن است بسته به مدل کمی
            # فرق کند؛ چند نام کلید رایج را امتحان می‌کنیم.
            chn = dev.get("chn") or dev.get("channel") or (i + 1)
            if chn in existing_channels:
                continue
            name = dev.get("name") or dev.get("dev_name") or f"کانال {chn}"
            # رفع درخواست: g_deviceList معمولاً IP واقعی دوربین شبکه‌ای متصل
            # به این کانال را هم دارد (کلیدهای رایج: ip / IP / ipAddress)؛
            # اگر موجود باشد همراه با نام/شماره کانال ثبت می‌شود.
            cam_ip = dev.get("ip") or dev.get("IP") or dev.get("ipAddress") or ""
            new_entries.append((chn, name, cam_ip))

        if not new_entries:
            QMessageBox.information(self, "چیزی برای افزودن نیست",
                                     "همه‌ی این کانال‌ها قبلاً به لیست شما اضافه شده‌اند.")
            return

        # (2.0.24-beta) به‌جای Yes/No برای همه‌ی کانال‌ها، کاربر تیک می‌زند
        # کدام کانال‌ها واقعاً اضافه و متصل شوند.
        dlg = NVRChannelSelectDialog(
            nvr.get("name") or nvr.get("ip") or "", new_entries, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dlg.selected()
        if not chosen:
            QMessageBox.information(self, "انصراف",
                                    "هیچ کانالی انتخاب نشد؛ چیزی اضافه نشد.")
            return
        new_entries = chosen

        self.statusBar().showMessage("در حال بررسی مسیر اتصال مستقیم دوربین‌ها...")
        self._direct_probe_thread = DirectCameraProbeThread(
            new_entries, nvr.get("user", ""), nvr.get("pass", ""), nvr.get("rtsp_port", "554"),
        )
        self._direct_probe_thread.progress_signal.connect(self.statusBar().showMessage)
        self._direct_probe_thread.finished_signal.connect(
            lambda results: self._on_direct_probe_finished(nvr, results)
        )
        self._direct_probe_thread.start()

    def _on_direct_probe_finished(self, nvr, results):
        """رفع درخواست: بعد از پیدا شدن (یا نشدن) مسیر مستقیم RTSP هر دوربین
        (در ترد جدا - نگاه کنید به DirectCameraProbeThread)، کانال‌ها ثبت
        می‌شوند: کانال‌های دارای IP دوربین با اتصال مستقیم به آن IP (پورت
        ۵۵۴ خودِ دوربین + یوزرنیم/رمز همین NVR)، و بقیه مثل قبل از طریق NVR."""
        self.statusBar().clearMessage()
        added_cams = []
        for r in results:
            if r["cam_ip"]:
                cam = self.camera_store.add_channel_camera(
                    nvr, r["chn"], r["name"], path=r["path"] or "",
                    camera_ip=r["cam_ip"], connect_ip=r["cam_ip"], connect_port="554",
                )
            else:
                cam = self.camera_store.add_channel_camera(nvr, r["chn"], r["name"], path="")
            added_cams.append(cam)

        self.reload_camera_list()
        # رفع درخواست: هر کانال تازه‌اضافه‌شده اتوماتیک به پنجره‌ی نمایش اضافه شود.
        not_shown = sum(1 for cam in added_cams if not self._auto_display_camera(cam))
        no_path_found = sum(1 for r in results if r["cam_ip"] and not r["path"])
        msg = f"{len(added_cams)} کانال اضافه شد."
        if no_path_found:
            msg += (
                f"\nبرای {no_path_found} دوربین، مسیر RTSP به‌طور خودکار پیدا نشد؛ "
                "برای این‌ها روی «ویرایش» کلیک کنید و مسیر را دستی وارد یا با "
                "«تشخیص خودکار» پیدا کنید."
            )
        if not_shown:
            msg += (
                f"\n{not_shown} کانال به دلیل پر بودن شبکه‌ی نمایش به‌صورت خودکار باز "
                "نشدند؛ برای باز کردن آن‌ها، تعداد نمایش هم‌زمان را افزایش دهید یا "
                "روی آن‌ها در لیست دابل‌کلیک کنید."
            )
        QMessageBox.information(self, "افزودن کامل شد", msg)

    def show_camera_context_menu(self, pos):
        item = self.camera_list.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        menu = QMenu(self)
        if data["type"] == "camera":
            edit_action = QAction("ویرایش", self)
            edit_action.triggered.connect(lambda: self.edit_camera(data["id"]))
            # (2.0.61-beta) گروه‌بندی
            group_action = QAction("📁 انتقال به گروه…", self)
            group_action.triggered.connect(lambda: self.move_camera_to_group(data["id"]))
            delete_action = QAction("حذف", self)
            delete_action.triggered.connect(lambda: self.delete_camera(data["id"]))
            # (2.0.68-beta) کنترل PTZ/لنز موتورایزد
            ptz_action = QAction("🎮 کنترل PTZ", self)
            ptz_action.triggered.connect(lambda: self.open_ptz_control(data["id"]))
            # (2.0.82-beta) شنیدن صدای دوربین (ترک صوتی RTSP)
            listen_action = QAction("🎧 شنیدن صدای دوربین", self)
            listen_action.triggered.connect(lambda: self.open_listen_dialog(data["id"]))
            menu.addAction(edit_action)
            menu.addAction(ptz_action)
            menu.addAction(listen_action)
            menu.addAction(group_action)
            menu.addAction(delete_action)
        elif data["type"] == "group":
            # (2.0.61-beta) گروه‌بندی: مدیریت پوشه‌ی گروه
            rename_action = QAction("✏ تغییر نام گروه", self)
            rename_action.triggered.connect(lambda: self.rename_camera_group(data["name"]))
            delete_group_action = QAction("🗑 حذف گروه", self)
            delete_group_action.triggered.connect(lambda: self.delete_camera_group(data["name"]))
            menu.addAction(rename_action)
            menu.addAction(delete_group_action)
        else:  # nvr
            rescan_action = QAction("بازخوانی کانال‌ها", self)
            rescan_action.triggered.connect(lambda: self.rescan_nvr(data["id"]))
            # رفع درخواست: برای NVRهایی که سرویس RTSP‌شان مشکل فریمور دارد
            # (پخش زنده و بازخوانی کانال از طریق RTSP/ONVIF جواب نمی‌دهد)،
            # یک راه جایگزین: باز کردن پنل وب واقعی خود NVR داخل برنامه (با
            # موتور Chromium) که هم پخش زنده در آن سالم کار می‌کند و هم
            # می‌توان لیست کانال‌های واقعی را از همان‌جا گرفت.
            webview_action = QAction("باز کردن پنل وب NVR (برای دستگاه‌های با RTSP خراب)", self)
            webview_action.triggered.connect(lambda: self.open_nvr_webview(data["id"]))
            # رفع درخواست: بررسی وضعیت هارددیسک NVR و جست‌وجوی بازه‌های
            # زمانی‌ای که واقعاً روی هارد خودِ NVR ضبط شده - از طریق همان
            # API وب رسمی سازنده (رجوع کنید به nvr_storage_api.py).
            storage_action = QAction("🗄 هارد و ضبط‌های NVR", self)
            storage_action.triggered.connect(lambda: self.open_nvr_storage(data["id"]))
            # (2.0.52-beta) قالب دلخواه آدرس پخش بازبینی: برای NVRهایی که
            # قالب استاندارد Hikvision/Dahua را پشتیبانی نمی‌کنند.
            playback_tpl_action = QAction("⚙ قالب آدرس پخش بازبینی", self)
            playback_tpl_action.triggered.connect(
                lambda: self.edit_nvr_playback_template(data["id"]))
            delete_action = QAction("حذف NVR و همه کانال‌ها", self)
            delete_action.triggered.connect(lambda: self.delete_nvr(data["id"]))
            # (2.0.61-beta) گروه‌بندی
            nvr_group_action = QAction("📁 انتقال به گروه…", self)
            nvr_group_action.triggered.connect(lambda: self.move_nvr_to_group(data["id"]))
            menu.addAction(rescan_action)
            menu.addAction(webview_action)
            menu.addAction(storage_action)
            menu.addAction(playback_tpl_action)
            menu.addAction(nvr_group_action)
            menu.addAction(delete_action)
        menu.exec(self.camera_list.mapToGlobal(pos))

    def _ask_group_name(self, current=""):
        """(2.0.61-beta) دیالوگ انتخاب/ساخت گروه؛ برمی‌گرداند: نام گروه،
        "" (بدون گروه)، یا None اگر انصراف داده شد."""
        groups = self.camera_store.get_groups()
        items = ["— (بدون گروه)"] + groups
        idx = items.index(current) if current in items else 0
        text, ok = QInputDialog.getItem(
            self, "گروه‌بندی دوربین‌ها",
            "گروه را انتخاب کنید یا نام جدیدی تایپ کنید:",
            items, idx, True)
        if not ok:
            return None
        text = (text or "").strip()
        if text == "— (بدون گروه)":
            return ""
        return text

    def move_camera_to_group(self, cam_id):
        """(2.0.61-beta) انتقال یک دوربین به گروه انتخابی."""
        cam = self.camera_store.get_camera(cam_id)
        if not cam:
            return
        g = self._ask_group_name((cam.get("group") or "").strip())
        if g is None:
            return
        self.camera_store.update_camera(cam_id, group=g)
        self.reload_camera_list()

    def move_nvr_to_group(self, nvr_id):
        """(2.0.61-beta) انتقال یک NVR (با کانال‌هایش) به گروه انتخابی."""
        nvr = self.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        g = self._ask_group_name((nvr.get("group") or "").strip())
        if g is None:
            return
        self.camera_store.update_nvr(nvr_id, group=g)
        self.reload_camera_list()

    def rename_camera_group(self, old_name):
        """(2.0.61-beta) تغییر نام یک گروه در همه‌ی اعضا."""
        text, ok = QInputDialog.getText(
            self, "تغییر نام گروه", "نام جدید گروه:", text=old_name)
        if not ok:
            return
        text = (text or "").strip()
        if not text or text == old_name:
            return
        if text in self.camera_store.get_groups():
            QMessageBox.warning(self, "گروه تکراری",
                                f"گروه «{text}» از قبل وجود دارد.")
            return
        self.camera_store.rename_group(old_name, text)
        self.reload_camera_list()

    def delete_camera_group(self, name):
        """(2.0.61-beta) حذف گروه؛ اعضا حذف نمی‌شوند، بدون گروه می‌شوند."""
        confirm = QMessageBox.question(
            self, "حذف گروه",
            f"گروه «{name}» حذف شود؟\n"
            "دوربین‌ها و NVRهای آن حذف نمی‌شوند؛ بدون گروه می‌شوند.")
        if confirm == QMessageBox.StandardButton.Yes:
            self.camera_store.clear_group(name)
            self.reload_camera_list()

    def edit_camera(self, cam_id):
        cam = self.camera_store.get_camera(cam_id)
        if not cam:
            return
        dialog = AddCameraDialog(self, existing_cam=cam)
        if dialog.exec():
            data = dialog.get_camera_data()
            self.camera_store.update_camera(cam_id, **data)
            self.reload_camera_list()

    def delete_camera(self, cam_id):
        confirm = QMessageBox.question(self, "تأیید حذف", "آیا از حذف این دوربین از لیست مطمئن هستید؟")
        if confirm == QMessageBox.StandardButton.Yes:
            self.camera_store.remove_camera(cam_id)
            self.reload_camera_list()

    def on_camera_item_activated(self, item, column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data["type"] != "camera":
            return
        cam = self.camera_store.get_camera(data["id"])
        if cam:
            self.open_live_view(cam)

    def on_scan_result_selected(self, item):
        text = item.text()
        if " " not in text:
            return
        ip = text.split(" ")[0]
        # رفع باگ: ردیف‌های موقت («در حال اسکن...» / «هیچ دستگاهی یافت نشد.»)
        # IP واقعی ندارند؛ فقط IPهایی که واقعاً در اسکن پیدا شده‌اند پردازش
        # می‌شوند.
        if ip not in self._scan_ports_by_ip:
            return

        if self.detect_thread is not None and self.detect_thread.isRunning():
            return  # یک تشخیص در حال اجراست؛ منتظر پایان آن بمانیم.

        # دابل‌کلیک یعنی فقط همین یک دستگاه (صف خالی است).
        self._detect_queue = []
        self._start_device_detect(ip)

    def on_add_selected_scan_results(self):
        """رفع درخواست: چند نتیجه‌ی اسکن هم‌زمان انتخاب و پشت‌سرهم اضافه شوند.
        چون هر تشخیص (و در ادامه‌ی آن، دیالوگ افزودن دوربین/NVR) نیاز به
        تعامل کاربر دارد، دستگاه‌ها یکی‌یکی (نه هم‌زمان) پردازش می‌شوند: بعد
        از بسته‌شدن دیالوگ مربوط به هر دستگاه، خودکار سراغ دستگاه بعدی در صف
        می‌رود."""
        if self.detect_thread is not None and self.detect_thread.isRunning():
            return

        ips = []
        # رفع درخواست: انتخاب چندتایی دیگر با Ctrl/Shift نیست؛ آیتم‌هایی که
        # چک‌باکس‌شان تیک خورده (Qt.CheckState.Checked) به‌عنوان انتخاب‌شده
        # در نظر گرفته می‌شوند.
        for i in range(self.scan_result_list.count()):
            item = self.scan_result_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            text = item.text()
            if " " not in text:
                continue
            ip = text.split(" ")[0]
            if ip not in self._scan_ports_by_ip:
                continue
            if ip not in ips:
                ips.append(ip)

        if not ips:
            QMessageBox.information(
                self, "موردی انتخاب نشده",
                "ابتدا چک‌باکس کنار یک یا چند دستگاه را از لیست نتایج اسکن تیک بزنید."
            )
            return

        self._detect_queue = ips[1:]
        self._start_device_detect(ips[0])

    def _start_device_detect(self, ip):
        # رفع درخواست: دیگر از کاربر «دوربین تکی یا NVR؟» پرسیده نمی‌شود؛
        # DeviceDetectThread با یک اتصال آزمایشی (ONVIF یا تست کانال‌ها)
        # خودش نوع دستگاه را تشخیص می‌دهد - رجوع کنید به device_detect.py.
        self._detect_ip = ip
        self.scan_result_list.setEnabled(False)
        self.add_selected_scan_btn.setEnabled(False)
        self.detect_status_label.setText(f"در حال تشخیص نوع دستگاه {ip}...")

        # رفع درخواست: تشخیص نوع دستگاه (و در ادامه، اتصال به دوربین/NVR) با
        # نام‌کاربری/رمز کادر بالای پنل اسکن شبکه انجام می‌شود، نه مقدار ثابت
        # admin/بدون‌رمز.
        scan_user, scan_pass = self._scan_credentials()
        self.detect_thread = DeviceDetectThread(
            ip=ip,
            open_ports=self._scan_ports_by_ip.get(ip, []),
            rtsp_port="554",
            user=scan_user,
            pwd=scan_pass,
            parent=self,
        )
        self.detect_thread.progress_signal.connect(self.detect_status_label.setText)
        self.detect_thread.detected_signal.connect(self._on_device_detected)
        self.detect_thread.failed_signal.connect(self._on_device_detect_failed)
        self.detect_thread.start()

    def _reset_detect_ui(self):
        self.scan_result_list.setEnabled(True)
        self.detect_status_label.setText("")

    def _advance_detect_queue(self):
        """بعد از بسته‌شدن دیالوگ دستگاه فعلی، اگر مورد دیگری در صفِ انتخاب
        چندتایی باقی مانده، تشخیص آن را شروع می‌کند."""
        if self._detect_queue:
            next_ip = self._detect_queue.pop(0)
            self._start_device_detect(next_ip)
        else:
            self.add_selected_scan_btn.setEnabled(True)

    def _on_device_detected(self, kind, payload):
        ip = self._detect_ip
        self._reset_detect_ui()
        scan_user, scan_pass = self._scan_credentials()
        if kind == "nvr":
            self.open_add_nvr_dialog(
                prefill_ip=ip,
                detected_brand=payload.get("brand"),
                detected_onvif_port=payload.get("onvif_port"),
                prefill_user=scan_user,
                prefill_pass=scan_pass,
            )
        else:
            self.open_add_camera_dialog(
                prefill_ip=ip,
                detected_path=payload.get("path"),
                detected_full_url=payload.get("full_url"),
                prefill_user=scan_user,
                prefill_pass=scan_pass,
            )
        self._advance_detect_queue()

    def _on_device_detect_failed(self, msg):
        ip = self._detect_ip
        self._reset_detect_ui()

        # تشخیص خودکار با نام کاربری/رمز پیش‌فرض (admin/بدون رمز) ممکن است روی
        # دستگاه‌هایی با اطلاعات ورود سفارشی شکست بخورد؛ در این حالت کاربر
        # می‌تواند به‌صورت دستی و با وارد کردن رمز درست، نوع دستگاه را انتخاب کند.
        box = QMessageBox(self)
        box.setWindowTitle("تشخیص خودکار ناموفق بود")
        box.setText(f"{msg}\n\nنوع دستگاه {ip} را به‌صورت دستی مشخص کنید:")
        camera_btn = box.addButton("دوربین تکی", QMessageBox.ButtonRole.AcceptRole)
        nvr_btn = box.addButton("NVR (چند کاناله)", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("انصراف", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        scan_user, scan_pass = self._scan_credentials()
        if box.clickedButton() == camera_btn:
            self.open_add_camera_dialog(prefill_ip=ip, prefill_user=scan_user, prefill_pass=scan_pass)
        elif box.clickedButton() == nvr_btn:
            self.open_add_nvr_dialog(prefill_ip=ip, prefill_user=scan_user, prefill_pass=scan_pass)
        self._advance_detect_queue()

    # ------------------------------------------------------ live view -----

    # (2.0.68-beta) کنترل PTZ/لنز موتورایزد --------------------------------
    def open_ptz_control(self, cam_id):
        """باز کردن دیالوگ کنترل PTZ دوربین (شناسایی خودکار در صورت نیاز)."""
        from ptz_dialog import PTZDialog
        cam = self.camera_store.get_camera(cam_id)
        if not cam:
            return
        def _save_info(info):
            self.camera_store.update_camera(cam_id, ptz=info)
        dlg = PTZDialog(cam, on_detected=_save_info, parent=self)
        dlg.exec()

    # (2.0.82-beta) شنیدن صدای دوربین (ترک صوتی RTSP) -----------------------
    def open_listen_dialog(self, cam_id):
        """باز کردن دیالوگ «شنیدن صدای دوربین» (دریافت صدای دوربین)."""
        from listen_dialog import ListenDialog
        cam = self.camera_store.get_camera(cam_id)
        if not cam:
            return
        # اگر کانال NVR است، IP و پورت RTSP خودِ NVR را هم به رکورد اضافه
        # می‌کنیم تا «شنیدن صدا» بتواند پروکسی NVR را امتحان کند (حتی اگر
        # کانال با «اتصال مستقیم» به IP دوربین ثبت شده باشد)
        nvr_id = cam.get("nvr_id")
        if nvr_id:
            nvr = self.camera_store.get_nvr(nvr_id)
            if nvr and nvr.get("ip"):
                cam = dict(cam)
                cam["_nvr_ip"] = nvr.get("ip")
                cam["_nvr_rtsp_port"] = nvr.get("rtsp_port") or 554
                cam["_nvr_user"] = nvr.get("user", "")
                cam["_nvr_pass"] = nvr.get("pass", "")
        dlg = ListenDialog(
            cam, parent=self,
            on_url_found=lambda url: self.camera_store.update_camera(
                cam_id, audio_url=url))
        dlg.exec()

    def _autodetect_ptz_async(self, cam_id):
        """شناسایی PTZ در ترد جدا پس از افزودن دوربین؛ نتیجه در رکورد ذخیره می‌شود."""
        import threading
        from ptz_control import detect_ptz_support, describe_support

        def _job():
            cam = self.camera_store.get_camera(cam_id)
            if not cam:
                return
            try:
                info = detect_ptz_support(cam)
            except Exception:
                return
            self.camera_store.update_camera(cam_id, ptz=info)
            if info.get("supported"):
                try:
                    self.statusBar().showMessage(
                        f"🎮 {cam.get('name') or cam.get('ip')}: {describe_support(info)}",
                        8000)
                except Exception:
                    pass
        threading.Thread(target=_job, daemon=True).start()

    def open_live_view(self, cam: dict):
        """دوربین انتخاب‌شده را در اولین خانه‌ی خالی شبکه‌ی نمایش باز می‌کند.
        اگر همان دوربین از قبل باز باشد، فقط همان خانه انتخاب (highlight)
        می‌شود. اگر هیچ خانه‌ی خالی نباشد، از کاربر می‌خواهد یکی را ببندد یا
        تعداد نمایش هم‌زمان را افزایش دهد."""
        if not self._ensure_password(cam):
            return  # کاربر از وارد کردن رمز صرف‌نظر کرد

        rtsp_url = self.camera_store.build_rtsp_url(cam)
        ok = self.camera_grid.assign_camera(cam, rtsp_url)
        if not ok:
            QMessageBox.information(
                self, "جایی خالی نیست",
                "همه‌ی خانه‌های شبکه‌ی نمایش پر است. ابتدا یکی را ببندید یا "
                "تعداد نمایش هم‌زمان را از بالای شبکه افزایش دهید."
            )

    def _ensure_password(self, cam: dict) -> bool:
        """(2.0.15-beta) رمز دوربین/NVR: اگر ذخیره‌ی امن فعال باشد، رمز از
        دیسک (رمزنگاری‌شده با DPAPI) خوانده شده و این‌جا از قبل در حافظه
        هست؛ در غیر این صورت (یا اگر رمزی ذخیره نشده باشد) از کاربر پرسیده
        می‌شود. رمز تازه‌واردشده بلافاصله رمزنگاری و ذخیره می‌شود تا در
        اجراهای بعدی پرسیده نشود (مگر این‌که کاربر ذخیره‌ی امن را خاموش
        کرده باشد). رمز واردشده فقط در حافظه (تا زمان بستن برنامه)
        نگه‌داشته می‌شود تا برای بقیه‌ی کانال‌های همان NVR در همین نشست
        دوباره پرسیده نشود."""
        nvr = self.camera_store.get_nvr(cam.get("nvr_id")) if cam.get("nvr_id") else None
        source = nvr if nvr is not None else cam

        if source.get("pass"):
            if nvr is not None and not cam.get("pass"):
                cam["pass"] = nvr["pass"]
            return True

        label = f"NVR «{source['name']}»" if nvr is not None else f"دوربین «{source['name']}»"
        pwd, ok = QInputDialog.getText(
            self, "رمز عبور مورد نیاز",
            f"برای اتصال، رمز عبور {label} را وارد کنید:",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return False

        source["pass"] = pwd
        if nvr is not None:
            # رمز بین همه‌ی کانال‌های همین NVR مشترک است؛ برای جلوگیری از
            # پرسیدن دوباره در همین نشست، روی همه‌ی آن‌ها هم اعمال می‌شود.
            for sibling in self.camera_store.cameras_for_nvr(nvr["id"]):
                sibling["pass"] = pwd
        # (2.0.15-beta) بلافاصله رمزنگاری و ذخیره شود تا در اجرای بعدی
        # پرسیده نشود (اگر ذخیره‌ی امن خاموش باشد، save رمز را نمی‌نویسد).
        try:
            self.camera_store.save()
            self.camera_store.save_nvrs()
        except Exception:
            pass
        return True

    def get_active_camera_frame(self):
        return self.camera_grid.get_selected_frame()

    def on_camera_dropped_on_grid(self, cam_id, slot_index):
        """رفع درخواست: وقتی کاربر یک دوربین را از لیست «دوربین‌ها و NVRهای
        من» گرفته و روی یک خانه از شبکه‌ی نمایش رها کند، همان‌جا باز می‌شود."""
        cam = self.camera_store.get_camera(cam_id)
        if not cam:
            return
        if not self._ensure_password(cam):
            return
        rtsp_url = self.camera_store.build_rtsp_url(cam)
        self.camera_grid.assign_camera_to_slot(cam, rtsp_url, slot_index)

    # ------------------------------------------------------ face library ---

    # --------------------------------------------------- ناوبری هدر/صفحه‌ها --

    def _build_header(self):
        """هدر بالای برنامه: دکمه‌های ناوبری بین صفحه‌ی اصلی (دوربین‌ها) و
        شش صفحه‌ی جداگانه‌ی «اعلام حریق»، «چهره‌ها»، «گزارش‌ها»،
        «پلاک‌خوان»، «ردیابی اشخاص» و «نقشه ساختمان»."""
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)

        # لوگوی برنامه در هدر: سپر + نام «IAS Viewer».
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        logo_label = QLabel()
        _logo_pix = QPixmap(LOGO_SHIELD)
        if not _logo_pix.isNull():
            logo_label.setPixmap(
                _logo_pix.scaledToHeight(42, Qt.TransformationMode.SmoothTransformation)
            )
        brand_row.addWidget(logo_label)
        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        name_col.setContentsMargins(0, 0, 0, 0)
        fa_name = QLabel(APP_NAME_FA)
        fa_name.setStyleSheet("font-size: 16px; font-weight: bold;")
        en_name = QLabel(APP_NAME_EN)
        en_name.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; letter-spacing: 3px;"
        )
        name_col.addWidget(fa_name)
        name_col.addWidget(en_name)
        brand_row.addLayout(name_col)
        header_layout.addLayout(brand_row)
        header_layout.addStretch()

        self.nav_buttons = {}
        for key, label in (
            ("home", "🏠 صفحه اصلی"),
            ("fire", "🔥 اعلام حریق"),
            ("face", "👤 چهره‌ها"),
            ("reports", "📊 گزارش‌ها"),
            ("plate", "🚗 پلاک‌خوان"),
            ("person", "👥 ردیابی اشخاص"),
            ("map", "🗺 نقشه ساختمان"),
            ("settings", "⚙️ تنظیمات"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{padding: 6px 14px; border-radius: 6px; font-size: 12px;}"
                f"QPushButton:checked{{background: {LOGO_BLUE}; color: white; font-weight: bold;}}"
            )
            btn.clicked.connect(lambda _checked=False, _key=key: self.show_page(_key))
            header_layout.addWidget(btn)
            self.nav_buttons[key] = btn
        # (2.0.53-beta) دکمه‌ی «راهنما»: در همه‌ی صفحه‌ها دیده می‌شود و
        # کاتالوگ PDF راهنما را روی صفحه‌ی مربوط به همان صفحه‌ی فعلی باز
        # می‌کند (رجوع کنید به app_help.py).
        help_btn = QPushButton("❓ راهنما")
        help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        help_btn.setStyleSheet(
            "QPushButton{padding: 6px 14px; border-radius: 6px; font-size: 12px;}"
        )
        help_btn.clicked.connect(self.open_help)
        header_layout.addWidget(help_btn)
        # نشان نسخه
        ver_label = QLabel(f"v{self.app_version}")
        ver_label.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; padding: 4px 8px;"
        )
        header_layout.addWidget(ver_label)
        # (2.0.72-beta) دکمه‌ی «آپدیت جدید» — فقط برای ادمین، فقط وقتی
        # Release جدیدی با فایل آپدیت پیدا شود (پیش‌فرض مخفی).
        self.header_update_btn = QPushButton("⬆️ آپدیت جدید!")
        self.header_update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_update_btn.setVisible(False)
        self.header_update_btn.setStyleSheet(
            "QPushButton{padding: 6px 14px; border-radius: 6px; font-size: 12px; "
            "background: #b8860b; color: white; font-weight: bold;}"
        )
        self.header_update_btn.clicked.connect(self._on_header_update_clicked)
        header_layout.addWidget(self.header_update_btn)
        # (2.0.72-beta) کاربر جاری + دکمه‌ی قفل (تعویض کاربر)
        self.user_label = QLabel()
        self.user_label.setStyleSheet(
            f"font-size: 11px; color: {TEXT_MUTED}; padding: 4px 8px;"
        )
        header_layout.addWidget(self.user_label)
        self.lock_btn = QPushButton("🔒 قفل")
        self.lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_btn.setStyleSheet(
            "QPushButton{padding: 6px 14px; border-radius: 6px; font-size: 12px;}"
        )
        self.lock_btn.setToolTip("قفل برنامه و ورود با کاربر دیگر")
        self.lock_btn.clicked.connect(self._on_lock_clicked)
        header_layout.addWidget(self.lock_btn)
        return header

    def _on_apply_update(self):
        """باز کردن دیالوگ انتخاب و اعمال «فایل آپدیت» (فقط ادمین)."""
        if not self._is_admin():
            QMessageBox.warning(self, "دسترسی",
                                "اعمال آپدیت فقط برای ادمین مجاز است.")
            return
        try:
            from updater import show_apply_update_dialog
            show_apply_update_dialog(parent=self)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن دیالوگ آپدیت ممکن نشد:\n{e}")

    def _on_alarm_sound_changed(self):
        """تازه‌سازی تنظیمات صدا در پخش‌کننده‌ی آژیر حریق."""
        try:
            if getattr(self, "alarm_player", None) is not None:
                self.alarm_player.reload()
        except Exception:
            pass

    def show_page(self, key):
        """تغییر صفحه‌ی فعال از طریق هدر؛ هر صفحه هنگام نمایش، داده‌هایش را
        با متد refresh خودش تازه می‌کند."""
        # (2.0.72-beta) دفاع عمقی سطح دسترسی: حتی اگر دکمه‌ای به‌خطا دیده
        # شود، صفحه‌ی بدون دسترسی باز نمی‌شود.
        if not self._has_access(key):
            return
        # (2.0.53-beta) کلید صفحه‌ی فعلی برای دکمه‌ی «راهنما» نگه داشته
        # می‌شود تا PDF روی صفحه‌ی مربوط به همین صفحه باز شود.
        self._current_page_key = key
        index = {"home": 0, "fire": 1, "face": 2, "reports": 3, "plate": 4,
                 "person": 5, "map": 6, "settings": 7}[key]
        if key == "map" and self.map_page is None:
            QMessageBox.warning(
                self, "صفحه‌ی نقشه در دسترس نیست",
                "فایل building_map_dialog.py (و building_map.py) کنار برنامه "
                "پیدا نشد؛ آن‌ها را کنار main.py بگذارید و دوباره اجرا کنید.")
            return
        page = self.pages.widget(index)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
        if key == "person":
            # بنر وضعیت موتور تشخیص شخص را هم تازه کن (رجوع کنید به
            # _refresh_person_detector_status).
            try:
                self._refresh_person_detector_status()
            except Exception:
                pass
        self.pages.setCurrentIndex(index)
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)

    # ================= (2.0.72-beta) کاربران، سطح دسترسی، آپدیت خودکار ======
    def _has_access(self, page_key):
        """آیا کاربر جاری به این صفحه دسترسی دارد؟ (None = حالت توسعه/تست)"""
        if self.current_user is None:
            return True
        try:
            from user_manager import UserManager
            return UserManager.can_access(self.current_user, page_key)
        except Exception:
            return True

    def _is_admin(self):
        if self.current_user is None:
            return True  # حالت توسعه/تست
        try:
            from user_manager import UserManager
            return UserManager.is_admin(self.current_user)
        except Exception:
            return True

    def apply_user_permissions(self):
        """اعمال سطح دسترسی کاربر جاری روی هدر، صفحه‌ها و دکمه‌ها."""
        try:
            user = self.current_user
            for key, btn in self.nav_buttons.items():
                btn.setVisible(self._has_access(key))
            is_admin = self._is_admin()
            try:
                self.settings_page.set_admin_mode(is_admin)
            except Exception:
                pass
            if user is not None:
                star = "⭐ " if is_admin else ""
                self.user_label.setText(f"👤 {star}{user.get('username', '')}")
                self.user_label.setVisible(True)
                self.lock_btn.setVisible(True)
            else:
                self.user_label.setVisible(False)
                self.lock_btn.setVisible(False)
            allowed = [k for k in ("home", "fire", "face", "reports", "plate",
                                   "person", "map", "settings")
                       if self._has_access(k)]
            if allowed and getattr(self, "_current_page_key", "home") not in allowed:
                self.show_page(allowed[0])
            if not is_admin:
                self.header_update_btn.setVisible(False)
                self._hide_update_toast()
        except Exception:
            pass

    def _on_lock_clicked(self):
        """قفل برنامه و ورود با کاربر دیگر (بدون بستن برنامه)."""
        try:
            from login_dialog import LoginDialog
            from user_manager import UserManager
            mgr = UserManager()
            dlg = LoginDialog(mgr, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.user:
                self.current_user = dlg.user
                self._update_notified_version = None
                self._pending_update_info = None
                self._hide_update_toast()
                self.header_update_btn.setVisible(False)
                self.apply_user_permissions()
                QMessageBox.information(
                    self, "ورود",
                    f"👤 وارد شدید: {dlg.user.get('username', '')}")
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"تعویض کاربر ممکن نشد:\n{e}")

    # ---------------------------------------------------------- آپدیت خودکار --
    def _schedule_update_checks(self):
        """بررسی خودکار: ۱۰ ثانیه بعد از بالا آمدن + هر ۱ ساعت."""
        try:
            QTimer.singleShot(10000, self._async_update_check)
            self._update_timer = QTimer(self)
            self._update_timer.setInterval(3600 * 1000)
            self._update_timer.timeout.connect(self._async_update_check)
            self._update_timer.start()
        except Exception:
            pass

    def _auto_check_enabled(self):
        try:
            import app_settings
            return bool(app_settings.get_auto_update_check())
        except Exception:
            return True

    def _async_update_check(self):
        if not self._auto_check_enabled():
            return

        def _worker():
            try:
                import update_checker
                info = update_checker.check_for_updates(self.app_version)
            except Exception:
                info = None
            try:
                self.update_check_done.emit(info)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def check_updates_now(self):
        """بررسی دستی آپدیت (دکمه‌ی «بررسی الآن» در تنظیمات)."""
        self._async_update_check()
        QMessageBox.information(
            self, "بررسی آپدیت",
            "🔍 در حال بررسی آپدیت جدید…\nاگر نسخه‌ی جدیدی منتشر شده باشد، هشدار آن نمایش داده می‌شود.")

    def _on_update_check_result(self, info):
        if not info or not self._is_admin() or not self._auto_check_enabled():
            return
        ver = info.get("version", "")
        if not ver:
            return
        # (1.0.0) جلوگیری از آلارم تکراری: نسخه‌ای که قبلاً هشدار داده شده
        # (حتی بعد از ری‌استارت) دوباره هشدار نمی‌دهیم.
        try:
            import app_settings
            _notified = app_settings.get_notified_update_version()
        except Exception:
            _notified = self._update_notified_version
        if ver == self._update_notified_version or ver == _notified:
            return  # برای همین نسخه قبلاً هشدار داده‌ایم
        self._update_notified_version = ver
        try:
            import app_settings
            app_settings.set_notified_update_version(ver)
        except Exception:
            pass
        self._pending_update_info = info
        self.header_update_btn.setVisible(True)
        self._show_update_toast(info)

    def _show_update_toast(self, info):
        """پاپ‌آپ گوشه‌ی پایین-چپ: «آپدیت جدید منتشر شد!»"""
        self._hide_update_toast()
        try:
            toast = QWidget(
                self, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
            toast.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            toast.setStyleSheet(
                "background: #1d2b1d; border: 2px solid #2e7d32; "
                "border-radius: 10px;")
            lay = QVBoxLayout(toast)
            lay.setContentsMargins(14, 12, 14, 12)
            title = QLabel(f"⬆️ آپدیت جدید منتشر شد!  (v{info.get('version', '')})")
            title.setStyleSheet("font-size: 14px; font-weight: bold; "
                                "color: #a5d6a7; border: none;")
            title.setWordWrap(True)
            lay.addWidget(title)
            notes = (info.get("notes", "") or "").strip().splitlines()
            if notes:
                nl = QLabel(notes[0][:140])
                nl.setWordWrap(True)
                nl.setStyleSheet("font-size: 11px; color: #cccccc; border: none;")
                lay.addWidget(nl)
            row = QHBoxLayout()
            go_btn = QPushButton("⬆️ دانلود و نصب")
            go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            go_btn.setStyleSheet(
                "QPushButton{background: #2e7d32; color: white; font-weight: bold; "
                "padding: 8px 16px; border-radius: 6px; font-size: 13px;}")
            go_btn.clicked.connect(self._on_header_update_clicked)
            x_btn = QPushButton("✖")
            x_btn.setFixedWidth(36)
            x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            x_btn.clicked.connect(self._hide_update_toast)
            row.addWidget(go_btn)
            row.addWidget(x_btn)
            lay.addLayout(row)
            toast.adjustSize()
            try:
                geo = self.geometry()
                x = geo.left() + 24
                y = geo.bottom() - toast.sizeHint().height() - 60
                toast.move(max(0, x), max(0, y))
            except Exception:
                pass
            self._update_toast = toast
            toast.show()
        except Exception:
            pass

    def _hide_update_toast(self):
        try:
            if self._update_toast is not None:
                self._update_toast.hide()
                self._update_toast.deleteLater()
        except Exception:
            pass
        self._update_toast = None

    def _on_header_update_clicked(self):
        info = getattr(self, "_pending_update_info", None)
        if not info:
            return
        self._download_and_apply_update(info)

    def _download_and_apply_update(self, info):
        """دانلود خودکار فایل آپدیت + اعتبارسنجی + اعمال (فقط ادمین)."""
        from PyQt6.QtWidgets import QProgressDialog
        if not self._is_admin():
            QMessageBox.warning(self, "دسترسی",
                                "نصب آپدیت فقط برای ادمین مجاز است.")
            return
        ver = info.get("version", "")
        notes = (info.get("notes", "") or "").strip()
        msg = f"آپدیت جدید v{ver} منتشر شده است.\n\n"
        if notes:
            msg += notes[:500] + "\n\n"
        msg += ("فایل آپدیت دانلود و بلافاصله اعمال می‌شود؛ "
                "برنامه بسته و دوباره باز می‌شود.\nادامه می‌دهید؟")
        if QMessageBox.question(
                self, "⬆️ آپدیت جدید",
                msg) != QMessageBox.StandardButton.Yes:
            return
        try:
            from updater import is_frozen
            if not is_frozen():
                QMessageBox.information(
                    self, "حالت توسعه",
                    "ℹ️ دانلود/اعمال خودکار آپدیت فقط در نسخه‌ی نصب‌شده کار می‌کند.")
                return
        except Exception:
            pass
        # --- دانلود با دیالوگ پیشرفت ---
        import tempfile
        dest = os.path.join(tempfile.gettempdir(),
                            f"IAS-CMS-Update-v{ver}.zip")
        dlg = QProgressDialog("در حال دانلود فایل آپدیت…", "انصراف",
                              0, 100, self)
        dlg.setWindowTitle("⬆️ دانلود آپدیت")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        class _Cancelled(Exception):
            pass

        def _prog(p):
            dlg.setValue(int(p))
            QApplication.processEvents()
            if dlg.wasCanceled():
                raise _Cancelled()

        try:
            import update_checker
            update_checker.download_update(info["download_url"], dest,
                                           progress_cb=_prog)
        except _Cancelled:
            dlg.close()
            return
        except Exception as e:
            dlg.close()
            QMessageBox.warning(self, "خطا", f"⚠️ دانلود آپدیت ممکن نشد:\n{e}")
            return
        dlg.close()
        # --- اعتبارسنجی فایل دانلودشده ---
        try:
            from updater import validate_update_zip, stage_and_launch_update
            ok, _vinfo = validate_update_zip(dest)
        except Exception as e:
            QMessageBox.warning(self, "خطا",
                                f"⚠️ فایل دانلودشده معتبر نیست:\n{e}")
            return
        if not ok:
            QMessageBox.warning(self, "خطا",
                                f"⚠️ فایل دانلودشده معتبر نیست:\n{_vinfo}")
            return
        # --- اعمال ---
        status_dlg = QProgressDialog("در حال آماده‌سازی آپدیت…", None, 0, 0,
                                     self)
        status_dlg.setWindowTitle("⬆️ اعمال آپدیت")
        status_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        status_dlg.setMinimumDuration(0)
        status_dlg.show()

        def _st(t):
            try:
                status_dlg.setLabelText(str(t))
                QApplication.processEvents()
            except Exception:
                pass

        ok, msg2 = stage_and_launch_update(dest, status_cb=_st)
        status_dlg.close()
        if not ok:
            QMessageBox.warning(self, "خطا", "⚠️ " + msg2)
            return
        QApplication.instance().quit()
        os._exit(0)

    def _on_map_camera_click(self, cam):
        """دابل‌کلیک روی دوربین در صفحه‌ی «نقشه ساختمان»: باز شدن پنجره‌ی
        شناور پخش زنده‌ی همان دوربین (بدون ترک صفحه‌ی نقشه)."""
        if not self._ensure_password(cam):
            return  # کاربر از وارد کردن رمز صرف‌نظر کرد
        rtsp_url = self.camera_store.build_rtsp_url(cam)
        dlg = CameraPreviewDialog(cam, rtsp_url, self.face_engine, parent=self)
        dlg.show()

    def _on_person_show_on_map(self, person_id):
        """دکمه‌ی «🗺 نمایش روی نقشه» در تب گزارش مسیر حرکت: باز کردن صفحه‌ی
        نقشه و رسم مسیر تردد روی نقشه‌ی طبقات. person_id=None یعنی «همه‌ی
        اشخاص» — هر شخص با خط‌چینِ رنگ مخصوص خودش."""
        if self.map_page is None:
            QMessageBox.warning(
                self, "صفحه‌ی نقشه در دسترس نیست",
                "فایل building_map_dialog.py کنار برنامه پیدا نشد.")
            return
        self.show_page("map")
        if person_id:
            self.map_page.show_person_path(person_id)
        else:
            self.map_page.show_all_person_paths()

    def open_help(self):
        """(2.0.53-beta) باز کردن کاتالوگ PDF «راهنمای کاربری» روی صفحه‌ی
        مربوط به صفحه‌ی فعلی برنامه (دکمه‌ی «❓ راهنما» در هدر).

        (2.0.74-beta) راهنما به سطح دسترسی کاربر گره خورده است: کاربر
        محدود (غیرادمین) نسخه‌ی فیلترشده‌ی راهنما را می‌بیند — فقط
        بخش‌های صفحه‌هایی که به آن‌ها دسترسی دارد + صفحه‌های عمومی
        (جلد/آشنایی/شروع سریع/عیب‌یابی)؛ بخش‌های مدیریتی (مدیریت
        کاربران/آپدیت خودکار) و صفحه‌های غیرمجاز در آن نیست. ادمین و
        حالت توسعه مثل قبل کل راهنما را می‌بینند."""
        from app_help import open_manual
        from user_manager import UserManager
        user = self.current_user
        if user is None or UserManager.is_admin(user):
            allowed = None  # دسترسی کامل: کل راهنما
        else:
            allowed = set(UserManager.allowed_pages(user))
        open_manual(getattr(self, "_current_page_key", "home"), parent=self,
                    allowed_pages=allowed)

    def open_face_gallery(self):
        """(2.0.111-beta) دکمه‌ی «🖼 دیدن تصاویر» پوشه‌ای را در اکسپلورر
        باز می‌کند که برنامه تصاویر رویدادها (چهره‌ها، پلاک‌ها و...) را در آن
        ذخیره می‌کند (report_images)."""
        try:
            import os
            import sys
            images_dir = report_store.IMAGES_DIR
            os.makedirs(images_dir, exist_ok=True)
            # در ویندوز os.startfile مطمئن‌ترین راه است.
            if sys.platform == "win32":
                try:
                    os.startfile(images_dir)
                    return
                except Exception:
                    pass
            # در مک از دستور open استفاده می‌کنیم.
            if sys.platform == "darwin":
                try:
                    import subprocess
                    subprocess.Popen(["open", images_dir])
                    return
                except Exception:
                    pass
            # fallback: QDesktopServices
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(images_dir)):
                raise RuntimeError("openUrl ناموفق بود")
        except Exception as e:
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "خطا",
                    f"باز کردن پوشه‌ی تصاویر ناموفق بود:\n{images_dir}\n\n{e}")
            except Exception:
                pass

    def on_face_event(self, cam, person, crop_frame):
        """برای هر چهره‌ای که هر یک از دوربین‌ها ببیند (شناخته‌شده یا
        تعریف‌نشده) فراخوانی می‌شود. (2.0.105-beta) چهره‌ها دوباره در «پنل
        رویدادها» نمایش داده می‌شوند (با تصویر بندانگشتی)؛ همچنین برای گالری
        «دیدن تصاویر» در حافظه نگه داشته و مثل قبل به‌صورت دائمی در گزارش‌ها
        ثبت می‌شوند.
        ``cam``: کل دیکشنری دوربین (نه فقط اسم) تا nvr_id/channel هم
        برای لینک «پخش ویدیوی NVR» در دیالوگ گزارش‌ها ذخیره شود."""
        # گیت لایسنس: اگر قابلیت چهره‌خوان فعال نباشد، چهره‌ای شناسایی
        # و ثبت نمی‌شود.
        try:
            from license import is_feature_enabled
            if not is_feature_enabled("face_recognition"):
                return
        except Exception:
            pass
        camera_name = cam.get("name", "")
        timestamp = time.strftime("%H:%M:%S")
        pixmap = _bgr_to_pixmap(crop_frame) if crop_frame is not None else None
        # تاریخچه‌ی همین نشست برای گالری «دیدن تصاویر».
        self._recent_face_events.insert(0, {
            "pixmap": pixmap,
            "camera": camera_name,
            "time": timestamp,
            "name": person.get("name", "") if person else "",
            "known": bool(person),
        })
        # جلوگیری از رشد بی‌حد حافظه در نشست‌های طولانی.
        while len(self._recent_face_events) > 300:
            self._recent_face_events.pop()

        # ثبت دائمی (با ساعت/تاریخ کامل و همین تصویر برش‌خورده) روی سیستم
        # (report_store.py).
        report_store.log_face_event(camera_name, person, crop_frame,
                                     nvr_id=cam.get("nvr_id"), channel=cam.get("channel"))

        # (2.0.105-beta) نمایش در «پنل رویدادها» با تصویر بندانگشتی.
        try:
            name = person.get("name", "") if person else ""
            if name:
                text = f"[{timestamp}] {camera_name}\n👤 {name}"
            else:
                text = f"[{timestamp}] {camera_name}\n❓ چهره ناشناس"
            item = QListWidgetItem(text)
            if pixmap is not None and not pixmap.isNull():
                item.setIcon(QIcon(pixmap))
            self.events_panel_list.insertItem(0, item)
            while self.events_panel_list.count() > 300:
                self.events_panel_list.takeItem(
                    self.events_panel_list.count() - 1)
        except Exception:
            pass

    # ------------------------------------------------------ plate reader ---

    def on_plate_event(self, cam, data):
        """برای هر پلاک تازه‌تأییدشده‌ای که یکی از دوربین‌های فعالِ پلاک‌خوان
        ببیند فراخوانی می‌شود. data دیکشنری {"plate_text", "plate_display",
        "conf", "crop", "box"} است (رجوع کنید به camera_stream.py).
        تطبیق با پلاک‌های تعریف‌شده + ثبت دائمی رویداد (با تصویر برش‌خورده)
        همین‌جا انجام می‌شود؛ اگر صفحه‌ی «پلاک‌خوان» باز باشد، گزارشش هم
        تازه می‌شود."""
        try:
            camera_name = cam.get("name", "")
            event = plate_store.log_event(
                camera_name,
                data.get("plate_text", ""),
                confidence=float(data.get("conf", 0.0) or 0.0),
                crop_bgr=data.get("crop"),
                nvr_id=cam.get("nvr_id") or "",
                channel=cam.get("channel"),
                plate_display=data.get("plate_display", ""),
            )
            # (2.0.74-beta) هر پلاک دیده‌شده (نه فقط تخلف‌ها) در «پنل
            # رویدادها» هم ثبت می‌شود — حتی اگر کاربر جاری به صفحه‌ی
            # «پلاک‌خوان» دسترسی نداشته باشد؛ کافی است ادمین پلاک‌خوان را
            # روی این دوربین فعال کرده باشد.
            self._push_plate_sighting_to_events(camera_name, data, event)
            # (2.0.15-beta) موتور قوانین جهت تردد: نقش ورود/خروج دوربین،
            # جهت مجاز مسیر، وضعیت داخل/خارج پلاک و ثبت تخلف در صورت نیاز.
            try:
                engine = getattr(self, "plate_direction", None)
                if engine is not None:
                    result = engine.process(cam, data, event)
                    # (2.0.56-beta) هر تخلف ثبت‌شده (از جمله «⛔ ورود
                    # غیرمجاز» برای پلاک لیست سیاه) در «پنل رویدادها» هم
                    # نمایش داده می‌شود.
                    try:
                        vids = (result or {}).get("violations") or []
                    except Exception:
                        vids = []
                    for vid in vids:
                        self._push_plate_violation_to_events(vid)
                # لینک دوربین/مسیر روی رویداد عبور (برای گزارش‌ها)
                try:
                    with plate_store._lock:
                        plate_store._conn.execute(
                            "UPDATE plate_events SET camera_id=?, lane_id=? WHERE id=?",
                            (str(cam.get("id", "")),
                             (cam.get("lane_id") or ""),
                             event.get("id", "")))
                        plate_store._conn.commit()
                except Exception:
                    pass
            except Exception as e:
                print(f"خطا در موتور جهت پلاک: {e}")
            # اگر کاربر همین حالا صفحه‌ی پلاک‌خوان (تب گزارش) را می‌بیند،
            # جدول را زنده تازه کن.
            try:
                if (hasattr(self, "plate_page") and self.plate_page is not None
                        and self.pages.currentWidget() is self.plate_page):
                    self.plate_page.run_report_search()
                    self.plate_page._update_stats()
                    # اگر تب «تخلفات تردد» دیده می‌شود، آن هم زنده تازه شود
                    pg = self.plate_page
                    if (getattr(pg, "tabs", None) is not None
                            and pg.tabs.currentWidget()
                            is getattr(pg, "violations_tab", None)):
                        pg.run_violations_search()
            except Exception:
                pass
        except Exception as e:
            print(f"خطا در ثبت رویداد پلاک: {e}")

    def _push_plate_sighting_to_events(self, camera_name, data, event=None):
        """(2.0.74-beta) ثبت «دیدن پلاک» در «پنل رویدادها»: ردیف با شماره‌ی
        پلاک، دوربین و تصویر برش‌خورده‌ی پلاک. برخلاف تخلف‌ها که قرمزند،
        این ردیف‌ها رنگ خنثی دارند. سقف ۳۰۰ ردیف مثل بقیه‌ی رویدادها.
        (2.0.77-beta) ردیف وضعیت‌محور شد: لیست سیاه → «⛔ ورود غیرمجاز»،
        لیست سفید → «⭐ لیست سفید»، تعریف‌شده → نام مالک + پلاک،
        تعریف‌نشده → «تعریف‌نشده». برای پلاک تحت‌نظر ردیف خنثی جدا زده
        نمی‌شود چون ردیف قرمز تخلف همان لحظه ثبت می‌شود (تکراری بود)."""
        try:
            data = data or {}
            event = event or {}
            timestamp = time.strftime("%H:%M:%S")
            plate_display = (data.get("plate_display")
                             or data.get("plate_text") or "")
            wl_kinds = set()
            try:
                wl_kinds = {w.get("kind") for w in plate_store.find_watchlist(
                    data.get("plate_text", ""))}
            except Exception:
                wl_kinds = set()
            # پلاک تحت‌نظر: ردیف قرمز تخلف جدا ثبت می‌شود؛ ردیف خنثی
            # تکراری نزن — فقط وقتی موتور جهت فعال است که ثبت تخلف قطعی است.
            if (("black" in wl_kinds or "white" in wl_kinds)
                    and getattr(self, "plate_direction", None) is not None):
                return
            if "black" in wl_kinds:
                status_line = "⛔ ورود غیرمجاز"
            elif "white" in wl_kinds:
                status_line = "⭐ لیست سفید"
            elif event.get("is_defined"):
                owner = (event.get("owner_name") or "").strip()
                status_line = owner if owner else "تعریف‌شده"
            else:
                status_line = "تعریف‌نشده"
            text = f"[{timestamp}] {camera_name}\n🚗 {status_line} | پلاک: {plate_display}"
            item = QListWidgetItem(text)
            try:
                crop = data.get("crop")
                pixmap = _bgr_to_pixmap(crop) if crop is not None else None
                if pixmap is not None and not pixmap.isNull():
                    item.setIcon(QIcon(pixmap))
            except Exception:
                pass
            self.events_panel_list.insertItem(0, item)
            while self.events_panel_list.count() > 300:
                self.events_panel_list.takeItem(
                    self.events_panel_list.count() - 1)
        except Exception:
            pass

    def _push_plate_violation_to_events(self, vid):
        """(2.0.56-beta) نمایش یک تخلف پلاک در «پنل رویدادها»: ردیف قرمز با
        نوع تخلف (مثل «⛔ ورود غیرمجاز» برای پلاک لیست سیاه)، شماره‌ی پلاک،
        دوربین و تصویر لحظه‌ی تخلف در صورت وجود."""
        try:
            if not vid or vid in self._shown_violation_ids:
                return
            self._shown_violation_ids.add(vid)
            # جلوگیری از رشد بی‌حد مجموعه‌ی شناسه‌ها در نشست‌های طولانی.
            if len(self._shown_violation_ids) > 2000:
                self._shown_violation_ids.clear()
                self._shown_violation_ids.add(vid)
            v = plate_store.get_violation(vid)
            if not v:
                return
            vtype = v.get("violation_type") or ""
            vlabel = plate_store.VIOLATION_LABELS.get(vtype, vtype)
            timestamp = time.strftime("%H:%M:%S")
            plate_display = v.get("plate_display") or v.get("plate_text") or ""
            camera_name = v.get("camera_name") or ""
            text = f"[{timestamp}] {vlabel}\nپلاک: {plate_display}"
            if camera_name:
                text += f" | {camera_name}"
            item = QListWidgetItem(text)
            sp = v.get("snapshot_path") or ""
            if sp:
                try:
                    pixmap = QPixmap(sp)
                    if not pixmap.isNull():
                        item.setIcon(QIcon(pixmap))
                except Exception:
                    pass
            item.setForeground(QColor("#c0392b"))
            self.events_panel_list.insertItem(0, item)
            # جلوگیری از رشد بی‌حد پنل در نشست‌های طولانی.
            while self.events_panel_list.count() > 300:
                self.events_panel_list.takeItem(self.events_panel_list.count() - 1)
        except Exception as e:
            print(f"خطا در نمایش تخلف پلاک در پنل رویدادها: {e}")

    def _get_plate_live_diag(self):
        """جمع‌آوری شمارنده‌های تشخیصی پلاک‌خوان همه‌ی دوربین‌های باز برای
        دیالوگ «وضعیت زنده‌ی پلاک‌خوان (تشخیصی)» صفحه‌ی پلاک‌خوان."""
        out = {}
        try:
            for slot in self.camera_grid.slots:
                try:
                    cam = getattr(slot, "cam", None)
                    name = (cam.get("name") or cam.get("ip") or "") if isinstance(cam, dict) else ""
                    th = getattr(slot, "stream_thread", None)
                    if th is not None and hasattr(th, "get_plate_diag"):
                        out[name or "دوربین"] = th.get_plate_diag()
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _on_camera_fire_toggle(self, cam_id, enabled):
        """اعمال زنده‌ی تیک «تشخیص حریق» صفحه‌ی «اعلام حریق» روی دوربینی که
        همین حالا در شبکه‌ی نمایش باز است."""
        try:
            for slot in self.camera_grid.slots:
                if slot.cam is not None and slot.cam.get("id") == cam_id:
                    slot.cam["fire_detection"] = bool(enabled)
                    slot.set_fire_detection(enabled)
        except Exception as e:
            print(f"خطا در اعمال تشخیص حریق روی دوربین باز: {e}")
        # پنل «هشدارهای حریق و دود» صفحه‌ی اصلی فقط وقتی دیده می‌شود که
        # حداقل یک دوربین تشخیص حریق فعال داشته باشد.
        self._refresh_fire_panel_visibility()

    def _on_camera_plate_toggle(self, cam_id, enabled):
        """اعمال زنده‌ی تیک «پلاک‌خوان» صفحه‌ی پلاک‌خوان روی دوربینی که همین
        حالا در شبکه‌ی نمایش باز است (بدون نیاز به بستن/باز کردن دوباره)."""
        try:
            for slot in self.camera_grid.slots:
                if slot.cam is not None and slot.cam.get("id") == cam_id:
                    slot.cam["plate_detection"] = bool(enabled)
                    slot.set_plate_detection(enabled)
        except Exception as e:
            print(f"خطا در اعمال پلاک‌خوان روی دوربین باز: {e}")

    def reapply_live_feature_flags(self):
        """بازنویسی وضعیت موتورهای دوربین‌های باز از روی رکورد تازه‌ی
        camera_store (بعد از آپلود لایسنس جدید و enforce_quotas).

        اگر لایسنس جدید تیک قابلیتی (پلاک‌خوان/حریق/ردیابی اشخاص) را از روی
        دوربینی برداشته باشد، موتورِ در حال اجرای آن دوربین هم همین‌جا
        خاموش می‌شود؛ وگرنه استریم باز با موتور قبلی به کارش ادامه می‌داد.
        """
        try:
            grid = getattr(self, "camera_grid", None)
            store = getattr(self, "camera_store", None)
            if grid is None or store is None:
                return
            for slot in grid.slots:
                cam = getattr(slot, "cam", None)
                if not isinstance(cam, dict):
                    continue
                try:
                    fresh = store.get_camera(cam.get("id"))
                except Exception:
                    fresh = None
                if not isinstance(fresh, dict):
                    fresh = cam
                try:
                    slot.set_plate_detection(bool(fresh.get("plate_detection")))
                except Exception:
                    pass
                try:
                    slot.set_fire_detection(bool(fresh.get("fire_detection")))
                except Exception:
                    pass
                try:
                    slot.set_person_tracking(bool(fresh.get("person_tracking")))
                except Exception:
                    pass
        except Exception as e:
            print(f"خطا در بازنویسی قابلیت‌های دوربین‌های باز: {e}")

    def on_person_event(self, cam, data):
        """برای هر رویداد ردیابی اشخاص که یکی از دوربین‌های فعالِ ردیابی
        ببیند فراخوانی می‌شود. data دیکشنری {"type": "candidate" /
        "candidate_ended" / "confirmed" / "ended", "local_id", ...} است
        (رجوع کنید به camera_stream.py).

        - candidate: نشان زرد «در حال شناسایی» روی نقشه — حس «در لحظه»،
          بدون ثبت در دیتابیس.
        - confirmed: تطبیق بین دوربینی (با کمک چهره: اگر چهره در بانک
          چهره‌ها شناخته‌شده باشد، همان هویت قطعی است حتی با لباس متفاوت)
          + ثبت «حضور» (ورود/خروج با ساعت دقیق) در person_store — در ترد
          اصلی انجام می‌شود؛ اگر صفحه‌ی «ردیابی اشخاص» باز باشد، جدول‌هایش
          هم زنده تازه می‌شوند."""
        try:
            etype = (data or {}).get("type")
            cam_id = cam.get("id") if isinstance(cam, dict) else None
            camera_name = (cam.get("name") or cam.get("ip") or "") \
                if isinstance(cam, dict) else ""
            key = (cam_id, data.get("local_id"))
            mp = getattr(self, "map_page", None)

            if etype == "candidate":
                # «در لحظه»: به‌محض دومین دیده‌شدن پیاپی، نشان زرد روی نقشه
                try:
                    if not hasattr(self, "_person_candidates"):
                        self._person_candidates = set()
                    self._person_candidates.add(key)
                    if mp is not None:
                        mp.set_live_person(cam_id, f"~{data.get('local_id')}",
                                           "…", camera_name, True,
                                           kind="candidate")
                except Exception:
                    pass
                return

            if etype == "candidate_ended":
                try:
                    if hasattr(self, "_person_candidates"):
                        self._person_candidates.discard(key)
                    if mp is not None:
                        mp.set_live_person(cam_id, f"~{data.get('local_id')}",
                                           "", "", False)
                except Exception:
                    pass
                return

            if etype == "confirmed":
                desc = data.get("descriptor") or {}
                vector = desc.get("vector")
                if vector is None:
                    return
                ts = time.time()
                face_pid = str(desc.get("face_person_id") or "")
                face_name = str(desc.get("face_name") or "")
                face_vector = desc.get("face_vector")
                # کاندیدای همین رد (اگر بود) را بردار؛ نشان سبز جایش می‌نشیند
                try:
                    if hasattr(self, "_person_candidates"):
                        self._person_candidates.discard(key)
                    if mp is not None:
                        mp.set_live_person(cam_id, f"~{data.get('local_id')}",
                                           "", "", False)
                except Exception:
                    pass
                person_id = None
                # ۱) کمک چهره: اگر چهره در بانک چهره‌ها شناخته‌شده است، اول
                # در دیتابیس دنبال همان هویت بگرد (قطعی، حتی با لباس عوض‌شده)
                if face_pid:
                    try:
                        person_id = person_store.find_by_face(face_pid)
                    except Exception:
                        person_id = None
                # ۲) تطبیق سراسری (ظاهر + امضای چهره)
                if person_id is None:
                    person_id, _dist = self._person_matcher.find_match(
                        vector, camera_name, ts,
                        face_vector=face_vector,
                        face_person_id=face_pid or None)
                is_new = person_id is None
                if is_new:
                    person_id = person_store.add_person(
                        desc, thumb_bgr=data.get("crop"))
                elif face_pid:
                    # شخص از مسیر دیگری (ظاهری) پیدا شد ولی چهره‌اش شناخته‌شده
                    # است: هویت چهره را به او بچسبان تا دفعه‌ی بعد قطعی شود
                    try:
                        person_store.set_person_face(
                            person_id, face_pid, face_name)
                    except Exception:
                        pass
                self._person_matcher.register(
                    person_id, vector, camera_name, ts,
                    face_vector=face_vector,
                    face_person_id=face_pid or None)
                sighting_id = person_store.start_sighting(
                    person_id, camera_name,
                    nvr_id=(cam.get("nvr_id") or "") if isinstance(cam, dict) else "",
                    channel=(cam.get("channel") if isinstance(cam, dict) else None),
                    snapshot_bgr=data.get("crop"))
                # کنترل تردد طبقاتی: اگر شخص در طبقه‌ی غیرمجاز دیده شد،
                # تخلف ثبت و هشدار داده می‌شود (هرگز نباید ردیابی را بشکند).
                try:
                    self._check_person_floor_access(
                        person_id, face_pid, face_name, cam_id, camera_name,
                        snapshot_bgr=data.get("crop"))
                except Exception:
                    pass
                # کد یکتای شخص را روی باکس تصویر همان دوربین بنویس
                # (لاتین می‌ماند چون cv2.putText فارسی رسم نمی‌کند)
                for slot in self.camera_grid.slots:
                    if slot.cam is not None and slot.cam.get("id") == cam_id \
                            and slot.stream_thread is not None:
                        slot.stream_thread.set_person_track_label(
                            data.get("local_id"), person_id)
                        break
                self._active_person_tracks[key] = {
                    "person_id": person_id, "sighting_id": sighting_id}
                # ردیابی «در لحظه»: همان ثانیه‌ی شناسایی، نشان زنده‌ی شخص
                # روی دوربینِ نقشه‌ی ساختمان می‌نشیند و اگر مسیر همین شخص
                # روی نقشه باز است، توقف جدید بی‌درنگ به آن اضافه می‌شود.
                # اگر چهره شناخته‌شده باشد، نامش هم روی نشان نقشه می‌آید.
                try:
                    if mp is not None:
                        code = (f"{person_id} · {face_name}"
                                if face_name else person_id)
                        mp.set_live_person(cam_id, person_id, code,
                                           camera_name, True, kind="live")
                        mp.append_live_stop(person_id)
                except Exception:
                    pass
            elif etype == "ended":
                info = self._active_person_tracks.pop(key, None)
                if info is not None:
                    person_store.end_sighting(info["sighting_id"])
                    try:
                        if mp is not None:
                            mp.set_live_person(cam_id, info["person_id"],
                                               info["person_id"], "", False)
                            # احتیاط: کاندیدای باقی‌مانده‌ی همین رد
                            mp.set_live_person(
                                cam_id, f"~{data.get('local_id')}",
                                "", "", False)
                    except Exception:
                        pass
                if hasattr(self, "_person_candidates"):
                    self._person_candidates.discard(key)
            else:
                return
            # اگر کاربر همین حالا صفحه‌ی «ردیابی اشخاص» را می‌بیند،
            # جدول‌ها را زنده تازه کن.
            try:
                if (hasattr(self, "person_page") and self.person_page is not None
                        and self.pages.currentWidget() is self.person_page):
                    self.person_page.refresh_persons_table()
                    self.person_page._reload_path_person_combo()
                    self.person_page.run_path_search()
            except Exception:
                pass
        except Exception as e:
            print(f"خطا در ثبت رویداد ردیابی اشخاص: {e}")

    def _on_camera_person_toggle(self, cam_id, enabled):
        """اعمال زنده‌ی تیک «ردیابی اشخاص» صفحه‌ی ردیابی اشخاص روی دوربینی
        که همین حالا در شبکه‌ی نمایش باز است (بدون نیاز به بستن/باز کردن
        دوباره). با خاموش شدن، ردهای باز آن دوربین بسته می‌شوند."""
        try:
            for slot in self.camera_grid.slots:
                if slot.cam is not None and slot.cam.get("id") == cam_id:
                    slot.cam["person_tracking"] = bool(enabled)
                    slot.set_person_tracking(enabled)
                    if not enabled:
                        # «حضور»های باز این دوربین را ببند
                        for key in [k for k in self._active_person_tracks
                                    if k[0] == cam_id]:
                            info = self._active_person_tracks.pop(key)
                            person_store.end_sighting(info["sighting_id"])
                            try:
                                mp = getattr(self, "map_page", None)
                                if mp is not None:
                                    mp.set_live_person(
                                        cam_id, info["person_id"],
                                        info["person_id"], "", False)
                            except Exception:
                                pass
                        # نشان‌های زرد «در حال شناسایی» این دوربین را هم بردار
                        try:
                            if hasattr(self, "_person_candidates"):
                                for key in [k for k in self._person_candidates
                                            if k[0] == cam_id]:
                                    self._person_candidates.discard(key)
                                    mp = getattr(self, "map_page", None)
                                    if mp is not None:
                                        mp.set_live_person(
                                            cam_id, f"~{key[1]}", "", "", False)
                        except Exception:
                            pass
        except Exception as e:
            print(f"خطا در اعمال ردیابی اشخاص روی دوربین باز: {e}")

    def _check_person_floor_access(self, person_id, face_person_id, face_name,
                                     cam_id, camera_name, snapshot_bgr=None):
        """کنترل تردد طبقاتی: اگر شخص در طبقه‌ی غیرمجاز دیده شد، تخلف ثبت
        و هشدار داده می‌شود. هیچ‌وقت نباید ردیابی را بشکند."""
        from floor_access import (resolve_camera_floor, evaluate_floor_access,
                                  get_floor_name)
        floor_id = resolve_camera_floor(
            cam_id, camera_store=getattr(self, "camera_store", None))
        if not floor_id:
            return  # طبقه‌ی دوربین نامشخص؛ چک نمی‌شود
        allowed, _reason, _is_defined = evaluate_floor_access(
            person_id, face_person_id, floor_id, person_store,
            face_engine=getattr(self, "face_engine", None))
        if allowed:
            return
        floor_name = get_floor_name(floor_id)
        viol = person_store.record_floor_violation(
            person_id, face_person_id or "", face_name or "",
            cam_id or "", camera_name or "", floor_id, floor_name,
            snapshot_bgr=snapshot_bgr)
        if viol is None:
            return  # داخل cooldown؛ قبلاً ثبت شده
        # (2.0.74-beta) ثبت تخلف طبقاتی در «پنل رویدادها» — مثل بقیه‌ی
        # هشدارهای فعالِ روی دوربین، حتی اگر کاربر جاری به صفحه‌ی
        # «ردیابی اشخاص» دسترسی نداشته باشد.
        self._push_floor_violation_to_events(camera_name, face_name,
                                             person_id, floor_name,
                                             snapshot_bgr)
        # هشدار صوتی تخلف طبقاتی — کاملاً مستقل از صدای حریق و ورود به محدوده
        # (تک‌بوق؛ آژیر ممتد آتش جداست و با تنظیم خودش کنترل می‌شود)
        try:
            _play_violation_beep()
        except Exception:
            pass
        # به‌روزرسانی زنده‌ی تب تخلفات (اگر صفحه باز است)
        try:
            page = getattr(self, "person_page", None)
            if page is not None and hasattr(page, "refresh_violations"):
                page.refresh_violations()
        except Exception:
            pass

    def _push_floor_violation_to_events(self, camera_name, face_name,
                                        person_id, floor_name, snapshot_bgr):
        """(2.0.74-beta) ثبت «تخلف تردد طبقاتی» در «پنل رویدادها»: ردیف
        قرمز با هویت شخص (یا «شخص ناشناس»)، دوربین، طبقه‌ی غیرمجاز و
        تصویر لحظه‌ی تخلف در صورت وجود. سقف ۳۰۰ ردیف مثل بقیه."""
        try:
            timestamp = time.strftime("%H:%M:%S")
            who = face_name or person_id or "شخص ناشناس"
            text = (f"[{timestamp}] {camera_name}\n"
                    f"🚫 تخلف تردد طبقاتی: {who} ← {floor_name}")
            item = QListWidgetItem(text)
            item.setForeground(QColor("#e74c3c"))
            try:
                pixmap = (_bgr_to_pixmap(snapshot_bgr)
                          if snapshot_bgr is not None else None)
                if pixmap is not None and not pixmap.isNull():
                    item.setIcon(QIcon(pixmap))
            except Exception:
                pass
            self.events_panel_list.insertItem(0, item)
            while self.events_panel_list.count() > 300:
                self.events_panel_list.takeItem(
                    self.events_panel_list.count() - 1)
        except Exception:
            pass

    def _refresh_person_detector_status(self):
        """به‌روزرسانی بنر وضعیت موتور تشخیص شخص در صفحه‌ی «ردیابی اشخاص».

        رفع درخواست «هیچ گزارشی از افراد ثبت نمیشه» بی‌هیچ توضیحی: علت
        معمولاً یکی از این‌هاست و حالا هر کدام با پیام مشخص روی همان
        صفحه دیده می‌شود:
        ۱) هیچ دوربینی در صفحه‌ی اصلی باز/در حال پخش نیست؛
        ۲) مدل تشخیص شخص (YOLOv8 در person_detector.py) بارگذاری نشده؛
        ۳) همه‌چیز سالم است و گزارش‌ها در حال ثبت‌اند.
        """
        page = getattr(self, "person_page", None)
        if page is None:
            return
        try:
            open_slots = [s for s in self.camera_grid.slots
                          if getattr(s, "cam", None) is not None
                          and getattr(s, "stream_thread", None) is not None]
        except Exception:
            open_slots = []
        if not open_slots:
            page.set_detector_status("no_camera")
            return
        try:
            states = [getattr(s, "_detector_available", None) for s in open_slots]
        except Exception:
            states = []
        if any(s is True for s in states):
            page.set_detector_status("ok")
            return
        if states and all(s is False for s in states):
            err = ""
            try:
                for s in open_slots:
                    if getattr(s, "_detector_error", ""):
                        err = s._detector_error
                        break
            except Exception:
                pass
            page.set_detector_status("error", err)
            return
        page.set_detector_status("loading")


    # ------------------------------------------------------------- scan ---

    def run_network_scan(self):
        # رفع باگ: قبلاً scan_subnet مستقیماً روی ترد UI اجرا می‌شد و کل برنامه
        # را برای طول مدت اسکن (چند ثانیه تا چند ده ثانیه) کاملاً فریز می‌کرد؛
        # حالا در یک QThread جداگانه (NetworkScanThread) اجرا می‌شود.
        if self.network_scan_thread is not None and self.network_scan_thread.isRunning():
            return

        subnet = self.subnet_input.text().strip()
        # (2.0.31-beta) اعتبارسنجی رنج IP قبل از شروع اسکن؛ قالب‌های مجاز:
        # «192.168.1»، «192.168.1.20-192.168.1.80»، «192.168.1.20-80»،
        # «192.168.2.0/24» و ترکیب چند رنج با ویرگول.
        try:
            ip_count = len(parse_ip_range(subnet))
        except ValueError as ex:
            QMessageBox.warning(self, "رنج IP نامعتبر", str(ex))
            return
        self.scan_result_list.clear()
        self.scan_result_list.addItem(f"در حال اسکن {ip_count} آدرس...")
        # رفع باگ: حین اسکن، لیست نتایج غیرفعال می‌شود تا دابل‌کلیک روی ردیف
        # موقت «در حال اسکن...» (که IP واقعی نیست) تشخیص دستگاه را با IP
        # نامعتبر شروع نکند.
        self.scan_result_list.setEnabled(False)
        self.scan_btn.setEnabled(False)

        self.network_scan_thread = NetworkScanThread(subnet, self)
        self.network_scan_thread.finished_signal.connect(self._on_network_scan_finished)
        self.network_scan_thread.start()

    def _on_network_scan_finished(self, devices):
        self.scan_btn.setEnabled(True)
        self.scan_result_list.setEnabled(True)
        self.scan_result_list.clear()
        self._scan_ports_by_ip = {}
        if not devices:
            self.scan_result_list.addItem("هیچ دستگاهی یافت نشد.")
            return

        for dev in devices:
            self._scan_ports_by_ip[dev["ip"]] = dev["ports"]
            ports_str = ",".join(map(str, dev["ports"]))
            item = QListWidgetItem(f"{dev['ip']} (پورت‌ها: {ports_str})")
            # رفع درخواست: به‌جای انتخاب با Ctrl/Shift، کنار هر دستگاه یک
            # چک‌باکس قرار می‌گیرد تا کاربر با تیک زدن، دستگاه‌های موردنظر
            # برای اتصال هم‌زمان را مشخص کند.
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.scan_result_list.addItem(item)

    # ------------------------------------------------------------ close ---

    def closeEvent(self, event):
        self.camera_grid.stop_all()
        # جلوگیری از کرش هنگام بستن برنامه در حین اسکن شبکه/تشخیص نوع دستگاه:
        # Qt هنگام تخریب یک QThread که هنوز در حال اجراست، کرش می‌کند.
        if self.network_scan_thread is not None and self.network_scan_thread.isRunning():
            self.network_scan_thread.wait(3000)
        if self.detect_thread is not None and self.detect_thread.isRunning():
            self.detect_thread.cancel()
            self.detect_thread.wait(3000)
        # (2.0.15-beta) ترد اسکن پایداری شبکه هم باید صریحاً متوقف شود.
        _probe = getattr(self, "_net_probe_thread", None)
        if _probe is not None and _probe.isRunning():
            try:
                _probe.stop()
            except Exception:
                pass
            _probe.wait(5000)
        # رفع درخواست «سیستم تشخیص دود و اعلام حریق»: همان دلیل بالا - همه‌ی
        # تردهای مانیتور پنل‌های فیزیکی اعلام حریق باید قبل از بسته‌شدن
        # برنامه صریحاً متوقف شوند.
        for panel_id in list(self._fire_alarm_threads.keys()):
            self._stop_fire_alarm_monitor(panel_id)
        # رفع درخواست «اتصال به سیستم اعلام حریق ساختمان + صدای هشدار»:
        # قطع آژیر ممتد هنگام خروج از برنامه تا در پس‌زمینه ادامه پیدا نکند.
        if getattr(self, "alarm_player", None) is not None:
            try:
                self.alarm_player.stop()
            except Exception:
                pass

        # (2.0.15-beta) هنگام خروج از برنامه، رمزهای حافظه پاک می‌شوند؛
        # رمزهای رمزنگاری‌شده‌ی روی دیسک (در صورت فعال بودن ذخیره‌ی امن)
        # دست‌نخورده می‌مانند تا در اجرای بعدی بدون پرسیدن خوانده شوند.
        self.camera_store.clear_all_passwords()
        # همان نکته برای پنل‌های اعلام حریق فیزیکی (fire_alarm_store.py).
        self.fire_alarm_store.clear_all_passwords()
        # رفع درخواست: کادر یوزرنیم/پسوورد بالای پنل اسکن شبکه هم هرگز روی
        # دیسک ذخیره نشده (فقط QLineEdit در حافظه بود) و هنگام خروج از
        # برنامه صراحتاً پاک می‌شود.
        self.scan_user_input.clear()
        self.scan_pass_input.clear()
        # (2.0.16-beta) تخلیه‌ی صف رویدادهای گزارش و پایان تمیز ترد نویسنده
        # تا هیچ رویدادی هنگام خروج گم نشود.
        try:
            report_store.close()
        except Exception:
            pass
        event.accept()


if __name__ == "__main__":
    # پاک‌سازی فایل موتور آپدیت مرحله‌ی قبل (اگر آپدیتی انجام شده باشد)؛
    # باید قبل از هر کاری انجام شود چون خود موتور در حال اجرا نیست.
    try:
        from update_apply import cleanup_updater_copy
        cleanup_updater_copy()
    except Exception:
        pass
    # مهاجرت یک‌باره‌ی دیتای نسخه‌های قبلی (کنار exe) به پوشه‌ی یکتای دیتا؛
    # باید قبل از ساخته‌شدن هر استوری (CameraStore و…) اجرا شود.
    try:
        from app_paths import migrate_legacy_data
        migrate_legacy_data()
    except Exception:
        pass
    # (2.0.35-beta - پایداری تحویل) بکاپ روزانه‌ی خودکار دیتابیس‌ها؛
    # فقط یک‌بار در روز و فقط ۷ بکاپ آخر نگه داشته می‌شود.
    try:
        from app_paths import get_data_dir
        from db_maintenance import run_startup_maintenance
        run_startup_maintenance(get_data_dir())
    except Exception:
        pass
    # (2.0.15-beta - پایداری) تور امنیت سراسری: هر استثنای مهارنشده در
    # ترد اصلی (اسلات‌ها/هندلرهای رویداد) یا تردهای پس‌زمینه، به‌جای بستن
    # ناگهانی برنامه («Close Program») در فایل crash.log ثبت می‌شود و
    # برنامه به کارش ادامه می‌دهد تا علت قابل ردیابی باشد.
    try:
        from app_paths import get_data_dir
        _crash_log = os.path.join(get_data_dir(), "crash.log")
    except Exception:
        _crash_log = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "crash.log")

    def _log_crash(where, exc_type, exc_value, exc_tb):
        try:
            import traceback, datetime
            with open(_crash_log, "a", encoding="utf-8") as f:
                f.write("\n==== %s | %s ====\n" % (
                    datetime.datetime.now().isoformat(timespec="seconds"), where))
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass

    def _sys_excepthook(exc_type, exc_value, exc_tb):
        _log_crash("main-thread", exc_type, exc_value, exc_tb)

    def _thread_excepthook(args):
        _log_crash("thread:" + str(getattr(args, "thread", "?")),
                   args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _sys_excepthook
    try:
        threading.excepthook = _thread_excepthook
    except Exception:
        pass

    from PyQt6.QtWidgets import QApplication as _QApplication

    class _SafeApplication(_QApplication):
        """QApplication با notify محافظت‌شده: استثنا در هر اسلات/رویداد
        (شایع‌ترین علت «Close Program» در PyQt) مهار و لاگ می‌شود و حلقه‌ی
        رویداد ادامه می‌یابد."""
        def notify(self, receiver, event):
            try:
                return super().notify(receiver, event)
            except Exception:
                _log_crash("qt-notify:%r" % (receiver,), *sys.exc_info())
                return False

    app = _SafeApplication(sys.argv)
    apply_theme(app)  # تم تیره‌ی سازگار با لوگوی IAS Viewer
    # (2.0.72-beta) دروازه‌ی ورود: نام‌کاربری + رمز عبور. در حالت موتور
    # آپدیت (--apply-update بالاتر خارج شد) و در تست‌ها (IAS_SKIP_LOGIN=1)
    # لاگین رد می‌شود.
    current_user = None
    if os.environ.get("IAS_SKIP_LOGIN") != "1":
        try:
            from login_dialog import run_login
            current_user = run_login()
        except Exception as e:
            print("login failed:", e)
            current_user = None
        if current_user is None:
            sys.exit(0)
    window = MainWindow(current_user=current_user)
    # برنامه از ابتدا ماکسیمایز باز می‌شود (درخواست قبلی کاربر).
    window.showMaximized()
    sys.exit(app.exec())
