# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Taha Arefi (طه عارفی)
# This file is part of IAS Viewer.
# IAS Viewer is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# -*- coding: utf-8 -*-
"""main_page.py — لایه‌ی کامل صفحه‌ی اصلی به‌صورت ویجت‌های قابل‌استفاده‌ی مجدد.

این ماژول «کل لایه‌ی صفحه‌ی اصلی» (چپ + وسط + راست) را در ویجت‌های مشترک
گرد هم می‌آورد تا هم در پنجره‌ی اصلی (MainWindow) و هم در پنجره‌های شناور
«نما»ی اتاق مانیتورینگ (MonitorWindow) از همان استفاده شود:

- CameraSidebarWidget: پنل چپ — لیست دوربین‌ها و NVRها (CameraTreeWidget)،
  افزودن دوربین/NVR، اسکن شبکه، منوی راست‌کلیک (ویرایش/حذف/گروه/PTZ/...)
- CameraPageWidget (از camera_page.py): وسط — نوار ابزار + شبکه‌ی دوربین‌ها
- EventsPanelWidget: پنل راست — پنل رویدادها + هشدارهای حریق و دود
- MainPageWidget: ترکیب هر سه در یک اسپلیتر افقی (single source of truth)

داده‌ها مشترک‌اند (همان camera_store و report_store) تا رفتار همه‌جا یکسان باشد.
"""


import os
import subprocess
import sys
import threading

from PyQt6.QtCore import Qt, QMimeData, QSize, QUrl
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QDrag
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMenu, QMessageBox, QPushButton,
                             QSplitter, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget, QGroupBox)

from add_camera_dialog import AddCameraDialog
from add_nvr_dialog import AddNVRDialog
from camera_page import CameraPageWidget
from nvr_channel_select_dialog import NVRChannelSelectDialog
from nvr_scanner import DirectCameraProbeThread
from nvr_storage_dialog import NVRStorageDialog

try:
    from nvr_webview_dialog import NVRWebViewDialog, _WEBENGINE_AVAILABLE
except ImportError:
    NVRWebViewDialog, _WEBENGINE_AVAILABLE = None, False

try:
    from building_fire_settings_dialog import BuildingFireSettingsDialog
    _BUILDING_FIRE_AVAILABLE = True
except ImportError:
    _BUILDING_FIRE_AVAILABLE = False
    BuildingFireSettingsDialog = None

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

