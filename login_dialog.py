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
    QMessageBox, QFormLayout,
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

    def _on_login(self):
        username = self.user_edit.text().strip()
        password = self.pass_edit.text()
        user = self.manager.verify(username, password)
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
