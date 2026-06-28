import os
import logging
import sqlite3
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")
REQUIRED_CHANNEL = "@dilemmapl"
DB_PATH = "bot_data.db"
PORT = int(os.getenv("PORT", 8080))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        joined_date TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS channels (
        channel_id INTEGER PRIMARY KEY,
        username TEXT,
        title TEXT,
        user_id INTEGER,
        added_date TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        message_id INTEGER,
        channel_id INTEGER,
        date TIMESTAMP,
        text TEXT,
        views INTEGER,
        forwards INTEGER,
        PRIMARY KEY (message_id, channel_id)
    )""")
    conn.commit()
    conn.close()
init_db()
def get_db():
    return sqlite3.connect(DB_PATH)
async def check_membership(context, user_id):
    try:
        chat_member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return chat_member.status in ["member", "administrator", "creator"]
    except:
        return False
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_membership(context, user.id):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")]]
        await update.message.reply_text(
            f"To use the bot, please join {REQUIRED_CHANNEL} first.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, joined_date) VALUES (?, ?, ?)",
              (user.id, user.username, datetime.now()))
    conn.commit()
    conn.close()
    keyboard = ReplyKeyboardMarkup([[KeyboardButton("My Channels")]], resize_keyboard=True)
    await update.message.reply_text("Welcome! Use the button below to manage your channels.", reply_markup=keyboard)
async def handle_my_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT channel_id, username, title FROM channels WHERE user_id=?", (user_id,))
    channels = c.fetchall()
    conn.close()
    if not channels:
        await update.message.reply_text("No channels added. Use /addchannel to add a channel.")
        return
    keyboard = []
    for ch in channels:
        display = ch[2] if ch[2] else ch[1] if ch[1] else str(ch[0])
        keyboard.append([InlineKeyboardButton(display, callback_data=f"channel_{ch[0]}")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="back_to_main")])
    await update.message.reply_text("Select your channels:", reply_markup=InlineKeyboardMarkup(keyboard))
async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Returned to main menu.")
    keyboard = ReplyKeyboardMarkup([[KeyboardButton("My Channels")]], resize_keyboard=True)
    await query.message.reply_text("Welcome! Use the button below to manage your channels.", reply_markup=keyboard)
async def channel_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    channel_id = int(query.data.split("_")[1])
    context.user_data["selected_channel"] = channel_id
    keyboard = [
        [InlineKeyboardButton("Top Posts", callback_data="analytics_top_posts")],
        [InlineKeyboardButton("Most Forwarded", callback_data="analytics_most_commented")],
        [InlineKeyboardButton("Recent Activity", callback_data="analytics_recent")],
        [InlineKeyboardButton("Back", callback_data="back_channels")]
    ]
    await query.edit_message_text("Select analysis type:", reply_markup=InlineKeyboardMarkup(keyboard))
async def analytics_top_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    channel_id = context.user_data.get("selected_channel")
    if not channel_id:
        await query.edit_message_text("Please select a channel first.")
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT message_id, text, views, forwards FROM messages WHERE channel_id=? ORDER BY views DESC LIMIT 10", (channel_id,))
    msgs = c.fetchall()
    conn.close()
    if not msgs:
        await query.edit_message_text("No messages received for this channel yet.")
        return
    text = "🔝 Top Posts:\n\n"
    for idx, (msg_id, msg_text, views, forwards) in enumerate(msgs, 1):
        preview = msg_text[:50] + "..." if msg_text and len(msg_text) > 50 else msg_text or "No text"
        text += f"{idx}. {preview}\n👁 {views} Views | 🔄 {forwards} Forwards\n\n"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_analytics")]]))
async def analytics_most_commented(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    channel_id = context.user_data.get("selected_channel")
    if not channel_id:
        await query.edit_message_text("Please select a channel first.")
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT message_id, text, views, forwards FROM messages WHERE channel_id=? ORDER BY forwards DESC LIMIT 10", (channel_id,))
    msgs = c.fetchall()
    conn.close()
    if not msgs:
        await query.edit_message_text("No messages received for this channel yet.")
        return
    text = "💬 Most Forwarded Posts:\n\n"
    for idx, (msg_id, msg_text, views, forwards) in enumerate(msgs, 1):
        preview = msg_text[:50] + "..." if msg_text and len(msg_text) > 50 else msg_text or "No text"
        text += f"{idx}. {preview}\n👁 {views} Views | 🔄 {forwards} Forwards\n\n"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_analytics")]]))
async def analytics_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    channel_id = context.user_data.get("selected_channel")
    if not channel_id:
        await query.edit_message_text("Please select a channel first.")
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT message_id, text, date, views, forwards FROM messages WHERE channel_id=? ORDER BY date DESC LIMIT 5", (channel_id,))
    msgs = c.fetchall()
    conn.close()
    if not msgs:
        await query.edit_message_text("No messages received for this channel yet.")
        return
    text = "🕒 Recent Activity:\n\n"
    for idx, (msg_id, msg_text, date_str, views, forwards) in enumerate(msgs, 1):
        preview = msg_text[:50] + "..." if msg_text and len(msg_text) > 50 else msg_text or "No text"
        text += f"{idx}. {preview}\n📅 {date_str[:16]}\n👁 {views} Views | 🔄 {forwards} Forwards\n\n"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_analytics")]]))
async def back_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    channel_id = context.user_data.get("selected_channel")
    if channel_id:
        keyboard = [
            [InlineKeyboardButton("Top Posts", callback_data="analytics_top_posts")],
            [InlineKeyboardButton("Most Forwarded", callback_data="analytics_most_commented")],
            [InlineKeyboardButton("Recent Activity", callback_data="analytics_recent")],
            [InlineKeyboardButton("Back", callback_data="back_channels")]
        ]
        await query.edit_message_text("Select analysis type:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text("Please select a channel first.")
async def back_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT channel_id, username, title FROM channels WHERE user_id=?", (user_id,))
    channels = c.fetchall()
    conn.close()
    if not channels:
        await query.edit_message_text("No channels found.")
        return
    keyboard = []
    for ch in channels:
        display = ch[2] if ch[2] else ch[1] if ch[1] else str(ch[0])
        keyboard.append([InlineKeyboardButton(display, callback_data=f"channel_{ch[0]}")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="back_to_main")])
    await query.edit_message_text("Select your channels:", reply_markup=InlineKeyboardMarkup(keyboard))
ADD_CHANNEL = 1
async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_membership(context, user_id):
        await update.message.reply_text("Please join the required channel first.")
        return
    await update.message.reply_text("Please enter the channel username (e.g. @username) or its numeric ID:")
    return ADD_CHANNEL
async def add_channel_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    if input_text.startswith("@"):
        chat_username = input_text[1:]
        try:
            chat = await context.bot.get_chat(f"@{chat_username}")
        except:
            await update.message.reply_text("Channel not found. Please enter a valid username.")
            return ADD_CHANNEL
    else:
        try:
            chat_id = int(input_text)
            chat = await context.bot.get_chat(chat_id)
        except:
            await update.message.reply_text("Invalid numeric ID.")
            return ADD_CHANNEL
    if chat.type not in ["channel", "supergroup"]:
        await update.message.reply_text("Please enter a valid channel.")
        return ADD_CHANNEL
    bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
    if bot_member.status not in ["administrator", "creator"]:
        await update.message.reply_text("The bot must be an admin in the channel to receive messages. Please add the bot as admin and try again.")
        return ADD_CHANNEL
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO channels (channel_id, username, title, user_id, added_date) VALUES (?, ?, ?, ?, ?)",
              (chat.id, chat.username, chat.title, user_id, datetime.now()))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Channel {chat.title or chat.username} added successfully.")
    keyboard = ReplyKeyboardMarkup([[KeyboardButton("My Channels")]], resize_keyboard=True)
    await update.message.reply_text("Use the button below to view your channels.", reply_markup=keyboard)
    return ConversationHandler.END
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END
async def store_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post:
        msg = update.channel_post
        channel_id = msg.chat_id
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT user_id FROM channels WHERE channel_id=?", (channel_id,))
        row = c.fetchone()
        if row:
            c.execute("INSERT OR IGNORE INTO messages (message_id, channel_id, date, text, views, forwards) VALUES (?, ?, ?, ?, ?, ?)",
                      (msg.message_id, channel_id, msg.date, msg.text or msg.caption or "", msg.views or 0, msg.forward_count or 0))
            conn.commit()
        conn.close()
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Help:\n"
        "/start - Start and check membership\n"
        "/addchannel - Add a new channel\n"
        "Button 'My Channels' - View channels and analytics\n"
        "/help - This message"
    )
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()
def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"Health server running on port {PORT}")
    server.serve_forever()
def main():
    app = Application.builder().token(TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addchannel", add_channel_start)],
        states={
            ADD_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex("^My Channels$"), handle_my_channels))
    app.add_handler(CallbackQueryHandler(channel_selection, pattern="^channel_"))
    app.add_handler(CallbackQueryHandler(analytics_top_posts, pattern="^analytics_top_posts$"))
    app.add_handler(CallbackQueryHandler(analytics_most_commented, pattern="^analytics_most_commented$"))
    app.add_handler(CallbackQueryHandler(analytics_recent, pattern="^analytics_recent$"))
    app.add_handler(CallbackQueryHandler(back_analytics, pattern="^back_analytics$"))
    app.add_handler(CallbackQueryHandler(back_channels, pattern="^back_channels$"))
    app.add_handler(CallbackQueryHandler(back_to_main, pattern="^back_to_main$"))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, store_channel_message))
    threading.Thread(target=run_health_server, daemon=True).start()
    app.run_polling()
if __name__ == "__main__":
    main()
