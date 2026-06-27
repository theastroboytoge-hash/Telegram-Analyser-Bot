import os
import asyncio
import csv
import io
import logging
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from aiohttp import web
from supabase import create_client
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, JobQueue, ContextTypes, filters
from telegram.error import TelegramError
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "https://your-app.onrender.com")
PORT = int(os.getenv("PORT", 8000))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
if not all([SUPABASE_URL, SUPABASE_KEY, BOT_TOKEN]):
    raise EnvironmentError("Missing required environment variables.")
if not RENDER_URL.startswith("https://"):
    RENDER_URL = "https://" + RENDER_URL.split("://")[-1]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
MAIN_KEYBOARD = ReplyKeyboardMarkup([
    ["📊 Analyze Post", "📈 Growth"],
    ["⏰ Best Time", "🔗 Create Link"],
    ["🖱 Link Stats", "📢 Referrals"],
    ["🏆 Top Posts", "📊 Compare Posts"],
    ["📁 Export CSV", "⚙️ Settings"]
], resize_keyboard=True)
async def supabase_execute_async(query_func):
    try:
        return await asyncio.to_thread(query_func)
    except Exception as e:
        logger.error(f"Supabase error: {e}")
        raise
async def main_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    state = context.user_data.get("state")
    if state:
        try:
            if state == "awaiting_growth_days":
                if text.isdigit():
                    days = int(text)
                else:
                    days = 7
                    await update.message.reply_text("Invalid number. Using default 7 days.")
                await show_growth(update, context, days)
            elif state == "awaiting_link_campaign":
                context.user_data["link_campaign"] = text
                context.user_data["state"] = "awaiting_link_target"
                await update.message.reply_text("Enter target URL:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
            elif state == "awaiting_link_target":
                campaign = context.user_data.pop("link_campaign", "campaign")
                target_url = text
                if not target_url.startswith(("http://", "https://")):
                    await update.message.reply_text("Invalid URL. Must start with http:// or https://")
                    context.user_data.pop("state", None)
                    return
                parsed = urlparse(target_url)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    await update.message.reply_text("Invalid URL format.")
                    context.user_data.pop("state", None)
                    return
                await create_tracked_link_internal(update, context, campaign, target_url)
            elif state == "awaiting_linkstats_campaign":
                await show_link_stats(update, context, text)
            elif state == "awaiting_post_id":
                if not text.isdigit():
                    await update.message.reply_text("Please enter a valid numeric post ID.")
                    context.user_data.pop("state", None)
                    return
                message_id = int(text)
                channel_id = context.user_data.pop("analyze_channel_id")
                await analyze_post(update, context, channel_id, message_id)
            elif state == "awaiting_compare_ids":
                parts = text.split()
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    channel_id = context.user_data.pop("compare_channel_id")
                    await compare_posts(update, context, channel_id, int(parts[0]), int(parts[1]))
                else:
                    await update.message.reply_text("Enter two post IDs separated by space.")
                    context.user_data.pop("state", None)
                    return
            elif state == "awaiting_loss_threshold":
                if not text.isdigit():
                    await update.message.reply_text("Please enter a valid number.")
                    context.user_data.pop("state", None)
                    return
                threshold = int(text)
                channel_id = context.user_data.pop("settings_channel_id")
                await supabase_execute_async(lambda: supabase.table("channels").update({"loss_alert_threshold": threshold}).eq("chat_id", str(channel_id)).execute())
                await update.message.reply_text(f"Loss alert threshold set to {threshold}.", reply_markup=MAIN_KEYBOARD)
            elif state == "awaiting_daily_report_time":
                try:
                    hour, minute = map(int, text.split(":"))
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        raise ValueError
                except:
                    await update.message.reply_text("Invalid time format. Use HH:MM (24h).")
                    context.user_data.pop("state", None)
                    return
                channel_id = context.user_data.pop("settings_channel_id")
                await supabase_execute_async(lambda: supabase.table("channels").update({"daily_report_time": f"{hour:02d}:{minute:02d}"}).eq("chat_id", str(channel_id)).execute())
                await update.message.reply_text(f"Daily report time set to {text}.", reply_markup=MAIN_KEYBOARD)
            context.user_data.pop("state", None)
        except Exception as e:
            logger.error(f"State handler error: {e}")
            await update.message.reply_text(f"Error: {e}")
            context.user_data.pop("state", None)
        return
    if text == "📊 Analyze Post":
        channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
        if not channels.data:
            await update.message.reply_text("No channels. Add bot as admin to a channel first.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"analyze_channel|{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📈 Growth":
        context.user_data["state"] = "awaiting_growth_days"
        await update.message.reply_text("Enter number of days (default 7):", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "⏰ Best Time":
        channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
        if not channels.data:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"besttime_channel|{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "🔗 Create Link":
        context.user_data["state"] = "awaiting_link_campaign"
        await update.message.reply_text("Enter campaign name:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "🖱 Link Stats":
        context.user_data["state"] = "awaiting_linkstats_campaign"
        await update.message.reply_text("Enter campaign name:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "📢 Referrals":
        channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
        if not channels.data:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"referrals_channel|{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "🏆 Top Posts":
        channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
        if not channels.data:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"top_channel|{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📊 Compare Posts":
        channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
        if not channels.data:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"compare_channel|{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📁 Export CSV":
        channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
        if not channels.data:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"export_channel|{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "⚙️ Settings":
        channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
        if not channels.data:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"settings_channel|{ch['chat_id']}")] for ch in channels.data]
        await update.message.reply_text("Select channel for settings:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("Use the menu.", reply_markup=MAIN_KEYBOARD)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Welcome! Choose an option:", reply_markup=MAIN_KEYBOARD)
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=MAIN_KEYBOARD)
async def show_growth(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int):
    user_id = update.effective_user.id
    channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
    if not channels.data:
        await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
        return
    for ch in channels.data:
        chat_id = ch["chat_id"]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        res = await supabase_execute_async(lambda: supabase.table("member_log").select("*").eq("chat_id", chat_id).gte("date", cutoff).order("date").execute())
        if len(res.data) < 2:
            await update.message.reply_text(f"{ch['title']}: Not enough data.")
            continue
        first = res.data[0]["count"]
        last = res.data[-1]["count"]
        growth = last - first
        growth_percent = (growth / first * 100) if first > 0 else 0
        report = f"📈 {ch['title']} Growth ({days}d):\n{first} → {last}\nChange: {growth:+d} ({growth_percent:+.1f}%)"
        await update.message.reply_text(report)
    await update.message.reply_text("Done.", reply_markup=MAIN_KEYBOARD)
async def create_tracked_link_internal(update, context, campaign, target_url):
    user_id = update.effective_user.id
    channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
    if not channels.data:
        await update.message.reply_text("No channels. Add bot to a channel first.", reply_markup=MAIN_KEYBOARD)
        return
    context.user_data["pending_link"] = {"campaign": campaign, "target_url": target_url}
    keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"linkchannel|{ch['chat_id']}")] for ch in channels.data]
    await update.message.reply_text("Select channel for this link:", reply_markup=InlineKeyboardMarkup(keyboard))
