# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Taha Arefi
"""Shim سازگاری (1.2.0): دیالوگ «پخش ویدیوی NVR» به «کنسول مدیریت دوربین»
(nvr_console_dialog.NVRConsoleDialog) ارتقا یافت؛ این ماژول فقط نام‌های
قدیمی را برای سازگاری با کدهای موجود نگه می‌دارد.
"""
from nvr_console_dialog import (
    NVRConsoleDialog as NVRPlaybackDialog,
    nvr_web_url,
    _PlaybackThread,
    PLAYBACK_FFMPEG_OPTS,
)

__all__ = [
    "NVRPlaybackDialog",
    "nvr_web_url",
    "_PlaybackThread",
    "PLAYBACK_FFMPEG_OPTS",
]
