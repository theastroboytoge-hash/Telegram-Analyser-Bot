import os
import asyncio
import logging
from datetime import datetime, timezone
from aiohttp import web
from supabase import create_client
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.getenv("PORT", 8000))
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 Top Posts"],["👁 Best Engagement"],["🔗 Forward Sources"],["⚙️ Settings"]], resize_keyboard=True)
BACK_KEYBOARD = ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome!\n\nForward a message from your channel to register.", reply_markup=MAIN_KEYBOARD)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    logger.info(f"TEXT HANDLER TRIGGERED from {user_id}: '{text}'")
    if text == "🔙 Back":
        await update.message.reply_text("Main Menu", reply_markup=MAIN_KEYBOARD)
        return
    res = supabase.table("channels").select("*").eq("owner_id", user_id).execute()
    if not res.data:
        await update.message.reply_text("❌ First forward a message from your channel.", reply_markup=MAIN_KEYBOARD)
        return
    channel = res.data[0]
    chat_id = channel["chat_id"]
    if text == "📊 Top Posts":
        await show_top_posts(update, chat_id)
    elif text == "👁 Best Engagement":
        await show_best_engagement(update, chat_id)
    elif text == "🔗 Forward Sources":
        await show_forward_sources(update, chat_id)
    elif text == "⚙️ Settings":
        await update.message.reply_text("⚙️ Settings under development...", reply_markup=BACK_KEYBOARD)
    else:
        await update.message.reply_text("Unknown command. Use the menu below.", reply_markup=MAIN_KEYBOARD)
async def register_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return
    if not message.forward_origin and not getattr(message, 'forward_from_chat', None):
        await message.reply_text("Please forward a message from your channel.")
        return
    chat = getattr(message.forward_origin, 'chat', None) or getattr(message, 'forward_from_chat', None)
    if not chat or chat.type not in ['channel', 'supergroup']:
        await message.reply_text("Only channels and supergroups are supported.")
        return
    user_id = update.effective_user.id
    try:
        data = {"chat_id": str(chat.id),"title": chat.title or "Unknown Channel","owner_id": user_id,"member_count": getattr(chat, 'member_count', 0)}
        supabase.table("channels").upsert(data).execute()
        await message.reply_text(f"✅ Channel «{chat.title}» registered successfully!", reply_markup=MAIN_KEYBOARD)
        logger.info(f"Channel registered: {chat.title} ({chat.id}) by user {user_id}")
    except Exception as e:
        logger.error(f"Error registering channel: {e}")
        await message.reply_text("❌ Error registering channel. Try again.")
async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    post = update.channel_post
    chat_id = str(post.chat.id)
    try:
        supabase.table("post_analytics").upsert({"chat_id": chat_id,"message_id": post.message_id,"views": 0,"members_at_time": 0,"created_at": datetime.now(timezone.utc).isoformat()}).execute()
        logger.info(f"New post saved → Channel: {chat_id} | Message: {post.message_id}")
    except Exception as e:
        logger.error(f"Error saving post analytics: {e}")
async def show_top_posts(update: Update, chat_id: str):
    try:
        res = supabase.table("post_analytics").select("*").eq("chat_id", chat_id).limit(10).execute()
        if not res.data:
            await update.message.reply_text("📭 No data yet.", reply_markup=BACK_KEYBOARD)
            return
        text = "🏆 **Top Posts**\n\n"
        for post in sorted(res.data, key=lambda x: x.get("views", 0), reverse=True):
            text += f"• Post {post['message_id']}: **{post.get('views', 0):,}** views\n"
        await update.message.reply_text(text, reply_markup=BACK_KEYBOARD, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in show_top_posts: {e}")
        await update.message.reply_text("❌ Error fetching stats.", reply_markup=BACK_KEYBOARD)
async def show_best_engagement(update: Update, chat_id: str):
    try:
        res = supabase.table("post_analytics").select("message_id,views,members_at_time").eq("chat_id", chat_id).limit(10).execute()
        if not res.data:
            await update.message.reply_text("📭 No data yet.", reply_markup=BACK_KEYBOARD)
            return
        text = "👁 **Best Engagement**\n\n"
        for post in sorted(res.data, key=lambda x: x.get("views", 0), reverse=True):
            members = max(post.get("members_at_time", 1), 1)
            rate = (post.get("views", 0) / members) * 100
            text += f"• Post {post['message_id']}: **{rate:.1f}%** engagement\n"
        await update.message.reply_text(text, reply_markup=BACK_KEYBOARD, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in show_best_engagement: {e}")
        await update.message.reply_text("❌ Error fetching stats.", reply_markup=BACK_KEYBOARD)
async def show_forward_sources(update: Update, chat_id: str):
    try:
        res = supabase.table("referrals").select("from_chat_title").eq("channel_id", chat_id).limit(15).execute()
        if not res.data:
            await update.message.reply_text("🔗 No forwards recorded yet.", reply_markup=BACK_KEYBOARD)
            return
        counts = {}
        for r in res.data:
            title = r["from_chat_title"] or "Unknown"
            counts[title] = counts.get(title, 0) + 1
        text = "🔗 **Forward Sources**\n\n"
        for title, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            text += f"• {title}: **{count}** times\n"
        await update.message.reply_text(text, reply_markup=BACK_KEYBOARD, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in show_forward_sources: {e}")
        await update.message.reply_text("❌ Error fetching stats.", reply_markup=BACK_KEYBOARD)
async def webhook_handler(request, application: Application):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return web.Response(text="OK")
async def health_check(request):
    return web.Response(text="OK")
async def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.FORWARDED, register_channel))
    application.add_handler(MessageHandler(filters.TEXT & \
                                           filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, channel_post_handler))
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
        logger.info(f"✅ Webhook set: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Webhook failed: {e}")
    logger.info("🚀 Bot is running...")
    await asyncio.Event().wait()
if __name__ == "__main__":
    asyncio.run(main())