async def show_link_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, campaign):
    user_id = update.effective_user.id
    channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
    if not channels.data:
        await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
        return
    found = False
    for ch in channels.data:
        res = await supabase_execute_async(lambda: supabase.table("link_clicks").select("*").eq("campaign", campaign).eq("chat_id", ch["chat_id"]).execute())
        if res.data:
            total_clicks = sum(r["clicks"] for r in res.data)
            await update.message.reply_text(f"{ch['title']}: Campaign {campaign} - {total_clicks} clicks")
            found = True
    if not found:
        await update.message.reply_text(f"No data for campaign '{campaign}'.", reply_markup=MAIN_KEYBOARD)
    else:
        await update.message.reply_text("Done.", reply_markup=MAIN_KEYBOARD)
async def analyze_post(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id, message_id):
    try:
        res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).eq("message_id", message_id).execute())
        if not res.data:
            await update.message.reply_text("Post not found in analytics.", reply_markup=MAIN_KEYBOARD)
            return
        post = res.data[0]
        views = post.get("views", 0)
        forwards = post.get("forwards", 0)
        engagement = post.get("engagement_rate", 0)
        members = post.get("members_at_time", 0)
        change = post.get("member_change_after", 0)
        report = (f"📊 Post Analysis #{message_id}\n👁 Views: {views}\n🔄 Forwards: {forwards}\n"
                  f"📈 Engagement: {engagement:.1f}%\n👥 Members: {members}\n📉 Change: {change:+d}")
        await update.message.reply_text(report, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        logger.error(f"Analyze post error: {e}")
        await update.message.reply_text(f"Error: {e}", reply_markup=MAIN_KEYBOARD)
async def best_time_report(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("timestamp, views").eq("chat_id", channel_id).gte("timestamp", cutoff).limit(500).execute())
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
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = await supabase_execute_async(lambda: supabase.table("referrals").select("from_chat_title").eq("channel_id", channel_id).gte("date", cutoff).limit(500).execute())
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
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).gte("timestamp", cutoff).limit(200).execute())
    if not res.data:
        await update.callback_query.edit_message_text("No data.")
        return
    scored = [(p["views"] * 1 + p["forwards"] * 5, p) for p in res.data]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:5]
    report = "🏆 Top Posts:\n"
    for score, post in top:
        report += f"#{post['message_id']} - Views:{post['views']}, Fwd:{post['forwards']} (Score:{score})\n"
    await update.callback_query.edit_message_text(report)
