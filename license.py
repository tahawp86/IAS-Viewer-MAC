# -*- coding: utf-8 -*-
"""license.py — مدیریت فایل لایسنس IAS-CMS.

مدل لایسنس:
- فایل `license.lic` یک JSON است: {"format", "payload", "signature"}
- payload شامل: مشتری، تاریخ صدور/انقضا، شناسه‌ی سخت‌افزاری (اختیاری)،
  سهمیه‌ی قابلیت‌ها و امضای Ed25519 روی بایت‌های کانونیکال payload.
- امضا با کلید خصوصی فروشنده (فقط نزد طه) در tools/make_license.py ساخته
  می‌شود؛ برنامه فقط با کلید عمومی جاسازی‌شده در license_crypto صحت را
  بررسی می‌کند. دست‌کاری فایل لایسنس امضا را باطل می‌کند.

رفتار بدون لایسنس معتبر: حالت محدود (۱ دوربین، بدون قابلیت هوشمند).
"""

import json
import os
import sys
from datetime import date

try:
    from license_crypto import LICENSE_PUBLIC_KEY, verify_message
except ImportError:  # اجرای مستقیم از tools/
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from license_crypto import LICENSE_PUBLIC_KEY, verify_message

LICENSE_FORMAT = "IAS-CMS-LICENSE-1"
LICENSE_FILENAME = "license.lic"

# سهمیه‌های حالت محدود (وقتی لایسنس معتبر نیست):
# تصویر همه‌ی دوربین‌ها نمایش داده می‌شود (cameras=0 یعنی نامحدود) ولی
# هیچ‌یک از قابلیت‌های سهمیه‌ای فعال نمی‌شوند.
DEMO_QUOTAS = {
    "cameras": 0,
    "plate": 0,
    "fire": 0,
    "person_tracking": 0,
}

# ترتیب نمایش سهمیه‌ها در دیالوگ
QUOTA_ORDER = ["cameras", "plate", "fire", "person_tracking"]

# ----------------------------------------------------------------------------
# قابلیت‌های روشن/خاموش (بدون سهمیه‌ی تعدادی — فقط فعال یا غیرفعال).
# در فایل لایسنس با کلید "features" می‌آیند؛ اگر در لایسنسی نباشند،
# پیش‌فرض «فعال» است تا لایسنس‌های قبلی نشکنند.
# ----------------------------------------------------------------------------
FEATURE_DEFS = {
    "people_counting": "شمارش افراد",
    "face_recognition": "چهره‌خوان (شناسایی چهره)",
    "zone_alerts": "هشدار ورود به محدوده",
}
FEATURE_ORDER = ["people_counting", "face_recognition", "zone_alerts"]

# حالت محدود (بدون لایسنس معتبر): فقط شمارش افراد فعال است
RESTRICTED_FEATURES = {
    "people_counting": True,
    "face_recognition": False,
    "zone_alerts": False,
}

# ----------------------------------------------------------------------------
# اسکیمای v2 — رجیستری کانونیکال قابلیت‌ها (پلتفرم-agnostic)
# (هم‌رفتار با license_common.py در ریپوی IAS-LicenseManager — سند مرجع:
# docs/LICENSE_SCHEMA.md؛ هر تغییری باید در هر دو جا هم‌زمان اعمال شود.)
# هر قابلیت: {"enabled": bool, "count": int}
#   - enabled=False یعنی قابلیت کاملاً خاموش (دکمه‌ی صفحه مخفی + دسترسی گیت‌شده)
#   - enabled=True و count=۰ یعنی فعال بدون محدودیت تعداد
#   - enabled=True و count=N>۰ یعنی حداکثر N دوربین می‌توانند از آن استفاده کنند
# «cameras» همیشه enabled است و سقف کلی همه‌ی سهمیه‌هاست.
# ----------------------------------------------------------------------------
SCHEMA_VERSION = 2

FEATURE_SCHEMA = [
    ("cameras", "تعداد کل دوربین‌ها"),
    ("plate", "دوربین‌های پلاک‌خوان"),
    ("fire", "دوربین‌های تشخیص حریق"),
    ("person_tracking", "دوربین‌های ردیابی اشخاص"),
    ("face_recognition", "دوربین‌های چهره‌خوان"),
    ("region_alert", "دوربین‌های هشدار محدوده"),
    ("people_counting", "دوربین‌های شمارش افراد"),
]
FEATURE_KEYS = [k for k, _ in FEATURE_SCHEMA]
FEATURE_TITLES = dict(FEATURE_SCHEMA)

