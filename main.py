import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from aiohttp import web
from supabase import create_client
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ChatMemberHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.getenv("PORT", 8000))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

MAIN_KEYBOARD = ReplyKeyboardMarkup([
    ["📊 Top Posts"],
    ["👁 Best Engagement"],
    ["🔗 Forward Sources"],
    ["⚙️ Settings"]
], resize_keyboard=True)

BACK_KEYBOARD = ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)

# ====================== HANDLERS ======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Channel Stats Bot!\n\n"
        "Forward any message from your channel to register it.",
        reply_markup=MAIN_KEYBOARD
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == "🔙 Back":
        await update.message.reply_text("Main Menu", reply_markup=MAIN_KEYBOARD)
        return

    # Get user's channel
    res = supabase.table("channels").select("*").eq("owner_id", user_id).execute()
    if not res.data:
        await update.message.reply_text("No channel found.\nForward a message from your channel.", reply_markup=MAIN_KEYBOARD)
        return

    channel = res.data[0]

    if text == "📊 Top Posts":
        await show_top_posts(update, channel["chat_id"])
    elif text == "👁 Best Engagement":
        await show_best_engagement(update, channel["chat_id"])
    elif text == "🔗 Forward Sources":
        await show_forward_sources(update, channel["chat_id"])
    elif text == "⚙️ Settings":
        await update.message.reply_text("Settings will be added later.", reply_markup=MAIN_KEYBOARD)

async def register_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not getattr(update.message, 'forward_from_chat', None):
        return

    chat = update.message.forward_from_chat
    user_id = update.effective_user.id

    try:
        supabase.table("channels").upsert({
            "chat_id": str(chat.id),
            "title": chat.title,
            "owner_id": user_id,
            "member_count": 0
        }).execute()

        await update.message.reply_text(f"✅ Channel '{chat.title}' registered successfully!", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        logger.error(f"Register error: {e}")
        await update.message.reply_text("Failed to register channel.")

async def show_top_posts(update: Update, chat_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = supabase.table("post_analytics").select("*").eq("chat_id", chat_id).gte("timestamp", cutoff).limit(10).execute()

    if not res.data:
        await update.message.reply_text("No data yet.", reply_markup=BACK_KEYBOARD)
        return

    text = "🏆 Top Posts (by views):\n\n"
    for post in sorted(res.data, key=lambda x: x.get("views", 0), reverse=True)[:8]:
        text += f"• Post {post['message_id']}: {post.get('views', 0)} views\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)

async def show_best_engagement(update: Update, chat_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = supabase.table("post_analytics").select("message_id,views,members_at_time").eq("chat_id", chat_id).gte("timestamp", cutoff).limit(10).execute()

    if not res.data:
        await update.message.reply_text("No data yet.", reply_markup=BACK_KEYBOARD)
        return

    text = "👁 Best Engagement (%):\n\n"
    for post in sorted(res.data, key=lambda x: (x.get("views",0) / max(x.get("members_at_time",1),1)), reverse=True)[:8]:
        rate = (post.get("views",0) / max(post.get("members_at_time",1),1)) * 100
        text += f"• Post {post['message_id']}: {rate:.1f}% ({post.get('views',0)} views)\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)

async def show_forward_sources(update: Update, chat_id):
    res = supabase.table("referrals").select("from_chat_title").eq("channel_id", chat_id).limit(15).execute()

    if not res.data:
        await update.message.reply_text("No forwards recorded yet.", reply_markup=BACK_KEYBOARD)
        return

    counts = {}
    for r in res.data:
        title = r["from_chat_title"]
        counts[title] = counts.get(title, 0) + 1

    text = "🔗 Where your channel was shared:\n\n"
    for title, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        text += f"• {title}: {count} times\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)

# ====================== CHANNEL HANDLERS ======================

async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    msg = update.channel_post
    chat_id = str(update.effective_chat.id)
    try:
        members = await context.bot.get_chat_member_count(chat_id)
    except:
        members = 0

    supabase.table("post_analytics").upsert({
        "chat_id": chat_id,
        "message_id": msg.message_id,
        "views": msg.views or 0,
        "forwards": msg.forwards or 0,
        "members_at_time": members,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }, on_conflict="chat_id,message_id").execute()

async def chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member:
        user = update.chat_member.new_chat_member.user
        status = update.chat_member.new_chat_member.status
        try:
            if status == "left":
                await context.bot.send_message(update.effective_user.id, f"👤 {user.full_name} left the channel.")
            elif status == "member":
                await context.bot.send_message(update.effective_user.id, f"👤 {user.full_name} joined the channel.")
        except:
            pass

# ====================== WEBHOOK ======================

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
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & \
                                   filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.FORWARDED, register_channel))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, channel_post_handler))
    app.add_handler(ChatMemberHandler(chat_member_handler))

    web_app = web.Application()
    web_app.router.add_get("/healthz", health_check)
    web_app.router.add_post("/webhook", lambda r: webhook_handler(r, app))

    await app.initialize()
    await app.start()

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    webhook_url = f"{RENDER_URL}/webhook"
    try:
        await app.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set: {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook set failed: {e}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
