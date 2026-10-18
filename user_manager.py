# -*- coding: utf-8 -*-
"""user_manager.py — مدیریت کاربران و سطح دسترسی IAS Viewer.

مدل امنیتی (2.0.72-beta):
  * ورود به برنامه با نام‌کاربری + رمز عبور اجباری است.
  * رمزها هرگز متن‌ساده ذخیره نمی‌شوند: PBKDF2-HMAC-SHA256 با ۲۰۰هزار
    تکرار و salt تصادفی ۱۶ بایتی (فقط کتابخانه‌ی استاندارد پایتون —
    بدون وابستگی جدید، طبق قانون «بدون نصب روی ویندوز کاربر»).
  * کاربر پیش‌فرض کارخانه: admin — در اولین ورود ادمین،
    تعویض اجباری رمز فعال می‌شود (must_change_password) چون این رمز در
    سورس عمومی ریپو دیده می‌شود.
  * ادمین می‌تواند کاربر تعریف کند و برای هر کاربر مشخص کند به کدام
    صفحه‌ها دسترسی دارد (home/fire/face/reports/plate/person/map/settings).
    «لایسنس» همیشه مخصوص ادمین است و در این مدل سطح‌بندی نمی‌شود.
"""

import hashlib
import json
import os
import secrets

try:
    from app_paths import get_data_dir
    _DATA_DIR = get_data_dir()
except Exception:
    _DATA_DIR = os.path.dirname(os.path.abspath(__file__))

USERS_FILE = os.path.join(_DATA_DIR, "users.json")

# رمز پیش‌فرض کارخانه‌ی ادمین — در اولین ورود باید عوض شود.
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "A12345678a"

# کاربر تست پیش‌فرض (درخواست طه، 2026-09-29) — فقط صفحه‌ی اصلی و تنظیمات.
DEFAULT_TEST_USER = "User"
DEFAULT_USER_PASS = "123456"
USER_PAGES = ("home", "settings")

_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16

# صفحه‌های قابل سطح‌بندی (کلید → لیبل فارسی)؛ ترتیب = ترتیب هدر.
ACCESS_PAGES = [
    ("home", "🏠 صفحه اصلی (پخش زنده)"),
    ("fire", "🔥 اعلام حریق"),
    ("face", "👤 چهره‌ها"),
    ("reports", "📊 گزارش‌ها"),
    ("plate", "🚗 پلاک‌خوان"),
    ("person", "👥 ردیابی اشخاص"),
    ("map", "🗺 نقشه ساختمان"),
    ("settings", "⚙️ تنظیمات"),
]
PAGE_KEYS = [k for k, _ in ACCESS_PAGES]


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """هش رمز با PBKDF2. خروجی: (salt_hex, hash_hex)."""
    if salt is None:
        salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt, _PBKDF2_ITERATIONS)
    return salt.hex(), dk.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """راستی‌آزمایی رمز با مقایسه‌ی زمان‌ثابت."""
    try:
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, _PBKDF2_ITERATIONS)
        return secrets.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _blank_user(username, is_admin=False):
    return {
        "username": username,
        "salt": "",
        "pass_hash": "",
        "is_admin": bool(is_admin),
        "must_change_password": False,
        # دسترسی صفحه‌ها؛ ادمین همیشه همه‌چیز دارد (در can_access).
        "permissions": {k: False for k in PAGE_KEYS},
    }


