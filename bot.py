import os
from datetime import datetime, timedelta
import hashlib
from supabase import create_client
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("بات آنالیز کانال فعال است.")
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
        supabase.table("member_log").insert({"chat_id": str(chat_id), "count": total_members, "date": datetime.utcnow().isoformat()}).execute()
        prev = supabase.table("member_log").select("*").eq("chat_id", str(chat_id)).order("date", desc=True).limit(2).execute()
        member_change = 0
        if len(prev.data) >= 2:
            member_change = prev.data[0]["count"] - prev.data[1]["count"]
        supabase.table("post_analytics").upsert({
            "message_id": message_id,
            "chat_id": str(chat_id),
            "views": views,
            "forwards": forwards,
            "engagement_rate": round(engagement_rate, 2),
            "members_at_time": total_members,
            "member_change_after": member_change,
            "timestamp": datetime.utcnow().isoformat()
        }, on_conflict="message_id,chat_id").execute()
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
    unique_id = hashlib.md5(f"{chat_id}{campaign}{datetime.utcnow()}".encode()).hexdigest()[:8]
    base_url = os.getenv("RENDER_URL", "https://your-bot.onrender.com")
    tracked_url = f"{base_url}/click/{unique_id}"
    supabase.table("link_clicks").insert({
        "unique_id": unique_id,
        "campaign": campaign,
        "target_url": target_url,
        "chat_id": str(chat_id),
        "clicks": 0,
        "created_at": datetime.utcnow().isoformat()
    }).execute()
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
    res = supabase.table("link_clicks").select("*").eq("campaign", campaign).eq("chat_id", str(chat_id)).execute()
    if not res.data:
        await update.message.reply_text("کمپینی با این نام یافت نشد.")
        return
    total_clicks = sum(r["clicks"] for r in res.data)
    await update.message.reply_text(f"📊 آمار کمپین {campaign}:\n🖱 تعداد کل کلیک‌ها: {total_clicks}")
async def best_time_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    res = supabase.table("post_analytics").select("timestamp, views").eq("chat_id", str(chat_id)).execute()
    if not res.data:
        await update.message.reply_text("هنوز داده کافی برای تحلیل بهترین زمان ارسال وجود ندارد.")
        return
    hour_map = {}
    for row in res.data:
        try:
            dt = datetime.fromisoformat(row["timestamp"])
            hour = dt.hour
            if hour not in hour_map:
                hour_map[hour] = {"total_views": 0, "count": 0}
            hour_map[hour]["total_views"] += row["views"]
            hour_map[hour]["count"] += 1
        except:
            continue
    if not hour_map:
        await update.message.reply_text("داده کافی وجود ندارد.")
        return
    sorted_hours = sorted(hour_map.items(), key=lambda x: x[1]["total_views"]/x[1]["count"], reverse=True)[:5]
    report = "⏰ بهترین ساعات ارسال (بر اساس میانگین ویو):\n\n"
    for hour, data in sorted_hours:
        avg = data["total_views"] / data["count"]
        report += f"🕐 ساعت {hour}:00 - میانگین {avg:.0f} ویو (از {data['count']} پست)\n"
    await update.message.reply_text(report)
async def channel_growth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    days = 7
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    res = supabase.table("member_log").select("*").eq("chat_id", str(chat_id)).gte("date", cutoff).order("date").execute()
    if len(res.data) < 2:
        await update.message.reply_text("داده کافی برای تحلیل رشد وجود ندارد.")
        return
    first = res.data[0]["count"]
    last = res.data[-1]["count"]
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
        res = supabase.table("link_clicks").select("*").eq("campaign", campaign).eq("chat_id", chat_id).execute()
        if res.data:
            supabase.table("link_clicks").update({"clicks": res.data[0]["clicks"] + 1}).eq("campaign", campaign).eq("chat_id", chat_id).execute()
            await query.edit_message_text(f"لینک کمپین {campaign}:\n{res.data[0]['target_url']}\n\n✅ کلیک شما ثبت شد.")
async def post_engagement_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    chat_id = str(update.effective_chat.id)
    msg_id = update.channel_post.message_id
    views = update.channel_post.views or 0
    forwards = update.channel_post.forwards or 0
    supabase.table("post_analytics").upsert({
        "message_id": msg_id,
        "chat_id": chat_id,
        "views": views,
        "forwards": forwards,
        "timestamp": datetime.utcnow().isoformat()
    }, on_conflict="message_id,chat_id").execute()
async def track_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.forward_from_chat:
        fwd_chat = update.message.forward_from_chat
        supabase.table("referrals").insert({
            "channel_id": str(update.effective_chat.id),
            "from_chat_id": str(fwd_chat.id),
            "from_chat_title": fwd_chat.title or "Unknown",
            "message_id": update.message.message_id,
            "date": datetime.utcnow().isoformat()
        }).execute()
async def referral_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    res = supabase.table("referrals").select("from_chat_title").eq("channel_id", str(chat_id)).execute()
    if not res.data:
        await update.message.reply_text("هنوز داده فورواردی ثبت نشده است.")
        return
    counts = {}
    for row in res.data:
        title = row["from_chat_title"]
        counts[title] = counts.get(title, 0) + 1
    sorted_titles = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    report = "📢 کانال‌هایی که بیشترین فوروارد را داشته‌اند:\n\n"
    for title, count in sorted_titles:
        report += f"📌 {title}: {count} فوروارد\n"
    await update.message.reply_text(report)
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
    application.run_polling()
if __name__ == "__main__":
    main()