class CameraSidebarWidget(QWidget):
    """پنل کناری چپ: اطلاعات ورود اسکن + اسکن شبکه + لیست دوربین‌ها/NVRها.

    همه‌ی منطق از MainWindow منتقل شده؛ فقط به camera_store و camera_grid
    (هدف باز کردن دوربین‌ها) وابسته است. داده‌ها مشترک‌اند (همان storeها).
    """

    def __init__(self, camera_store, camera_grid, parent=None,
                 on_camera_added=None, status_message=None):
        super().__init__(parent)
        self.camera_store = camera_store
        self.camera_grid = camera_grid
        self._on_camera_added = on_camera_added
        self._status_message = status_message
        self._net_probe_thread = None
        self._direct_probe_thread = None
        self.network_scan_thread = None
        self.detect_thread = None
        self._detect_queue = []
        self._detect_ip = None
        self._scan_ports_by_ip = {}

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # --- اطلاعات ورود برای اتصال به دوربین‌ها (فقط حافظه) ---
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
        layout.addWidget(scan_cred_group)

        # --- اسکن شبکه ---
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
        self.scan_result_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.scan_result_list.itemDoubleClicked.connect(self.on_scan_result_selected)
        self.add_selected_scan_btn = QPushButton("+ افزودن دستگاه‌های انتخاب‌شده")
        self.add_selected_scan_btn.clicked.connect(self.on_add_selected_scan_results)
        self.detect_status_label = QLabel("")
        self.detect_status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        scan_layout.addWidget(self.subnet_input)
        scan_layout.addWidget(self.scan_btn)
        scan_layout.addWidget(self.scan_result_list)
        scan_layout.addWidget(self.add_selected_scan_btn)
        scan_layout.addWidget(self.detect_status_label)
        scan_group.setLayout(scan_layout)
        layout.addWidget(scan_group)

        # --- لیست دوربین‌ها و NVRهای من ---
        cam_group = QGroupBox("دوربین‌ها و NVRهای من")
        cam_layout = QVBoxLayout()
        add_btn_row = QHBoxLayout()
        self.add_camera_btn = QPushButton("افزودن دوربین")
        self.add_camera_btn.clicked.connect(lambda: self.open_add_camera_dialog())
        self.add_nvr_btn = QPushButton("افزودن NVR")
        self.add_nvr_btn.clicked.connect(lambda: self.open_add_nvr_dialog())
        add_btn_row.addWidget(self.add_camera_btn, 1)
        add_btn_row.addWidget(self.add_nvr_btn, 1)
        self.camera_list = CameraTreeWidget()
        self.camera_list.setHeaderHidden(True)
        self.camera_list.itemDoubleClicked.connect(self.on_camera_item_activated)
        self.camera_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.camera_list.customContextMenuRequested.connect(self.show_camera_context_menu)
        connect_hint = QLabel("برای پخش زنده روی یک دوربین/کانال دابل‌کلیک کنید. کلیک راست: ویرایش/حذف/بازخوانی کانال‌ها")
        connect_hint.setStyleSheet("color: #888; font-size: 10px;")
        connect_hint.setWordWrap(True)
        cam_layout.addLayout(add_btn_row)
        cam_layout.addWidget(self.camera_list)
        cam_layout.addWidget(connect_hint)
        cam_group.setLayout(cam_layout)
        layout.addWidget(cam_group)

        self.net_probe_btn = QPushButton("📶 تست پایداری شبکه")
        self.net_probe_btn.setToolTip(
            "اسکن پهنای باند و تاخیر شبکه‌ی همه‌ی دوربین‌ها (چند ثانیه طول می‌کشد)")
        self.net_probe_btn.clicked.connect(self._on_network_probe_clicked)
        layout.addWidget(self.net_probe_btn)

        self.setLayout(layout)
        self.reload_camera_list()

    def _status(self, msg, ms=8000):
        if callable(self._status_message):
            try:
                self._status_message(msg, ms)
            except Exception:
                pass

    # -- متدهای منتقل‌شده از MainWindow (بدون تغییر منطق) در ادامه می‌آیند --


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

    def reload_camera_list(self):
        self.camera_list.clear()
        # پنل «هشدارهای حریق و دود» فقط وقتی دیده می‌شود که حداقل یک دوربین
        # تشخیص حریق فعال داشته باشد (با افزودن/حذف/ویرایش دوربین تازه می‌شود).

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
            if callable(self._on_camera_added):
                try:
                    self._on_camera_added(cam)
                except Exception:
                    pass
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

        self._status("در حال بررسی مسیر اتصال مستقیم دوربین‌ها...")
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
                    self._status(
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


    def _scan_credentials(self):
        """رفع درخواست: نام‌کاربری/رمز کادر بالای پنل اسکن شبکه را برمی‌گرداند
        (این مقادیر هرگز روی دیسک ذخیره نمی‌شوند - فقط در حافظه‌ی همین کادر)."""
        user = self.scan_user_input.text().strip() or "admin"
        pwd = self.scan_pass_input.text()
        return user, pwd

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


class EventsPanelWidget(QWidget):
    """پنل کناری راست: پنل رویدادها + هشدارهای حریق و دود.

    در پنجره‌ی اصلی با add_event/add_fire_event به‌صورت زنده تغذیه می‌شود؛
    در پنجره‌های «نما» با refresh_from_report_store از همان report_store
    مشترک خوانده می‌شود (داده‌ی مشترک، بدون ثبت تکراری).
    """

    def __init__(self, camera_store, parent=None,
                 alarm_player=None, building_fire=None, on_building_fire_log=None,
                 report_store=None):
        super().__init__(parent)
        self.camera_store = camera_store
        self._report_store = report_store
        self._alarm_player = alarm_player
        self._building_fire = building_fire
        self._on_building_fire_log = on_building_fire_log
        self._recent_face_events = []
        self._shown_violation_ids = set()
        self._video_loss_alerted = {}
        self._video_loss_down = set()

        # --- پنل رویدادها ---
        events_panel_group = QGroupBox("🚨 پنل رویدادها")
        events_panel_layout = QVBoxLayout()
        gallery_btn = QPushButton("🖼 دیدن تصاویر")
        gallery_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gallery_btn.clicked.connect(self.open_face_gallery)
        events_panel_layout.addWidget(gallery_btn)
        self.events_panel_list = QListWidget()
        self.events_panel_list.setIconSize(QSize(64, 64))
        self.events_panel_list.setWordWrap(True)
        events_panel_layout.addWidget(self.events_panel_list)
        events_panel_group.setLayout(events_panel_layout)

        # --- پنل هشدارهای حریق و دود ---
        fire_panel_group = QGroupBox("🔥 هشدارهای حریق و دود")
        self.fire_panel_group = fire_panel_group
        fire_panel_layout = QVBoxLayout()
        self.fire_panel_list = QListWidget()
        self.fire_panel_list.setIconSize(QSize(64, 64))
        self.fire_panel_list.setWordWrap(True)
        fire_panel_layout.addWidget(self.fire_panel_list)
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

        events_panel_group.setMinimumHeight(120)
        fire_panel_group.setMinimumHeight(120)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(events_panel_group)
        self.right_splitter.addWidget(fire_panel_group)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.right_splitter)
        self.setLayout(layout)
        self._refresh_fire_panel_visibility()

    # -- API عمومی --
    def add_event(self, text):
        """افزودن یک ردیف به پنل رویدادها (تغذیه‌ی زنده در پنجره‌ی اصلی)."""
        self.events_panel_list.addItem(text)
        self.events_panel_list.scrollToBottom()

    def add_fire_event(self, text):
        """افزودن یک ردیف به پنل حریق."""
        self.fire_panel_list.addItem(text)
        self.fire_panel_list.scrollToBottom()

    def refresh_from_report_store(self, limit=100):
        """خواندن رویدادهای اخیر از report_store مشترک (برای پنجره‌های نما)."""
        rs = self._report_store
        if rs is None:
            try:
                from report_store import report_store as _rs
                rs = _rs
            except Exception:
                return
        try:
            rows = list(rs.query(limit=limit) or [])
        except Exception:
            return
        self.events_panel_list.clear()
        self.fire_panel_list.clear()
        for r in rows:
            try:
                ts = str(r[0])
                kind = str(r[1])
                cam = str(r[2]) if len(r) > 2 else ""
            except Exception:
                continue
            line = ts + " — " + kind + " — " + cam
            item = QListWidgetItem(line)
            try:
                item.setData(Qt.ItemDataRole.UserRole, ts)
            except Exception:
                pass
            kl = kind.lower()
            # رویدادهای حریق در report_store با kind="fire_smoke_visual" ثبت می‌شوند
            is_fire = ("fire_smoke" in kl) or (kind.strip() in ("حریق", "دود"))
            if is_fire:
                self.fire_panel_list.addItem(item)
            else:
                self.events_panel_list.addItem(item)
        try:
            self.events_panel_list.scrollToBottom()
            self.fire_panel_list.scrollToBottom()
        except Exception:
            pass

    # -- متدهای منتقل‌شده از MainWindow (بدون تغییر منطق) در ادامه می‌آیند --



    def stop_fire_alarm_sound(self):
        """قطع صدای آژیر حریق (دکمه‌ی 🔇 قطع صدای هشدار)."""
        if self._alarm_player is not None:
            try:
                self._alarm_player.stop()
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
            self._building_fire.reload()
            self._alarm_player.reload()


    # ------------------------------------------------------- camera list ---

        # (2.0.15-beta) نشان پایداری شبکه روی هر دوربین (از آخرین اسکن)

    def open_face_gallery(self):
        """(2.0.111-beta) دکمه‌ی «🖼 دیدن تصاویر» پوشه‌ای را در اکسپلورر
        باز می‌کند که برنامه تصاویر رویدادها (چهره‌ها، پلاک‌ها و...) را در آن
        ذخیره می‌کند (report_images)."""
        try:
            import os
            import sys
            _rs = self._report_store
            if _rs is None:
                from report_store import report_store as _rs
            images_dir = _rs.IMAGES_DIR
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