# نگاشت کلیدهای قدیمی (v1) به کلید کانونیکال — فقط برای خواندن/مهاجرت
LEGACY_ALIASES = {
    "zone_alerts": "region_alert",   # تیک قدیمی «هشدار ورود به محدوده»
}

# وضعیت قابلیت‌ها در حالت محدود (بدون لایسنس معتبر) — هم‌معنی با
# DEMO_QUOTAS / RESTRICTED_FEATURES بالا: تصویر دوربین‌ها آزاد، فقط
# شمارش افراد فعال، بقیه‌ی قابلیت‌های لایسنسی قفل (deny-by-default).
RESTRICTED_FEATURE_CONFIG = {
    "cameras": {"enabled": True, "count": 0},
    "plate": {"enabled": False, "count": 0},
    "fire": {"enabled": False, "count": 0},
    "person_tracking": {"enabled": False, "count": 0},
    "face_recognition": {"enabled": False, "count": 0},
    "region_alert": {"enabled": False, "count": 0},
    "people_counting": {"enabled": True, "count": 0},
}


def _norm_entry(v) -> tuple:
    """نرمال‌سازی یک ورودی feature_config به (enabled, count)."""
    if isinstance(v, dict):
        enabled = bool(v.get("enabled", True))
        try:
            count = max(0, int(v.get("count", 0) or 0))
        except (TypeError, ValueError):
            count = 0
        return enabled, count
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        try:
            return bool(v[0]), max(0, int(v[1] or 0))
        except (TypeError, ValueError):
            return bool(v[0]), 0
    if isinstance(v, bool):
        return v, 0
    return True, 0


def migrate_v1_to_v2(payload: dict) -> dict:
    """مهاجرت payload قدیمی (v1) به feature_config کانونیکال — برای خواننده‌ها."""
    quotas = payload.get("quotas") or {}
    qe = payload.get("quota_enabled") or {}
    feats = payload.get("features") or {}
    fc = {}
    for key in FEATURE_KEYS:
        try:
            count = max(0, int(quotas.get(key, 0)))
        except (TypeError, ValueError):
            count = 0
        enabled = bool(qe.get(key, True))
        if key == "cameras":
            enabled = True
        fc[key] = {"enabled": enabled, "count": count}
    # تیک‌های flat قدیمی (people_counting/face_recognition/zone_alerts):
    # خاموش بودن هرکدام یعنی قابلیت کانونیکال متناظر خاموش است.
    for legacy_key, v in feats.items():
        canon = LEGACY_ALIASES.get(legacy_key, legacy_key)
        if canon in fc and not v:
            fc[canon]["enabled"] = False
    return fc


def normalize_features(payload: dict) -> dict:
    """خروجی یکدست برای خواننده‌ها در هر دو نسخه: {key: {"enabled", "count"}}."""
    if isinstance(payload, dict) and payload.get("schema_version", 1) >= 2 \
            and isinstance(payload.get("feature_config"), dict):
        fc = {}
        for key in FEATURE_KEYS:
            enabled, count = _norm_entry(payload["feature_config"].get(key, (True, 0)))
            if key == "cameras":
                enabled = True
            fc[key] = {"enabled": enabled, "count": count}
        return fc
    return migrate_v1_to_v2(payload or {})


def feature_enabled(fc: dict, key: str) -> bool:
    """آیا این قابلیت در لایسنس فعال است؟ (برای نمایش/مخفی دکمه‌ی صفحه و گیت دسترسی)"""
    return bool((fc or {}).get(key, {}).get("enabled", False))


