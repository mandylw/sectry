# -*- coding: utf-8 -*-

import base64
import io
import logging
import uuid

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)

import config
import storage
import validators
import os


#extra var
#Text_place_holder = "🎉 تبریک! شما جزو ۷ تیم اول ثبت‌نام‌کننده هستید! 🥳\nفقط با تکمیل ثبت‌نام، ۲۰۰ هزار تومان تخفیف ویژه روی ورودی تیم‌تون دریافت می‌کنید. 🔥"

EARLY_BIRD_LIMIT = 9
EARLY_BIRD_MESSAGE = (
    "🎉🔥 تبریک\\! شما جزو ۸ تیم اول هستید\\! 🔥🎉\n"
    "به پاس ثبت‌نام زودهنگام شما، ۲۰۰ هزار تومان تخفیف ویژه براتون در نظر گرفته شده\\! 🏆💰\n"
    "💳 مبلغ اصلی ثبت‌نام:\n"
    "~۱,۰۰۰,۰۰۰ تومان~\n"
    "🎁 مبلغ قابل پرداخت شما:\n"
    "💥 ۸۰۰,۰۰۰ تومان 💥\n"
    "⏰ این تخفیف ویژه فقط برای ۸ تیم اول ثبت‌نام‌کننده در نظر گرفته شده و شما موفق شدید این امتیاز رو دریافت کنید\\! 🥳🏆\n"
    "💳 لطفاً مبلغ ۸۰۰,۰۰۰ تومان رو به شماره کارت زیر واریز کنید:\n"
    "`6219861978028610`\n"
    "👤 به نام: سروش بیات\n"
    "📸 پس از پرداخت، تصویر رسید رو ارسال کنید تا ثبت‌نام تیم شما نهایی بشه\\.\n"
    "🚀 تبریک می‌گیم؛ شما یکی از اولین تیم‌های این تورنمنت هستید\\! 🏆🔥"
)




logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger(__name__)

# صف ارسال پیام گروه ادمین؛ در post_init (بعد از بالا آمدن event loop)
# ساخته و استارت می‌شود. به‌صورت global نگه داشته می‌شود چون یک شیء
# asyncio است و نباید داخل bot_data (که pickle می‌شود) قرار بگیرد.
admin_queue: storage.AdminMessageQueue | None = None

# ─────────────────────────────────────────────────────────────
# state های ConversationHandler
# ─────────────────────────────────────────────────────────────
(
    STATE_SQUAD_NAME,
    STATE_SQUAD_LOGO,
    STATE_LEADER_FULLNAME,
    STATE_LEADER_IGN,
    STATE_LEADER_GAMEID,
    STATE_LEADER_ZONEID,
    STATE_LEADER_PHONE,
    STATE_LEADER_PHOTO,
    STATE_MEMBERS_COUNT,
    STATE_MEMBER_FULLNAME,
    STATE_MEMBER_IGN,
    STATE_MEMBER_GAMEID,
    STATE_MEMBER_ZONEID,
    STATE_MEMBER_PHONE,
    STATE_MEMBER_PHOTO,
    STATE_PAYMENT_RECEIPT,
) = range(16)

STATUS_LABELS = {
    "collecting": "در حال تکمیل اطلاعات",
    "pending_review": "در انتظار بررسی ادمین",
    "approved": "✅ تأیید شده",
    "rejected_payment": "❌ رسید پرداخت رد شده",
    "rejected_squad": "⛔️ اسکواد رد شده",
}

ADMIN_ACTION_STATUS = {
    "approve": "approved",
    "reject_payment": "rejected_payment",
    "reject_squad": "rejected_squad",
}

ADMIN_ACTION_LABELS = {
    "approve": "✅ تأیید شد توسط ادمین",
    "reject_payment": "❌ رسید پرداخت رد شد توسط ادمین",
    "reject_squad": "⛔️ اسکواد رد شد توسط ادمین",
}

LEADER_NOTICE = {
    "approve": "✅ ثبت‌نام شما با موفقیت تأیید شد.",
    "reject_payment": "❌ رسید پرداخت تأیید نشد. لطفاً پرداخت را دوباره انجام دهید.",
    "reject_squad": (
        "❌ ثبت‌نام شما تأیید نشد. برای پیگیری به شناسهٔ زیر مراجعه کنید: "
        f"{config.SUPPORT_USERNAME}"
    ),
}

