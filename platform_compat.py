# -*- coding: utf-8 -*-
"""platform_compat.py — لایه‌ی سازگاری پلتفرم برای نسخه‌ی مک IAS Viewer.

این ریپو (IAS-Viewer-MAC) همان کد IAS-CMS است با حداقل تغییرات برای macOS.
همه‌ی تفاوت‌های پلتفرم این‌جا متمرکز شده تا سینک بعدی با ریپوی ویندوز ساده بماند:

- is_macos() / is_windows(): تشخیص پلتفرم
- get_app_bundle_path(): مسیر IAS Viewer.app در حالت frozen
- macos_hwid(): شناسه‌ی سخت‌افزاری مک (IOPlatformUUID)
- macos_total_ram_gb(): حافظه‌ی کل سیستم از روی sysctl
- macos_keychain_set/get(): ذخیره‌ی امن رمز در Keychain (جایگزین DPAPI)
- macos_msgbox(): دیالوگ خطا با osascript (جایگزین MessageBoxW)
- macos_open(): باز کردن فایل/پوشه با Finder (جایگزین os.startfile)

این ماژول عمداً هیچ وابستگی خارجی ندارد (فقط کتابخانه‌ی استاندارد پایتون).
"""

import os
import shutil
import subprocess
import sys


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return os.name == "nt"


def get_app_bundle_path() -> str | None:
    """مسیر پوشه‌ی IAS Viewer.app در حالت frozen؛ در غیر این صورت None."""
    if not is_macos():
        return None
    try:
        exe = os.path.abspath(sys.executable)
        p = exe
        # .../IAS Viewer.app/Contents/MacOS/IAS Viewer
        while True:
            parent = os.path.dirname(p)
            if not parent or parent == p:
                return None
            if parent.endswith(".app"):
                return parent
            p = parent
    except Exception:
        return None


def macos_hwid() -> str | None:
    """شناسه‌ی یکتای سخت‌افزار مک (IOPlatformUUID)؛ None اگر نشد."""
    if not is_macos():
        return None
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                # "IOPlatformUUID" = "XXXXXXXX-XXXX-..."
                parts = line.split("=", 1)
                if len(parts) == 2:
                    uuid = parts[1].strip().strip('"')
                    if uuid:
                        return "MAC-" + uuid.upper()
    except Exception:
        pass
    return None


def macos_total_ram_gb() -> float | None:
    """حافظه‌ی فیزیکی کل (گیگابایت) روی مک؛ None اگر نشد."""
    if not is_macos():
        return None
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return int(out) / (1024 ** 3)
    except Exception:
        return None


_KEYCHAIN_SERVICE = "IASViewer"


def _security() -> str | None:
    if not is_macos():
        return None
    return shutil.which("security")


def macos_keychain_available() -> bool:
    return _security() is not None


