import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from aiohttp import web
from supabase import create_client
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ChatMemberHandler, ContextTypes, filters
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.getenv("PORT", 8000))
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 Top Posts"],["👁 Best Engagement"],["🔗 Forward Sources"],["⚙️ Settings"]], resize_keyboard=True)
BACK_KEYBOARD = ReplyKeyboardMarkup([["🔙 Back to Menu"]], resize_keyboard=True)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("=== START COMMAND RECEIVED ===")
    await update.message.reply_text("Welcome! Forward a message from your channel to register it.", reply_markup=MAIN_KEYBOARD)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"=== TEXT MESSAGE RECEIVED: {update.message.text} ===")
    text = update.message.text.strip()
    if text == "🔙 Back to Menu":
        await update.message.reply_text("Main Menu:", reply_markup=MAIN_KEYBOARD)
        return
    user_id = update.effective_user.id
    channels_res = await supabase.table("channels").select("*").eq("owner_id", user_id).execute()
    if not channels_res.data:
        await update.message.reply_text("No channel found. Forward a message from channel to register.", reply_markup=MAIN_KEYBOARD)
        return
    channel = channels_res.data[0]
    if text == "📊 Top Posts":
        await show_top_posts(update, channel["chat_id"])
    elif text == "👁 Best Engagement":
        await show_best_engagement(update, channel["chat_id"])
    elif text == "🔗 Forward Sources":
        await show_referrals(update, channel["chat_id"])
    elif text == "⚙️ Settings":
        await update.message.reply_text("Settings coming soon.", reply_markup=MAIN_KEYBOARD)
    else:
        await update.message.reply_text("Use the buttons below.", reply_markup=MAIN_KEYBOARD)
async def register_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("=== FORWARDED MESSAGE RECEIVED ===")
    if update.message and getattr(update.message, 'forward_from_chat', None):
        chat = update.message.forward_from_chat
        user_id = update.effective_user.id
        logger.info(f"Registering channel: {chat.title} (ID: {chat.id})")
        try:
            await supabase.table("channels").upsert({
                "chat_id": str(chat.id),
                "title": chat.title,
                "owner_id": user_id,
                "member_count": 0
            }).execute()
            await update.message.reply_text(f"✅ Channel '{chat.title}' registered!", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            logger.error(f"Register error: {e}")
            await update.message.reply_text("Failed to register channel.")
    else:
        logger.warning("Forwarded handler triggered but no forward_from_chat")
async def show_top_posts(update: Update, chat_id):
    res = await supabase.table("post_analytics").select("*").eq("chat_id", chat_id).limit(5).execute()
    if not res.data:
        await update.message.reply_text("No posts data yet.", reply_markup=BACK_KEYBOARD)
        return
    text = "🏆 Top Posts:\n\n"
    for post in sorted(res.data, key=lambda x: x.get("views",0), reverse=True):
        text += f"Post {post['message_id']}: {post.get('views',0)} views\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def show_best_engagement(update: Update, chat_id):
    res = await supabase.table("post_analytics").select("views,members_at_time,message_id").eq("chat_id", chat_id).limit(5).execute()
    if not res.data:
        await update.message.reply_text("No data yet.", reply_markup=BACK_KEYBOARD)
        return
    text = "👁 Best Engagement:\n\n"
    for post in sorted(res.data, key=lambda x: x.get("views",0), reverse=True):
        rate = (post.get("views",0) / max(post.get("members_at_time",1),1)) * 100
        text += f"Post {post['message_id']}: {rate:.1f}%\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def show_referrals(update: Update, chat_id):
    res = await supabase.table("referrals").select("from_chat_title").eq("channel_id", chat_id).limit(10).execute()
    if not res.data:
        await update.message.reply_text("No forwards yet.", reply_markup=BACK_KEYBOARD)
        return
    text = "🔗 Forward Sources:\n\n"
    counts = {}
    for r in res.data:
        counts[r["from_chat_title"]] = counts.get(r["from_chat_title"], 0) + 1
    for title, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        text += f"• {title}: {count} times\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def post_engagement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post:
        logger.info(f"Channel post detected: {update.channel_post.message_id}")
async def webhook_handler(request, application: Application):
    try:
        data = await request.json()
        logger.info(f"Webhook received update: {data.get('update_id')}")
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return web.Response(text="OK")
async def health_check(request):
    return web.Response(text="OK")
async def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start), group=0)
    application.add_handler(MessageHandler(filters.TEXT & \
                                           filters.COMMAND, handle_text), group=1)
    application.add_handler(MessageHandler(filters.FORWARDED, register_channel_handler), group=2)
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, post_engagement_handler), group=3)
    app = web.Application()
    app.router.add_get("/healthz", health_check)
    app.router.add_post("/webhook", lambda r: webhook_handler(r, application))
    await application.initialize()
    await application.start()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    webhook_url = f"{RENDER_URL}/webhook"
    try:
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set: {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook set failed: {e}")
    await asyncio.Event().wait()
if __name__ == "__main__":
    asyncio.run(main())