async def compare_posts(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id, id1, id2):
    try:
        p1 = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).eq("message_id", id1).execute())
        p2 = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).eq("message_id", id2).execute())
        if not p1.data or not p2.data:
            await update.message.reply_text("One or both posts not found.", reply_markup=MAIN_KEYBOARD)
            return
        p1, p2 = p1.data[0], p2.data[0]
        report = f"📊 Comparison:\n#{id1}: {p1['views']} views, {p1['forwards']} fwd\n#{id2}: {p2['views']} views, {p2['forwards']} fwd"
        await update.message.reply_text(report, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        logger.error(f"Compare error: {e}")
        await update.message.reply_text(f"Error comparing: {e}", reply_markup=MAIN_KEYBOARD)
async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).gte("timestamp", cutoff).order("timestamp").limit(1000).execute())
    if not res.data:
        await update.callback_query.edit_message_text("No data.")
        return
    output = io.StringIO()
    try:
        writer = csv.writer(output)
        writer.writerow(["message_id", "views", "forwards", "engagement_rate", "timestamp"])
        for row in res.data:
            writer.writerow([row["message_id"], row["views"], row["forwards"], row.get("engagement_rate", ""), row["timestamp"]])
        csv_content = output.getvalue()
        await context.bot.send_document(chat_id=update.effective_chat.id, document=io.BytesIO(csv_content.encode()), filename="analytics.csv")
        await update.callback_query.edit_message_text("CSV exported.")
    finally:
        output.close()