# تصویر جای‌گذار ۱×۱ پیکسل شفاف — برای رزرو پیام‌های عکسی که بعداً با
# edit_message_media با تصویر واقعی جایگزین می‌شوند (چون Bot API اجازه
# نمی‌دهد یک پیام متنی به پیام عکسی تبدیل شود، ولی عکس‌به‌عکس را
# می‌شود ویرایش کرد).
_PLACEHOLDER_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _placeholder_photo() -> io.BytesIO:
    return io.BytesIO(base64.b64decode(_PLACEHOLDER_PNG_B64))


def calculate_cost(members_count: int) -> int:
    """هزینهٔ نهایی را طبق فرمول اعلام‌شده محاسبه می‌کند."""
    FREE_MEMBERS = 6
    if members_count <= config.MIN_MEMBERS:
        return config.BASE_COST
    extra = members_count - config.MIN_MEMBERS
    return config.BASE_COST + extra * config.EXTRA_PLAYER_COST


def build_admin_keyboard(squad_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تأیید ثبت‌نام", callback_data=f"approve:{squad_id}")],
            [InlineKeyboardButton("❌ رد رسید پرداخت", callback_data=f"reject_payment:{squad_id}")],
            [InlineKeyboardButton("⛔️ رد اسکواد", callback_data=f"reject_squad:{squad_id}")],
        ]
    )


def build_squad_info_text(squad: dict) -> str:
    status_label = STATUS_LABELS.get(squad["status"], squad["status"])
    return (
        "🏆 اطلاعات اسکواد\n"
        f"نام اسکواد: {squad['squad_name']}\n"
        f"تعداد اعضا: {squad.get('members_count') or '-'}\n"
        f"وضعیت: {status_label}"
    )


# ─────────────────────────────────────────────────────────────
# کمک‌تابع‌های ارتباط با گروه ادمین (همه از طریق صف)
# ─────────────────────────────────────────────────────────────