def feature_count(fc: dict, key: str) -> int:
    """سقف تعداد دوربین این قابلیت؛ ۰ یعنی نامحدود."""
    try:
        return max(0, int((fc or {}).get(key, {}).get("count", 0)))
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------------------
# مسیرها
# ----------------------------------------------------------------------------
def _app_dirs():
    """پوشه‌هایی که فایل لایسنس در آن‌ها جست‌وجو می‌شود."""
    dirs = []
    # کنار فایل اجرایی (حالت exe / portable)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
    # پوشه‌ی داده‌ی برنامه
    try:
        from app_paths import get_data_dir
        dirs.append(get_data_dir())
    except Exception:
        pass
    # کنار سورس (حالت توسعه)
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    # پوشه‌ی جاری
    dirs.append(os.getcwd())
    seen, out = set(), []
    for d in dirs:
        d = os.path.abspath(d)
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_license_file():
    for d in _app_dirs():
        p = os.path.join(d, LICENSE_FILENAME)
        if os.path.isfile(p):
            return p
    return None


def default_license_path():
    """مسیر پیشنهادی برای ذخیره‌ی لایسنس بارگذاری‌شده."""
    try:
        from app_paths import get_data_dir
        return os.path.join(get_data_dir(), LICENSE_FILENAME)
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            LICENSE_FILENAME)


# ----------------------------------------------------------------------------
# شناسه‌ی سخت‌افزاری
# ----------------------------------------------------------------------------
def get_hwid():
    """شناسه‌ی یکتای این سیستم (برای قفل سخت‌افزاری لایسنس)."""
    # مک: IOPlatformUUID
    try:
        import platform_compat as _pc
        if _pc.is_macos():
            hwid = _pc.macos_hwid()
            if hwid:
                return hwid
    except Exception:
        pass
    # ویندوز: MachineGuid رجیستری
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
            if guid:
                return "WIN-" + str(guid).upper()
    except Exception:
        pass
    # جایگزین: MAC آدرس
    try:
        import uuid
        return "MAC-%012X" % (uuid.getnode() & 0xFFFFFFFFFFFF)
    except Exception:
        return "UNKNOWN"


# ----------------------------------------------------------------------------
# ساخت / بررسی امضا
# ----------------------------------------------------------------------------
def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def create_license_file(payload: dict, private_hex: str) -> dict:
    """ساخت دیکشنری لایسنس امضاشده (استفاده در ابزار فروشنده)."""
    from license_crypto import sign_message
    sig = sign_message(private_hex, canonical_bytes(payload))
    return {"format": LICENSE_FORMAT, "payload": payload, "signature": sig}


def verify_license_data(data: dict):
    """بررسی ساختار و امضای لایسنس؛ برمی‌گرداند (payload, error)."""
    if not isinstance(data, dict):
        return None, "ساختار فایل لایسنس نامعتبر است."
    if data.get("format") != LICENSE_FORMAT:
        return None, "نسخه‌ی فرمت لایسنس پشتیبانی نمی‌شود."
    payload = data.get("payload")
    sig = data.get("signature")
    if not isinstance(payload, dict) or not isinstance(sig, str):
        return None, "ساختار فایل لایسنس نامعتبر است."
    if not verify_message(LICENSE_PUBLIC_KEY, canonical_bytes(payload), sig):
        return None, "امضای لایسنس معتبر نیست (فایل دست‌کاری شده)."
    return payload, None


def check_payload_validity(payload: dict):
    """بررسی انقضا و قفل سخت‌افزاری؛ برمی‌گرداند خطا یا None."""
    exp = payload.get("expires")
    if exp:
        try:
            exp_d = date.fromisoformat(str(exp))
        except ValueError:
            return "تاریخ انقضای لایسنس نامعتبر است."
        if date.today() > exp_d:
            return "مدت لایسنس به پایان رسیده است."
    hwid = payload.get("hwid")
    if hwid and str(hwid).upper() != get_hwid().upper():
        return "این لایسنس متعلق به سیستم دیگری است."
    return None


