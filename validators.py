# -*- coding: utf-8 -*-
"""
توابع اعتبارسنجی و نرمال‌سازی ورودی‌های کاربر.

نکته: کاربران فارسی‌زبان معمولاً اعداد را با صفحه‌کلید فارسی/عربی وارد
می‌کنند (۰۱۲۳...) بنابراین قبل از هر بررسی عددی، ارقام به انگلیسی
تبدیل می‌شوند تا ورودی‌های معتبر به‌اشتباه رد نشوند.
"""

import re

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# فقط حروف فارسی، فاصله و نیم‌فاصله مجاز است
_PERSIAN_NAME_RE = re.compile(r"^[\u0600-\u06FF\u200c\s]{2,60}$")

# شمارهٔ موبایل ایرانی استاندارد‌شده: با 09 شروع و ۱۱ رقم باشد
_PHONE_RE = re.compile(r"^09\d{9}$")


def to_english_digits(text: str) -> str:
    """ارقام فارسی و عربی را به ارقام انگلیسی تبدیل می‌کند."""
    return text.translate(_PERSIAN_DIGITS).translate(_ARABIC_INDIC_DIGITS)


def is_valid_persian_name(text: str) -> bool:
    """نام و نام خانوادگی باید فقط شامل حروف فارسی باشد."""
    return bool(_PERSIAN_NAME_RE.match(text.strip()))


def extract_numeric_id(text: str) -> str | None:
    """Game ID / Zone ID باید فقط عددی باشند. در صورت معتبر بودن رشتهٔ
    عددی (با ارقام انگلیسی) را برمی‌گرداند، وگرنه None."""
    normalized = to_english_digits(text.strip())
    if normalized.isdigit() and len(normalized) > 0:
        return normalized
    return None


def normalize_iranian_phone(text: str) -> str | None:
    """شمارهٔ تلفن را نرمال‌سازی و اعتبارسنجی می‌کند.
    فرمت‌های ورودی قابل قبول: 09xxxxxxxxx / +989xxxxxxxxx /
    0098 9xxxxxxxxx / 989xxxxxxxxx (با یا بدون فاصله و خط تیره).
    در صورت معتبر بودن، فرمت یکنواخت 09xxxxxxxxx را برمی‌گرداند."""
    s = to_english_digits(text.strip())
    s = s.replace(" ", "").replace("-", "").replace("‌", "")

    if s.startswith("+98"):
        s = "0" + s[3:]
    elif s.startswith("0098"):
        s = "0" + s[4:]
    elif s.startswith("98") and len(s) == 12:
        s = "0" + s[2:]

    if _PHONE_RE.match(s):
        return s
    return None


def is_non_empty(text: str) -> bool:
    return bool(text and text.strip())