async def reserve_admin_messages(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """۱۲ پیام جای‌گذار در گروه ادمین رزرو می‌کند: ۱ اسکواد + ۱ لیدر +
    ۹ عضو + ۱ پرداخت. شناسهٔ همهٔ پیام‌ها برگردانده می‌شود تا بعداً
    ویرایش/حذف شوند."""
    bot = context.bot
    ids: dict = {}

    msg = await admin_queue.enqueue(lambda: bot.send_message(config.ADMIN_GROUP_ID, "."))
    ids["squad_info"] = msg.message_id

    msg = await admin_queue.enqueue(
        lambda: bot.send_photo(config.ADMIN_GROUP_ID, _placeholder_photo(), caption=".")
    )
    ids["leader_info"] = msg.message_id

    ids["members"] = []
    for _ in range(config.MAX_MEMBERS):
        msg = await admin_queue.enqueue(
            lambda: bot.send_photo(config.ADMIN_GROUP_ID, _placeholder_photo(), caption=".")
        )
        ids["members"].append(msg.message_id)

    msg = await admin_queue.enqueue(
        lambda: bot.send_photo(config.ADMIN_GROUP_ID, _placeholder_photo(), caption=".")
    )
    ids["payment"] = msg.message_id

    return ids


async def finalize_text(context, message_id: int, text: str) -> None:
    bot = context.bot
    try:
        await admin_queue.enqueue(
            lambda: bot.edit_message_text(chat_id=config.ADMIN_GROUP_ID, message_id=message_id, text=text)
        )
    except Exception:
        logger.exception("ویرایش پیام متنی گروه ادمین ناموفق بود (message_id=%s)", message_id)


async def finalize_photo(context, message_id: int, file_id: str, caption: str, reply_markup=None) -> None:
    bot = context.bot
    media = InputMediaPhoto(media=file_id, caption=caption)
    try:
        await admin_queue.enqueue(
            lambda: bot.edit_message_media(
                chat_id=config.ADMIN_GROUP_ID, message_id=message_id, media=media, reply_markup=reply_markup
            )
        )
    except Exception:
        logger.exception("ویرایش پیام عکسی گروه ادمین ناموفق بود (message_id=%s)", message_id)


async def edit_caption(context, message_id: int, caption: str, reply_markup=None) -> None:
    bot = context.bot
    try:
        await admin_queue.enqueue(
            lambda: bot.edit_message_caption(
                chat_id=config.ADMIN_GROUP_ID, message_id=message_id, caption=caption, reply_markup=reply_markup
            )
        )
    except Exception:
        logger.exception("ویرایش کپشن پیام گروه ادمین ناموفق بود (message_id=%s)", message_id)


async def delete_admin_message(context, message_id: int) -> None:
    bot = context.bot
    try:
        await admin_queue.enqueue(
            lambda: bot.delete_message(chat_id=config.ADMIN_GROUP_ID, message_id=message_id)
        )
    except Exception:
        pass  # پیام ممکن است از قبل حذف شده باشد؛ مشکلی نیست


async def push_squad_status(context, squad: dict) -> None:
    text = build_squad_info_text(squad)
    if squad.get("logo_file_id"):
        await edit_caption(context, squad["message_ids"]["squad_info"], text)
    else:
        await finalize_text(context, squad["message_ids"]["squad_info"], text)


async def cleanup_squad(context, squad: dict) -> None:
    """همهٔ پیام‌های رزروشدهٔ یک اسکواد را حذف و منابع ایندکس را آزاد می‌کند
    (برای حالت timeout یا لغو دستی)."""
    message_ids = (
        [squad["message_ids"]["squad_info"], squad["message_ids"]["leader_info"]]
        + squad["message_ids"]["members"]
        + [squad["message_ids"]["payment"]]
    )
    for mid in message_ids:
        await delete_admin_message(context, mid)

    await storage.release_squad_name(context.bot_data, squad["squad_name"])
    for phone in squad.get("reserved_phones", []):
        await storage.release_phone(context.bot_data, phone)


# ─────────────────────────────────────────────────────────────
# شروع کار
# ─────────────────────────────────────────────────────────────


async def is_user_in_required_chats(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    for chat in config.REQUIRED_CHATS:
        try:
            member = await context.bot.get_chat_member(chat["chat_id"], user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception:
            return False
    return True


def build_join_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(chat["title"], url=chat["invite_link"])] for chat in config.REQUIRED_CHATS]
    buttons.append([InlineKeyboardButton("✅ بررسی دوباره", callback_data="check_membership")])
    return InlineKeyboardMarkup(buttons)



async def send_start_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎮 ثبت‌نام در تورنمنت", callback_data="show_intro")]]
    )
    with open("startcaption.txt", "r", encoding="utf-8") as f:
        caption_text = f.read()
    with open("PHTO1.jpg", "rb") as photo:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption_text,
            reply_markup=keyboard,
        )

REGISTRATION_OPEN = False  # 🔴 فردا موقعش که شد این رو کن True
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not REGISTRATION_OPEN:
        await update.message.reply_text("⏳ ثبت‌نام هنوز شروع نشده. لطفاً بعداً دوباره تلاش کنید.")
        return

    user_id = update.effective_user.id
    if not await is_user_in_required_chats(context, user_id):
        await update.message.reply_text(
            "برای استفاده از ربات، ابتدا باید عضو موارد زیر شوید:",
            reply_markup=build_join_keyboard(),
        )
        return
    await send_start_message(update.effective_chat.id, context)


async def cb_check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    if await is_user_in_required_chats(context, user_id):
        await query.answer("عضویت تایید شد ✅")
        await query.message.delete()
        await send_start_message(update.effective_chat.id, context)
    else:
        await query.answer("هنوز عضو همهٔ موارد نشدید ❌", show_alert=True)



async def cb_show_intro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("شروع ثبت‌نام", callback_data="begin_registration")]])
    await query.edit_message_caption(
        "⏳ شما ۵۰ دقیقه فرصت دارید ثبت‌نام را تکمیل کنید؛ در غیر این صورت، ثبت‌نام لغو خواهد شد.",
        reply_markup=keyboard,
    )


async def cb_begin_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_reply_markup(reply_markup=None)
    #await context.bot.send_message(update.effective_chat.id,Text_place_holder)
    await context.bot.send_message(update.effective_chat.id ,"لطفاً نام اسکواد خود را ارسال کنید:")
    return STATE_SQUAD_NAME


async def unexpected_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """برای ورودی‌هایی که در state فعلی انتظارشان نمی‌رود (مثلاً عکس به‌جای
    متن)؛ state تغییر نمی‌کند و کاربر دوباره راهنمایی می‌شود."""
    await update.message.reply_text("❗️ ورودی نامعتبر است. لطفاً طبق راهنمای پیام قبلی پاسخ دهید.")