class UserManager:
    def __init__(self, path: str | None = None):
        self.path = path or USERS_FILE
        self.users: dict = {}
        self.load()

    # ---------------------------------------------------------- ذخیره‌سازی --
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            users = data.get("users", {}) if isinstance(data, dict) else {}
            # اعتبارسنجی حداقلی ساختار هر کاربر
            clean = {}
            for name, u in users.items():
                if not isinstance(u, dict):
                    continue
                rec = _blank_user(name, u.get("is_admin", False))
                rec["salt"] = str(u.get("salt", ""))
                rec["pass_hash"] = str(u.get("pass_hash", ""))
                rec["must_change_password"] = bool(u.get("must_change_password", False))
                perms = u.get("permissions", {})
                if isinstance(perms, dict):
                    for k in PAGE_KEYS:
                        rec["permissions"][k] = bool(perms.get(k, False))
                clean[name] = rec
            self.users = clean
        except Exception:
            self.users = {}

    def save(self):
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"users": self.users}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    # -------------------------------------------------------------- ادمین ----
    def ensure_default_admin(self):
        """اگر هیچ کاربری وجود نداشت، ادمین پیش‌فرض کارخانه را بساز."""
        if self.users:
            return False
        rec = _blank_user(DEFAULT_ADMIN_USER, is_admin=True)
        salt, h = hash_password(DEFAULT_ADMIN_PASS)
        rec["salt"] = salt
        rec["pass_hash"] = h
        rec["must_change_password"] = True  # تعویض اجباری در اولین ورود
        rec["permissions"] = {k: True for k in PAGE_KEYS}
        self.users[DEFAULT_ADMIN_USER] = rec
        self.save()
        return True

    def ensure_default_user(self):
        """اطمینان از وجود کاربر User با دسترسی فقط به صفحه‌ی اصلی و تنظیمات.

        اگر کاربر وجود نداشت با رمز پیش‌فرض (123456) ساخته می‌شود؛ اگر وجود
        داشت رمزش دست نمی‌خورد ولی سطح دسترسی‌اش همیشه به home+settings
        محدود می‌ماند (is_admin هم False می‌شود).
        """
        # (1.2.0) مایگریشن: کاربر قدیمی "Test" به "User" تغییر نام می‌دهد.
        changed = False
        if "Test" in self.users:
            if DEFAULT_TEST_USER not in self.users:
                self.users[DEFAULT_TEST_USER] = self.users.pop("Test")
            else:
                del self.users["Test"]
            changed = True
        rec = self.users.get(DEFAULT_TEST_USER)
        if rec is None:
            rec = _blank_user(DEFAULT_TEST_USER, is_admin=False)
            salt, h = hash_password(DEFAULT_USER_PASS)
            rec["salt"] = salt
            rec["pass_hash"] = h
            rec["must_change_password"] = False
            self.users[DEFAULT_TEST_USER] = rec
            changed = True
        want_perms = {k: (k in USER_PAGES) for k in PAGE_KEYS}
        if rec.get("permissions") != want_perms:
            rec["permissions"] = want_perms
            changed = True
        if rec.get("is_admin"):
            rec["is_admin"] = False
            changed = True
        if changed:
            self.save()
        return changed

    def admins(self):
        return [u for u in self.users.values() if u.get("is_admin")]

    # ---------------------------------------------------------------- ورود --
    def verify(self, username: str, password: str):
        """راستی‌آزمایی ورود. خروجی: رکورد کاربر یا None."""
        u = self.users.get((username or "").strip())
        if not u or not u.get("pass_hash"):
            return None
        if verify_password(password or "", u["salt"], u["pass_hash"]):
            return u
        return None

    def set_password(self, username: str, new_password: str) -> bool:
        u = self.users.get(username)
        if not u:
            return False
        salt, h = hash_password(new_password)
        u["salt"] = salt
        u["pass_hash"] = h
        u["must_change_password"] = False
        self.save()
        return True

    # -------------------------------------------------------- مدیریت کاربران --
    def create_user(self, username, password, is_admin=False, permissions=None):
        username = (username or "").strip()
        if not username or username in self.users or not password:
            return False
        rec = _blank_user(username, is_admin)
        salt, h = hash_password(password)
        rec["salt"] = salt
        rec["pass_hash"] = h
        if permissions:
            for k in PAGE_KEYS:
                rec["permissions"][k] = bool(permissions.get(k, False))
        self.users[username] = rec
        self.save()
        return True

    def update_user(self, username, password=None, is_admin=None, permissions=None):
        u = self.users.get(username)
        if not u:
            return False
        if password:
            salt, h = hash_password(password)
            u["salt"] = salt
            u["pass_hash"] = h
            u["must_change_password"] = False
        if is_admin is not None:
            # آخرین ادمین را نمی‌شود خلع کرد.
            if u.get("is_admin") and not is_admin and len(self.admins()) <= 1:
                return False
            u["is_admin"] = bool(is_admin)
        if permissions is not None:
            for k in PAGE_KEYS:
                u["permissions"][k] = bool(permissions.get(k, False))
        self.save()
        return True

    def delete_user(self, username) -> bool:
        u = self.users.get(username)
        if not u:
            return False
        # ادمین پیش‌فرض و آخرین ادمین حذف نمی‌شوند.
        if u.get("is_admin") and len(self.admins()) <= 1:
            return False
        del self.users[username]
        self.save()
        return True

    # --------------------------------------------------------------- دسترسی --
    @staticmethod
    def is_admin(user) -> bool:
        return bool(user and user.get("is_admin"))

    @staticmethod
    def can_access(user, page_key: str) -> bool:
        """آیا کاربر به این صفحه دسترسی دارد؟ ادمین = همه‌چیز."""
        if not user:
            return False
        if user.get("is_admin"):
            return True
        return bool(user.get("permissions", {}).get(page_key, False))

    @staticmethod
    def allowed_pages(user):
        return [k for k in PAGE_KEYS if UserManager.can_access(user, k)]