# ----------------------------------------------------------------------------
# بارگذاری و وضعیت
# ----------------------------------------------------------------------------
class LicenseState:
    def __init__(self, valid=False, customer="", issued="", expires="",
                 quotas=None, hwid=None, features=None, quota_enabled=None,
                 feature_config=None, error="", path=""):
        self.valid = valid
        self.customer = customer
        self.issued = issued
        self.expires = expires
        self.quotas = quotas or dict(DEMO_QUOTAS)
        self.hwid = hwid
        # دیفالت «فعال» برای سازگاری با لایسنس‌های قدیمی بدون فیلد features؛
        # حالت محدود (نامعتبر) در effective_features جداگانه مدیریت می‌شود.
        self.features = features if features is not None else \
            {k: True for k in FEATURE_ORDER}
        # فعال/غیرفعال بودن سهمیه‌های تعدادی؛ نبود در لایسنس = فعال
        # (سازگاری با لایسنس‌های صادرشده قبل از v1.2.0 نرم‌افزار صدور)
        self.quota_enabled = quota_enabled if quota_enabled is not None else \
            {k: True for k in QUOTA_ORDER}
        # خروجی کانونیکال normalize_features (اسکیمای v2) — مرجع گیت
        # صفحات و قابلیت‌ها. برای حالت نامعتبر از حالت محدود استفاده می‌شود.
        self.feature_config = feature_config
        self.error = error
        self.path = path

    def get_feature_config(self):
        """feature_config مؤثر: لایسنس معتبر ← normalize، وگرنه حالت محدود."""
        if self.valid and isinstance(self.feature_config, dict) \
                and len(self.feature_config):
            return self.feature_config
        return {k: dict(v) for k, v in RESTRICTED_FEATURE_CONFIG.items()}

    @property
    def days_left(self):
        if not self.valid or not self.expires:
            return None
        try:
            return (date.fromisoformat(self.expires) - date.today()).days
        except ValueError:
            return None


def load_license(path=None) -> LicenseState:
    p = path or find_license_file()
    if not p:
        return LicenseState(error="فایل لایسنس یافت نشد.")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return LicenseState(error="فایل لایسنس خراب است.", path=p)
    payload, err = verify_license_data(data)
    if err:
        return LicenseState(error=err, path=p)
    err = check_payload_validity(payload)
    if err:
        return LicenseState(error=err, path=p,
                            customer=payload.get("customer", ""),
                            expires=payload.get("expires") or "")
    # اسکیمای v2: قابلیت‌ها فقط از normalize_features خوانده می‌شوند —
    # برای فایل‌های v1 مهاجرت در حافظه انجام می‌شود (قرارداد خواننده،
    # سند docs/LICENSE_SCHEMA.md در ریپوی IAS-LicenseManager، بخش ۳).
    fc = normalize_features(payload)
    quotas = {}
    quota_enabled = {}
    for k in FEATURE_KEYS:
        # count عیناً از normalize می‌آید (۰ = نامحدود). «سقف مؤثر =
        # کمینه‌ی سقف قابلیت و سقف cameras» در سطح مصرف خودبه‌خود برقرار
        # است: مصرف هر قابلیت از تعداد کل دوربین‌ها بیشتر نمی‌شود و سقف
        # کل دوربین‌ها هنگام افزودن دوربین اعمال می‌شود (check_quota).
        quotas[k] = feature_count(fc, k)
        quota_enabled[k] = feature_enabled(fc, k)
    # نگاشت به کلیدهای قدیمی features برای سازگاری با گیت‌های موجود
    features = {
        "people_counting": feature_enabled(fc, "people_counting"),
        "face_recognition": feature_enabled(fc, "face_recognition"),
        "zone_alerts": feature_enabled(fc, "region_alert"),
    }
    return LicenseState(
        valid=True,
        customer=payload.get("customer", ""),
        issued=payload.get("issued", ""),
        expires=payload.get("expires") or "",
        quotas=quotas,
        hwid=payload.get("hwid"),
        features=features,
        quota_enabled=quota_enabled,
        feature_config=fc,
        path=p,
    )


def effective_quotas(state=None) -> dict:
    """سهمیه‌های مؤثر برنامه: از لایسنس معتبر، وگرنه حالت محدود."""
    st = state or load_license()
    if st.valid:
        return dict(st.quotas)
    return dict(DEMO_QUOTAS)


def effective_features(state=None) -> dict:
    """وضعیت قابلیت‌های روشن/خاموش؛ بدون لایسنس معتبر = حالت محدود
    (فقط شمارش افراد)."""
    st = state or load_license()
    if st.valid:
        return dict(st.features)
    return dict(RESTRICTED_FEATURES)


# نگاشت معکوس: کلید کانونیکال ← کلید قدیمی‌ای که effective_features با آن
# کار می‌کند (region_alert ← zone_alerts)
_CANONICAL_TO_LEGACY = {v: k for k, v in LEGACY_ALIASES.items()}


