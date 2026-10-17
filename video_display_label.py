# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Taha Arefi
"""ویجت نمایش ویدیو با قابلیت رسم/ویرایش محدوده‌های هشدار (استخراج‌شده از
main.py برای استفاده‌ی مجدد در کنسول مدیریت دوربین گزارش ویدیویی NVR)."""
from PyQt6.QtWidgets import QLabel
from PyQt6.QtGui import QPainter, QPen, QColor, QPolygonF
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal

from camera_stream import region_to_polygon


class VideoDisplayLabel(QLabel):
    """رفع درخواست: امکان رسم «محدوده‌ی هشدار» به‌شکل یک چندضلعیِ دلخواه
    (Polygon) روی زمین/تصویر زنده‌ی هر دوربین - جایگزین نسخه‌ی قبلی که فقط
    یک مستطیل با کشیدن (drag) ماوس رسم می‌کرد. کاربر با کلیک‌های متوالی روی
    نقاط دلخواه (مثلاً گوشه‌های واقعی یک اتاق یا راهرو، که لزوماً مستطیل
    نیستند) محدوده را می‌سازد؛ برنامه نقاط را به‌ترتیب به هم وصل می‌کند.
    برای بستن محدوده: یا روی همان نقطه‌ی اول (با یک دایره‌ی متمایز مشخص
    شده) کلیک کنید، یا دابل‌کلیک کنید (حداقل به ۳ نقطه نیاز است). با کلیک
    راست، رسمِ در حال انجام (نقاط هنوز بسته‌نشده) بدون تاثیر روی
    محدوده‌های قبلاً تایید‌شده لغو می‌شود.

    رفع درخواست «قابلیت ادیت‌کردن»: هر محدوده‌ی «در انتظار» - چه تازه با
    کلیک‌ها بسته شده و هنوز نام‌گذاری نشده، چه یک محدوده‌ی قبلاً تایید‌شده
    که کاربر از دیالوگ «مدیریت محدوده‌ها» برای ویرایش شکلش را باز کرده، چه
    محدوده‌ی خودکارِ «کل تصویر» (رجوع کنید به
    CameraSlotWidget.start_auto_full_frame_region) - همیشه با ماوس قابل
    تغییر است: کلیک-و-درگ روی هر گوشه جابه‌جایش می‌کند، کلیک روی وسط یک
    یال یک گوشه‌ی تازه اضافه می‌کند و کلیک راست روی یک گوشه حذفش می‌کند
    (حداقل ۳ گوشه لازم است).

    منطق مختصات: چون setPixmap با KeepAspectRatio یک pixmap کوچک‌تر یا
    مساوی اندازه‌ی خودِ لیبل تولید می‌کند و QLabel آن را وسط‌چین (AlignCenter)
    نمایش می‌دهد، مستطیل واقعیِ تصویر داخل لیبل همیشه یک مستطیل هم‌مرکز به
    اندازه‌ی pixmap فعلی است (_frame_rect). نقاط رسم‌شده با ماوس (پیکسل
    لیبل) با این مستطیل به مختصات نرمال 0..1 (نسبت به خودِ فریم دوربین، نه
    اندازه‌ی لیبل) تبدیل و نگه‌داشته می‌شوند تا با تغییر اندازه‌ی پنجره/شبکه
    هم موقعیت محدوده‌ها درست بماند."""

    region_drawn = pyqtSignal(list)  # لیستی از (x,y) نرمال‌شده‌ی 0..1، حداقل ۳ نقطه
    # (2.0.38-beta) هر تغییر در نقاطِ «در حال رسم» (افزودن نقطه، لغو با
    # کلیک راست، بستن چندضلعی) — تا نوار ابزار (تایید/لغو رسم) زنده به‌روز
    # شود و دکمه‌ی «لغو رسم» حتی قبل از بسته‌شدن چندضلعی دیده شود.
    draw_progress_changed = pyqtSignal()

    # فاصله‌ی (به پیکسلِ لیبل) که کلیک نزدیک نقطه‌ی اول را «بستن محدوده»
    # حساب می‌کنیم - نه یک نقطه‌ی تازه.
    _CLOSE_THRESHOLD_PX = 14.0

    # رفع درخواست «بتونه اندازه و شکل محدوده رو تغییر بده»: فاصله‌ی (به
    # پیکسلِ لیبل) که کلیک/درگ نزدیک یکی از گوشه‌های محدوده‌ی در انتظار
    # (pending - چه تازه رسم‌شده و هنوز نام‌گذاری‌نشده، چه یک محدوده‌ی
    # قبلاً تایید‌شده که برای ویرایش شکل بارگذاری شده) را «گرفتنِ همان
    # گوشه برای جابه‌جایی/حذف» حساب می‌کنیم؛ همین آستانه برای «کلیک روی
    # نزدیک‌ترین یال» هم استفاده می‌شود که یک نقطه‌ی تازه به آن یال اضافه
    # می‌کند (برای ریزتر کردن شکل).
    _VERTEX_HIT_PX = 10.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.draw_mode = False
        # نقاطِ نرمال (0..1) محدوده‌ای که کاربر همین الان دارد با کلیک‌های
        # متوالی می‌سازد و هنوز نبسته (تایید نکرده) است.
        self._draw_points = []
        # موقعیت فعلی ماوس (نرمال) - فقط برای رسم خط‌چینِ پیش‌نمایش از آخرین
        # نقطه‌ی کلیک‌شده تا زیر نشانگر ماوس.
        self._hover_norm = None
        # رفع درخواست: برخلاف خط فرضیِ قدیمی (که بعد از تایید دیگر روی
        # تصویر دیده نمی‌شد)، محدوده‌های تایید‌شده همیشه با یک قاب نازک و
        # برچسبِ شماره/نام‌شان روی تصویر نمایش داده می‌شوند - چون می‌توانند
        # چندتایی و نام‌دار باشند و کاربر باید مرزشان را ببیند.
        self._confirmed_regions = []  # لیستی از دیکشنری {"number","name","points"}
        # محدوده‌ای که تازه بسته شده ولی هنوز کاربر نامش را تایید نکرده، یا
        # یک محدوده‌ی قبلاً تایید‌شده که همین الان برای ویرایش شکل/اندازه
        # بارگذاری شده - برای پیش‌نمایش (رجوع کنید به set_pending_points_norm).
        # برخلاف نقاطِ در حال رسم (_draw_points)، این نقاط همیشه با ماوس
        # قابل کشیدن/افزودن/حذف‌کردن هستند (رجوع کنید به mousePressEvent).
        self._pending_points = None
        # True یعنی این پیش‌نمایش، ویرایشِ یک محدوده‌ی از قبل تایید‌شده است
        # (رنگ سبز) - False یعنی یک محدوده‌ی تازه که هنوز تایید نشده (رنگ
        # زرد، رفتار قبلی). فقط برای تمایز بصری استفاده می‌شود.
        self._editing_existing = False
        # اندیس گوشه‌ای که همین الان با درگ ماوس در حال جابه‌جایی است؛ None
        # یعنی هیچ گوشه‌ای در حال کشیده‌شدن نیست.
        self._drag_vertex_idx = None
        # رفع درخواست «محدوده‌های رسم‌شده نباید وقتی زوم می‌کنم جابه‌جا بشن»:
        # چون لیبل همیشه کراپِ زوم‌شده را نمایش می‌دهد ولی نقاط محدوده نرمالِ
        # کل فریم‌اند، کراپِ جاری (نرمالِ کل فریم: x0,y0,w,h) را از
        # CameraSlotWidget می‌گیریم تا نگاشت‌ها زوم‌محور شوند. None = کل فریم.
        self._zoom_crop_norm = None
        self.setMouseTracking(True)

    def set_draw_mode(self, enabled: bool):
        self.draw_mode = bool(enabled)
        self.setCursor(Qt.CursorShape.CrossCursor if self.draw_mode else Qt.CursorShape.ArrowCursor)
        self._draw_points = []
        self._hover_norm = None
        self.update()
        self.draw_progress_changed.emit()

    def set_zoom_crop_norm(self, crop):
        """کراپِ نمایشیِ جاری (x0, y0, w, h نرمالِ کل فریم) یا None برای کل فریم."""
        self._zoom_crop_norm = tuple(crop) if crop is not None else None
        self.update()

    def set_pending_points_norm(self, points, editing_existing=False):
        """محدوده‌ی «بسته‌شده ولی هنوز تایید/نام‌گذاری نشده» یا «در حال
        ویرایش» را برای پیش‌نمایش تنظیم می‌کند؛ None یعنی هیچ محدوده‌ای در
        انتظار نیست. ``editing_existing=True`` یعنی این نقاط متعلق به یک
        محدوده‌ی قبلاً تایید‌شده است که کاربر دارد شکلش را ویرایش می‌کند
        (رنگ سبز به‌جای زرد - رجوع کنید به CameraSlotWidget.start_edit_region)."""
        self._pending_points = [tuple(p) for p in points] if points else None
        self._editing_existing = bool(editing_existing) if self._pending_points else False
        self._drag_vertex_idx = None
        self.update()

    def pending_points_norm(self):
        """نقاطِ نرمال‌شده‌ی فعلیِ محدوده‌ی در انتظار/در حال ویرایش (شامل
        هر جابه‌جایی/افزودن/حذف گوشه‌ای که کاربر تا همین لحظه با ماوس انجام
        داده) را برمی‌گرداند؛ None اگر چیزی در انتظار نیست."""
        return list(self._pending_points) if self._pending_points else None

    def set_confirmed_regions(self, regions):
        """لیست محدوده‌های نهایی/فعال این دوربین را برای رسم دائمی روی
        تصویر تنظیم می‌کند."""
        self._confirmed_regions = list(regions or [])
        self.update()

    def _frame_rect(self):
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return None
        pm_w, pm_h = pixmap.width(), pixmap.height()
        if pm_w <= 0 or pm_h <= 0:
            return None
        x0 = (self.width() - pm_w) / 2.0
        y0 = (self.height() - pm_h) / 2.0
        return QRectF(x0, y0, pm_w, pm_h)

    def _widget_to_norm(self, point):
        rect = self._frame_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        x = (point.x() - rect.x()) / rect.width()
        y = (point.y() - rect.y()) / rect.height()
        if self._zoom_crop_norm is not None:
            x0, y0, cw, ch = self._zoom_crop_norm
            x = x0 + x * cw
            y = y0 + y * ch
        return (min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))

    def _norm_to_widget(self, norm_point):
        rect = self._frame_rect()
        if rect is None:
            return None
        fx, fy = norm_point[0], norm_point[1]
        if self._zoom_crop_norm is not None:
            x0, y0, cw, ch = self._zoom_crop_norm
            if cw <= 0 or ch <= 0:
                return None
            fx = (fx - x0) / cw
            fy = (fy - y0) / ch
        return QPointF(rect.x() + fx * rect.width(), rect.y() + fy * rect.height())

    def _pending_vertex_at(self, widget_pos):
        """اندیسِ نزدیک‌ترین گوشه‌ی محدوده‌ی در انتظار به widget_pos را
        برمی‌گرداند - اگر در فاصله‌ی _VERTEX_HIT_PX بود؛ وگرنه None."""
        if not self._pending_points:
            return None
        for i, norm_p in enumerate(self._pending_points):
            wp = self._norm_to_widget(norm_p)
            if wp is None:
                continue
            if (widget_pos - wp).manhattanLength() <= self._VERTEX_HIT_PX:
                return i
        return None

    @staticmethod
    def _closest_point_on_segment(p, a, b):
        """نزدیک‌ترین نقطه روی پاره‌خط a-b به نقطه‌ی p و فاصله‌اش تا آن."""
        ax, ay, bx, by, px, py = a.x(), a.y(), b.x(), b.y(), p.x(), p.y()
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq < 1e-9 else min(max(((px - ax) * dx + (py - ay) * dy) / length_sq, 0.0), 1.0)
        cx, cy = ax + t * dx, ay + t * dy
        return QPointF(cx, cy), ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5

    def _pending_edge_insert(self, widget_pos):
        """رفع درخواست «تغییر شکل محدوده»: اگر widget_pos به یکی از یال‌های
        محدوده‌ی در انتظار نزدیک باشد، اندیسی که باید یک گوشه‌ی تازه در آن
        درج شود + مختصات نرمال همان نقطه را برمی‌گرداند - وگرنه None. این
        اجازه می‌دهد کاربر با کلیک روی وسط یک ضلع، آن را به دو ضلع تبدیل
        کند و شکل را دقیق‌تر کند (مثلاً دور زدن یک مانع)."""
        pts = self._pending_points
        if not pts or len(pts) < 2:
            return None
        n = len(pts)
        best = None
        for i in range(n):
            a = self._norm_to_widget(pts[i])
            b = self._norm_to_widget(pts[(i + 1) % n])
            if a is None or b is None:
                continue
            _, dist = self._closest_point_on_segment(widget_pos, a, b)
            if dist <= self._VERTEX_HIT_PX and (best is None or dist < best[1]):
                best = (i, dist)
        if best is None:
            return None
        norm_point = self._widget_to_norm(widget_pos)
        if norm_point is None:
            return None
        return best[0] + 1, norm_point

    def _finish_polygon(self):
        """رفع درخواست: بستن محدوده‌ی در حال رسم (حداقل ۳ نقطه لازم است) و
        ارسال سیگنال region_drawn با همان نقاط - یک‌بار، چه از راه کلیک
        نزدیک نقطه‌ی اول چه از راه دابل‌کلیک."""
        if len(self._draw_points) >= 3:
            points = list(self._draw_points)
            self._draw_points = []
            self._hover_norm = None
            self.update()
            self.draw_progress_changed.emit()
            self.region_drawn.emit(points)

    def mousePressEvent(self, event):
        if self.draw_mode and self._frame_rect() is not None:
            if event.button() == Qt.MouseButton.RightButton:
                # لغو رسمِ در حال انجام (نقاط هنوز بسته‌نشده)؛ محدوده‌های
                # قبلاً تایید‌شده دست‌نخورده می‌مانند.
                self._draw_points = []
                self._hover_norm = None
                self.update()
                self.draw_progress_changed.emit()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                norm = self._widget_to_norm(event.position())
                if norm is None:
                    return
                # اگر با شروع یک محدوده‌ی تازه، پیش‌نمایش محدوده‌ی «بسته‌شده
                # ولی هنوز تاییدنشده‌»ی قبلی روی تصویر معلق مانده بود، همین
                # الان پاکش می‌کنیم - وگرنه دو پیش‌نمایش هم‌زمان گیج‌کننده
                # می‌شود (دقیقاً همان رفتار نسخه‌ی قبلیِ مستطیلی: شروع یک drag
                # تازه، پیش‌نمایش مستطیل معلقِ قبلی را از اولویت رسم می‌انداخت).
                if not self._draw_points:
                    self._pending_points = None
                # اگر حداقل ۳ نقطه داریم و کلیک نزدیک نقطه‌ی اول است، محدوده
                # بسته می‌شود؛ در غیر این صورت یک نقطه‌ی تازه اضافه می‌شود.
                if len(self._draw_points) >= 3:
                    first_widget = self._norm_to_widget(self._draw_points[0])
                    if first_widget is not None and (event.position() - first_widget).manhattanLength() <= self._CLOSE_THRESHOLD_PX:
                        self._finish_polygon()
                        return
                self._draw_points.append(norm)
                self.update()
                self.draw_progress_changed.emit()
                return
        # رفع درخواست «بتونه اندازه و شکل محدوده تغییر بده»: وقتی در حالت
        # رسمِ فعال (کلیک‌های متوالی) نیستیم ولی یک محدوده‌ی در انتظار/در
        # حال ویرایش روی تصویر هست، همان محدوده همیشه با ماوس قابل تغییر
        # است - کلیک چپ روی یک گوشه = گرفتنِ آن برای جابه‌جایی (درگ)، کلیک
        # چپ روی وسط یک یال = افزودن گوشه‌ی تازه در همان‌جا، کلیک راست روی
        # یک گوشه = حذف همان گوشه (تا وقتی حداقل ۳ گوشه باقی بماند).
        if not self.draw_mode and self._pending_points and self._frame_rect() is not None:
            pos = event.position()
            if event.button() == Qt.MouseButton.RightButton:
                idx = self._pending_vertex_at(pos)
                if idx is not None and len(self._pending_points) > 3:
                    del self._pending_points[idx]
                    self.update()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                idx = self._pending_vertex_at(pos)
                if idx is not None:
                    self._drag_vertex_idx = idx
                    return
                inserted = self._pending_edge_insert(pos)
                if inserted is not None:
                    insert_at, norm_point = inserted
                    self._pending_points.insert(insert_at, norm_point)
                    self._drag_vertex_idx = insert_at
                    self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_vertex_idx is not None and self._pending_points:
            norm = self._widget_to_norm(event.position())
            if norm is not None:
                self._pending_points[self._drag_vertex_idx] = norm
                self.update()
            return
        if self.draw_mode and self._draw_points:
            self._hover_norm = self._widget_to_norm(event.position())
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_vertex_idx is not None:
            self._drag_vertex_idx = None
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.draw_mode and event.button() == Qt.MouseButton.LeftButton and len(self._draw_points) >= 3:
            self._finish_polygon()
            return
        super().mouseDoubleClickEvent(event)

    def _region_polygon_widget(self, region):
        """نقاط یک محدوده (چه جدید با \"points\" چه قدیمیِ \"rect\") را به
        مختصات پیکسلِ لیبل (برای رسم) تبدیل می‌کند."""
        polygon_norm = region_to_polygon(region) if isinstance(region, dict) else list(region)
        pts = []
        for p in polygon_norm:
            wp = self._norm_to_widget(p)
            if wp is None:
                return None
            pts.append(wp)
        return pts if len(pts) >= 3 else None

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)

        # محدوده‌های تایید‌شده همیشه با قاب آبی نازک و برچسب شماره/نام‌شان
        # رسم می‌شوند.
        for region in self._confirmed_regions:
            pts = self._region_polygon_widget(region)
            if pts is None:
                continue
            pen = QPen(QColor("#3498db"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF(pts))
            label = f"{region.get('number', '')}"
            if region.get("name"):
                label += f" / {region['name']}"
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(pts[0] + QPointF(4, 14), label)

        # اولویت با «رسمِ در حال انجام» (نقاطی که همین الان کاربر دارد
        # کلیک می‌کند) است؛ اگر خالی بود، پیش‌نمایش محدوده‌ی تازه‌بسته‌شده
        # ولی هنوز تاییدنشده نشان داده می‌شود - دقیقاً همان اولویت نسخه‌ی
        # قبلیِ مستطیلی (drag در حال انجام روی پیش‌نمایشِ معلق اولویت داشت).
        if self._draw_points:
            pen = QPen(QColor("#f1c40f"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            widget_pts = [self._norm_to_widget(p) for p in self._draw_points]
            # یال‌های تاکنون کلیک‌شده (باز - هنوز بسته نشده)
            for i in range(len(widget_pts) - 1):
                painter.drawLine(widget_pts[i], widget_pts[i + 1])
            # خط‌چینِ پیش‌نمایش از آخرین نقطه تا زیر نشانگر ماوس
            if self._hover_norm is not None:
                hover_pt = self._norm_to_widget(self._hover_norm)
                if hover_pt is not None:
                    dash_pen = QPen(QColor("#f1c40f"))
                    dash_pen.setWidth(1)
                    dash_pen.setStyle(Qt.PenStyle.DashLine)
                    painter.setPen(dash_pen)
                    painter.drawLine(widget_pts[-1], hover_pt)
                    painter.setPen(pen)
            # دایره‌ی روی هر نقطه؛ نقطه‌ی اول بزرگ‌تر و متمایز - همان‌جایی
            # که کلیک روی آن محدوده را می‌بندد.
            for i, wp in enumerate(widget_pts):
                r = 5.0 if i == 0 else 3.0
                painter.setBrush(QColor("#f1c40f") if i == 0 else QColor("#1e1e1e"))
                painter.drawEllipse(wp, r, r)
        elif self._pending_points is not None:
            pts = self._region_polygon_widget({"points": self._pending_points})
            if pts is not None:
                # رفع درخواست: رنگ سبز = این پیش‌نمایش، ویرایشِ یک محدوده‌ی
                # قبلاً تایید‌شده است؛ رنگ زرد = یک محدوده‌ی تازه (چه با
                # کلیک‌های متوالی رسم شده، چه با «تشخیص خودکار محدوده»
                # ساخته شده) که هنوز تایید/نام‌گذاری نشده.
                color = QColor("#2ecc71") if self._editing_existing else QColor("#f1c40f")
                pen = QPen(color)
                pen.setWidth(2)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolygon(QPolygonF(pts))
                # رفع درخواست «بتونه اندازه و شکل محدوده تغییر بده»: یک
                # دسته‌ی توپُر روی هر گوشه - نشانه‌ی این‌که این گوشه با ماوس
                # قابل کشیدن (تغییر شکل/اندازه) است (رجوع کنید به
                # mousePressEvent/mouseMoveEvent بالا).
                handle_pen = QPen(QColor("#1e1e1e"))
                handle_pen.setWidth(1)
                painter.setPen(handle_pen)
                painter.setBrush(color)
                for wp in pts:
                    painter.drawEllipse(wp, 5.0, 5.0)
        painter.end()
