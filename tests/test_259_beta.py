# -*- coding: utf-8 -*-
"""تست 2.0.72-beta: مدیریت کاربران + آپدیت خودکار + سخت‌سازی امنیتی.

سناریو:
 ۱) ورود اجباری با نام‌کاربری/رمز؛ رمزها با PBKDF2 ذخیره می‌شوند (نه متن‌ساده).
 ۲) ادمین پیش‌فرض کارخانه (admin) در اولین ورود مجبور به تعویض رمز است.
 ۳) سطح دسترسی صفحه‌ها برای کاربر عادی اعمال می‌شود؛ لایسنس همیشه ادمین.
 ۴) بررسی خودکار آپدیت از GitHub Releases (بدون شبکه‌ی واقعی — mock).
 ۵) رگرسیون امنیتی استاتیک: بدون shell=True، بدون eval/pickle روی داده‌ی
    نامطمئن، استخراج ZIP امن (Zip-Slip)، بدون رمز متن‌ساده روی دیسک.
"""
import io
import json
import os
import re
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
VERSION = "2.0.76-beta"


def test_version_bumped():
    with open(os.path.join(REPO, "version.txt"), encoding="utf-8") as f:
        assert f.read().strip() == VERSION, "version.txt باید 2.0.74-beta باشد"


# ------------------------------------------------- user_manager (بدون Qt) --
def _fresh_mgr():
    import user_manager as um
    d = tempfile.mkdtemp()
    mgr = um.UserManager(path=os.path.join(d, "users.json"))
    return um, mgr


def test_default_admin_created_with_forced_change():
    um, mgr = _fresh_mgr()
    assert mgr.ensure_default_admin() is True
    assert mgr.ensure_default_admin() is False  # دوباره‌خوانی نسازد
    u = mgr.verify("admin", "A12345678a")
    assert u is not None, "ادمین پیش‌فرض باید لاگین کند"
    assert u.get("must_change_password") is True, \
        "رمز کارخانه باید در اولین ورود عوض شود"
    assert um.UserManager.is_admin(u)
    assert mgr.verify("admin", "wrong-pass") is None
    assert mgr.verify("nobody", "x") is None


def test_passwords_hashed_not_plaintext():
    um, mgr = _fresh_mgr()
    mgr.ensure_default_admin()
    assert mgr.create_user("op1", "secret12", permissions={"home": True})
    assert mgr.set_password("admin", "NewPass123")
    raw = open(mgr.path, encoding="utf-8").read()
    for secret in ("A12345678a", "secret12", "NewPass123"):
        assert secret not in raw, f"رمز متن‌ساده روی دیسک است: {secret}"
    # هش‌ها یکتا و قابل راستی‌آزمایی‌اند
    assert mgr.verify("op1", "secret12") is not None
    assert mgr.verify("op1", "secret13") is None
    u = mgr.verify("admin", "NewPass123")
    assert u["must_change_password"] is False, \
        "بعد از تعویض رمز، پرچم اجبار باید پاک شود"


def test_permission_model():
    um, mgr = _fresh_mgr()
    mgr.ensure_default_admin()
    assert mgr.create_user("op1", "secret12",
                           permissions={"home": True, "fire": True})
    op = mgr.verify("op1", "secret12")
    assert um.UserManager.can_access(op, "home") is True
    assert um.UserManager.can_access(op, "fire") is True
    assert um.UserManager.can_access(op, "plate") is False
    assert um.UserManager.can_access(op, "settings") is False
    assert um.UserManager.allowed_pages(op) == ["home", "fire"]
    assert um.UserManager.is_admin(op) is False
    admin = mgr.verify("admin", "A12345678a")
    assert um.UserManager.can_access(admin, "plate") is True  # ادمین = همه‌چیز


def test_last_admin_protected():
    um, mgr = _fresh_mgr()
    mgr.ensure_default_admin()
    assert mgr.delete_user("admin") is False, "آخرین ادمین نباید حذف شود"
    assert mgr.create_user("a2", "pass1234", is_admin=True)
    assert mgr.delete_user("a2") is True
    assert mgr.update_user("admin", is_admin=False) is False, \
        "آخرین ادمین نباید خلع شود"