def macos_keychain_set(account: str, secret: str) -> bool:
    """ذخیره‌ی رمز در Keychain کاربر جاری. True اگر موفق بود.

    (1.2.0 - بلاکر امنیتی به گزارش IVDagent) بازنویسی با ctypes و
    Security.framework به‌جای پروسس `security`: رمز هرگز در argv هیچ پروسسی
    قرار نمی‌گیرد (نسخه‌ی قبلی آن را با فلگ -w به خط فرمان می‌داد که در
    خروجی ps برای هر پروسس محلی قابل مشاهده بود). هیچ وابستگی جدیدی ندارد.
    رفتار -U حفظ شده: اگر آیتم تکراری بود، مقدارش آپدیت می‌شود
    (SecKeychainFindGenericPassword + SecKeychainItemModifyAttributesAndData).
    """
    if not account or not secret:
        return False
    try:
        import ctypes
        sec = _security_framework()
        if sec is None:
            return False
        service_b = _KEYCHAIN_SERVICE.encode("utf-8")
        account_b = account.encode("utf-8")
        secret_b = secret.encode("utf-8")

        sec.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        sec.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
        ]
        status = sec.SecKeychainAddGenericPassword(
            None, len(service_b), service_b,
            len(account_b), account_b,
            len(secret_b), secret_b, None,
        )
        if status == 0:
            return True
        if status != _ERR_SEC_DUPLICATE_ITEM:
            return False
        # آیتم تکراری است: پیداش کن و مقدار را آپدیت کن (معادل فلگ -U).
        sec.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        sec.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        password_len = ctypes.c_uint32(0)
        password_data = ctypes.c_void_p(None)
        item_ref = ctypes.c_void_p(None)
        status = sec.SecKeychainFindGenericPassword(
            None, len(service_b), service_b,
            len(account_b), account_b,
            ctypes.byref(password_len), ctypes.byref(password_data),
            ctypes.byref(item_ref),
        )
        try:
            if status != 0 or not item_ref.value:
                return False
            sec.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
            sec.SecKeychainItemModifyAttributesAndData.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_uint32, ctypes.c_void_p,
            ]
            status = sec.SecKeychainItemModifyAttributesAndData(
                item_ref, None, len(secret_b), secret_b)
            return status == 0
        finally:
            try:
                if password_data.value:
                    sec.SecKeychainItemFreeContent.restype = ctypes.c_int32
                    sec.SecKeychainItemFreeContent.argtypes = [
                        ctypes.c_void_p, ctypes.c_void_p]
                    sec.SecKeychainItemFreeContent(None, password_data)
            except Exception:
                pass
            _cf_release(item_ref.value)
    except Exception:
        return False


def _security_framework():
    """لود Security.framework با ctypes (کش‌شده)؛ None اگر ممکن نبود."""
    global _SECURITY_FW
    try:
        fw = _SECURITY_FW
    except NameError:
        fw = None
    if fw is not None:
        return fw
    if not is_macos():
        return None
    try:
        import ctypes
        import ctypes.util
        path = ctypes.util.find_library("Security")
        if not path:
            path = "/System/Library/Frameworks/Security.framework/Security"
        fw = ctypes.CDLL(path)
        _SECURITY_FW = fw
        return fw
    except Exception:
        return None


def _cf_release(ptr):
    """CFRelease روی یک ref؛ خطا بی‌صدا."""
    if not ptr:
        return
    try:
        import ctypes
        import ctypes.util
        cf_path = ctypes.util.find_library("CoreFoundation")
        if not cf_path:
            cf_path = ("/System/Library/Frameworks/CoreFoundation.framework/"
                       "CoreFoundation")
        cf = ctypes.CDLL(cf_path)
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease(ptr)
    except Exception:
        pass


# errSecDuplicateItem از SecBase.h — برای منطق «آپدیت در صورت تکراری بودن».
_ERR_SEC_DUPLICATE_ITEM = -25299


def macos_keychain_get(account: str) -> str | None:
    """خواندن رمز از Keychain کاربر جاری؛ None اگر پیدا نشد."""
    sec = _security()
    if not sec:
        return None
    try:
        r = subprocess.run(
            [sec, "find-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            val = r.stdout.strip()
            return val if val else None
    except Exception:
        pass
    return None


def macos_keychain_delete(account: str) -> bool:
    sec = _security()
    if not sec:
        return False
    try:
        r = subprocess.run(
            [sec, "delete-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-a", account],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def macos_msgbox(text: str, title: str = "IAS Viewer") -> None:
    """نمایش دیالوگ خطا روی مک (جایگزین MessageBoxW)."""
    if not is_macos():
        return
    try:
        # نقل‌قول برای AppleScript
        t = str(text).replace("\\", "\\\\").replace('"', '\\"')
        ti = str(title).replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e",
             f'display dialog "{t}" with title "{ti}" buttons {{"باشه"}} '
             f'default button "باشه" with icon stop'],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


def macos_open(path: str) -> bool:
    """باز کردن فایل/پوشه با برنامه‌ی پیش‌فرض مک (جایگزین os.startfile)."""
    if not is_macos():
        return False
    try:
        r = subprocess.run(["open", path], capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False