def is_feature_enabled(name: str, state=None) -> bool:
    """آیا قابلیت on/off داده‌شده فعال است؟"""
    # کلید کانونیکال اسکیمای v2 هم پذیرفته می‌شود (region_alert = zone_alerts)
    name = _CANONICAL_TO_LEGACY.get(name, name)
    try:
        return bool(effective_features(state).get(name, False))
    except Exception:
        return False


def license_feature_enabled(key: str, state=None) -> bool:
    """گیت قابلیت بر اساس اسکیمای v2 (قرارداد خواننده‌ی سند اسکیما):

    - لایسنس معتبر: فقط خروجی normalize_features ملاک است
      (feature_enabled روی feature_config).
    - بدون لایسنس معتبر: حالت محدود — قابلیت‌های لایسنسی قفل‌اند
      (deny-by-default)؛ فقط cameras و people_counting باز می‌مانند.
    کلیدهای ناشناخته همیشه False برمی‌گردانند.
    """
    try:
        st = state or load_license()
        fc = st.get_feature_config()
        return feature_enabled(fc, key)
    except Exception:
        return False


def license_feature_limit(key: str, state=None) -> int:
    """سقف تعداد دوربین یک قابلیت از خود لایسنس؛ ۰ = نامحدود.

    سقف مؤثر هنگام مصرف = کمینه‌ی این عدد و سقف cameras (قرارداد
    اسکیما)؛ در عمل مصرف هیچ قابلیتی از تعداد کل دوربین‌ها بیشتر
    نمی‌شود و سقف کل هنگام افزودن دوربین اعمال می‌شود."""
    try:
        st = state or load_license()
        if not st.valid:
            return 0
        return int(st.quotas.get(key, 0))
    except Exception:
        return 0


def is_quota_enabled(name: str, state=None) -> bool:
    """آیا سهمیه‌ی تعدادی داده‌شده در لایسنس فعال است؟

    بدون لایسنس معتبر False برمی‌گرداند (حالت محدود جداگانه مدیریت
    می‌شود)؛ لایسنس‌های قدیمی بدون فیلد quota_enabled همه را فعال
    در نظر می‌گیرند.
    """
    try:
        st = state or load_license()
        if not st.valid:
            return False
        return bool(st.quota_enabled.get(name, True))
    except Exception:
        return True


def install_license_file(src_path: str) -> str:
    """کپی فایل لایسنس به محل استاندارد؛ مسیر نهایی را برمی‌گرداند."""
    dst = default_license_path()
    with open(src_path, "rb") as f:
        blob = f.read()
    # اعتبارسنجی قبل از نصب
    data = json.loads(blob.decode("utf-8"))
    payload, err = verify_license_data(data)
    if err:
        raise ValueError(err)
    err = check_payload_validity(payload)
    if err:
        raise ValueError(err)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(blob)
    return dst


# ----------------------------------------------------------------------------
# دیالوگ مدیریت لایسنس (از صفحه‌ی تنظیمات ← بخش لایسنس ← «مشاهده‌ی لایسنس»)
# ----------------------------------------------------------------------------
def _quota_title(key: str) -> str:
    try:
        from admin_quota import quota_title
        return quota_title(key)
    except Exception:
        return {"cameras": "تعداد کل دوربین‌ها", "plate": "دوربین‌های پلاک‌خوان",
                "fire": "دوربین‌های تشخیص حریق",
                "person_tracking": "دوربین‌های ردیابی اشخاص"}.get(key, key)


