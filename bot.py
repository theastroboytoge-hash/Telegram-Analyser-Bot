import os
from datetime import datetime, timedelta
import hashlib
from aiohttp import web
from supabase import create_client
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "https://your-app.onrender.com")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Analytics bot is active.")
async def analyze_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please enter post ID.\nExample: /analyze 12345")
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
            f"📊 Post Analysis #{message_id}\n"
            f"👁 Views: {views}\n"
            f"🔄 Forwards: {forwards}\n"
            f"📈 Engagement Rate: {engagement_rate:.1f}%\n"
            f"👥 Members at Post Time: {total_members}\n"
            f"📉 Member Change After Post: {member_change:+d}\n"
        )
        await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
async def create_tracked_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /tracklink <campaign_name> <target_url>\nExample: /tracklink offer1 https://example.com")
        return
    campaign = context.args[0]
    target_url = context.args[1]
    chat_id = update.effective_chat.id
    unique_id = hashlib.md5(f"{chat_id}{campaign}{datetime.utcnow()}".encode()).hexdigest()[:8]
    tracked_url = f"{RENDER_URL}/click/{unique_id}"
    supabase.table("link_clicks").insert({
        "unique_id": unique_id,
        "campaign": campaign,
        "target_url": target_url,
        "chat_id": str(chat_id),
        "clicks": 0,
        "created_at": datetime.utcnow().isoformat()
    }).execute()
    await update.message.reply_text(
        f"🔗 Tracked link created:\n{tracked_url}\n\n"
        f"Campaign: {campaign}\n"
        f"Target: {target_url}\n\n"
        f"Use this link in your posts."
    )
async def link_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /linkstats <campaign_name>")
        return
    campaign = context.args[0]
    chat_id = update.effective_chat.id
    res = supabase.table("link_clicks").select("*").eq("campaign", campaign).eq("chat_id", str(chat_id)).execute()
    if not res.data:
        await update.message.reply_text("Campaign not found.")
        return
    total_clicks = sum(r["clicks"] for r in res.data)
    await update.message.reply_text(f"📊 Campaign {campaign}:\n🖱 Total Clicks: {total_clicks}")
async def best_time_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    res = supabase.table("post_analytics").select("timestamp, views").eq("chat_id", str(chat_id)).execute()
    if not res.data:
        await update.message.reply_text("Not enough data yet.")
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
        await update.message.reply_text("Not enough data.")
        return
    sorted_hours = sorted(hour_map.items(), key=lambda x: x[1]["total_views"]/x[1]["count"], reverse=True)[:5]
    report = "⏰ Best Posting Times (by average views):\n\n"
    for hour, data in sorted_hours:
        avg = data["total_views"] / data["count"]
        report += f"🕐 {hour}:00 - Avg {avg:.0f} views ({data['count']} posts)\n"
    await update.message.reply_text(report)
async def channel_growth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    days = 7
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    res = supabase.table("member_log").select("*").eq("chat_id", str(chat_id)).gte("date", cutoff).order("date").execute()
    if len(res.data) < 2:
        await update.message.reply_text("Not enough data for growth analysis.")
        return
    first = res.data[0]["count"]
    last = res.data[-1]["count"]
    growth = last - first
    growth_percent = (growth / first * 100) if first > 0 else 0
    report = (
        f"📈 Channel Growth ({days} days):\n"
        f"👥 Members: {first} → {last}\n"
        f"📊 Change: {growth:+d} ({growth_percent:+.1f}%)\n"
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
            await query.edit_message_text(f"Campaign {campaign} link:\n{res.data[0]['target_url']}\n\n✅ Click recorded.")
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
        await update.message.reply_text("No forward data recorded yet.")
        return
    counts = {}
    for row in res.data:
        title = row["from_chat_title"]
        counts[title] = counts.get(title, 0) + 1
    sorted_titles = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    report = "📢 Top Forwarding Channels:\n\n"
    for title, count in sorted_titles:
        report += f"📌 {title}: {count} forwards\n"
    await update.message.reply_text(report)
async def health_check(request):
    return web.Response(text="OK")
async def main():
    app = web.Application()
    app.router.add_get("/healthz", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()
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
    webhook_url = f"{RENDER_URL}/webhook"
    await application.bot.set_webhook(url=webhook_url)
    await application.run_polling()
if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