def test_short_and_duplicate_users_rejected():
    um, mgr = _fresh_mgr()
    mgr.ensure_default_admin()
    assert mgr.create_user("op1", "secret12") is True
    assert mgr.create_user("op1", "other12") is False, "نام تکراری"
    assert mgr.create_user("", "secret12") is False, "نام خالی"


# ------------------------------------------------- update_checker (mock) ---
def test_version_compare():
    import update_checker as uc
    assert uc.is_newer("2.0.73-beta", "2.0.72-beta")
    assert uc.is_newer("2.0.72", "2.0.72-beta")  # نهایی > بتا
    assert uc.is_newer("v2.0.73-beta", "2.0.72-beta")
    assert not uc.is_newer("2.0.72-beta", "2.0.72-beta")
    assert not uc.is_newer("2.0.71-beta", "2.0.72-beta")
    assert not uc.is_newer("2.0.72-beta", "2.0.73-beta")


def _fake_release(tag, with_asset=True, draft=False, prerelease=True):
    assets = []
    if with_asset:
        assets.append({
            "name": f"IAS-CMS-Update-v{tag}.zip",
            "browser_download_url":
                f"https://github.com/x/y/releases/download/v{tag}/IAS-CMS-Update-v{tag}.zip",
        })
    return {"tag_name": f"v{tag}", "assets": assets, "draft": draft,
            "prerelease": prerelease,
            "body": "notes", "published_at": "2026-01-01"}


def test_check_for_updates_mocked():
    import update_checker as uc
    real = uc._api_get
    seen_urls = []
    try:
        # ریلیز جدیدتر (حتی prerelease) با فایل آپدیت → هشدار
        def _fake(url, timeout=12):
            seen_urls.append(url)
            return [_fake_release("2.0.73-beta")]
        uc._api_get = _fake
        info = uc.check_for_updates("2.0.72-beta")
        assert seen_urls and "latest" not in seen_urls[-1], \
            "باید از لیست ریلیزها خواند (بتا در latest نیست)"
        assert info and info["version"] == "2.0.73-beta"
        assert info["download_url"].endswith(".zip")

        uc._api_get = lambda url, timeout=12: [_fake_release("2.0.72-beta")]
        assert uc.check_for_updates("2.0.72-beta") is None, "نسخه‌ی برابر"

        uc._api_get = lambda url, timeout=12: [_fake_release("2.0.71-beta")]
        assert uc.check_for_updates("2.0.72-beta") is None, "نسخه‌ی قدیمی‌تر"

        uc._api_get = lambda url, timeout=12: [
            _fake_release("2.0.73-beta", with_asset=False)]
        assert uc.check_for_updates("2.0.72-beta") is None, \
            "بدون asset فایل آپدیت نباید هشدار داد"

        # draft رد می‌شود؛ ریلیز بعدیِ معتبر پیدا می‌شود
        uc._api_get = lambda url, timeout=12: [
            _fake_release("2.0.74-beta", draft=True),
            _fake_release("2.0.73-beta")]
        info = uc.check_for_updates("2.0.72-beta")
        assert info and info["version"] == "2.0.73-beta"

        def _boom(url, timeout=12):
            raise OSError("no network")
        uc._api_get = _boom
        assert uc.check_for_updates("2.0.72-beta") is None, "خطا = سکوت"
    finally:
        uc._api_get = real


# ------------------------------------------------- updater: امنیت و بازسازی --
def test_safe_extract_rejects_traversal():
    import updater
    d = tempfile.mkdtemp()
    zp = os.path.join(d, "evil.zip")
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("../evil.txt", "x")
        z.writestr("ok/file.txt", "y")
    dest = os.path.join(d, "out")
    try:
        updater.safe_extract_zip(zp, dest)
        raised = False
    except ValueError:
        raised = True
    assert raised, "مسیر ../ باید رد شود (Zip-Slip)"
    assert not os.path.exists(os.path.join(d, "evil.txt"))


def test_stage_and_launch_exists():
    import updater
    assert callable(updater.stage_and_launch_update), \
        "هسته‌ی مشترک اعمال آپدیت باید وجود داشته باشد"
    assert callable(updater.validate_update_zip)
    assert callable(updater.safe_extract_zip)


# ------------------------------------------------- اسکن استاتیک امنیتی --------
def _py_files():
    out = []
    for root, dirs, files in os.walk(REPO):
        if "__pycache__" in root or "tests" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


