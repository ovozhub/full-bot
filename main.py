import logging
import asyncio
import os
from pathlib import Path
from aiohttp import web

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import CreateChannelRequest, InviteToChannelRequest

# 📂 sessions va progress fayllari
Path("sessions").mkdir(exist_ok=True)
PROGRESS_FILE = "progress.txt"

# ——— TELEGRAM API ———
api_id = 25351311
api_hash = "7b854af9996797aa9ca67b42f1cd5cbe"
bot_token = "8350150569:AAEfax1UQn1AnpWrDdwFo0c7zCzDklkcbJk"

# 🔑 Kirish paroli
ACCESS_PASSWORD = "dnx"

# 🎯 Avtomatik qo‘shiladigan bot
TARGET_BOT = "@oxang_bot"

# ——— LOGGER ———
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ——— Holatlar ———
ASK_PASSWORD, PHONE, CODE, PASSWORD = range(4)

# ——— Sessionlar va avtorizatsiya ———
sessions = {}
authorized_users = set()

# Bu yerda avtomatik tasklarni boshqarish uchun lug‘at (user_id yoki phone asosida)
running_tasks = {}

# progress faylidan o‘qish
def load_progress(phone):
    path = f"sessions/{phone}_progress.txt"
    if os.path.exists(path):
        with open(path, "r") as f:
            try:
                return int(f.read().strip())
            except:
                return 0
    return 0


# progress faylga yozish
def save_progress(phone, value):
    path = f"sessions/{phone}_progress.txt"
    with open(path, "w") as f:
        f.write(str(value))


