import json
import os
import uuid

from rtsp_utils import build_rtsp_url as _build_rtsp_url
import credential_vault as _vault


def _save_passwords_enabled():
    """آیا ذخیره‌ی امن رمزها فعال است؟ (پیش‌فرض: بله - دستور کاربر 2.0.15)"""
    try:
        from app_settings import load_settings
        return bool(load_settings().get("save_passwords", True))
    except Exception:
        return True


class CameraStore:
    """Persists the list of cameras the user has connected to before,
    each with a custom display name (درب اصلی، حیاط، راهرو و ...).

    از این نسخه به بعد، NVRها هم نگهداری می‌شوند (در فایل جدای nvrs.json).
    هر کانال کشف‌شده از یک NVR، به‌صورت یک «دوربین» عادی در cameras.json ذخیره
    می‌شود (تا کل بقیه‌ی برنامه - پخش زنده، Face Library و ... بدون تغییر با آن
    کار کند)، اما فیلدهای اضافه‌ی nvr_id و channel به آن اضافه می‌شود تا در UI
    زیر همان NVR گروه‌بندی شود.
    """

    def __init__(self, path=None, nvr_path=None):
        # مسیر پیش‌فرض: پوشه‌ی یکتای دیتای کاربر (app_paths) تا نسخه‌ی
        # نصب‌شده و portable دیتابیس جدا نسازند.
        if path is None or nvr_path is None:
            try:
                from app_paths import get_data_dir
                _dd = get_data_dir()
            except Exception:
                _dd = "."
            if path is None:
                path = os.path.join(_dd, "cameras.json")
            if nvr_path is None:
                nvr_path = os.path.join(_dd, "nvrs.json")
        self.path = path
        self.nvr_path = nvr_path
        self.cameras = []
        self.nvrs = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.cameras = json.load(f)
            except Exception as e:
                print(f"خطا در بارگذاری لیست دوربین‌ها: {e}")
                self.cameras = []
        if os.path.exists(self.nvr_path):
            try:
                with open(self.nvr_path, "r", encoding="utf-8") as f:
                    self.nvrs = json.load(f)
            except Exception as e:
                print(f"خطا در بارگذاری لیست NVRها: {e}")
                self.nvrs = []

        # (2.0.15-beta به دستور کاربر) رمزها دیگر متن ساده روی دیسک نیستند؛
        # اگر ذخیره‌ی امن فعال باشد، pass_enc (رمزنگاری‌شده با DPAPI/Keychain)
        # خوانده و در حافظه به pass تبدیل می‌شود تا پخش زنده بدون پرسیدن
        # دوباره کار کند. اگر فایل‌های قدیمی رمز متن‌ساده داشته باشند، موقع
        # ذخیره‌ی بعدی رمزنگاری می‌شوند (مهاجرت خودکار)؛ اگر ذخیره‌ی امن
        # خاموش باشد، رفتار قبلی برمی‌گردد: رمز هرگز روی دیسک نمی‌ماند.
        if _save_passwords_enabled():
            for item in list(self.cameras) + list(self.nvrs):
                blob = item.get("pass_enc")
                if blob and not item.get("pass"):
                    plain = _vault.decrypt(blob)
                    if plain:
                        item["pass"] = plain
        else:
            if any(c.get("pass") for c in self.cameras):
                for c in self.cameras:
                    c["pass"] = ""
                self.save()
            if any(n.get("pass") for n in self.nvrs):
                for n in self.nvrs:
                    n["pass"] = ""
                self.save_nvrs()

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._for_disk(self.cameras), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطا در ذخیره لیست دوربین‌ها: {e}")

    def save_nvrs(self):
        try:
            with open(self.nvr_path, "w", encoding="utf-8") as f:
                json.dump(self._for_disk(self.nvrs), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطا در ذخیره لیست NVRها: {e}")

    @staticmethod
    def _for_disk(items):
        """آماده‌سازی آیتم‌ها برای نوشتن روی دیسک.

        - اگر ذخیره‌ی امن فعال باشد: رمز حافظه (pass) رمزنگاری و در pass_enc
          نوشته می‌شود؛ pass متن‌ساده هرگز روی دیسک نمی‌رود.
        - اگر خاموش باشد: رفتار قبلی (رمز کلاً ذخیره نمی‌شود).
        """
        cleaned = []
        for item in items:
            item_copy = dict(item)
            pwd = item_copy.pop("pass", "") or ""
            if _save_passwords_enabled() and pwd:
                blob = _vault.encrypt(pwd)
                if blob:
                    item_copy["pass_enc"] = blob
                # اگر رمزنگاری شکست خورد، pass_enc قبلی (در صورت وجود) حفظ
                # می‌شود تا رمز از دست نرود.
            elif not _save_passwords_enabled():
                item_copy.pop("pass_enc", None)
            # pass متن‌ساده هرگز نوشته نمی‌شود
            item_copy["pass"] = ""
            cleaned.append(item_copy)
        return cleaned

    @staticmethod
    def _without_passwords(items):
        """نگه‌داشته‌شده برای سازگاری؛ حالا _for_disk جایگزین آن است."""
        return CameraStore._for_disk(items)

    def clear_all_passwords(self):
        """رمزهای حافظه (pass) را پاک می‌کند؛ pass_enc روی دیسک دست‌نخورده
        می‌ماند تا در اجرای بعدی دوباره خوانده شود. هنگام خروج از برنامه
        صدا زده می‌شود."""
        for cam in self.cameras:
            cam["pass"] = ""
        for nvr in self.nvrs:
            nvr["pass"] = ""

    def wipe_saved_passwords(self):
        """حذف کامل رمزهای ذخیره‌شده (از دیسک و حافظه)؛ وقتی کاربر ذخیره‌ی
        امن را در تنظیمات خاموش می‌کند صدا زده می‌شود."""
        for item in list(self.cameras) + list(self.nvrs):
            item["pass"] = ""
            item.pop("pass_enc", None)
        self.save()
        self.save_nvrs()

    # ------------------------------------------------------------ cameras --

    def add_camera(self, name, ip, port, user, pwd, path, nvr_id=None, channel=None, full_url=None,
                   camera_ip=None, floor_id="", group="", bitrate_kbps=0, ptz=None):
        cam = {
            "id": str(uuid.uuid4()),
            "name": name or ip,
            "ip": ip,
            "port": port,
            "user": user,
            "pass": pwd,
            "path": path,
            "nvr_id": nvr_id,
            "channel": channel,
            "full_url": full_url,
            # (2.0.61-beta) گروه‌بندی دوربین‌ها: نام گروه نمایشی (مثلاً
            # «طبقه اول»، «پارکینگ»)؛ خالی یعنی بدون گروه.
            "group": group or "",
            # (2.0.64-beta) بیت‌ریت درخواستی این دوربین (کیلوبیت/ثانیه)؛
            # صفر یعنی «خودکار» — در تخصیص پهنای باند، سهم برابر می‌گیرد.
            "bitrate_kbps": max(0, int(bitrate_kbps or 0)),
            # (2.0.68-beta) نتیجه‌ی شناسایی PTZ/لنز موتورایزد (dict از
            # ptz_control.detect_ptz_support)؛ None یعنی هنوز شناسایی نشده.
            "ptz": ptz,
            # طبقه‌ی دوربین (کنترل تردد طبقاتی) — از نقشه‌ی ساختمان سینک
            # می‌شود یا دستی در تنظیمات دوربین ست می‌شود.
            "floor_id": floor_id or "",
            # رفع درخواست: برای کانال‌های NVR که از پشت آن‌ها یک دوربین شبکه‌ای
            # واقعی شناسایی شده، IP خودِ آن دوربین (متفاوت از "ip" که برای
            # دوربین‌های زیر NVR همان IP خود NVR است و برای اتصال RTSP از طریق
            # NVR استفاده می‌شود) اینجا صرفاً به‌عنوان اطلاعات/متادیتا نگه
            # داشته می‌شود. برای دوربین‌های مستقل (بدون NVR) معمولاً خالی است.
            "camera_ip": camera_ip or "",
        }
        self.cameras.append(cam)
        self.save()
        return cam

    def update_camera(self, cam_id, **fields):
        for cam in self.cameras:
            if cam["id"] == cam_id:
                cam.update(fields)
                self.save()
                return cam
        return None

    def remove_camera(self, cam_id):
        self.cameras = [c for c in self.cameras if c["id"] != cam_id]
        self.save()

    def get_camera(self, cam_id):
        for cam in self.cameras:
            if cam["id"] == cam_id:
                return cam
        return None

    @staticmethod
    def get_bitrate_kbps(cam):
        """بیت‌ریت درخواستی دوربین (kbps)؛ برای رکوردهای قدیمیِ بدون فیلد، ۰."""
        try:
            return max(0, int(float((cam or {}).get("bitrate_kbps") or 0)))
        except Exception:
            return 0

    def get_cameras(self):
        """همه‌ی دوربین‌ها (مستقیم + کانال‌های NVR) — برای شمارش سهمیه‌ی لایسنس."""
        return [c for c in self.cameras if isinstance(c, dict)]

    def get_camera_floor_id(self, cam_id):
        """floor_id دوربین (کنترل تردد طبقاتی)؛ «» یعنی تعریف نشده."""
        cam = self.get_camera(cam_id)
        if not cam:
            return ""
        return str(cam.get("floor_id") or "")

    def cameras_for_nvr(self, nvr_id):
        return [c for c in self.cameras if c.get("nvr_id") == nvr_id]

    def find_nvr_channel_by_camera_name(self, camera_name):
        """رفع درخواست «دکمه‌ی پخش ویدیو ظاهر نمی‌شود»: خیلی از ردیف‌های
        قدیمی‌تر گزارش‌ها (ثبت‌شده قبل از اضافه‌شدن ستون‌های nvr_id/channel
        به report_store.py، یا لحظه‌ای که به هر دلیلی این دو مقدار هنگام
        ثبت رویداد خالی مانده) در پایگاه‌داده nvr_id/channel ندارند و دکمه
        برایشان اصلاً ساخته نمی‌شد. اینجا به‌عنوان یک راه جایگزین، بر اساس
        همان نامِ دوربینِ ذخیره‌شده در ردیف گزارش، در لیست *فعلیِ* دوربین‌ها/
        NVRها (cameras.json) دنبال یک دوربین با همین نام که زیرمجموعه‌ی یک
        NVR باشد می‌گردیم؛ اگر پیدا شد، nvr_id/channel همان دوربین برگردانده
        می‌شود تا بازپخش همچنان امکان‌پذیر باشد.

        این یک تطبیق دقیق (نه قطعی) است: اگر دوربین بعداً حذف/تغییرنام داده
        شده باشد یا چند دوربین هم‌نام وجود داشته باشد، ممکن است نتیجه نادرست
        یا خالی باشد - برای همین فقط وقتی از ستون‌های خودِ ردیف گزارش چیزی در
        دسترس نیست به‌کار می‌رود."""
        if not camera_name:
            return None, None
        for cam in self.cameras:
            if cam.get("name") == camera_name and cam.get("nvr_id"):
                return cam.get("nvr_id"), cam.get("channel")
        return None, None

    def get_groups(self):
        """(2.0.61-beta) لیست مرتب نام گروه‌های موجود (دوربین‌ها + NVRها)."""
        groups = set()
        for c in self.cameras:
            g = (c.get("group") or "").strip()
            if g:
                groups.add(g)
        for n in self.nvrs:
            g = (n.get("group") or "").strip()
            if g:
                groups.add(g)
        return sorted(groups)

    def rename_group(self, old_name, new_name):
        """(2.0.61-beta) تغییر نام گروه در همه‌ی دوربین‌ها و NVRها."""
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not old_name or not new_name or old_name == new_name:
            return 0
        count = 0
        for c in self.cameras:
            if (c.get("group") or "").strip() == old_name:
                c["group"] = new_name
                count += 1
        for n in self.nvrs:
            if (n.get("group") or "").strip() == old_name:
                n["group"] = new_name
                count += 1
        if count:
            self.save()
            self.save_nvrs()
        return count

    def clear_group(self, name):
        """(2.0.61-beta) حذف گروه: اعضا بدون گروه می‌شوند (حذف نمی‌شوند)."""
        name = (name or "").strip()
        if not name:
            return 0
        count = 0
        for c in self.cameras:
            if (c.get("group") or "").strip() == name:
                c["group"] = ""
                count += 1
        for n in self.nvrs:
            if (n.get("group") or "").strip() == name:
                n["group"] = ""
                count += 1
        if count:
            self.save()
            self.save_nvrs()
        return count

    def standalone_cameras(self):
        """دوربین‌هایی که به هیچ NVR متصل نیستند (اتصال مستقیم)."""
        return [c for c in self.cameras if not c.get("nvr_id")]

    # --------------------------------------------------------------- nvrs --

    def add_nvr(self, name, ip, rtsp_port, onvif_port, user, pwd, brand="",
                camera_brand="", playback_template="", group="", use_https=False):
        nvr = {
            "id": str(uuid.uuid4()),
            "name": name or ip,
            "ip": ip,
            "rtsp_port": rtsp_port,
            "onvif_port": onvif_port,
            "user": user,
            "pass": pwd,
            "brand": brand,
            # (1.2.0) پنل وب با HTTPS (فالبک «باز کردن وب NVR»).
            "use_https": bool(use_https),
            # (2.0.61-beta) گروه‌بندی: مثل دوربین‌ها، NVR هم می‌تواند عضو
            # یک گروه نمایشی باشد؛ خالی یعنی بدون گروه.
            "group": group or "",
            # رفع درخواست: برند دوربین‌های متصل هم جدا از برند خود NVR ذخیره
            # می‌شود تا هنگام «بازخوانی کانال‌ها» دوباره به‌صورت پیش‌فرض همان
            # انتخاب قبلی در دیالوگ بیاید (رجوع کنید به nvr_scanner.py).
            "camera_brand": camera_brand,
            # (2.0.52-beta) قالب دلخواه آدرس RTSP «پخش بازبینی» این NVR
            # (رجوع کنید به nvr_playback._custom_template_url).
            "playback_template": playback_template or "",
        }
        self.nvrs.append(nvr)
        self.save_nvrs()
        return nvr

    def update_nvr(self, nvr_id, **fields):
        for nvr in self.nvrs:
            if nvr["id"] == nvr_id:
                nvr.update(fields)
                self.save_nvrs()
                return nvr
        return None

    def remove_nvr(self, nvr_id, cascade=True):
        """حذف NVR؛ در صورت cascade=True تمام کانال‌های ثبت‌شده‌ی زیر آن هم حذف می‌شوند."""
        self.nvrs = [n for n in self.nvrs if n["id"] != nvr_id]
        self.save_nvrs()
        if cascade:
            self.cameras = [c for c in self.cameras if c.get("nvr_id") != nvr_id]
            self.save()

    def get_nvr(self, nvr_id):
        for nvr in self.nvrs:
            if nvr["id"] == nvr_id:
                return nvr
        return None

    def add_channel_camera(self, nvr: dict, channel: int, name: str, path: str = "", full_url: str = None,
                            camera_ip: str = None, connect_ip: str = None, connect_port: str = None):
        """یک کانال کشف‌شده‌ی NVR را به‌عنوان دوربین جدید (متصل به آن NVR) ثبت می‌کند.

        رفع درخواست: اگر IP واقعی دوربین این کانال شناسایی شده باشد
        (``connect_ip``)، اتصال دقیقاً مثل افزودن یک دوربین تکی مستقیماً به
        همان IP/پورت دوربین انجام می‌شود - نه با پروکسی از طریق NVR - در
        حالی که کانال همچنان زیر همین NVR (``nvr_id``) در لیست گروه‌بندی
        می‌ماند."""
        return self.add_camera(
            name=name,
            ip=connect_ip or nvr["ip"],
            port=connect_port or nvr.get("rtsp_port", "554"),
            user=nvr.get("user", ""),
            pwd=nvr.get("pass", ""),
            path=path,
            nvr_id=nvr["id"],
            channel=channel,
            full_url=full_url,
            camera_ip=camera_ip,
        )

    @staticmethod
    def build_rtsp_url(cam: dict) -> str:
        # اگر آدرس کامل استریم (مثلاً از طریق کشف ONVIF) از قبل مشخص شده، همان استفاده
        # می‌شود؛ فقط در صورت نبود نام‌کاربری/رمز در خود URL، این اطلاعات اضافه می‌شوند.
        full_url = cam.get("full_url")
        if full_url:
            user, pwd = cam.get("user", ""), cam.get("pass", "")
            if user and pwd and "@" not in full_url:
                scheme_sep = "://"
                idx = full_url.find(scheme_sep)
                if idx != -1:
                    scheme = full_url[:idx + len(scheme_sep)]
                    rest = full_url[idx + len(scheme_sep):]
                    return f"{scheme}{user}:{pwd}@{rest}"
            return full_url

        # رفع باگ: قبلاً user/pass بدون URL-encode مستقیم داخل رشته جایگذاری
        # می‌شد؛ رمز عبورهای رایج دوربین‌ها که شامل @ # : / هستند، آدرس RTSP
        # را خراب می‌کردند و اتصال بی‌دلیل شکست می‌خورد (رجوع کنید به
        # rtsp_utils.build_rtsp_url).
        ip, port = cam["ip"], cam["port"]
        user, pwd, path = cam.get("user", ""), cam.get("pass", ""), cam.get("path", "")
        return _build_rtsp_url(ip, port, user, pwd, path)
