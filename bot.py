import os
import hashlib
import asyncio
import csv
import io
from datetime import datetime, timedelta, time
from aiohttp import web
from supabase import create_client
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "https://your-app.onrender.com")
PORT = int(os.getenv("PORT", 8000))
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
MAIN_KEYBOARD = ReplyKeyboardMarkup([
    ["📊 Analyze Post", "📈 Growth"],
    ["⏰ Best Time", "🔗 Create Link"],
    ["🖱 Link Stats", "📢 Referrals"],
    ["🏆 Top Posts", "📊 Compare Posts"],
    ["📁 Export CSV", "⚙️ Settings"]
], resize_keyboard=True)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Welcome! Choose an option:", reply_markup=MAIN_KEYBOARD)
async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    if text == "📊 Analyze Post":
        channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
        if not channels.data:
            await update.message.reply_text("No channels. Add bot as admin to a channel first.")
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"analyze_channel_{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📈 Growth":
        context.user_data["state"] = "awaiting_growth_days"
        await update.message.reply_text("Enter number of days (default 7):", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "⏰ Best Time":
        channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
        if not channels.data:
            await update.message.reply_text("No channels.")
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"besttime_channel_{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "🔗 Create Link":
        context.user_data["state"] = "awaiting_link_campaign"
        await update.message.reply_text("Enter campaign name:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "🖱 Link Stats":
        context.user_data["state"] = "awaiting_linkstats_campaign"
        await update.message.reply_text("Enter campaign name:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "📢 Referrals":
        channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
        if not channels.data:
            await update.message.reply_text("No channels.")
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"referrals_channel_{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "🏆 Top Posts":
        channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
        if not channels.data:
            await update.message.reply_text("No channels.")
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"top_channel_{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📊 Compare Posts":
        channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
        if not channels.data:
            await update.message.reply_text("No channels.")
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"compare_channel_{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📁 Export CSV":
        channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
        if not channels.data:
            await update.message.reply_text("No channels.")
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"export_channel_{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "⚙️ Settings":
        channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
        if not channels.data:
            await update.message.reply_text("No channels.")
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"settings_channel_{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel for settings:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("Use the menu.", reply_markup=MAIN_KEYBOARD)
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=MAIN_KEYBOARD)
async def handle_state_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if not state:
        return
    if state == "awaiting_growth_days":
        try:
            days = int(update.message.text) if update.message.text.isdigit() else 7
        except:
            days = 7
        await show_growth(update, context, days)
    elif state == "awaiting_link_campaign":
        context.user_data["link_campaign"] = update.message.text
        context.user_data["state"] = "awaiting_link_target"
        await update.message.reply_text("Enter target URL:")
    elif state == "awaiting_link_target":
        campaign = context.user_data.pop("link_campaign", "campaign")
        target_url = update.message.text
        await create_tracked_link_internal(update, context, campaign, target_url)
    elif state == "awaiting_linkstats_campaign":
        campaign = update.message.text
        await show_link_stats(update, context, campaign)
    elif state == "awaiting_loss_threshold":
        try:
            threshold = int(update.message.text)
            channel_id = context.user_data.pop("settings_channel_id")
            supabase.table("channels").update({"loss_alert_threshold": threshold}).eq("chat_id", str(channel_id)).execute()
            await update.message.reply_text(f"Loss alert threshold set to {threshold}.", reply_markup=MAIN_KEYBOARD)
        except:
            await update.message.reply_text("Invalid number.")
    elif state == "awaiting_daily_report_time":
        try:
            report_time = update.message.text
            hour, minute = map(int, report_time.split(":"))
            channel_id = context.user_data.pop("settings_channel_id")
            supabase.table("channels").update({"daily_report_time": f"{hour:02d}:{minute:02d}"}).eq("chat_id", str(channel_id)).execute()
            await update.message.reply_text(f"Daily report time set to {report_time}.", reply_markup=MAIN_KEYBOARD)
        except:
            await update.message.reply_text("Invalid format. Use HH:MM (24h).")
    elif state == "awaiting_compare_ids":
        parts = update.message.text.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            channel_id = context.user_data.pop("compare_channel_id")
            await compare_posts(update, context, channel_id, int(parts[0]), int(parts[1]))
        else:
            await update.message.reply_text("Enter two post IDs separated by space.")
    context.user_data.pop("state", None)
async def show_growth(update: Update, context: ContextTypes.DEFAULT_TYPE, days=7):
    user_id = update.effective_user.id
    channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
    if not channels.data:
        await update.message.reply_text("No channels.")
        return
    for ch in channels.data:
        chat_id = ch["chat_id"]
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        res = supabase.table("member_log").select("*").eq("chat_id", chat_id).gte("date", cutoff).order("date").execute()
        if len(res.data) < 2:
            await update.message.reply_text(f"{ch['title']}: Not enough data.")
            continue
        first = res.data[0]["count"]
        last = res.data[-1]["count"]
        growth = last - first
        growth_percent = (growth / first * 100) if first > 0 else 0
        report = f"📈 {ch['title']} Growth ({days}d):\n{first} → {last}\nChange: {growth:+d} ({growth_percent:+.1f}%)"
        await update.message.reply_text(report)
async def create_tracked_link_internal(update: Update, context: ContextTypes.DEFAULT_TYPE, campaign, target_url):
    user_id = update.effective_user.id
    channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
    if not channels.data:
        await update.message.reply_text("No channels. Add bot to a channel first.")
        return
    keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"linkchannel_{ch['chat_id']}_{campaign}_{target_url}")] for ch in channels.data]
    await update.message.reply_text("Select channel for this link:", reply_markup=InlineKeyboardMarkup(keyboard))
