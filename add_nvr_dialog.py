from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QLabel,
    QComboBox, QSpinBox, QPushButton, QListWidget, QListWidgetItem,
    QDialogButtonBox, QMessageBox
)

from nvr_scanner import NVRScanThread, BRAND_LABELS
from scanner import NetworkScanThread


class AddNVRDialog(QDialog):
    """افزودن یک NVR: اطلاعات اتصال گرفته می‌شود، سپس کانال‌های (دوربین‌های)
    متصل به آن اسکن و لیست می‌شوند تا کاربر انتخاب کند کدام‌ها اضافه شوند."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("افزودن NVR")
        self.setMinimumWidth(420)
        self.scan_thread = None
        self.net_scan_thread = None
        # نتیجه‌ی اسکن موازی شبکه (لیست {"ip","ports"})؛ تا تمام نشده None است
        # تا کانال‌های پیداشده با «در انتظار اسکن شبکه» نمایش داده شوند.
        self._net_devices = None
        self._net_scan_done = False
        self.found_channels = []  # [{"channel": int, "name": str, "path_or_url": str, "is_full_url": bool}]

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("مثلاً: اتاق سرور، NVR ساختمان اصلی")
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP دستگاه NVR")
        self.rtsp_port_input = QLineEdit("554")
        self.onvif_port_input = QLineEdit("80")
        self.onvif_port_input.setPlaceholderText("اختیاری - برای کشف دقیق‌تر کانال‌ها")
        self.user_input = QLineEdit("admin")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        # (1.2.0) برای NVRهایی که پنل وب‌شان فقط روی HTTPS است (فالبک
        # «باز کردن وب NVR» در گزارش ویدیویی از همین استفاده می‌کند).
        self.https_check = QCheckBox("پنل وب با HTTPS (به‌جای HTTP)")

        self.brand_combo = QComboBox()
        for key, label in BRAND_LABELS.items():
            self.brand_combo.addItem(label, key)

        # رفع درخواست: علاوه بر برند خود دستگاه NVR، برند دوربین‌های متصل به
        # آن هم به‌عنوان معیار جستجوی الگوی مسیر هر کانال قابل انتخاب است
        # (دقیقاً مثل بخش تشخیص خودکار مسیر یک دوربین تکی) - چون در عمل ممکن
        # است NVR یک برند باشد ولی دوربین‌های متصل به آن برند دیگری داشته
        # باشند و مسیر واقعی استریم هر کانال از الگوی برند دوربین پیروی کند.
        self.camera_brand_combo = QComboBox()
        for key, label in BRAND_LABELS.items():
            self.camera_brand_combo.addItem(label, key)

        self.max_channels_input = QSpinBox()
        self.max_channels_input.setRange(1, 128)
        self.max_channels_input.setValue(16)

        form = QFormLayout()
        form.addRow("نام NVR:", self.name_input)
        form.addRow("آدرس IP:", self.ip_input)
        form.addRow("پورت RTSP:", self.rtsp_port_input)
        form.addRow("پورت ONVIF (اختیاری):", self.onvif_port_input)
        form.addRow("نام کاربری:", self.user_input)
        form.addRow("رمز عبور:", self.pass_input)
        form.addRow("پنل وب:", self.https_check)
        form.addRow("برند NVR:", self.brand_combo)
        form.addRow("برند دوربین‌های متصل:", self.camera_brand_combo)
        # (2.0.61-beta) گروه‌بندی — کامبوی قابل‌ویرایش با گروه‌های موجود
        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.addItem("— (بدون گروه)", "")
        try:
            _groups = parent.camera_store.get_groups() if hasattr(parent, "camera_store") else []
            for _g in _groups:
                self.group_combo.addItem(_g, _g)
        except Exception:
            pass
        self.group_combo.setToolTip(
            "گروه نمایشی این NVR (مثلاً «طبقه اول»).\n"
            "می‌توانید نام جدیدی هم تایپ کنید.")
        form.addRow("📁 گروه:", self.group_combo)
        form.addRow("حداکثر تعداد کانال برای بررسی:", self.max_channels_input)

        self.scan_btn = QPushButton("جستجوی کانال‌های متصل")
        self.scan_btn.clicked.connect(self.start_scan)
        # نکته (رفع باگ قفل‌شدن دیالوگ حین اسکن): NVRScanThread از قبل متد
        # cancel() را داشت اما در نسخه‌ی قبلی هیچ دکمه‌ای آن را صدا نمی‌زد و کل
        # دکمه‌ها (از جمله Cancel) حین اسکن غیرفعال می‌شدند؛ اگر اسکن طول
        # می‌کشید یا دستگاه پاسخ نمی‌داد، کاربر هیچ راهی برای لغو/بستن دیالوگ
        # از طریق دکمه‌ها نداشت (فقط بستن با ضربدر که باعث کرش هنگام تخریب
        # ترد در حال اجرا می‌شد). اکنون خود دکمه‌ی جستجو حین اسکن به «لغو
        # جستجو» تبدیل و همچنان فعال می‌ماند.

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")

        self.channels_list = QListWidget()
        self.channels_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)

        select_row = QHBoxLayout()
        select_all_btn = QPushButton("انتخاب همه")
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        select_none_btn = QPushButton("لغو انتخاب همه")
        select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(select_all_btn)
        select_row.addWidget(select_none_btn)
        select_row.addStretch()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("افزودن NVR و کانال‌های انتخاب‌شده")
        self.buttons.accepted.connect(self.handle_accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.scan_btn)
        # (2.0.52-beta) افزودن مستقیم کانال‌ها از روی NVR بدون اسکن شبکه/RTSP:
        # با الگوی استریم اصلی برند انتخاب‌شده، N کانال فوراً به لیست اضافه
        # می‌شوند تا کاربر تیک بزند و تأیید کند.
        direct_row = QHBoxLayout()
        direct_row.addWidget(QLabel("افزودن مستقیم:"))
        self.direct_spin = QSpinBox()
        self.direct_spin.setRange(1, 128)
        self.direct_spin.setValue(16)
        self.direct_spin.setSuffix(" کانال")
        direct_row.addWidget(self.direct_spin)
        self.direct_btn = QPushButton("➕ افزودن مستقیم کانال‌ها (بدون اسکن)")
        self.direct_btn.clicked.connect(self._on_direct_add)
        direct_row.addWidget(self.direct_btn)
        direct_row.addStretch()
        layout.addLayout(direct_row)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("کانال‌های شناسایی‌شده (تیک بزنید تا اضافه شوند):"))
        layout.addWidget(self.channels_list)
        layout.addLayout(select_row)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

    def set_detected_brand(self, brand=None, onvif_port=None):
        """نوع دستگاه از قبل (با device_detect.py) NVR تشخیص داده شده؛ برند/پورت
        ONVIF شناسایی‌شده را از پیش پر می‌کند و بلافاصله اسکن کانال‌ها را
        شروع می‌کند تا کاربر مجبور به کلیک دوباره روی «جستجو» نباشد."""
        if brand:
            idx = self.brand_combo.findData(brand)
            if idx != -1:
                self.brand_combo.setCurrentIndex(idx)
        if onvif_port:
            self.onvif_port_input.setText(str(onvif_port))
        QTimer.singleShot(0, self.start_scan)

    # ------------------------------------------------------------- scan ---

    @staticmethod
    def _subnet_of(ip):
        """ساب‌نت (سه اکتت اول) یک IPv4 معتبر؛ در غیر این صورت None."""
        try:
            parts = ip.strip().split(".")
            if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                return ".".join(parts[:3])
        except Exception:
            pass
        return None

    def _on_direct_add(self):
        """(2.0.52-beta) افزودن مستقیم N کانال از روی NVR بدون هیچ اسکنی؛
        مسیر هر کانال از روی الگوی «استریم اصلی» برند انتخاب‌شده ساخته می‌شود
        و آیتم‌ها تیک‌خورده به لیست اضافه می‌شوند تا جریان عادی تأیید
        (handle_accept) بدون تغییر کار کند."""
        from nvr_scanner import CHANNEL_TEMPLATES, _format_templates
        n = self.direct_spin.value()
        brand = self.brand_combo.currentData()
        templates = CHANNEL_TEMPLATES.get(brand) or CHANNEL_TEMPLATES["generic"]
        main_template = templates[0]  # اولین الگو = استریم اصلی
        # جایگزینی نتایج قبلی (اسکن یا افزودن مستقیم قبلی) تا لیست تکراری نشود
        self.channels_list.clear()
        self.found_channels = []
        for ch in range(1, n + 1):
            path = _format_templates([main_template], ch)[0]
            self._on_channel_found(ch, f"کانال {ch}", path)
        self.status_label.setText(
            f"✅ {n} کانال مستقیماً با الگوی برند «{self.brand_combo.currentText()}» "
            f"اضافه شد (بدون اسکن)؛ تیک بزنید و «افزودن» را بزنید.")
        self._update_done_status()

    def start_scan(self):
        if self.scan_thread is not None and self.scan_thread.isRunning():
            # دکمه در حالت اسکن به «لغو جستجو» تبدیل شده؛ کلیک دوباره یعنی لغو.
            self.status_label.setText("در حال لغو جستجو...")
            self._cancel_all_scans()
            self.scan_btn.setEnabled(False)
            return

        ip = self.ip_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "خطا", "لطفاً آدرس IP دستگاه NVR را وارد کنید.")
            return

        self.channels_list.clear()
        self.found_channels = []
        self._net_devices = None
        self._net_scan_done = False
        self.scan_btn.setText("لغو جستجو")
        # فقط دکمه‌ی OK غیرفعال می‌شود (چون نتایج هنوز کامل نیست)؛ خود
        # QDialogButtonBox دیگر به‌صورت کامل غیرفعال نمی‌شود تا کاربر همیشه
        # بتواند دیالوگ را از طریق دکمه‌ی Cancel ببندد.
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(False)
        self.status_label.setText("در حال جستجوی کانال‌ها از وب NVR + اسکن شبکه...")

        onvif_port = self.onvif_port_input.text().strip()
        self.scan_thread = NVRScanThread(
            ip=ip,
            rtsp_port=self.rtsp_port_input.text().strip() or "554",
            onvif_port=onvif_port,
            user=self.user_input.text().strip(),
            pwd=self.pass_input.text().strip(),
            brand=self.brand_combo.currentData(),
            camera_brand=self.camera_brand_combo.currentData(),
            max_channels=self.max_channels_input.value(),
        )
        self.scan_thread.progress_signal.connect(self.status_label.setText)
        self.scan_thread.channel_found_signal.connect(self._on_channel_found)
        self.scan_thread.finished_signal.connect(self._on_scan_finished)
        self.scan_thread.failed_signal.connect(self._on_scan_failed)
        self.scan_thread.start()

        # رفع درخواست: هم‌زمان با جستجوی کانال‌ها از وب NVR، کل ساب‌نت همان
        # IP هم اسکن می‌شود؛ IPهایی که هم در کانال‌های NVR باشند و هم در
        # اسکن شبکه دیده شوند، به‌عنوان «دوربین تأییدشده» علامت می‌خورند.
        subnet = self._subnet_of(ip)
        if subnet:
            self.net_scan_thread = NetworkScanThread(subnet, self)
            self.net_scan_thread.finished_signal.connect(self._on_network_scan_finished)
            self.net_scan_thread.start()
        else:
            self._net_scan_done = True

    def _cancel_all_scans(self):
        if self.scan_thread is not None and self.scan_thread.isRunning():
            self.scan_thread.cancel()
        if self.net_scan_thread is not None and self.net_scan_thread.isRunning():
            self.net_scan_thread.cancel()

    def _net_ips(self):
        try:
            return {d.get("ip") for d in (self._net_devices or []) if d.get("ip")}
        except Exception:
            return set()

    def _match_status(self, camera_ip):
        """وضعیت تطبیق IP دوربین کانال با اسکن شبکه:
        None=نامشخص (کانال آنالوگ/بدون IP)، 'pending'=اسکن شبکه هنوز تمام
        نشده، 'matched'=در شبکه دیده شد، 'not_found'=در شبکه دیده نشد."""
        if not camera_ip:
            return None
        if not self._net_scan_done:
            return "pending"
        return "matched" if camera_ip in self._net_ips() else "not_found"

    def _channel_item_text(self, entry, default_name):
        is_full_url = entry.get("is_full_url")
        camera_ip = entry.get("camera_ip") or ""
        source_label = "ONVIF" if is_full_url else entry.get("path_or_url", "")
        if camera_ip:
            source_label += f"  —  IP دوربین: {camera_ip}"
            source_label += " (اتصال مستقیم)" if (entry.get("direct") or is_full_url) else " (از طریق NVR)"
        status = self._match_status(camera_ip)
        if status == "matched":
            source_label += "  ✅ در شبکه تأیید شد"
        elif status == "not_found":
            source_label += "  ⚠ در اسکن شبکه دیده نشد"
        return f"{default_name}   ({source_label})"

    def _on_channel_found(self, channel, name, path_or_url, camera_ip="", direct=False):
        is_full_url = path_or_url.startswith("rtsp://")
        entry = {
            "channel": channel,
            "name": name,
            "path_or_url": path_or_url,
            "is_full_url": is_full_url,
            # رفع درخواست: IP واقعی دوربین شبکه‌ای متصل به این کانال (در صورت
            # تشخیص از طریق API وب NVR یا ONVIF)؛ اگر یافت نشود خالی می‌ماند
            # (مثلاً کانال آنالوگ یا دستگاه این اطلاعات را نمی‌دهد).
            "camera_ip": camera_ip or "",
            # اگر True: باید مستقیماً به camera_ip وصل شد (مسیر روی خودِ
            # دوربین تست و تایید شده)، نه از طریق پروکسی NVR.
            "direct": bool(direct),
        }
        self.found_channels.append(entry)

        default_name = f"{self.name_input.text().strip() or 'NVR'} - کانال {channel}"
        item = QListWidgetItem(self._channel_item_text(entry, default_name))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setData(Qt.ItemDataRole.UserRole + 1, default_name)
        self.channels_list.addItem(item)

    def _on_network_scan_finished(self, devices):
        """اسکن موازی شبکه تمام شد؛ برچسب تطبیق همه‌ی کانال‌های پیداشده
        (حتی آن‌هایی که قبل از پایان اسکن شبکه پیدا شده بودند) تازه می‌شود."""
        self._net_devices = list(devices or [])
        self._net_scan_done = True
        self._refresh_channel_match_annotations()
        # اگر جستجوی کانال‌ها زودتر تمام شده بود، وضعیت نهایی را اعلام کن.
        if self.scan_thread is not None and not self.scan_thread.isRunning():
            self._update_done_status()

    def _refresh_channel_match_annotations(self):
        for i in range(self.channels_list.count()):
            item = self.channels_list.item(i)
            entry = item.data(Qt.ItemDataRole.UserRole)
            default_name = item.data(Qt.ItemDataRole.UserRole + 1)
            if entry is None:
                continue
            item.setText(self._channel_item_text(entry, default_name or ""))

    def _update_done_status(self):
        count = len(self.found_channels)
        matched = sum(1 for e in self.found_channels
                      if self._match_status(e.get("camera_ip") or "") == "matched")
        if count:
            extra = f" ({matched} دوربین در شبکه تأیید شد)" if matched else ""
            self.status_label.setText(
                f"{count} کانال یافت شد{extra}. کانال‌های موردنظر برای افزودن را تیک بزنید.")

    def _on_scan_finished(self, count):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("جستجوی کانال‌های متصل")
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(True)
        if self._net_scan_done:
            self._update_done_status()
        else:
            self.status_label.setText(
                f"{count} کانال یافت شد؛ اسکن شبکه هنوز ادامه دارد...")

    def _on_scan_failed(self, msg):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("جستجوی کانال‌های متصل")
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(True)
        # اگر اسکن شبکه هنوز ادامه دارد، لغوش کن تا دیالوگ در حالت تمیز بماند.
        self._cancel_all_scans()
        self._net_scan_done = True
        self.status_label.setText(msg)
        QMessageBox.warning(self, "نتیجه جستجو", msg)

    def _stop_scan_thread(self):
        """در صورت فعال بودن اسکن، آن را لغو و منتظر پایان امن ترد می‌ماند.
        بدون این کار، اگر کاربر دیالوگ را حین اسکن ببندد، Qt هنگام تخریب یک
        QThread هنوز در حال اجرا کرش می‌کند - یکی از منابع بسته شدن ناگهانی
        برنامه هنگام کار با NVR. هر دو ترد (جستجوی کانال NVR و اسکن موازی
        شبکه) لغو/منتظر می‌مانند."""
        self._cancel_all_scans()
        for thread in (self.scan_thread, self.net_scan_thread):
            if thread is not None and thread.isRunning():
                thread.wait(3000)

    def closeEvent(self, event):
        self._stop_scan_thread()
        event.accept()

    def reject(self):
        self._stop_scan_thread()
        super().reject()

    def _set_all_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.channels_list.count()):
            self.channels_list.item(i).setCheckState(state)

    # ------------------------------------------------------------ accept ---

    def handle_accept(self):
        ip = self.ip_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "خطا", "لطفاً آدرس IP دستگاه NVR را وارد کنید.")
            return

        selected = []
        for i in range(self.channels_list.count()):
            item = self.channels_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                entry = item.data(Qt.ItemDataRole.UserRole)
                default_name = item.data(Qt.ItemDataRole.UserRole + 1)
                selected.append((entry, default_name))

        if not selected and self.found_channels:
            QMessageBox.warning(self, "خطا", "حداقل یک کانال را برای افزودن انتخاب کنید.")
            return

        if not self.found_channels:
            confirm = QMessageBox.question(
                self, "بدون کانال",
                "هیچ کانالی جستجو/انتخاب نشده است. آیا فقط خود NVR (بدون کانال) اضافه شود؟"
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self._selected_channels = selected
        self.accept()

    def get_nvr_data(self):
        # نکته: کلید "pass" (نه "pwd") استفاده شده چون دقیقاً همان نامی است که در
        # camera_store برای فیلد ذخیره‌شده‌ی NVR/دوربین به‌کار می‌رود (update_nvr /
        # cam["pass"])؛ "pass" کلمه‌ی رزرو شده‌ی پایتون است پس نمی‌تواند نام آرگومان
        # تابع باشد، به همین دلیل در main.py هنگام فراخوانی add_nvr به‌صورت دستی به
        # آرگومان pwd نگاشت می‌شود.
        _gdata = self.group_combo.currentData()
        if isinstance(_gdata, str):
            _group = _gdata.strip()
        else:
            _group = (self.group_combo.currentText() or "").strip()
            if _group == "— (بدون گروه)":
                _group = ""
        return {
            "name": self.name_input.text().strip() or self.ip_input.text().strip(),
            "ip": self.ip_input.text().strip(),
            "rtsp_port": self.rtsp_port_input.text().strip() or "554",
            "onvif_port": self.onvif_port_input.text().strip(),
            "user": self.user_input.text().strip(),
            "pass": self.pass_input.text().strip(),
            "brand": self.brand_combo.currentData(),
            "camera_brand": self.camera_brand_combo.currentData(),
            "group": _group,
            "use_https": bool(self.https_check.isChecked()),
        }

    def get_selected_channels(self):
        """[(entry_dict, default_name), ...] entry_dict has channel/path_or_url/is_full_url."""
        return getattr(self, "_selected_channels", [])