async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    keyboard = [
        [InlineKeyboardButton("Set Loss Alert Threshold", callback_data=f"setloss|{channel_id}")],
        [InlineKeyboardButton("Set Daily Report Time", callback_data=f"setreport|{channel_id}")],
    ]
    await update.callback_query.edit_message_text("Settings:", reply_markup=InlineKeyboardMarkup(keyboard))
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    async def owner_check(channel_id):
        try:
            ch_res = await supabase_execute_async(lambda: supabase.table("channels").select("owner_id").eq("chat_id", str(channel_id)).maybe_single().execute())
            if not ch_res.data or ch_res.data.get("owner_id") != user_id:
                await query.edit_message_text("Access denied.")
                return False
            return True
        except:
            await query.edit_message_text("Access denied.")
            return False
    try:
        if data.startswith("analyze_channel|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            context.user_data["state"] = "awaiting_post_id"
            context.user_data["analyze_channel_id"] = channel_id
            await query.edit_message_text("Send post ID:")
        elif data.startswith("besttime_channel|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            await best_time_report(update, context, channel_id)
        elif data.startswith("referrals_channel|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            await referral_report(update, context, channel_id)
        elif data.startswith("top_channel|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            await top_posts_report(update, context, channel_id)
        elif data.startswith("compare_channel|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            context.user_data["state"] = "awaiting_compare_ids"
            context.user_data["compare_channel_id"] = channel_id
            await query.edit_message_text("Enter two post IDs separated by space:")
        elif data.startswith("export_channel|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            await export_csv(update, context, channel_id)
        elif data.startswith("settings_channel|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            await settings_menu(update, context, channel_id)
        elif data.startswith("setloss|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            context.user_data["state"] = "awaiting_loss_threshold"
            context.user_data["settings_channel_id"] = channel_id
            await query.edit_message_text("Enter loss alert threshold (number):")
        elif data.startswith("setreport|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            context.user_data["state"] = "awaiting_daily_report_time"
            context.user_data["settings_channel_id"] = channel_id
            await query.edit_message_text("Enter time (HH:MM, 24h):")
        elif data.startswith("linkchannel|"):
            channel_id = data.split("|")[1]
            if not await owner_check(channel_id):
                return
            pending = context.user_data.pop("pending_link", {})
            campaign = pending.get("campaign")
            target_url = pending.get("target_url")
            if not campaign or not target_url:
                await query.edit_message_text("Link creation data lost. Please try again.")
                return
            unique_id = uuid.uuid4().hex
            tracked_url = f"{RENDER_URL}/click/{unique_id}"
            await supabase_execute_async(lambda: supabase.table("link_clicks").insert({
                "unique_id": unique_id,
                "campaign": campaign,
                "target_url": target_url,
                "chat_id": channel_id,
                "clicks": 0,
                "created_at": datetime.now(timezone.utc).isoformat()
            }).execute())
            await query.edit_message_text(f"Link created:\n{tracked_url}")
        else:
            await query.edit_message_text("Unknown option.")
    except Exception as e:
        logger.error(f"Callback error: {e}")
        try:
            await query.edit_message_text("An error occurred. Please try again.")
        except:
            pass
async def post_engagement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    chat_id = str(update.effective_chat.id)
    channel_exists = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id").eq("chat_id", chat_id).execute())
    if not channel_exists.data:
        return
    msg_id = update.channel_post.message_id
    views = update.channel_post.views or 0
    forwards = update.channel_post.forwards or 0
    current_count_res = await supabase_execute_async(lambda: supabase.table("channels").select("member_count").eq("chat_id", chat_id).execute())
    old_count = current_count_res.data[0]["member_count"] if current_count_res.data else 0
    try:
        new_count = await context.bot.get_chat_member_count(chat_id)
    except TelegramError as e:
        logger.error(f"Failed to get member count for {chat_id}: {e}")
        new_count = old_count
    total_members = new_count
    engagement_rate = (views / total_members * 100) if total_members > 0 else 0
    member_change = new_count - old_count
    await supabase_execute_async(lambda: supabase.table("post_analytics").upsert({
        "message_id": msg_id,
        "chat_id": chat_id,
        "views": views,
        "forwards": forwards,
        "engagement_rate": round(engagement_rate, 2),
        "members_at_time": total_members,
        "member_change_after": member_change,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }, on_conflict="message_id,chat_id").execute())
    await supabase_execute_async(lambda: supabase.table("channels").upsert({"chat_id": chat_id, "member_count": new_count}, on_conflict="chat_id").execute())
    await supabase_execute_async(lambda: supabase.table("member_log").insert({"chat_id": chat_id, "count": new_count, "date": datetime.now(timezone.utc).isoformat()}).execute())
    if old_count > 0:
        threshold_res = await supabase_execute_async(lambda: supabase.table("channels").select("loss_alert_threshold").eq("chat_id", chat_id).execute())
        threshold = threshold_res.data[0].get("loss_alert_threshold", 10) if threshold_res.data else 10
        if (old_count - new_count) >= threshold:
            owner_res = await supabase_execute_async(lambda: supabase.table("channels").select("owner_id").eq("chat_id", chat_id).execute())
            if owner_res.data:
                owner_id = owner_res.data[0]["owner_id"]
                try:
                    await context.bot.send_message(owner_id, f"⚠️ Channel lost {old_count - new_count} members after post {msg_id}.")
                except TelegramError as e:
                    logger.error(f"Failed to send alert: {e}")
async def track_referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.forward_from_chat:
        fwd_chat = update.message.forward_from_chat
        await supabase_execute_async(lambda: supabase.table("referrals").insert({
            "channel_id": str(update.effective_chat.id),
            "from_chat_id": str(fwd_chat.id),
            "from_chat_title": fwd_chat.title or "Unknown",
            "message_id": update.message.message_id,
            "date": datetime.now(timezone.utc).isoformat()
        }).execute())
async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.my_chat_member:
        new_status = update.my_chat_member.new_chat_member.status
        chat = update.effective_chat
        if chat.type == "channel":
            if new_status in ("administrator", "member"):
                existing = await supabase_execute_async(lambda: supabase.table("channels").select("owner_id").eq("chat_id", str(chat.id)).execute())
                if not existing.data:
                    await supabase_execute_async(lambda: supabase.table("channels").insert({
                        "chat_id": str(chat.id),
                        "title": chat.title,
                        "owner_id": update.effective_user.id,
                        "member_count": 0,
                        "loss_alert_threshold": 10,
                        "daily_report_time": "08:00"
                    }).execute())
                else:
                    await supabase_execute_async(lambda: supabase.table("channels").update({"title": chat.title}).eq("chat_id", str(chat.id)).execute())
            elif new_status in ("left", "kicked"):
                await supabase_execute_async(lambda: supabase.table("channels").delete().eq("chat_id", str(chat.id)).execute())
async def log_member_counts_job(context: ContextTypes.DEFAULT_TYPE):
    channels_res = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id").execute())
    for ch in channels_res.data:
        chat_id = ch["chat_id"]
        try:
            new_count = await context.bot.get_chat_member_count(chat_id)
            await supabase_execute_async(lambda: supabase.table("channels").update({"member_count": new_count}).eq("chat_id", chat_id).execute())
        except Exception as e:
            logger.error(f"Failed to log member count for {chat_id}: {e}")
async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone.utc).strftime("%H:%M")
    channels_res = await supabase_execute_async(lambda: supabase.table("channels").select("*").execute())
    sent_key = "daily_report_sent_today"
    if sent_key not in context.bot_data:
        context.bot_data[sent_key] = {}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ch in channels_res.data:
        if ch.get("daily_report_time", "08:00") == now:
            channel_key = f"{ch['chat_id']}_{today_str}"
            if channel_key in context.bot_data[sent_key].get(today_str, set()):
                continue
            context.bot_data[sent_key].setdefault(today_str, set()).add(channel_key)
            owner_id = ch["owner_id"]
            cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            res = await supabase_execute_async(lambda: supabase.table("member_log").select("*").eq("chat_id", ch["chat_id"]).gte("date", cutoff).order("date").execute())
            if len(res.data) >= 2:
                first = res.data[0]["count"]
                last = res.data[-1]["count"]
                growth = last - first
                report = f"📈 Daily Report {ch['title']}:\nMembers: {first} → {last} ({growth:+d})"
                try:
                    await context.bot.send_message(owner_id, report)
                except TelegramError as e:
                    logger.error(f"Daily report send failed: {e}")
async def clear_old_report_keys(context: ContextTypes.DEFAULT_TYPE):
    sent_key = "daily_report_sent_today"
    if sent_key in context.bot_data:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        context.bot_data[sent_key].pop(yesterday, None)
async def click_handler(request):
    unique_id = request.match_info.get("unique_id")
    res = await supabase_execute_async(lambda: supabase.table("link_clicks").select("*").eq("unique_id", unique_id).execute())
    if not res.data:
        return web.Response(text="Link not found", status=404)
    link = res.data[0]
    target_url = link["target_url"]
    success = False
    for attempt in range(3):
        try:
            await supabase_execute_async(lambda: supabase.rpc("increment_link_click", {"uid": unique_id}).execute())
            success = True
            break
        except Exception as e:
            logger.warning(f"RPC attempt {attempt+1} failed: {e}")
            try:
                current = link["clicks"]
                update_res = await supabase_execute_async(lambda: supabase.table("link_clicks").update({"clicks": current + 1}).eq("unique_id", unique_id).eq("clicks", current).execute())
                if update_res.data and len(update_res.data) > 0:
                    success = True
                    break
            except:
                pass
            await asyncio.sleep(0.1)
    if not success:
        logger.error(f"Failed to increment click for {unique_id}")
    raise web.HTTPFound(target_url)
async def health_check(request):
    return web.Response(text="OK")
async def webhook_handler(request, application: Application):
    if WEBHOOK_SECRET:
        header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header_token != WEBHOOK_SECRET:
            return web.Response(status=403)
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=500)
    return web.Response()
async def main():
    application = Application.builder().token(BOT_TOKEN).job_queue(JobQueue()).updater(None).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.FORWARDED, track_referral_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, main_dispatcher))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, post_engagement_handler))
    application.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))
    app = web.Application()
    app.router.add_get("/healthz", health_check)
    app.router.add_get("/click/{unique_id}", click_handler)
    app.router.add_post("/webhook", lambda request: webhook_handler(request, application))
    await application.initialize()
    await application.start()
    application.job_queue.run_repeating(log_member_counts_job, interval=21600, first=60)
    application.job_queue.run_repeating(daily_report_job, interval=60, first=1)
    application.job_queue.run_repeating(clear_old_report_keys, interval=86400, first=3600)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    webhook_url = f"{RENDER_URL}/webhook"
    try:
        await application.bot.set_webhook(url=webhook_url, secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None)
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
    await asyncio.Event().wait()
    await application.stop()
    await runner.cleanup()
if __name__ == "__main__":
    asyncio.run(main())