# ─────────────────────────────────────────────────────────────
# نام اسکواد
# ─────────────────────────────────────────────────────────────

async def receive_squad_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not validators.is_non_empty(name) or len(name) > 60:
        await update.message.reply_text("❗️ نام اسکواد نامعتبر است. لطفاً یک نام معتبر ارسال کنید.")
        return STATE_SQUAD_NAME

    reserved = await storage.try_reserve_squad_name(context.bot_data, name)
    if not reserved:
        await update.message.reply_text("❗️ این نام اسکواد قبلاً ثبت شده است. لطفاً نام دیگری انتخاب کنید.")
        return STATE_SQUAD_NAME

    await update.message.reply_text("⏳ در حال آماده‌سازی ثبت‌نام، لطفاً چند لحظه صبر کنید…")

    squad_id = uuid.uuid4().hex[:8]
    message_ids = await reserve_admin_messages(context)

    squad = {
        "squad_id": squad_id,
        "squad_name": name,
        "leader_chat_id": update.effective_chat.id,
        "leader": {},
        "members": [],
        "members_count": 0,
        "cost": 0,
        "message_ids": message_ids,
        "status": "collecting",
        "reserved_phones": [],
    }
    context.bot_data["squads"][squad_id] = squad
    await push_squad_status(context, squad)

    context.user_data["squad_id"] = squad_id
    context.user_data["squad_name"] = name

    await update.message.reply_text("🖼 لطفاً لوگوی اسکواد را ارسال کنید:")
    return STATE_SQUAD_LOGO



#لوگوی اسکواد؟

async def receive_squad_logo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❗️ لطفاً یک تصویر ارسال کنید.")
        return STATE_SQUAD_LOGO

    file_id = update.message.photo[-1].file_id
    squad = context.bot_data["squads"][context.user_data["squad_id"]]
    squad["logo_file_id"] = file_id

    bot = context.bot
    old_message_id = squad["message_ids"]["squad_info"]
    msg = await admin_queue.enqueue(
        lambda: bot.send_photo(config.ADMIN_GROUP_ID, file_id, caption=build_squad_info_text(squad))
    )
    squad["message_ids"]["squad_info"] = msg.message_id
    await delete_admin_message(context, old_message_id)

    await update.message.reply_text(
        "اطلاعات لیدر را وارد می‌کنیم.\n\n👤 لطفاً نام و نام خانوادگی خود را (فقط به فارسی) ارسال کنید:"
    )
    return STATE_LEADER_FULLNAME





# ─────────────────────────────────────────────────────────────
# اطلاعات لیدر
# ─────────────────────────────────────────────────────────────

async def receive_leader_fullname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not validators.is_valid_persian_name(text):
        await update.message.reply_text(
            "❗️ نام و نام خانوادگی باید فقط شامل حروف فارسی باشد. لطفاً دوباره ارسال کنید:"
        )
        return STATE_LEADER_FULLNAME
    context.user_data["leader_full_name"] = text
    await update.message.reply_text("🎮 لطفاً نام داخل بازی (IGN) خود را ارسال کنید:")
    return STATE_LEADER_IGN


async def receive_leader_ign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not validators.is_non_empty(text) or len(text) > 40  :
        await update.message.reply_text("❗️ IGN نامعتبر است. لطفاً دوباره ارسال کنید:")
        return STATE_LEADER_IGN
    context.user_data["leader_ign"] = text
    await update.message.reply_text("🆔 لطفاً Game ID خود را ارسال کنید (فقط عدد):")
    return STATE_LEADER_GAMEID


async def receive_leader_gameid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = validators.extract_numeric_id(update.message.text)
    if value is None or len(value)< 6:
        await update.message.reply_text("❗️ Game ID باید فقط عدد باشد. لطفاً دوباره ارسال کنید:")
        return STATE_LEADER_GAMEID
    context.user_data["leader_game_id"] = value
    await update.message.reply_text("🌐 لطفاً Zone ID خود را ارسال کنید (فقط عدد):")
    return STATE_LEADER_ZONEID


