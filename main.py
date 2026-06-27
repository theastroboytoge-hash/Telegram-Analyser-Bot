import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ChatMemberHandler, ContextTypes, filters
from telegram.error import TelegramError
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 Top Posts"],["👁 Best Engagement"],["🔗 Forward Sources"],["⚙️ Settings"]], resize_keyboard=True)
BACK_KEYBOARD = ReplyKeyboardMarkup([["🔙 Back to Menu"]], resize_keyboard=True)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Add the bot as admin to your channel then send /addchannel", reply_markup=ReplyKeyboardMarkup([["/addchannel"]], resize_keyboard=True))
async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Forward a message from your channel or send /done after adding the bot as admin.", reply_markup=ReplyKeyboardMarkup([["/done"]], resize_keyboard=True))
async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.forward_from_chat:
        await update.message.reply_text("Please forward a message from the channel.")
        return
    chat = update.message.forward_from_chat
    user_id = update.effective_user.id
    try:
        await supabase.table("channels").upsert({"chat_id": str(chat.id),"title": chat.title,"owner_id": user_id,"member_count": 0}).execute()
        await update.message.reply_text(f"✅ Channel '{chat.title}' registered successfully!", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        await update.message.reply_text("Error registering channel.")
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🔙 Back to Menu":
        await update.message.reply_text("Main Menu:", reply_markup=MAIN_KEYBOARD)
        return
    user_id = update.effective_user.id
    channels = await supabase.table("channels").select("*").eq("owner_id", user_id).execute()
    if not channels.data:
        await update.message.reply_text("Please register a channel first.", reply_markup=MAIN_KEYBOARD)
        return
    channel = channels.data[0]
    if text == "📊 Top Posts":
        await show_top_posts(update, channel["chat_id"])
    elif text == "👁 Best Engagement":
        await show_best_engagement(update, channel["chat_id"])
    elif text == "🔗 Forward Sources":
        await show_referrals(update, channel["chat_id"])
    elif text == "⚙️ Settings":
        await update.message.reply_text("Settings coming soon.", reply_markup=MAIN_KEYBOARD)
async def show_top_posts(update: Update, chat_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = await supabase.table("post_analytics").select("*").eq("chat_id", chat_id).gte("timestamp", cutoff).limit(10).execute()
    if not res.data:
        await update.message.reply_text("No data yet.", reply_markup=BACK_KEYBOARD)
        return
    text = "🏆 Top Posts:\n\n"
    for post in sorted(res.data, key=lambda x: x.get("views", 0), reverse=True)[:5]:
        text += f"📌 Post {post['message_id']}: {post.get('views',0)} views\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def show_best_engagement(update: Update, chat_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = await supabase.table("post_analytics").select("views,members_at_time,message_id").eq("chat_id", chat_id).gte("timestamp", cutoff).execute()
    if not res.data:
        await update.message.reply_text("No data yet.", reply_markup=BACK_KEYBOARD)
        return
    text = "👁 Best Engagement Posts:\n\n"
    for post in sorted(res.data, key=lambda x: (x.get("views",0) / max(x.get("members_at_time",1),1)), reverse=True)[:5]:
        rate = (post.get("views",0) / max(post.get("members_at_time",1),1)) * 100
        text += f"📌 Post {post['message_id']}: {rate:.1f}% ({post.get('views',0)} views)\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def show_referrals(update: Update, chat_id):
    res = await supabase.table("referrals").select("from_chat_title").eq("channel_id", chat_id).limit(20).execute()
    if not res.data:
        await update.message.reply_text("No forwards yet.", reply_markup=BACK_KEYBOARD)
        return
    counts = {}
    for r in res.data:
        title = r["from_chat_title"]
        counts[title] = counts.get(title, 0) + 1
    text = "🔗 Forward Sources:\n\n"
    for title, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        text += f"• {title}: {count} times\n"
    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
async def post_engagement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    chat_id = str(update.effective_chat.id)
    msg = update.channel_post
    views = msg.views or 0
    forwards = msg.forwards or 0
    try:
        member_count = await context.bot.get_chat_member_count(chat_id)
    except:
        member_count = 0
    await supabase.table("post_analytics").upsert({"chat_id": chat_id,"message_id": msg.message_id,"views": views,"forwards": forwards,"members_at_time": member_count,"timestamp": datetime.now(timezone.utc).isoformat()}, on_conflict="chat_id,message_id").execute()
async def member_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.my_chat_member:
        status = update.my_chat_member.new_chat_member.status
        if status in ["left", "kicked"]:
            await context.bot.send_message(update.effective_user.id, f"⚠️ Bot was removed from channel: {update.effective_chat.title}")
async def notify_member_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member:
        user = update.chat_member.new_chat_member.user
        status = update.chat_member.new_chat_member.status
        if status == "left":
            await context.bot.send_message(update.effective_user.id, f"👤 User {user.full_name} left the channel.")
        elif status == "member":
            await context.bot.send_message(update.effective_user.id, f"👤 User {user.full_name} joined the channel.")
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addchannel", add_channel))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(MessageHandler(filters.TEXT & \
                                   filters.COMMAND, main_menu))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, post_engagement_handler))
    app.add_handler(ChatMemberHandler(member_update_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(notify_member_change))
    app.run_polling()
if __name__ == "__main__":
    asyncio.run(main())
