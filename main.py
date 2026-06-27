import os
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ChatMemberHandler, ContextTypes, filters
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 Top Posts"],["👁 Best Engagement"],["🔗 Forward Sources"],["⚙️ Settings"]], resize_keyboard=True)
BACK_KEYBOARD = ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Forward a message from your channel to register.", reply_markup=MAIN_KEYBOARD)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    logger.info(f"TEXT RECEIVED: {text}")
    user_id = update.effective_user.id
    if text == "🔙 Back":
        await update.message.reply_text("Main Menu", reply_markup=MAIN_KEYBOARD)
        return
    res = supabase.table("channels").select("*").eq("owner_id", user_id).execute()
    if not res.data:
        await update.message.reply_text("No channel found. Forward a channel message.", reply_markup=MAIN_KEYBOARD)
        return
    channel = res.data[0]
    if text == "📊 Top Posts":
        await show_top_posts(update, channel["chat_id"])
    elif text == "👁 Best Engagement":
        await show_best_engagement(update, channel["chat_id"])
    elif text == "🔗 Forward Sources":
        await show_forward_sources(update, channel["chat_id"])
async def register_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("FORWARDED MESSAGE RECEIVED")
    if getattr(update.message, 'forward_from_chat', None):
        chat = update.message.forward_from_chat
        user_id = update.effective_user.id
        supabase.table("channels").upsert({"chat_id": str(chat.id),"title": chat.title,"owner_id": user_id,"member_count": 0}).execute()
        await update.message.reply_text(f"✅ Channel '{chat.title}' registered!", reply_markup=MAIN_KEYBOARD)
async def show_top_posts(update: Update, chat_id):
    res = supabase.table("post_analytics").select("*").eq("chat_id", chat_id).limit(8).execute()
    if not res.data:
        await update.message.reply_text("No data yet.", reply_markup=BACK_KEYBOARD)
        return
    text = "🏆 Top Posts:\n\n"
    for post in sorted(res.data, key=lambda x: x.get("views", 0), reverse=True):
        text += f"• Post {post['message_id']}: {post.get('views',0)} views\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def show_best_engagement(update: Update, chat_id):
    res = supabase.table("post_analytics").select("message_id,views,members_at_time").eq("chat_id", chat_id).limit(8).execute()
    if not res.data:
        await update.message.reply_text("No data yet.", reply_markup=BACK_KEYBOARD)
        return
    text = "👁 Best Engagement:\n\n"
    for post in sorted(res.data, key=lambda x: x.get("views",0), reverse=True):
        rate = (post.get("views",0) / max(post.get("members_at_time",1),1)) * 100
        text += f"• Post {post['message_id']}: {rate:.1f}%\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def show_forward_sources(update: Update, chat_id):
    res = supabase.table("referrals").select("from_chat_title").eq("channel_id", chat_id).limit(10).execute()
    if not res.data:
        await update.message.reply_text("No forwards yet.", reply_markup=BACK_KEYBOARD)
        return
    counts = {}
    for r in res.data:
        counts[r["from_chat_title"]] = counts.get(r["from_chat_title"], 0) + 1
    text = "🔗 Forward Sources:\n\n"
    for title, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        text += f"• {title}: {count} times\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post:
        logger.info("Channel post received")
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & \
                                           filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.FORWARDED, register_channel))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, channel_post_handler))
    application.run_polling()
if __name__ == "__main__":
    main()
