# -*- coding: utf-8 -*-
"""
این ماژول دو مسئولیت اصلی دارد:

۱) AdminMessageQueue
   صف ارسال پیام به گروه ادمین. چون هر اسکواد ۱۲ پیام در گروه ادمین
   تولید می‌کند، در صورت ثبت‌نام هم‌زمان چند تیم ممکن است به محدودیت
   نرخ ارسال تلگرام (Flood Control) بربخوریم. این صف همهٔ فراخوانی‌های
   API مربوط به گروه ادمین (send_message / send_photo / edit_message_*
   / delete_message) را یکی‌یکی و با فاصلهٔ زمانی کوتاه اجرا می‌کند.

۲) ایندکس درون‌حافظه‌ای برای جلوگیری از ثبت‌نام تکراری
   (نام اسکواد و شماره‌تلفن‌ها).

نکتهٔ فنی دربارهٔ «بدون دیتابیس» و «بازسازی ایندکس بعد از ری‌استارت»:
---------------------------------------------------------------
Telegram Bot API متدی برای خواندن تاریخچهٔ پیام‌های یک چت ندارد (چیزی
شبیه به «getChatHistory» وجود ندارد)؛ تنها راهی که یک ربات می‌تواند به
محتوای یک پیام قدیمی دسترسی پیدا کند این است که شناسهٔ آن پیام را از
قبل، در جایی، نگه داشته باشد. به همین دلیل، ایندکس (شاملِ نام‌های
رزروشده، شماره‌تلفن‌های رزروشده و نگاشت squad_id به شناسهٔ پیام‌های
هر اسکواد) داخل `bot_data` نگه‌داری می‌شود؛ همان محلی که طبق درخواست
باید از PicklePersistence برای ماندگاری وضعیت conversation استفاده
شود. `bot_data` صرفاً یک فایل pickle سبک روی دیسک است، نه یک دیتابیس
رابطه‌ای/سرور جداگانه، و دقیقاً هم‌خانوادهٔ همان مکانیزمی است که برای
ذخیرهٔ وضعیت FSM هم به‌کار رفته. با این حساب:

  - منبع حقیقی و آرشیو نهایی اطلاعات همچنان پیام‌های گروه ادمین هستند.
  - این ایندکس صرفاً یک کش سریع برای تشخیص تکراری‌هاست و با بازیابی
    خودکار bot_data توسط PicklePersistence در لحظهٔ استارت، بدون نیاز
    به فراخوانی اضافه به API تلگرام، به‌روز باقی می‌ماند.
  - اگر همین فایل pickle هم پاک شود، هیچ اطلاعاتی از دست نمی‌رود (همه
    چیز در گروه آرشیو شده)؛ فقط تشخیص خودکار تکراری تا ثبت‌نام‌های
    جدید غیرفعال می‌شود و ادمین باید موارد مشکوک را دستی بررسی کند.
"""

import asyncio
import random

from telegram.ext import ExtBot

import config


class AdminMessageQueue:
    """صف ارسال ترتیبی و کندشدهٔ پیام‌ها به گروه ادمین."""

    def __init__(self, bot: ExtBot):
        self._bot = bot
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            self._worker_task = None

    async def _worker(self) -> None:
        while True:
            action, future = await self._queue.get()
            try:
                result = await action()
                if not future.done():
                    future.set_result(result)
            except Exception as exc:  # هر خطایی به فراخواننده برگردانده شود
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._queue.task_done()
                await asyncio.sleep(
                    random.uniform(config.QUEUE_MIN_DELAY, config.QUEUE_MAX_DELAY)
                )

    async def enqueue(self, action):
        """یک فراخوانی async (بدون آرگومان، مثلاً lambda) را به صف اضافه
        می‌کند و منتظر نتیجهٔ آن می‌ماند. اجرای واقعی فراخوانی، ترتیبی و
        با فاصلهٔ زمانی کنترل‌شده نسبت به بقیهٔ آیتم‌های صف است."""
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put((action, future))
        return await future


# ─────────────────────────────────────────────────────────────
# ایندکس درون‌حافظه‌ای (روی bot_data)
# ─────────────────────────────────────────────────────────────

# قفل سراسری برای جلوگیری از race condition هنگام «چک و رزرو»ی
# هم‌زمان نام اسکواد یا شماره‌تلفن توسط چند کاربر. یک قفل ساده کافی
# است و نیازی به معماری پیچیده‌تر (مثلاً قفل جداگانه به‌ازای هر منبع)
# نیست، چون خود عملیات چک‌وست بسیار سریع است.
index_lock = asyncio.Lock()


def ensure_index(bot_data: dict) -> None:
    """مطمئن می‌شود ساختارهای پایهٔ ایندکس در bot_data وجود دارند.
    در استارت اول برنامه این‌ها ساخته می‌شوند و در استارت‌های بعدی
    (به لطف PicklePersistence) همان مقادیر قبلی از دیسک بازیابی
    می‌شوند — همین یعنی «بازسازی ایندکس بعد از ری‌استارت»."""
    bot_data.setdefault("reserved_names", set())
    bot_data.setdefault("reserved_phones", set())
    bot_data.setdefault("squads", {})


async def try_reserve_squad_name(bot_data: dict, name: str) -> bool:
    """در صورت آزاد بودن نام، آن را رزرو کرده و True برمی‌گرداند."""
    async with index_lock:
        names = bot_data["reserved_names"]
        if name in names:
            return False
        names.add(name)
        return True


async def release_squad_name(bot_data: dict, name: str) -> None:
    async with index_lock:
        bot_data["reserved_names"].discard(name)


async def try_reserve_phone(bot_data: dict, phone: str) -> bool:
    async with index_lock:
        phones = bot_data["reserved_phones"]
        if phone in phones:
            return False
        phones.add(phone)
        return True


async def release_phone(bot_data: dict, phone: str) -> None:
    async with index_lock:
        bot_data["reserved_phones"].discard(phone)