async def receive_leader_zoneid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = validators.extract_numeric_id(update.message.text)
    if value is None or len(value) < 4 :
        await update.message.reply_text("❗️ Zone ID باید فقط عدد باشد. لطفاً دوباره ارسال کنید:")
        return STATE_LEADER_ZONEID
    context.user_data["leader_zone_id"] = value
    await update.message.reply_text("📱 لطفاً شمارهٔ تلفن همراه خود را ارسال کنید (مثال: 09123456789):")
    return STATE_LEADER_PHONE


async def receive_leader_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = validators.normalize_iranian_phone(update.message.text)
    if phone is None:
        await update.message.reply_text(
            "❗️ شمارهٔ تلفن معتبر نیست. لطفاً یک شمارهٔ موبایل ایرانی معتبر ارسال کنید:"
        )
        return STATE_LEADER_PHONE

    reserved = await storage.try_reserve_phone(context.bot_data, phone)
    if not reserved:
        await update.message.reply_text(
            "❗️ این شماره تلفن قبلاً در این تورنمنت ثبت شده است. لطفاً شمارهٔ دیگری ارسال کنید:"
        )
        return STATE_LEADER_PHONE

    context.user_data["leader_phone"] = phone
    squad = context.bot_data["squads"][context.user_data["squad_id"]]
    squad["reserved_phones"].append(phone)

    await update.message.reply_text("🖼 لطفاً تصویر پروفایل بازی (اسکرین‌شات پروفایل) خود را ارسال کنید:")
    return STATE_LEADER_PHOTO


async def receive_leader_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❗️ لطفاً یک تصویر ارسال کنید.")
        return STATE_LEADER_PHOTO

    file_id = update.message.photo[-1].file_id
    squad = context.bot_data["squads"][context.user_data["squad_id"]]

    leader = {
        "full_name": context.user_data["leader_full_name"],
        "ign": context.user_data["leader_ign"],
        "game_id": context.user_data["leader_game_id"],
        "zone_id": context.user_data["leader_zone_id"],
        "phone": context.user_data["leader_phone"],
        "photo_file_id": file_id,
    }
    squad["leader"] = leader

    caption = (
        f"👤 اطلاعات لیدر اسکواد «{squad['squad_name']}»\n"
        f"نام و نام خانوادگی: {leader['full_name']}\n"
        f"IGN: {leader['ign']}\n"
        f"Game ID: {leader['game_id']}\n"
        f"Zone ID: {leader['zone_id']}\n"
        f"شماره تلفن: {leader['phone']}"
    )
    await finalize_photo(context, squad["message_ids"]["leader_info"], file_id, caption)

    await update.message.reply_text(
        f"👥 اسکواد شما چند عضو دارد؟ (بدون احتساب لیدر، بین {config.MIN_MEMBERS} تا {config.MAX_MEMBERS} نفر)"
    )
    return STATE_MEMBERS_COUNT


# ─────────────────────────────────────────────────────────────
# تعداد اعضا
# ─────────────────────────────────────────────────────────────

async def receive_members_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = validators.extract_numeric_id(update.message.text)
    if value is None or not (config.MIN_MEMBERS <= int(value) <= config.MAX_MEMBERS):
        await update.message.reply_text(
            f"❗️ تعداد اعضا باید عددی بین {config.MIN_MEMBERS} تا {config.MAX_MEMBERS} باشد. "
            "لطفاً دوباره ارسال کنید:"
        )
        return STATE_MEMBERS_COUNT

    count = int(value)
    squad = context.bot_data["squads"][context.user_data["squad_id"]]
    squad["members_count"] = count
    await push_squad_status(context, squad)

    context.user_data["members_count"] = count
    context.user_data["current_member_index"] = 1
    context.user_data["current_member"] = {}

    await update.message.reply_text("👤 لطفاً نام و نام خانوادگی عضو شمارهٔ ۱ را (فقط به فارسی) ارسال کنید:")
    return STATE_MEMBER_FULLNAME


# ─────────────────────────────────────────────────────────────
# اطلاعات اعضا (حلقه‌ای، تا تکمیل تعداد اعلام‌شده)
# ─────────────────────────────────────────────────────────────

async def receive_member_fullname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not validators.is_valid_persian_name(text):
        await update.message.reply_text(
            "❗️ نام و نام خانوادگی باید فقط شامل حروف فارسی باشد. لطفاً دوباره ارسال کنید:"
        )
        return STATE_MEMBER_FULLNAME
    context.user_data["current_member"]["full_name"] = text
    await update.message.reply_text("🎮 لطفاً نام داخل بازی (IGN) این عضو را ارسال کنید:")
    return STATE_MEMBER_IGN


