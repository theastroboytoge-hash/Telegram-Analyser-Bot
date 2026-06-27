import os
import asyncio
import datetime
import hashlib
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from pymongo import MongoClient
import aiohttp
MONGO_URL = os.getenv("MONGO_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
client = MongoClient(MONGO_URL)
db = client.tg_analytics
stats_col = db.channel_stats
posts_col = db.post_analytics
clicks_col = db.link_clicks
member_log = db.member_log
referral_col = db.referrals
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("بات آنالیز کانال فعال است.")
async def track_daily_members(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    try:
        count = await context.bot.get_chat_member_count(chat_id)
        member_log.insert_one({"chat_id": chat_id, "count": count, "date": datetime.datetime.utcnow()})
    except Exception as e:
        print(f"Error in daily tracking: {e}")
async def analyze_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("لطفاً آیدی عددی پست را وارد کنید. مثال:\n/analyze 12345")
        return
    message_id = int(context.args[0])
    chat_id = update.effective_chat.id
    try:
        post = await context.bot.get_message(chat_id, message_id)
        views = post.views or 0
        forwards = post.forwards or 0
        total_members = await context.bot.get_chat_member_count(chat_id)
        engagement_rate = (views / total_members) * 100 if total_members > 0 else 0
        prev_member = member_log.find_one({"chat_id": str(chat_id)}, sort=[("date", -1)])
        member_change = 0
        if prev_member:
            latest_count = await context.bot.get_chat_member_count(chat_id)
            member_change = latest_count - prev_member["count"]
        posts_col.update_one(
            {"message_id": message_id, "chat_id": str(chat_id)},
            {"$set": {
                "views": views,
                "forwards": forwards,
                "engagement_rate": round(engagement_rate, 2),
                "members_at_time": total_members,
                "member_change_after": member_change,
                "timestamp": datetime.datetime.utcnow()
            }},
            upsert=True
        )
        report = (
            f"📊 تحلیل پست {message_id}\n"
            f"👁 ویو: {views}\n"
            f"🔄 فوروارد: {forwards}\n"
            f"📈 نرخ تعامل: {engagement_rate:.1f}%\n"
            f"👥 اعضا هنگام پست: {total_members}\n"
            f"📉 تغییر اعضا پس از پست: {member_change:+d}\n"
        )
        await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")
async def create_tracked_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("استفاده: /tracklink <نام_کمپین> <لینک_هدف>\nمثال: /tracklink offer1 https://example.com")
        return
    campaign = context.args[0]
    target_url = context.args[1]
    chat_id = update.effective_chat.id
    unique_id = hashlib.md5(f"{chat_id}{campaign}{datetime.datetime.utcnow()}".encode()).hexdigest()[:8]
    base_url = os.getenv("RENDER_URL", "https://your-bot.onrender.com")
    tracked_url = f"{base_url}/click/{unique_id}"
    clicks_col.insert_one({
        "unique_id": unique_id,
        "campaign": campaign,
        "target_url": target_url,
        "chat_id": str(chat_id),
        "clicks": 0,
        "created_at": datetime.datetime.utcnow()
    })
    await update.message.reply_text(
        f"🔗 لینک رهگیر ساخته شد:\n{tracked_url}\n\n"
        f"کمپین: {campaign}\n"
        f"هدف: {target_url}\n\n"
        f"این لینک را در پست‌های خود استفاده کنید."
    )
async def link_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /linkstats <نام_کمپین>")
        return
    campaign = context.args[0]
    chat_id = update.effective_chat.id
    records = list(clicks_col.find({"campaign": campaign, "chat_id": str(chat_id)}))
    if not records:
        await update.message.reply_text("کمپینی با این نام یافت نشد.")
        return
    total_clicks = sum(r.get("clicks", 0) for r in records)
    await update.message.reply_text(f"📊 آمار کمپین {campaign}:\n🖱 تعداد کل کلیک‌ها: {total_clicks}")
async def best_time_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    pipeline = [
        {"$match": {"chat_id": str(chat_id)}},
        {"$group": {
            "_id": {"$hour": "$timestamp"},
            "avg_views": {"$avg": "$views"},
            "count": {"$sum": 1}
        }},
        {"$sort": {"avg_views": -1}},
        {"$limit": 5}
    ]
    results = list(posts_col.aggregate(pipeline))
    if not results:
        await update.message.reply_text("هنوز داده کافی برای تحلیل بهترین زمان ارسال وجود ندارد.")
        return
    report = "⏰ بهترین ساعات ارسال (بر اساس میانگین ویو):\n\n"
    for r in results:
        report += f"🕐 ساعت {r['_id']}:00 - میانگین {r['avg_views']:.0f} ویو (از {r['count']} پست)\n"
    await update.message.reply_text(report)
async def channel_growth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    days = 7
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    records = list(member_log.find({"chat_id": str(chat_id), "date": {"$gte": cutoff}}).sort("date", 1))
    if len(records) < 2:
        await update.message.reply_text("داده کافی برای تحلیل رشد وجود ندارد.")
        return
    first = records[0]["count"]
    last = records[-1]["count"]
    growth = last - first
    growth_percent = (growth / first * 100) if first > 0 else 0
    report = (
        f"📈 رشد کانال در {days} روز گذشته:\n"
        f"👥 اعضا: از {first} به {last}\n"
        f"📊 تغییر: {growth:+d} ({growth_percent:+.1f}%)\n"
    )
    await update.message.reply_text(report)
async def handle_inline_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("track_"):
        campaign = data.replace("track_", "")
        chat_id = str(query.message.chat.id)
        target = clicks_col.find_one({"campaign": campaign, "chat_id": chat_id})
        if target:
            clicks_col.update_one({"campaign": campaign, "chat_id": chat_id}, {"$inc": {"clicks": 1}})
            await query.edit_message_text(f"لینک کمپین {campaign}:\n{target['target_url']}\n\n✅ کلیک شما ثبت شد.")
async def post_engagement_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    chat_id = str(update.effective_chat.id)
    msg_id = update.channel_post.message_id
    views = update.channel_post.views or 0
    forwards = update.channel_post.forwards or 0
    posts_col.update_one(
        {"message_id": msg_id, "chat_id": chat_id},
        {"$set": {
            "views": views,
            "forwards": forwards,
            "timestamp": datetime.datetime.utcnow()
        }},
        upsert=True
    )
async def track_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.forward_from_chat:
        fwd_chat = update.message.forward_from_chat
        referral_col.insert_one({
            "channel_id": str(update.effective_chat.id),
            "from_chat_id": str(fwd_chat.id),
            "from_chat_title": fwd_chat.title or "Unknown",
            "message_id": update.message.message_id,
            "date": datetime.datetime.utcnow()
        })
async def referral_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    pipeline = [
        {"$match": {"channel_id": str(chat_id)}},
        {"$group": {"_id": "$from_chat_title", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    results = list(referral_col.aggregate(pipeline))
    if not results:
        await update.message.reply_text("هنوز داده فورواردی ثبت نشده است.")
        return
    report = "📢 کانال‌هایی که بیشترین فوروارد را داشته‌اند:\n\n"
    for r in results:
        report += f"📌 {r['_id']}: {r['count']} فوروارد\n"
    await update.message.reply_text(report)
async def click_webhook(request):
    unique_id = request.match_info.get("unique_id")
    record = clicks_col.find_one({"unique_id": unique_id})
    if not record:
        return aiohttp.web.Response(text="Link not found", status=404)
    clicks_col.update_one({"unique_id": unique_id}, {"$inc": {"clicks": 1}})
    target = record["target_url"]
    raise aiohttp.web.HTTPFound(target)
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("analyze", analyze_post))
    application.add_handler(CommandHandler("tracklink", create_tracked_link))
    application.add_handler(CommandHandler("linkstats", link_stats))
    application.add_handler(CommandHandler("besttime", best_time_report))
    application.add_handler(CommandHandler("growth", channel_growth))
    application.add_handler(CommandHandler("referrals", referral_report))
    application.add_handler(CallbackQueryHandler(handle_inline_button, pattern="^track_"))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, post_engagement_buttons))
    application.add_handler(MessageHandler(filters.FORWARDED, track_referral))
    for chat_id in os.getenv("TRACK_CHANNELS", "").split(","):
        if chat_id.strip():
            application.job_queue.run_daily(track_daily_members, time=datetime.time(hour=23, minute=0), chat_id=chat_id.strip())
    application.run_polling()
if __name__ == "__main__":
    main()