# Progress bar funksiyasi
def generate_progress_bar(current, total, length=10):
    percent = int((current / total) * 100) if total else 0
    filled = int(length * current // total) if total else 0
    bar = '▰' * filled + '▱' * (length - filled)
    return f"{bar} {percent}% ({current} / {total})"


# ——— START ———
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in authorized_users:
        return await ask_phone(update)
    await update.message.reply_text("🔒 Kirish parolini kiriting:")
    return ASK_PASSWORD


# ——— Parol tekshirish ———
async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() != ACCESS_PASSWORD:
        await update.message.reply_text("❌ Noto‘g‘ri parol.")
        return ConversationHandler.END
    authorized_users.add(update.effective_user.id)
    return await ask_phone(update)


# ——— Telefon so‘rash ———
async def ask_phone(update: Update):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text("📞 Telefon raqamingizni yuboring:", reply_markup=keyboard)
    return PHONE


# ——— Telefon qabul qilish ———
async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.contact.phone_number if update.message.contact else update.message.text.strip()
    if not phone.startswith('+') or not phone[1:].isdigit():
        await update.message.reply_text("Telefon raqam + bilan boshlanishi va raqam bo‘lishi kerak.")
        return PHONE

    context.user_data['phone'] = phone
    client = TelegramClient(f"sessions/{phone}", api_id, api_hash)
    sessions[update.effective_user.id] = client

    await client.connect()
    if not await client.is_user_authorized():
        try:
            await client.send_code_request(phone)
            await update.message.reply_text("📩 Kod yuborildi, kiriting:")
            return CODE
        except Exception as e:
            await update.message.reply_text(f"❌ Xato: {e}")
            return ConversationHandler.END
    await update.message.reply_text("✅ Akkount ulandi.")
    asyncio.create_task(auto_group_task(update.effective_user.id, client, phone, context))
    return ConversationHandler.END


# ——— Kod qabul qilish ———
async def code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client = sessions.get(update.effective_user.id)
    phone = context.user_data.get('phone')
    try:
        await client.sign_in(phone, update.message.text.strip())
    except errors.SessionPasswordNeededError:
        await update.message.reply_text("🔑 2 bosqichli parolni kiriting:")
        return PASSWORD
    except Exception as e:
        await update.message.reply_text(f"❌ Xato: {e}")
        return ConversationHandler.END
    await update.message.reply_text("✅ Akkount ulandi.")
    asyncio.create_task(auto_group_task(update.effective_user.id, client, phone, context))
    return ConversationHandler.END


# ——— 2FA parol ———
async def password_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client = sessions.get(update.effective_user.id)
    phone = context.user_data.get('phone')
    try:
        await client.sign_in(password=update.message.text.strip())
    except Exception as e:
        await update.message.reply_text(f"❌ Xato: {e}")
        return ConversationHandler.END
    await update.message.reply_text("✅ Akkount ulandi.")
    asyncio.create_task(auto_group_task(update.effective_user.id, client, phone, context))
    return ConversationHandler.END


# ——— Avtomatik guruh yaratish vazifasi ———
async def auto_group_task(user_id, client, phone, context, total_groups=500, batch_size=50, delay_between_batches=86400):
    if running_tasks.get(user_id or phone):
        # Agar shu user yoki phone uchun vazifa allaqachon ishga tushgan bo‘lsa, boshqa task yaratmaymiz
        await context.bot.send_message(user_id, f"⚠️ `{phone}` uchun guruh yaratish jarayoni allaqachon ishlamoqda.", parse_mode="Markdown")
        return

    running_tasks[user_id or phone] = True
    start_index = load_progress(phone)

    while start_index < total_groups:
        end_index = min(start_index + batch_size, total_groups)

        status_message = await context.bot.send_message(
            user_id,
            f"📱 `{phone}` raqam uchun guruhlar yaratilyapti: {start_index + 1} - {end_index}\n"
            f"{generate_progress_bar(start_index, total_groups)}",
            parse_mode="Markdown"
        )

        for i in range(start_index + 1, end_index + 1):
            try:
                result = await client(CreateChannelRequest(
                    title=f"Guruh #{i}",
                    about="Avtomatik guruh",
                    megagroup=True
                ))
                channel = result.chats[0]
                try:
                    await client(InviteToChannelRequest(channel, [TARGET_BOT]))
                except:
                    pass
                save_progress(phone, i)

                # Progressni yangilash
                if i % 1 == 0:
                    await status_message.edit_text(
                        f"📱 `{phone}` raqam uchun guruhlar yaratilyapti: {start_index + 1} - {end_index}\n"
                        f"{generate_progress_bar(i, total_groups)}",
                        parse_mode="Markdown"
                    )
            except Exception as e:
                await context.bot.send_message(user_id, f"❌ Guruh #{i} yaratishda xato: {e}")

            await asyncio.sleep(2)

        start_index = end_index

        await context.bot.send_message(user_id, f"✅ `{phone}` uchun {end_index} tagacha guruh yaratildi.")

        if start_index >= total_groups:
            await context.bot.send_message(user_id, f"🎉 `{phone}` raqam uchun barcha {total_groups} guruh yaratildi.")
            break

        # 24 soat kutish
        await asyncio.sleep(delay_between_batches)

    await client.disconnect()
    sessions.pop(user_id, None)
    running_tasks[user_id or phone] = False


# ——— Bekor qilish ———
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Bekor qilindi.")
    if (client := sessions.pop(update.effective_user.id, None)):
        await client.disconnect()
    return ConversationHandler.END


# 🌐 WEB SERVER
async def handle(_):
    return web.Response(text="Bot alive!")


async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Web-server {port} portda ishga tushdi.")
    while True:
        await asyncio.sleep(3600)


# ——— Avtomatik ishga tushirish uchun barcha sessiyalarni qayta tiklash ———
async def auto_resume_all_sessions(context):
    for file in os.listdir("sessions"):
        if file.endswith(".session"):
            phone = file.replace(".session", "")
            client = TelegramClient(f"sessions/{phone}", api_id, api_hash)
            await client.connect()
            if await client.is_user_authorized():
                logger.info(f"[✔] {phone} session uchun avtomatik davom ettirilmoqda...")
                # user_id yo‘q, shuning uchun xabar botdan userga yuborilmaydi, lekin o‘zgartirishingiz mumkin
                asyncio.create_task(auto_group_task(
                    user_id=None,
                    client=client,
                    phone=phone,
                    context=context
                ))


# 🤖 BOT ISHGA TUSHIRISH
async def run_bot():
    application = Application.builder().token(bot_token).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
            PHONE: [MessageHandler(filters.TEXT | filters.CONTACT, phone_received)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, code_received)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(conv_handler)

    logger.info("🤖 Bot ishga tushdi.")

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # 24 soatda bir marta barcha sessiyalarni tiklash (auto davom ettirish)
    async def periodic_runner():
        while True:
            await auto_resume_all_sessions(application)
            await asyncio.sleep(86400)  # 24 soat

    asyncio.create_task(periodic_runner())

    await asyncio.Event().wait()


# ASOSIY
async def main():
    await asyncio.gather(
        start_webserver(),
        run_bot()
    )


if __name__ == "__main__":
    asyncio.run(main())