async def show_link_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, campaign):
    user_id = update.effective_user.id
    channels = supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute()
    if not channels.data:
        await update.message.reply_text("No channels.")
        return
    for ch in channels.data:
        res = supabase.table("link_clicks").select("*").eq("campaign", campaign).eq("chat_id", ch["chat_id"]).execute()
        if res.data:
            total_clicks = sum(r["clicks"] for r in res.data)
            await update.message.reply_text(f"{ch['title']}: Campaign {campaign} - {total_clicks} clicks")
    await update.message.reply_text("Done.", reply_markup=MAIN_KEYBOARD)
async def post_engagement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    current_count = supabase.table("channels").select("member_count").eq("chat_id", chat_id).execute()
    if current_count.data:
        old_count = current_count.data[0]["member_count"]
    else:
        old_count = 0
    new_count = await context.bot.get_chat_member_count(chat_id)
    supabase.table("channels").upsert({"chat_id": chat_id, "member_count": new_count}, on_conflict="chat_id").execute()
    if old_count > 0:
        threshold_data = supabase.table("channels").select("loss_alert_threshold").eq("chat_id", chat_id).execute()
        threshold = threshold_data.data[0].get("loss_alert_threshold", 10) if threshold_data.data else 10
        if (old_count - new_count) >= threshold:
            owner_id = supabase.table("channels").select("owner_id").eq("chat_id", chat_id).execute().data[0]["owner_id"]
            await context.bot.send_message(owner_id, f"⚠️ Channel lost {old_count - new_count} members after post {msg_id}.")
    supabase.table("member_log").insert({"chat_id": chat_id, "count": new_count, "date": datetime.utcnow().isoformat()}).execute()