def open_license_dialog(parent, camera_store=None):
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                                 QFileDialog, QMessageBox, QGridLayout,
                                 QApplication)
    from PyQt6.QtCore import Qt

    state = load_license()

    dlg = QDialog(parent)
    dlg.setWindowTitle("🔑 مدیریت لایسنس")
    dlg.setMinimumWidth(430)
    dlg.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    lay = QVBoxLayout(dlg)

    if state.valid:
        status = f"✅ <b>لایسنس معتبر</b> — {state.customer or 'بدون نام'}"
    else:
        status = (f"⛔ <b>لایسنس معتبر نیست</b> — حالت محدود فعال است"
                  f"<br><span style='color:#a00'>{state.error}</span>")
    lab = QLabel(status)
    lab.setWordWrap(True)
    lay.addWidget(lab)

    grid = QGridLayout()
    grid.addWidget(QLabel("مشتری:"), 0, 0)
    grid.addWidget(QLabel(state.customer or "—"), 0, 1)
    grid.addWidget(QLabel("تاریخ انقضا:"), 1, 0)
    grid.addWidget(QLabel(state.expires or "دائمی"), 1, 1)
    if state.valid and state.days_left is not None:
        grid.addWidget(QLabel("روزهای باقی‌مانده:"), 2, 0)
        grid.addWidget(QLabel(str(state.days_left)), 2, 1)
    grid.addWidget(QLabel("قفل سخت‌افزاری:"), 3, 0)
    grid.addWidget(QLabel("فعال" if state.hwid else "غیرفعال"), 3, 1)
    lay.addLayout(grid)

    lay.addWidget(QLabel("<b>سهمیه‌های این لایسنس:</b>"))
    qgrid = QGridLayout()
    try:
        from admin_quota import count_usage
        usage = {k: count_usage(camera_store, k) for k in QUOTA_ORDER} \
            if camera_store else {}
    except Exception:
        usage = {}
    for i, key in enumerate(QUOTA_ORDER):
        q = state.quotas.get(key, 0)
        u = usage.get(key, 0) if isinstance(usage, dict) else 0
        if state.valid and not state.quota_enabled.get(key, True):
            qtext = "❌ غیرفعال"
        elif q == 0:
            qtext = "نامحدود"
        else:
            qtext = f"{u} از {q}"
        qgrid.addWidget(QLabel(_quota_title(key) + ":"), i, 0)
        qgrid.addWidget(QLabel(qtext), i, 1)
    lay.addLayout(qgrid)

    lay.addWidget(QLabel("<b>قابلیت‌ها:</b>"))
    fgrid = QGridLayout()
    feats = effective_features(state)
    for i, key in enumerate(FEATURE_ORDER):
        on = feats.get(key, False)
        fgrid.addWidget(QLabel(FEATURE_DEFS.get(key, key) + ":"), i, 0)
        fgrid.addWidget(QLabel("✅ فعال" if on else "❌ غیرفعال"), i, 1)
    lay.addLayout(fgrid)

    hwid_row = QLabel(f"شناسه‌ی سخت‌افزاری این سیستم:<br><code>{get_hwid()}</code>")
    hwid_row.setWordWrap(True)
    hwid_row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(hwid_row)

    def _copy_hwid():
        QApplication.clipboard().setText(get_hwid())
        QMessageBox.information(dlg, "کپی شد",
                                "شناسه‌ی سخت‌افزاری در حافظه کپی شد.")

    def _load_file():
        fp, _ = QFileDialog.getOpenFileName(
            dlg, "انتخاب فایل لایسنس", "", "License (*.lic);;All (*)")
        if not fp:
            return
        try:
            dst = install_license_file(fp)
        except Exception as e:
            QMessageBox.warning(dlg, "خطا", f"لایسنس پذیرفته نشد:\n{e}")
            return
        QMessageBox.information(
            dlg, "انجام شد",
            f"لایسنس با موفقیت نصب شد:\n{dst}\n\nبرنامه را یک‌بار ببندید و دوباره باز کنید.")
        dlg.accept()

    btn_copy = QPushButton("📋 کپی شناسه‌ی سخت‌افزاری")
    btn_copy.clicked.connect(_copy_hwid)
    lay.addWidget(btn_copy)

    btn_load = QPushButton("📂 بارگذاری فایل لایسنس…")
    btn_load.clicked.connect(_load_file)
    lay.addWidget(btn_load)

    btn_close = QPushButton("بستن")
    btn_close.clicked.connect(dlg.accept)
    lay.addWidget(btn_close)

    dlg.exec()


# ----------------------------------------------------------------------------
# اطلاع‌رسانی «چه قابلیت‌هایی باز شد»: بعد از آپلود فایل لایسنس در تنظیمات،
# تفاوت وضعیت قبلی و جدید محاسبه و در یک پنجره‌ی اطلاعیه نمایش داده می‌شود.
# ----------------------------------------------------------------------------

