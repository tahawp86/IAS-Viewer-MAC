# -*- coding: utf-8 -*-
"""login_dialog.py — دیالوگ ورود به IAS Viewer (2.0.72-beta).

گردش کار:
  * اول UserManager.ensure_default_admin() صدا زده می‌شود.
  * دیالوگ ورود (نام‌کاربری + رمز)؛ در صورت انصراف، برنامه بسته می‌شود.
  * اگر must_change_password (ادمین پیش‌فرض کارخانه)، دیالوگ تعویض
    اجباری رمز باز می‌شود و بدون تعیین رمز جدید ادامه ممکن نیست.

run_login() رکورد کاربر موفق یا None برمی‌گرداند.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QFormLayout, QFileDialog,
)

import user_manager
from user_manager import UserManager


class ChangePasswordDialog(QDialog):
    """تعویض رمز (اجباری در اولین ورود ادمین / اختیاری بعداً)."""

    def __init__(self, username, forced=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("تعویض رمز عبور")
        self.setModal(True)
        self.setMinimumWidth(360)
        self.username = username
        self.forced = forced
        self.new_password = None

        lay = QVBoxLayout(self)
        if forced:
            info = QLabel(
                "🔐 این اولین ورود با رمز پیش‌فرض کارخانه است.\n"
                "به دلایل امنیتی باید همین حالا رمز جدید تعیین کنید.")
        else:
            info = QLabel(f"🔐 تعویض رمز کاربر «{username}»")
        info.setWordWrap(True)
        lay.addWidget(info)

        form = QFormLayout()
        self.new_edit = QLineEdit()
        self.new_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_edit.setPlaceholderText("رمز جدید (حداقل ۶ کاراکتر)")
        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_edit.setPlaceholderText("تکرار رمز جدید")
        form.addRow("رمز جدید:", self.new_edit)
        form.addRow("تکرار رمز:", self.confirm_edit)
        lay.addLayout(form)

        row = QHBoxLayout()
        row.addStretch()
        ok_btn = QPushButton("✅ ثبت رمز جدید")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self._on_ok)
        row.addWidget(ok_btn)
        if not forced:
            cancel_btn = QPushButton("انصراف")
            cancel_btn.clicked.connect(self.reject)
            row.addWidget(cancel_btn)
        lay.addLayout(row)

    def _on_ok(self):
        p1 = self.new_edit.text()
        p2 = self.confirm_edit.text()
        if len(p1) < 6:
            QMessageBox.warning(self, "رمز ضعیف",
                                "رمز جدید باید حداقل ۶ کاراکتر باشد.")
            return
        if p1 != p2:
            QMessageBox.warning(self, "عدم تطابق",
                                "رمز جدید و تکرار آن یکسان نیستند.")
            return
        self.new_password = p1
        self.accept()

    def closeEvent(self, event):
        # در حالت اجباری، بستن پنجره = انصراف از ورود
        if self.forced:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)


class LoginDialog(QDialog):
    def __init__(self, manager: UserManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.user = None
        self.setWindowTitle("ورود به IAS Viewer")
        self.setModal(True)
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        title = QLabel("🛡 IAS Viewer")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        lay.addWidget(title)
        sub = QLabel("برای ورود، نام‌کاربری و رمز عبور را وارد کنید.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #888; font-size: 12px;")
        lay.addWidget(sub)

        form = QFormLayout()
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("نام‌کاربری")
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setPlaceholderText("رمز عبور")
        self.pass_edit.returnPressed.connect(self._on_login)
        form.addRow("نام‌کاربری:", self.user_edit)
        form.addRow("رمز عبور:", self.pass_edit)
        lay.addLayout(form)

        self.err_lbl = QLabel("")
        self.err_lbl.setStyleSheet("color: #e05252; font-size: 12px;")
        self.err_lbl.setWordWrap(True)
        self.err_lbl.setVisible(False)
        lay.addWidget(self.err_lbl)

        # --- بخش لایسنس (فاز ۲): بارگذاری فایل لایسنس از همین صفحه‌ی ورود
        # هم ممکن است (بخش تنظیمات سر جایش می‌ماند). اگر فایل لایسنس حساب
        # کاربر (app_user) داشته باشد، ورود با همان حساب هم انجام می‌شود.
        lic_row = QHBoxLayout()
        self.license_status_lbl = QLabel("")
        self.license_status_lbl.setStyleSheet("color: #888; font-size: 12px;")
        self.license_status_lbl.setWordWrap(True)
        lic_row.addWidget(self.license_status_lbl, 1)
        self.license_btn = QPushButton("📂 بارگذاری فایل لایسنس…")
        self.license_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.license_btn.clicked.connect(self._on_license_upload)
        lic_row.addWidget(self.license_btn)
        lay.addLayout(lic_row)
        self._license_state = None
        self._refresh_license_status()

        row = QHBoxLayout()
        row.addStretch()
        login_btn = QPushButton("🔓 ورود")
        login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        login_btn.setDefault(True)
        login_btn.clicked.connect(self._on_login)
        row.addWidget(login_btn)
        cancel_btn = QPushButton("خروج از برنامه")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)
        lay.addLayout(row)

    def _refresh_license_status(self):
        """تازه‌سازی متن وضعیت لایسنس در دیالوگ ورود."""
        try:
            from license import load_license, license_app_user_state
            st = load_license()
        except Exception:
            st = None
        self._license_state = st
        try:
            if st is not None and st.valid:
                au_state = license_app_user_state(st)
                if au_state == "valid":
                    txt = (f"🔑 لایسنس معتبر ({st.customer or 'بدون نام'}) — "
                           "ورود با حساب داخل فایل لایسنس هم فعال است.")
                elif au_state == "malformed":
                    txt = ("⚠️ بخش حساب کاربر در فایل لایسنس معتبر نیست؛ "
                           "ورود لایسنسی غیرفعال است.")
                else:
                    txt = f"🔑 لایسنس معتبر ({st.customer or 'بدون نام'})"
            else:
                txt = ("🔑 فایل لایسنس بارگذاری نشده است — برنامه در حالت "
                       "محدود کار می‌کند؛ برای فعال‌سازی کامل، فایل لایسنس "
                       "را بارگذاری کنید.")
            self.license_status_lbl.setText(txt)
        except Exception:
            pass

    def _on_license_upload(self):
        """بارگذاری فایل لایسنس از صفحه‌ی ورود (همان مسیر تنظیمات)."""
        from license import install_license_file
        fp, _ = QFileDialog.getOpenFileName(
            self, "انتخاب فایل لایسنس", "", "License (*.lic)")
        if not fp:
            return
        try:
            install_license_file(fp)
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"لایسنس پذیرفته نشد:\n{e}")
            return
        self._refresh_license_status()
        QMessageBox.information(
            self, "انجام شد",
            "لایسنس با موفقیت نصب شد. ✅\n"
            "حالا می‌توانید با نام‌کاربری و رمز داخل فایل لایسنس وارد شوید.")

    def _on_login(self):
        username = self.user_edit.text().strip()
        password = self.pass_edit.text()
        user = self.manager.verify(username, password)
        if not user:
            # مسیر دوم: حساب کاربر داخل فایل لایسنس (app_user) — فقط وقتی
            # لایسنس معتبر و app_user سالم باشد؛ در غیر این صورت fail-closed.
            # ترتیب مهم است: حساب‌های محلی (از جمله ادمین بدون لایسنس)
            # همیشه اول و دست‌نخورده می‌مانند.
            try:
                from license import (load_license, verify_license_user,
                                     license_session_user)
                st = load_license()
                if st.valid and verify_license_user(username, password, st):
                    user = license_session_user(st)
            except Exception:
                user = None
        if not user:
            self.err_lbl.setText("⚠️ نام‌کاربری یا رمز عبور اشتباه است.")
            self.err_lbl.setVisible(True)
            self.pass_edit.clear()
            self.pass_edit.setFocus()
            return
        # تعویض اجباری رمز پیش‌فرض کارخانه
        if user.get("must_change_password"):
            dlg = ChangePasswordDialog(username, forced=True, parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.new_password:
                return  # انصراف از ورود
            self.manager.set_password(username, dlg.new_password)
            user = self.manager.users.get(username)
        self.user = user
        self.accept()


def run_login(parent=None):
    """اجرای کامل گردش ورود. خروجی: رکورد کاربر یا None (انصراف)."""
    mgr = UserManager()
    mgr.ensure_default_admin()
    mgr.ensure_default_user()
    dlg = LoginDialog(mgr, parent=parent)
    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.user:
        return dlg.user
    return None