class MainPageWidget(QWidget):
    """ترکیب هر سه ستون صفحه‌ی اصلی در یک اسپلیتر افقی.

    هم در MainWindow و هم در MonitorWindow از همین استفاده می‌شود
    (single source of truth). داده‌ها مشترک‌اند (همان storeها).
    """

    def __init__(self, face_engine, camera_store, report_store=None,
                 on_face_event=None, on_external_camera_drop=None,
                 on_region_alert=None, on_fire_event=None, on_plate_event=None,
                 on_person_event=None, on_audio_url_found=None, on_audio_creds_save=None,
                 on_open_monitor_room=None, on_people_toggle_all=None,
                 alarm_player=None, building_fire=None, on_building_fire_log=None,
                 status_message=None, parent=None):
        super().__init__(parent)
        self.camera_store = camera_store
        self._left_panel_width = 200

        # وسط: دیوار دوربین‌ها
        self.camera_page = CameraPageWidget(
            face_engine, camera_store,
            on_face_event,
            on_external_camera_drop=on_external_camera_drop,
            on_region_alert=on_region_alert,
            on_fire_event=on_fire_event,
            on_plate_event=on_plate_event,
            on_person_event=on_person_event,
            on_audio_url_found=on_audio_url_found,
            on_audio_creds_save=on_audio_creds_save,
            on_open_monitor_room=on_open_monitor_room,
            on_people_toggle_all=on_people_toggle_all,
            show_sidebar_toggle=True,
            on_toggle_sidebar=self.toggle_sidebar,
            parent=self,
        )

        # چپ: سایدبار (به گرید صفحه‌ی خودمان وصل است)
        self.sidebar = CameraSidebarWidget(
            camera_store, self.camera_page.camera_grid, parent=self,
            status_message=status_message,
        )
        # بعد از تغییر لیست، دیده‌شدن پنل حریق تازه شود
        _orig_reload = self.sidebar.reload_camera_list
        def _reload_and_refresh():
            _orig_reload()
            self.events_panel._refresh_fire_panel_visibility()
        self.sidebar.reload_camera_list = _reload_and_refresh

        # راست: پنل رویدادها
        self.events_panel = EventsPanelWidget(
            camera_store, parent=self,
            alarm_player=alarm_player, building_fire=building_fire,
            on_building_fire_log=on_building_fire_log,
            report_store=report_store,
        )

        self.left_widget = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.sidebar)
        self.left_widget.setLayout(left_layout)
        self.left_widget.setMaximumWidth(230)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.left_widget)
        self.splitter.addWidget(self.camera_page)
        self.splitter.addWidget(self.events_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 7)
        self.splitter.setStretchFactor(2, 2)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)
        self.setLayout(layout)

    # -- پراپرتی‌های سازگاری --
    @property
    def camera_grid(self):
        return self.camera_page.camera_grid

    @property
    def camera_list(self):
        return self.sidebar.camera_list

    @property
    def events_panel_list(self):
        return self.events_panel.events_panel_list

    @property
    def fire_panel_list(self):
        return self.events_panel.fire_panel_list

    def toggle_sidebar(self):
        """نمایش/مخفی‌کردن پنل کناری سمت چپ."""
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