async def receive_member_ign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not validators.is_non_empty(text) or len(text) > 40:
        await update.message.reply_text("❗️ IGN نامعتبر است. لطفاً دوباره ارسال کنید:")
        return STATE_MEMBER_IGN
    context.user_data["current_member"]["ign"] = text
    await update.message.reply_text("🆔 لطفاً Game ID این عضو را ارسال کنید (فقط عدد):")
    return STATE_MEMBER_GAMEID


async def receive_member_gameid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = validators.extract_numeric_id(update.message.text)
    if value is None or len(value) < 6:
        await update.message.reply_text("❗️ Game ID باید فقط عدد باشد. لطفاً دوباره ارسال کنید:")
        return STATE_MEMBER_GAMEID
    context.user_data["current_member"]["game_id"] = value
    await update.message.reply_text("🌐 لطفاً Zone ID این عضو را ارسال کنید (فقط عدد):")
    return STATE_MEMBER_ZONEID


async def receive_member_zoneid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = validators.extract_numeric_id(update.message.text)
    if value is None or len(value) < 4 :
        await update.message.reply_text("❗️ Zone ID باید فقط عدد باشد. لطفاً دوباره ارسال کنید:")
        return STATE_MEMBER_ZONEID
    context.user_data["current_member"]["zone_id"] = value
    await update.message.reply_text("📱 لطفاً شمارهٔ تلفن همراه این عضو را ارسال کنید:")
    return STATE_MEMBER_PHONE


async def receive_member_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = validators.normalize_iranian_phone(update.message.text)
    if phone is None:
        await update.message.reply_text(
            "❗️ شمارهٔ تلفن معتبر نیست. لطفاً یک شمارهٔ موبایل ایرانی معتبر ارسال کنید:"
        )
        return STATE_MEMBER_PHONE

    reserved = await storage.try_reserve_phone(context.bot_data, phone)
    if not reserved:
        await update.message.reply_text("❗️ این شماره تلفن قبلاً ثبت شده است. لطفاً شمارهٔ دیگری ارسال کنید:")
        return STATE_MEMBER_PHONE

    context.user_data["current_member"]["phone"] = phone
    squad = context.bot_data["squads"][context.user_data["squad_id"]]
    squad["reserved_phones"].append(phone)

    await update.message.reply_text("🖼 لطفاً تصویر پروفایل بازی این عضو را ارسال کنید:")
    return STATE_MEMBER_PHOTO


async def receive_member_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❗️ لطفاً یک تصویر ارسال کنید.")
        return STATE_MEMBER_PHOTO

    file_id = update.message.photo[-1].file_id
    index = context.user_data["current_member_index"]
    member = context.user_data["current_member"]
    member["photo_file_id"] = file_id

    squad = context.bot_data["squads"][context.user_data["squad_id"]]
    squad["members"].append(member)

    caption = (
        f"🎮 عضو شمارهٔ {index} اسکواد «{squad['squad_name']}»\n"
        f"نام و نام خانوادگی: {member['full_name']}\n"
        f"IGN: {member['ign']}\n"
        f"Game ID: {member['game_id']}\n"
        f"Zone ID: {member['zone_id']}\n"
        f"شماره تلفن: {member['phone']}"
    )
    message_id = squad["message_ids"]["members"][index - 1]
    await finalize_photo(context, message_id, file_id, caption)

    await update.message.reply_text(f"✅ اطلاعات عضو شمارهٔ {index} با موفقیت ثبت شد.")

    members_count = context.user_data["members_count"]
    if index < members_count:
        context.user_data["current_member_index"] = index + 1
        context.user_data["current_member"] = {}
        await update.message.reply_text(
            f"👤 لطفاً نام و نام خانوادگی عضو شمارهٔ {index + 1} را (فقط به فارسی) ارسال کنید:"
        )
        return STATE_MEMBER_FULLNAME

    # همهٔ اعضا ثبت شدند؛ اسلات‌های عکسِ رزروشدهٔ استفاده‌نشده حذف شوند
    for extra_id in squad["message_ids"]["members"][members_count:]:
        await delete_admin_message(context, extra_id)

    cost = calculate_cost(members_count)
    squad["cost"] = cost

    remaining = context.bot_data.setdefault("early_bird_remaining", EARLY_BIRD_LIMIT)
    if remaining > 0:
        context.bot_data["early_bird_remaining"] = remaining - 1
        await update.message.reply_text(EARLY_BIRD_MESSAGE, parse_mode="MarkdownV2")
    else:
        await update.message.reply_text(
            f"💰 هزینهٔ نهایی ثبت‌نام اسکواد شما: {cost:,} تومان\n\n"
            f"💳 لطفاً مبلغ فوق را به شمارهٔ کارت زیر واریز کنید:\n"
            f"6219861978028610\n"
            f"به نام: سروش بیات\n\n"
            f"سپس تصویر رسید پرداخت را ارسال کنید:"
        )

    return STATE_PAYMENT_RECEIPT


