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

# (1.2.0) دیوار دوربین‌ها: UI مرکزی صفحه‌ی اصلی به‌صورت ویجت
# قابل‌استفاده‌ی مجدد (single source of truth برای پنجره‌ی اصلی و نماها).
from camera_page import (CameraPageWidget, CameraGridWidget, CameraSlotWidget,
                         RegionManagerDialog, _bgr_to_pixmap, _play_alarm_beep,
                         _PROCESS_EVERY_N)
from main_page import MainPageWidget
from camera_stream import CameraStreamThread, region_to_polygon, is_low_spec_mode
from video_display_label import VideoDisplayLabel
# (2.0.61-beta) منطق خالص تشخیص قطع/وصل مجدد تصویر (بدون وابستگی به Qt)
from video_loss import transition as _video_loss_transition, cooldown_ok as _video_loss_cooldown_ok
# نکته: floor_detector عمداً در بالای فایل import نمی‌شود؛ transformers و
# SegFormer چند صد مگابایت رم می‌گیرند. فقط وقتی کاربر واقعاً دکمه‌ی «تشخیص
# هوشمند زمین» را بزند، در همان لحظه بارگذاری می‌شود - رجوع کنید به
# _get_floor_detect_thread در camera_page.py.
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

# نگاشت تعداد نمایش هم‌زمان دوربین‌ها به چیدمان (ردیف, ستون) شبکه‌ی نمایش.
# اعداد دقیقاً همان مقادیر درخواستی هستند: 1، 4، 9، 16، 32، 64.




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




class MainWindow(QMainWindow):
    # (2.0.72-beta) نتیجه‌ی بررسی آپدیت از ترد پس‌زمینه به ترد UI
    update_check_done = pyqtSignal(object)

    @property
    def camera_grid(self):
        """شبکه‌ی دوربین‌ها — از طریق دیوار (سازگاری به عقب)."""
        return self.camera_wall.camera_grid

    # (1.2.0) پراپرتی‌های سازگاری: این attributeها حالا در MainPageWidget
    # زندگی می‌کنند؛ کدهای قدیمی بدون تغییر کار می‌کنند.
    @property
    def camera_wall(self):
        return self.main_page.camera_page

    @property
    def camera_list(self):
        return self.main_page.sidebar.camera_list

    @property
    def events_panel_list(self):
        return self.main_page.events_panel.events_panel_list

    @property
    def fire_panel_list(self):
        return self.main_page.events_panel.fire_panel_list

    @property
    def fire_panel_group(self):
        return self.main_page.events_panel.fire_panel_group

    @property
    def splitter(self):
        return self.main_page.splitter

    @property
    def left_widget(self):
        return self.main_page.left_widget

    @property
    def right_splitter(self):
        return self.main_page.events_panel.right_splitter

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
        self.init_ui()
        # (لیست دوربین‌ها در سازنده‌ی CameraSidebarWidget بارگذاری می‌شود.)
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

        # (1.2.0) کل لایه‌ی صفحه‌ی اصلی (چپ + وسط + راست) به‌صورت ویجت
        # قابل‌استفاده‌ی مجدد استخراج شد (main_page.py — MainPageWidget)؛
        # هم در پنجره‌ی اصلی و هم در پنجره‌های شناور «نما»ی اتاق مانیتورینگ
        # از همان استفاده می‌شود (single source of truth). داده‌ها مشترک‌اند.
        self.main_page = MainPageWidget(
            self.face_engine, self.camera_store,
            on_face_event=self.on_face_event,
            on_external_camera_drop=self.on_camera_dropped_on_grid,
            on_region_alert=self.on_region_alert,
            on_fire_event=self.on_fire_event,
            on_plate_event=self.on_plate_event,
            on_person_event=self.on_person_event,
            on_open_monitor_room=self._on_monitor_room_clicked,
            alarm_player=self.alarm_player,
            building_fire=self.building_fire,
            on_building_fire_log=self._on_building_fire_log,
            status_message=lambda msg, ms=8000: self.statusBar().showMessage(msg, ms),
            report_store=report_store,
            parent=self,
        )
        self.main_page.camera_page.detector_status_changed.connect(
            self._refresh_person_detector_status)
        # (2.0.61-beta) هشدار قطع تصویر هر خانه به MainWindow وصل می‌شود
        # تا در «پنل رویدادها» ثبت و بوق هشدار پخش شود.
        self.main_page.camera_page.video_loss.connect(self._on_video_loss)
        home_widget = QWidget()
        home_layout = QHBoxLayout(home_widget)
        home_layout.setContentsMargins(0, 0, 0, 0)
        home_layout.addWidget(self.main_page)

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
        self.main_page.events_panel._refresh_fire_panel_visibility()
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
                on_person_toggle=self._on_camera_person_toggle,
                alarm_player=self.alarm_player, building_fire=self.building_fire,
                on_building_fire_log=self._on_building_fire_log)
        try:
            self._monitor_room_mgr.open_setup_dialog(self)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن اتاق مانیتورینگ ممکن نشد:\n{e}")

    # ----------------------------------------------------- محدوده‌ی هشدار --


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









    def _save_audio_creds(self, cam_id, user, pwd):
        """(2.0.110-beta) ذخیره‌ی یوزر/پس جدیدی که کاربر برای صدا وارد کرد."""
        try:
            self.camera_store.update_camera(cam_id, **{"user": user, "pass": pwd})
        except Exception:
            pass




















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
        self.main_page.events_panel._refresh_fire_panel_visibility()

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


    def closeEvent(self, event):
        self.camera_grid.stop_all()
        # جلوگیری از کرش هنگام بستن برنامه در حین اسکن شبکه/تشخیص نوع دستگاه:
        # Qt هنگام تخریب یک QThread که هنوز در حال اجراست، کرش می‌کند.
        _sb = self.main_page.sidebar
        if _sb.network_scan_thread is not None and _sb.network_scan_thread.isRunning():
            _sb.network_scan_thread.wait(3000)
        if _sb.detect_thread is not None and _sb.detect_thread.isRunning():
            _sb.detect_thread.cancel()
            _sb.detect_thread.wait(3000)
        # (2.0.15-beta) ترد اسکن پایداری شبکه هم باید صریحاً متوقف شود.
        _probe = getattr(_sb, "_net_probe_thread", None)
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
        self.main_page.sidebar.scan_user_input.clear()
        self.main_page.sidebar.scan_pass_input.clear()
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