async def track_referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.forward_from_chat:
        fwd_chat = update.message.forward_from_chat
        supabase.table("referrals").insert({
            "channel_id": str(update.effective_chat.id),
            "from_chat_id": str(fwd_chat.id),
            "from_chat_title": fwd_chat.title or "Unknown",
            "message_id": update.message.message_id,
            "date": datetime.utcnow().isoformat()
        }).execute()
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("analyze_channel_"):
        channel_id = data.split("_")[2]
        context.user_data["state"] = "awaiting_post_id"
        context.user_data["analyze_channel_id"] = channel_id
        await query.edit_message_text("Send post ID:")
    elif data.startswith("besttime_channel_"):
        channel_id = data.split("_")[2]
        await best_time_report(update, context, channel_id)
    elif data.startswith("referrals_channel_"):
        channel_id = data.split("_")[2]
        await referral_report(update, context, channel_id)
    elif data.startswith("top_channel_"):
        channel_id = data.split("_")[2]
        await top_posts_report(update, context, channel_id)
    elif data.startswith("compare_channel_"):
        channel_id = data.split("_")[2]
        context.user_data["state"] = "awaiting_compare_ids"
        context.user_data["compare_channel_id"] = channel_id
        await query.edit_message_text("Enter two post IDs separated by space:")
    elif data.startswith("export_channel_"):
        channel_id = data.split("_")[2]
        await export_csv(update, context, channel_id)
    elif data.startswith("settings_channel_"):
        channel_id = data.split("_")[2]
        await settings_menu(update, context, channel_id)
    elif data.startswith("setloss_"):
        channel_id = data.split("_")[1]
        context.user_data["state"] = "awaiting_loss_threshold"
        context.user_data["settings_channel_id"] = channel_id
        await query.edit_message_text("Enter loss alert threshold (number):")
    elif data.startswith("setreport_"):
        channel_id = data.split("_")[1]
        context.user_data["state"] = "awaiting_daily_report_time"
        context.user_data["settings_channel_id"] = channel_id
        await query.edit_message_text("Enter time (HH:MM, 24h):")
    elif data.startswith("linkchannel_"):
        parts = data.split("_", 3)
        channel_id = parts[1]
        campaign = parts[2]
        target_url = parts[3]
        unique_id = hashlib.md5(f"{channel_id}{campaign}{datetime.utcnow()}".encode()).hexdigest()[:8]
        tracked_url = f"{RENDER_URL}/click/{unique_id}"
        supabase.table("link_clicks").insert({
            "unique_id": unique_id,
            "campaign": campaign,
            "target_url": target_url,
            "chat_id": channel_id,
            "clicks": 0,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        await query.edit_message_text(f"Link created:\n{tracked_url}")
    elif data.startswith("track_"):
        campaign = data.replace("track_", "")
        chat_id = str(query.message.chat.id)
        res = supabase.table("link_clicks").select("*").eq("campaign", campaign).eq("chat_id", chat_id).execute()
        if res.data:
            supabase.table("link_clicks").update({"clicks": res.data[0]["clicks"] + 1}).eq("campaign", campaign).eq("chat_id", chat_id).execute()
            await query.edit_message_text(f"Campaign {campaign} link:\n{res.data[0]['target_url']}\n✅ Click recorded.")
async def handle_post_id_for_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") == "awaiting_post_id":
        message_id = int(update.message.text)
        channel_id = context.user_data.pop("analyze_channel_id")
        await analyze_post(update, context, channel_id, message_id)
        context.user_data.pop("state", None)
async def analyze_post(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id, message_id):
    try:
        post = await context.bot.get_message(channel_id, message_id)
        views = post.views or 0
        forwards = post.forwards or 0
        total_members = await context.bot.get_chat_member_count(channel_id)
        engagement_rate = (views / total_members) * 100 if total_members > 0 else 0
        supabase.table("member_log").insert({"chat_id": channel_id, "count": total_members, "date": datetime.utcnow().isoformat()}).execute()
        prev = supabase.table("member_log").select("*").eq("chat_id", channel_id).order("date", desc=True).limit(2).execute()
        member_change = 0
        if len(prev.data) >= 2:
            member_change = prev.data[0]["count"] - prev.data[1]["count"]
        supabase.table("post_analytics").upsert({
            "message_id": message_id,
            "chat_id": channel_id,
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
            f"📈 Engagement: {engagement_rate:.1f}%\n"
            f"👥 Members: {total_members}\n"
            f"📉 Change: {member_change:+d}"
        )
        await update.message.reply_text(report, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
async def best_time_report(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    res = supabase.table("post_analytics").select("timestamp, views").eq("chat_id", channel_id).execute()
    if not res.data:
        await update.callback_query.edit_message_text("Not enough data.")
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
        await update.callback_query.edit_message_text("No data.")
        return
    sorted_hours = sorted(hour_map.items(), key=lambda x: x[1]["total_views"]/x[1]["count"], reverse=True)[:5]
    report = "⏰ Best Times:\n"
    for hour, data in sorted_hours:
        avg = data["total_views"] / data["count"]
        report += f"{hour}:00 - Avg {avg:.0f} views ({data['count']} posts)\n"
    await update.callback_query.edit_message_text(report)
async def referral_report(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    res = supabase.table("referrals").select("from_chat_title").eq("channel_id", channel_id).execute()
    if not res.data:
        await update.callback_query.edit_message_text("No referrals.")
        return
    counts = {}
    for row in res.data:
        title = row["from_chat_title"]
        counts[title] = counts.get(title, 0) + 1
    sorted_titles = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    report = "📢 Top Forwarders:\n"
    for title, count in sorted_titles:
        report += f"{title}: {count}\n"
    await update.callback_query.edit_message_text(report)
async def top_posts_report(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    res = supabase.table("post_analytics").select("*").eq("chat_id", channel_id).execute()
    if not res.data:
        await update.callback_query.edit_message_text("No data.")
        return
    scored = []
    for post in res.data:
        score = post["views"] * 1 + post["forwards"] * 5
        scored.append((score, post))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:5]
    report = "🏆 Top Posts:\n"
    for score, post in top:
        report += f"#{post['message_id']} - Views:{post['views']}, Fwd:{post['forwards']} (Score:{score})\n"
    await update.callback_query.edit_message_text(report)
async def compare_posts(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id, id1, id2):
    try:
        p1 = supabase.table("post_analytics").select("*").eq("chat_id", channel_id).eq("message_id", id1).single().execute()
        p2 = supabase.table("post_analytics").select("*").eq("chat_id", channel_id).eq("message_id", id2).single().execute()
        if not p1.data or not p2.data:
            await update.message.reply_text("One or both posts not found.")
            return
        p1 = p1.data
        p2 = p2.data
        report = f"📊 Comparison:\n#{id1}: {p1['views']} views, {p1['forwards']} fwd\n#{id2}: {p2['views']} views, {p2['forwards']} fwd"
        await update.message.reply_text(report, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        await update.message.reply_text(f"Error comparing: {e}")
async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    res = supabase.table("post_analytics").select("*").eq("chat_id", channel_id).order("timestamp").execute()
    if not res.data:
        await update.callback_query.edit_message_text("No data.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["message_id", "views", "forwards", "engagement_rate", "timestamp"])
    for row in res.data:
        writer.writerow([row["message_id"], row["views"], row["forwards"], row.get("engagement_rate", ""), row["timestamp"]])
    csv_content = output.getvalue()
    await context.bot.send_document(chat_id=update.effective_chat.id, document=io.BytesIO(csv_content.encode()), filename="analytics.csv")
    await update.callback_query.edit_message_text("CSV exported.")
async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    keyboard = [
        [InlineKeyboardButton("Set Loss Alert Threshold", callback_data=f"setloss_{channel_id}")],
        [InlineKeyboardButton("Set Daily Report Time", callback_data=f"setreport_{channel_id}")],
    ]
    await update.callback_query.edit_message_text("Settings:", reply_markup=InlineKeyboardMarkup(keyboard))
async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.my_chat_member and update.my_chat_member.new_chat_member.status == "administrator":
        chat = update.effective_chat
        if chat.type == "channel":
            owner_id = update.effective_user.id
            supabase.table("channels").upsert({
                "chat_id": str(chat.id),
                "title": chat.title,
                "owner_id": owner_id,
                "member_count": 0,
                "loss_alert_threshold": 10,
                "daily_report_time": "08:00"
            }, on_conflict="chat_id").execute()
async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    channels = supabase.table("channels").select("*").execute()
    for ch in channels.data:
        chat_id = ch["chat_id"]
        owner_id = ch["owner_id"]
        report_time_str = ch.get("daily_report_time", "08:00")
        now = datetime.utcnow().strftime("%H:%M")
        if report_time_str == now:
            cutoff = (datetime.utcnow() - timedelta(days=1)).isoformat()
            res = supabase.table("member_log").select("*").eq("chat_id", chat_id).gte("date", cutoff).order("date").execute()
            if len(res.data) >= 2:
                first = res.data[0]["count"]
                last = res.data[-1]["count"]
                growth = last - first
                report = f"📈 Daily Report {ch['title']}:\nMembers: {first} → {last} ({growth:+d})"
                try:
                    await context.bot.send_message(owner_id, report)
                except:
                    pass
async def health_check(request):
    return web.Response(text="OK")
async def main():
    app = web.Application()
    app.router.add_get("/healthz", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^\d'), handle_post_id_for_analysis))
    application.add_handler(MessageHandler(filters.TEXT, handle_state_text))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, post_engagement_handler))
    application.add_handler(MessageHandler(filters.FORWARDED, track_referral_handler))
    application.add_handler(MessageHandler(filters.StatusUpdate.MY_CHAT_MEMBER, on_chat_member_update))
    application.job_queue.run_repeating(daily_report_job, interval=60, first=10)
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await asyncio.Event().wait()
if __name__ == "__main__":
    asyncio.run(main())