# ─────────────────────────────────────────────────────────────
# پرداخت
# ─────────────────────────────────────────────────────────────

async def receive_payment_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❗️ لطفاً تصویر رسید پرداخت را ارسال کنید.")
        return STATE_PAYMENT_RECEIPT

    file_id = update.message.photo[-1].file_id
    squad = context.bot_data["squads"][context.user_data["squad_id"]]

    caption = f"💳 پرداختی اسکواد: {squad['squad_name']}\nمبلغ: {squad['cost']:,} تومان"
    keyboard = build_admin_keyboard(squad["squad_id"])
    await finalize_photo(context, squad["message_ids"]["payment"], file_id, caption, reply_markup=keyboard)

    squad["status"] = "pending_review"
    await push_squad_status(context, squad)

    await update.message.reply_text("⏳ درخواست شما ثبت شد و در انتظار بررسی ادمین است.")

    context.user_data.clear()
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────
# timeout و لغو دستی
# ─────────────────────────────────────────────────────────────

async def handle_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    squad_id = context.user_data.get("squad_id")
    chat_id = None

    if squad_id:
        squad = context.bot_data.get("squads", {}).pop(squad_id, None)
        if squad:
            chat_id = squad["leader_chat_id"]
            await cleanup_squad(context, squad)

    if chat_id is None and update and update.effective_chat:
        chat_id = update.effective_chat.id

    if chat_id is not None:
        try:
            await context.bot.send_message(
                chat_id,
                "⏰ زمان ۵۰ دقیقه‌ای ثبت‌نام به پایان رسید و ثبت‌نام شما لغو شد. "
                "برای تلاش دوباره دستور /start را بزنید.",
            )
        except Exception:
            logger.exception("ارسال پیام پایان‌زمان به کاربر ناموفق بود")

    context.user_data.clear()
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    squad_id = context.user_data.get("squad_id")
    if squad_id:
        squad = context.bot_data.get("squads", {}).pop(squad_id, None)
        if squad:
            await cleanup_squad(context, squad)

    context.user_data.clear()
    await update.message.reply_text("❌ ثبت‌نام لغو شد. برای شروع دوباره دستور /start را بزنید.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────
# دکمه‌های مدیریتی گروه ادمین
# ─────────────────────────────────────────────────────────────

async def admin_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, _, squad_id = query.data.partition(":")

    squad = context.bot_data.get("squads", {}).get(squad_id)
    if squad is None:
        await query.answer("اطلاعات این اسکواد یافت نشد (ممکن است قبلاً حذف شده باشد).", show_alert=True)
        return

    # از این خط تا خط بعدی هیچ await ای وجود ندارد؛ یعنی این چک-و-ست
    # به‌صورت اتمیک نسبت به بقیهٔ callbackهای هم‌زمان اجرا می‌شود، پس
    # اگر چند ادمین هم‌زمان کلیک کنند فقط اولی واقعاً پردازش می‌شود.
    if squad["status"] != "pending_review":
        await query.answer("این اسکواد قبلاً پردازش شده است.", show_alert=True)
        return
    squad["status"] = ADMIN_ACTION_STATUS[action]

    await query.answer("در حال پردازش…")

    caption = (
        f"💳 پرداختی اسکواد: {squad['squad_name']}\n"
        f"مبلغ: {squad['cost']:,} تومان\n\n"
        f"{ADMIN_ACTION_LABELS[action]}"
    )
    await edit_caption(context, squad["message_ids"]["payment"], caption, reply_markup=None)
    await push_squad_status(context, squad)

    if action == "reject_squad":
        await storage.release_squad_name(context.bot_data, squad["squad_name"])
        for phone in squad.get("reserved_phones", []):
            await storage.release_phone(context.bot_data, phone)

    try:
        await context.bot.send_message(squad["leader_chat_id"], LEADER_NOTICE[action])
    except Exception:
        logger.exception("ارسال پیام نتیجهٔ بررسی به لیدر ناموفق بود")


# ─────────────────────────────────────────────────────────────
# مدیریت خطا
# ─────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("خطای پیش‌بینی‌نشده در پردازش یک آپدیت", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ خطایی غیرمنتظره رخ داد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
            )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# راه‌اندازی برنامه