def _fmt_quota_limit(v) -> str:
    try:
        return "نامحدود" if int(v or 0) == 0 else str(int(v))
    except Exception:
        return "نامحدود"


def describe_license_changes(old_state, new_state) -> list:
    """مقایسه‌ی دو وضعیت لایسنس؛ خروجی: خطوط فارسی تغییرات (فعال/غیرفعال/سقف)."""
    lines = []
    try:
        old_f = effective_features(old_state)
        new_f = effective_features(new_state)
        for k in FEATURE_ORDER:
            o, n = bool(old_f.get(k)), bool(new_f.get(k))
            name = FEATURE_DEFS.get(k, k)
            if n and not o:
                lines.append(f"✅ {name} فعال شد")
            elif o and not n:
                lines.append(f"⛔ {name} غیرفعال شد")
        old_q = effective_quotas(old_state)
        new_q = effective_quotas(new_state)
        for k in QUOTA_ORDER:
            o_en = is_quota_enabled(k, old_state)
            n_en = is_quota_enabled(k, new_state)
            title = _quota_title(k)
            if n_en and not o_en:
                lines.append(
                    f"✅ {title} فعال شد — سقف: {_fmt_quota_limit(new_q.get(k, 0))}")
            elif o_en and not n_en:
                lines.append(f"⛔ {title} غیرفعال شد")
            elif n_en and o_en:
                try:
                    ov, nv = int(old_q.get(k, 0) or 0), int(new_q.get(k, 0) or 0)
                except Exception:
                    ov, nv = 0, 0
                if ov != nv:
                    lines.append(
                        f"🔼 سقف {title}: {_fmt_quota_limit(ov)} ← {_fmt_quota_limit(nv)}")
    except Exception:
        pass
    return lines


def show_license_announcement(parent, old_state, new_state, extra_lines=None):
    """پنجره‌ی اطلاعیه بعد از آپلود لایسنس: چه قابلیت‌هایی باز/بسته شد.

    extra_lines: خطوط گزارش اقدامات خودکار (مثلاً حذف قابلیت از دوربین‌های
    اضافی چون سقف لایسنس کم شده) که زیر تغییرات نشان داده می‌شود.
    """
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton)
    from PyQt6.QtCore import Qt
    changes = describe_license_changes(old_state, new_state)
    dlg = QDialog(parent)
    dlg.setWindowTitle("لایسنس فعال شد")
    dlg.setMinimumWidth(440)
    dlg.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    lay = QVBoxLayout(dlg)
    lay.setSpacing(10)
    lay.addWidget(QLabel("🎉 <b>لایسنس با موفقیت نصب و فعال شد.</b>"))
    try:
        cust = (new_state.customer or "").strip() if new_state else ""
        exp = (new_state.expires or "").strip() if new_state else ""
        info = []
        if cust:
            info.append(f"مشتری: <b>{cust}</b>")
        info.append(f"انقضا: <b>{exp or 'دائمی'}</b>")
        lbl = QLabel("<br>".join(info))
        lbl.setStyleSheet("color: #555; font-size: 12px;")
        lay.addWidget(lbl)
    except Exception:
        pass
    if changes:
        lay.addWidget(QLabel("<b>تغییرات این لایسنس:</b>"))
        body = QLabel("<br>".join(changes))
        body.setWordWrap(True)
        body.setStyleSheet("font-size: 13px;")
        lay.addWidget(body)
    else:
        lay.addWidget(QLabel("تغییری در قابلیت‌ها نسبت به قبل ایجاد نشد."))
    if extra_lines:
        lay.addWidget(QLabel("<b>اقدامات خودکار روی دوربین‌ها:</b>"))
        ebody = QLabel("<br>".join(extra_lines))
        ebody.setWordWrap(True)
        ebody.setStyleSheet("font-size: 13px;")
        lay.addWidget(ebody)
    lay.addWidget(QLabel(
        "<span style='color:#888; font-size:11px;'>قابلیت‌های مجاز هم‌اکنون "
        "فعال‌اند؛ نیازی به بستن و باز کردن برنامه نیست.</span>"))
    ok = QPushButton("باشه")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    lay.addWidget(ok)
    dlg.exec()