def test_no_shell_execution():
    bad = []
    for p in _py_files():
        src = open(p, encoding="utf-8").read()
        if "shell=True" in src or re.search(r"\bos\.system\s*\(", src):
            bad.append(os.path.basename(p))
    assert not bad, f"اجرای shell در این فایل‌ها: {bad}"


def test_no_eval_pickle_on_untrusted():
    bad = []
    for p in _py_files():
        src = open(p, encoding="utf-8").read()
        if re.search(r"(?<![\w.])eval\s*\(", src):
            bad.append(os.path.basename(p) + ":eval")
        if re.search(r"pickle\.loads?\s*\(", src):
            bad.append(os.path.basename(p) + ":pickle")
    assert not bad, f"eval/pickle مشکوک در: {bad}"


def test_no_telemetry_urls():
    """هیچ URL خارجی به‌جز دانلود مدل پلاک (huggingface) و API ریلیزهای
    گیت‌هاب نباید در کد برنامه باشد."""
    allowed_hosts = ("huggingface.co", "hf-mirror.com", "api.github.com",
                     "github.com", "127.0.0.1")
    # IPهای خصوصی/محلی (placeholder یا دستگاه کاربر) تله‌متری نیستند
    local_ip = re.compile(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)")
    bad = []
    for p in _py_files():
        if os.path.basename(p) == "prepare_plate_assets.py":
            continue  # اسکریپت CI
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r"https?://([A-Za-z0-9.\-]+)", src):
            host = m.group(1)
            if local_ip.match(host):
                continue
            if not any(h in host for h in allowed_hosts):
                bad.append(f"{os.path.basename(p)}:{host}")
    assert not bad, f"URL خارجی غیرمجاز: {bad}"


# ------------------------------------------------- Qt (در صورت امکان) --------
def _qt_available():
    try:
        import PyQt6.QtWidgets  # noqa
        import cv2  # noqa
        return True
    except Exception:
        return False


def test_login_dialog_logic():
    if not _qt_available():
        return  # در محیط بدون Qt رد می‌شود
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import types
    fr = types.ModuleType("face_recognition")
    fr.face_locations = lambda *a, **k: []
    fr.face_encodings = lambda *a, **k: []
    sys.modules.setdefault("face_recognition", fr)
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["test"])
    um, mgr = _fresh_mgr()
    mgr.ensure_default_admin()
    mgr.create_user("op1", "secret12", permissions={"home": True})
    from login_dialog import LoginDialog
    dlg = LoginDialog(mgr)
    dlg.user_edit.setText("op1")
    dlg.pass_edit.setText("secret12")
    dlg._on_login()
    assert dlg.user and dlg.user["username"] == "op1"
    dlg2 = LoginDialog(mgr)
    dlg2.user_edit.setText("op1")
    dlg2.pass_edit.setText("wrong")
    dlg2._on_login()
    assert dlg2.user is None and not dlg2.err_lbl.isHidden()


def test_main_window_applies_permissions():
    if not _qt_available():
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import types
    fr = types.ModuleType("face_recognition")
    fr.face_locations = lambda *a, **k: []
    fr.face_encodings = lambda *a, **k: []
    sys.modules.setdefault("face_recognition", fr)
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["test"])
    um, mgr = _fresh_mgr()
    mgr.ensure_default_admin()
    mgr.create_user("op1", "secret12", permissions={"home": True})
    import main as app_main
    op = mgr.verify("op1", "secret12")
    w = app_main.MainWindow(current_user=op)
    w.resize(1600, 900)
    w.show()
    app.processEvents()
    assert w.nav_buttons["home"].isVisible()
    assert not w.nav_buttons["plate"].isVisible()
    assert not w.nav_buttons["settings"].isVisible()
    assert w.settings_page._license_group.isHidden(), \
        "لایسنس برای غیرادمین مخفی"
    assert w.settings_page._users_group.isHidden()
    assert not w.header_update_btn.isVisible()
    w.show_page("plate")  # محافظ: نباید عوض شود
    assert w._current_page_key == "home"
    admin = mgr.verify("admin", "A12345678a")
    w2 = app_main.MainWindow(current_user=admin)
    w2.resize(1600, 900)
    w2.show()
    app.processEvents()
    assert all(b.isVisible() for b in w2.nav_buttons.values())
    assert not w2.settings_page._license_group.isHidden()
    assert not w2.settings_page._users_group.isHidden()
    w.close()
    w2.close()