# ─────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    """بعد از بالا آمدن event loop و قبل از شروع polling اجرا می‌شود.
    ساختارهای پایهٔ ایندکس را تضمین می‌کند (که با PicklePersistence از
    اجرای قبلی بازیابی شده‌اند) و صف ارسال پیام گروه ادمین را می‌سازد."""
    global admin_queue
    storage.ensure_index(application.bot_data)
    admin_queue = storage.AdminMessageQueue(application.bot)
    admin_queue.start()
    logger.info(
        "ربات آماده شد. تعداد اسکوادهای موجود در ایندکس: %d",
        len(application.bot_data.get("squads", {})),
    )


def main() -> None:
    persistence = PicklePersistence(
        filepath=config.PERSISTENCE_FILE,
        update_interval=config.PERSISTENCE_UPDATE_INTERVAL,
    )

    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    text_filter = filters.TEXT & ~filters.COMMAND
    catch_all = MessageHandler(filters.ALL & ~filters.COMMAND, unexpected_input)

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_begin_registration, pattern="^begin_registration$")],
        states={
            STATE_SQUAD_NAME: [MessageHandler(text_filter, receive_squad_name), catch_all],
            STATE_SQUAD_LOGO: [MessageHandler(filters.PHOTO, receive_squad_logo), catch_all],
            STATE_LEADER_FULLNAME: [MessageHandler(text_filter, receive_leader_fullname), catch_all],
            STATE_LEADER_IGN: [MessageHandler(text_filter, receive_leader_ign), catch_all],
            STATE_LEADER_GAMEID: [MessageHandler(text_filter, receive_leader_gameid), catch_all],
            STATE_LEADER_ZONEID: [MessageHandler(text_filter, receive_leader_zoneid), catch_all],
            STATE_LEADER_PHONE: [MessageHandler(text_filter, receive_leader_phone), catch_all],
            STATE_LEADER_PHOTO: [MessageHandler(filters.PHOTO, receive_leader_photo), catch_all],
            STATE_MEMBERS_COUNT: [MessageHandler(text_filter, receive_members_count), catch_all],
            STATE_MEMBER_FULLNAME: [MessageHandler(text_filter, receive_member_fullname), catch_all],
            STATE_MEMBER_IGN: [MessageHandler(text_filter, receive_member_ign), catch_all],
            STATE_MEMBER_GAMEID: [MessageHandler(text_filter, receive_member_gameid), catch_all],
            STATE_MEMBER_ZONEID: [MessageHandler(text_filter, receive_member_zoneid), catch_all],
            STATE_MEMBER_PHONE: [MessageHandler(text_filter, receive_member_phone), catch_all],
            STATE_MEMBER_PHOTO: [MessageHandler(filters.PHOTO, receive_member_photo), catch_all],
            STATE_PAYMENT_RECEIPT: [MessageHandler(filters.PHOTO, receive_payment_receipt), catch_all],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, handle_timeout),
                CallbackQueryHandler(handle_timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=config.REGISTRATION_TIMEOUT_SECONDS,
        persistent=True,
        name="registration_conversation",
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CallbackQueryHandler(cb_check_membership, pattern="^check_membership$"))
    application.add_handler(CallbackQueryHandler(cb_show_intro, pattern="^show_intro$"))
    application.add_handler(conv_handler)
    application.add_handler(
        CallbackQueryHandler(admin_button_handler, pattern="^(approve|reject_payment|reject_squad):")
    )
    application.add_error_handler(error_handler)

    logger.info("ربات در حال اجراست…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
