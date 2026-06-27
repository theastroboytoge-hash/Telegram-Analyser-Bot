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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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
MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 Analyze Post", "📈 Growth"],["⏰ Best Time", "🔗 Create Link"],["🖱 Link Stats", "📢 Referrals"],["🏆 Top Posts", "📊 Compare Posts"],["📁 Export CSV", "⚙️ Settings"]], resize_keyboard=True)
async def supabase_execute_async(query_func):
    try:
        return await asyncio.to_thread(query_func)
    except Exception as e:
        logger.error(f"Supabase error: {e}")
        raise
async def get_user_channels(user_id):
    res = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id,title").eq("owner_id", user_id).execute())
    return res.data or []
async def is_channel_owner(user_id, channel_id):
    res = await supabase_execute_async(lambda: supabase.table("channels").select("owner_id").eq("chat_id", str(channel_id)).maybe_single().execute())
    return res.data and res.data.get("owner_id") == user_id
async def main_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    state = context.user_data.get("state")
    if state:
        try:
            if state == "awaiting_growth_days":
                days = int(text) if text.isdigit() else 7
                await show_growth(update, context, days)
            elif state == "awaiting_link_campaign":
                context.user_data["link_campaign"] = text
                context.user_data["state"] = "awaiting_link_target"
                await update.message.reply_text("Enter target URL:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
            elif state == "awaiting_link_target":
                campaign = context.user_data.pop("link_campaign", "default")
                target_url = text.strip()
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
        except Exception as e:
            logger.error(f"State handler error: {e}")
            await update.message.reply_text("An error occurred.")
        finally:
            context.user_data.pop("state", None)
        return
    if text == "📊 Analyze Post":
        channels = await get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("No channels. Add bot as admin to a channel first.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"analyze_channel|{ch['chat_id']}")] for ch in channels]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📈 Growth":
        context.user_data["state"] = "awaiting_growth_days"
        await update.message.reply_text("Enter number of days (default 7):", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "⏰ Best Time":
        channels = await get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"besttime_channel|{ch['chat_id']}")] for ch in channels]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "🔗 Create Link":
        context.user_data["state"] = "awaiting_link_campaign"
        await update.message.reply_text("Enter campaign name:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "🖱 Link Stats":
        context.user_data["state"] = "awaiting_linkstats_campaign"
        await update.message.reply_text("Enter campaign name:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    elif text == "📢 Referrals":
        channels = await get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"referrals_channel|{ch['chat_id']}")] for ch in channels]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "🏆 Top Posts":
        channels = await get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"top_channel|{ch['chat_id']}")] for ch in channels]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📊 Compare Posts":
        channels = await get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"compare_channel|{ch['chat_id']}")] for ch in channels]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "📁 Export CSV":
        channels = await get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"export_channel|{ch['chat_id']}")] for ch in channels]
        await update.message.reply_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif text == "⚙️ Settings":
        channels = await get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
            return
        keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"settings_channel|{ch['chat_id']}")] for ch in channels]
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
    channels = await get_user_channels(user_id)
    if not channels:
        await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
        return
    for ch in channels:
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
    channels = await get_user_channels(user_id)
    if not channels:
        await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
        return
    context.user_data["pending_link"] = {"campaign": campaign, "target_url": target_url}
    keyboard = [[InlineKeyboardButton(ch["title"], callback_data=f"linkchannel|{ch['chat_id']}")] for ch in channels]
    await update.message.reply_text("Select channel for this link:", reply_markup=InlineKeyboardMarkup(keyboard))
async def show_link_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, campaign):
    user_id = update.effective_user.id
    channels = await get_user_channels(user_id)
    if not channels:
        await update.message.reply_text("No channels.", reply_markup=MAIN_KEYBOARD)
        return
    found = False
    for ch in channels:
        res = await supabase_execute_async(lambda: supabase.table("link_clicks").select("clicks").eq("campaign", campaign).eq("chat_id", ch["chat_id"]).execute())
        if res.data:
            total = sum(r["clicks"] for r in res.data)
            await update.message.reply_text(f"{ch['title']}: Campaign '{campaign}' - {total} clicks")
            found = True
    if not found:
        await update.message.reply_text(f"No data for campaign '{campaign}'.", reply_markup=MAIN_KEYBOARD)
    else:
        await update.message.reply_text("Done.", reply_markup=MAIN_KEYBOARD)
async def analyze_post(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id, message_id):
    try:
        res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).eq("message_id", message_id).execute())
        if not res.data:
            await update.message.reply_text("Post not found.", reply_markup=MAIN_KEYBOARD)
            return
        post = res.data[0]
        report = (f"📊 Post Analysis #{message_id}\n👁 Views: {post.get('views',0)}\n🔄 Forwards: {post.get('forwards',0)}\n📈 Engagement: {post.get('engagement_rate',0):.1f}%\n👥 Members: {post.get('members_at_time',0)}\n📉 Change: {post.get('member_change_after',0):+d}")
        await update.message.reply_text(report, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        logger.error(f"Analyze error: {e}")
        await update.message.reply_text("Error analyzing post.", reply_markup=MAIN_KEYBOARD)
async def best_time_report(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("timestamp,views").eq("chat_id", channel_id).gte("timestamp", cutoff).limit(500).execute())
    if not res.data:
        await update.callback_query.edit_message_text("Not enough data.")
        return
    hour_map = {}
    for row in res.data:
        try:
            dt = datetime.fromisoformat(row["timestamp"].replace('Z', '+00:00'))
            hour = dt.hour
            if hour not in hour_map:
                hour_map[hour] = {"total": 0, "count": 0}
            hour_map[hour]["total"] += row["views"] or 0
            hour_map[hour]["count"] += 1
        except:
            continue
    if not hour_map:
        await update.callback_query.edit_message_text("No data.")
        return
    sorted_hours = sorted(hour_map.items(), key=lambda x: x[1]["total"]/x[1]["count"], reverse=True)[:5]
    report = "⏰ Best Times:\n"
    for h, data in sorted_hours:
        avg = data["total"] / data["count"]
        report += f"{h}:00 - Avg {avg:.0f} views ({data['count']} posts)\n"
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
    scored = [(p.get("views",0) * 1 + p.get("forwards",0) * 5, p) for p in res.data]
    scored.sort(key=lambda x: x[0], reverse=True)
    report = "🏆 Top Posts:\n"
    for _, post in scored[:5]:
        report += f"#{post['message_id']} - Views:{post.get('views',0)}, Fwd:{post.get('forwards',0)}\n"
    await update.callback_query.edit_message_text(report)
async def compare_posts(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id, id1, id2):
    try:
        p1_res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).eq("message_id", id1).execute())
        p2_res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).eq("message_id", id2).execute())
        if not p1_res.data or not p2_res.data:
            await update.message.reply_text("One or both posts not found.", reply_markup=MAIN_KEYBOARD)
            return
        p1, p2 = p1_res.data[0], p2_res.data[0]
        report = f"📊 Comparison:\n#{id1}: {p1.get('views',0)} views, {p1.get('forwards',0)} fwd\n#{id2}: {p2.get('views',0)} views, {p2.get('forwards',0)} fwd"
        await update.message.reply_text(report, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        logger.error(f"Compare error: {e}")
        await update.message.reply_text("Error comparing posts.", reply_markup=MAIN_KEYBOARD)
async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = await supabase_execute_async(lambda: supabase.table("post_analytics").select("*").eq("chat_id", channel_id).gte("timestamp", cutoff).order("timestamp").limit(1000).execute())
    if not res.data:
        await update.callback_query.edit_message_text("No data.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["message_id", "views", "forwards", "engagement_rate", "timestamp"])
    for row in res.data:
        writer.writerow([row["message_id"], row.get("views",0), row.get("forwards",0), row.get("engagement_rate",""), row["timestamp"]])
    csv_content = output.getvalue()
    output.close()
    await context.bot.send_document(chat_id=update.effective_chat.id, document=io.BytesIO(csv_content.encode()), filename="analytics.csv")
    await update.callback_query.edit_message_text("CSV exported.")
async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    keyboard = [[InlineKeyboardButton("Set Loss Alert Threshold", callback_data=f"setloss|{channel_id}")],[InlineKeyboardButton("Set Daily Report Time", callback_data=f"setreport|{channel_id}")]]
    await update.callback_query.edit_message_text("Channel Settings:", reply_markup=InlineKeyboardMarkup(keyboard))
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    try:
        if data.startswith("analyze_channel|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            context.user_data["state"] = "awaiting_post_id"
            context.user_data["analyze_channel_id"] = channel_id
            await query.edit_message_text("Send post ID:")
        elif data.startswith("besttime_channel|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            await best_time_report(update, context, channel_id)
        elif data.startswith("referrals_channel|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            await referral_report(update, context, channel_id)
        elif data.startswith("top_channel|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            await top_posts_report(update, context, channel_id)
        elif data.startswith("compare_channel|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            context.user_data["state"] = "awaiting_compare_ids"
            context.user_data["compare_channel_id"] = channel_id
            await query.edit_message_text("Enter two post IDs separated by space:")
        elif data.startswith("export_channel|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            await export_csv(update, context, channel_id)
        elif data.startswith("settings_channel|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            await settings_menu(update, context, channel_id)
        elif data.startswith("setloss|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            context.user_data["state"] = "awaiting_loss_threshold"
            context.user_data["settings_channel_id"] = channel_id
            await query.edit_message_text("Enter loss alert threshold (number):")
        elif data.startswith("setreport|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            context.user_data["state"] = "awaiting_daily_report_time"
            context.user_data["settings_channel_id"] = channel_id
            await query.edit_message_text("Enter time (HH:MM, 24h):")
        elif data.startswith("linkchannel|"):
            channel_id = data.split("|")[1]
            if not await is_channel_owner(user_id, channel_id):
                await query.edit_message_text("Access denied.")
                return
            pending = context.user_data.pop("pending_link", {})
            if not pending:
                await query.edit_message_text("Link data lost. Try again.")
                return
            unique_id = uuid.uuid4().hex
            tracked_url = f"{RENDER_URL}/click/{unique_id}"
            await supabase_execute_async(lambda: supabase.table("link_clicks").insert({"unique_id": unique_id,"campaign": pending["campaign"],"target_url": pending["target_url"],"chat_id": channel_id,"clicks": 0,"created_at": datetime.now(timezone.utc).isoformat()}).execute())
            await query.edit_message_text(f"✅ Link created:\n{tracked_url}")
        else:
            await query.edit_message_text("Unknown option.")
    except Exception as e:
        logger.error(f"Callback error: {e}")
        try:
            await query.edit_message_text("An error occurred.")
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
    current_res = await supabase_execute_async(lambda: supabase.table("channels").select("member_count").eq("chat_id", chat_id).maybe_single().execute())
    old_count = current_res.data.get("member_count", 0) if current_res.data else 0
    try:
        new_count = await context.bot.get_chat_member_count(chat_id)
    except:
        new_count = old_count
    total_members = new_count
    engagement_rate = (views / total_members * 100) if total_members > 0 else 0
    member_change = new_count - old_count
    await supabase_execute_async(lambda: supabase.table("post_analytics").upsert({"message_id": msg_id,"chat_id": chat_id,"views": views,"forwards": forwards,"engagement_rate": round(engagement_rate, 2),"members_at_time": total_members,"member_change_after": member_change,"timestamp": datetime.now(timezone.utc).isoformat()}, on_conflict="message_id,chat_id").execute())
    await supabase_execute_async(lambda: supabase.table("channels").upsert({"chat_id": chat_id, "member_count": new_count, "title": update.effective_chat.title}, on_conflict="chat_id").execute())
    await supabase_execute_async(lambda: supabase.table("member_log").insert({"chat_id": chat_id, "count": new_count, "date": datetime.now(timezone.utc).isoformat()}).execute())
    if old_count > 0 and (old_count - new_count) >= 10:
        owner_res = await supabase_execute_async(lambda: supabase.table("channels").select("owner_id,loss_alert_threshold").eq("chat_id", chat_id).maybe_single().execute())
        if owner_res.data:
            threshold = owner_res.data.get("loss_alert_threshold", 10)
            if (old_count - new_count) >= threshold:
                try:
                    await context.bot.send_message(owner_res.data["owner_id"], f"⚠️ Channel lost {old_count - new_count} members after post {msg_id}.")
                except:
                    pass
async def track_referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.forward_from_chat:
        fwd = update.message.forward_from_chat
        await supabase_execute_async(lambda: supabase.table("referrals").insert({"channel_id": str(update.effective_chat.id),"from_chat_id": str(fwd.id),"from_chat_title": fwd.title or "Unknown","message_id": update.message.message_id,"date": datetime.now(timezone.utc).isoformat()}).execute())
async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.my_chat_member:
        return
    new_status = update.my_chat_member.new_chat_member.status
    chat = update.effective_chat
    if chat.type != "channel":
        return
    chat_id_str = str(chat.id)
    if new_status in ("administrator", "member"):
        existing = await supabase_execute_async(lambda: supabase.table("channels").select("owner_id").eq("chat_id", chat_id_str).execute())
        if not existing.data:
            await supabase_execute_async(lambda: supabase.table("channels").insert({"chat_id": chat_id_str,"title": chat.title,"owner_id": update.effective_user.id,"member_count": 0,"loss_alert_threshold": 10,"daily_report_time": "08:00"}).execute())
        else:
            await supabase_execute_async(lambda: supabase.table("channels").update({"title": chat.title}).eq("chat_id", chat_id_str).execute())
    elif new_status in ("left", "kicked"):
        await supabase_execute_async(lambda: supabase.table("channels").delete().eq("chat_id", chat_id_str).execute())
async def log_member_counts_job(context: ContextTypes.DEFAULT_TYPE):
    channels = await supabase_execute_async(lambda: supabase.table("channels").select("chat_id").execute())
    for ch in channels.data:
        try:
            count = await context.bot.get_chat_member_count(ch["chat_id"])
            await supabase_execute_async(lambda: supabase.table("channels").update({"member_count": count}).eq("chat_id", ch["chat_id"]).execute())
        except Exception as e:
            logger.debug(f"Member count failed for {ch['chat_id']}: {e}")
async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    now_str = datetime.now(timezone.utc).strftime("%H:%M")
    channels = await supabase_execute_async(lambda: supabase.table("channels").select("*").execute())
    sent_key = "daily_report_sent"
    if sent_key not in context.bot_data:
        context.bot_data[sent_key] = {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ch in channels.data:
        if ch.get("daily_report_time") == now_str:
            key = f"{ch['chat_id']}_{today}"
            if key in context.bot_data[sent_key].get(today, set()):
                continue
            context.bot_data[sent_key].setdefault(today, set()).add(key)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            res = await supabase_execute_async(lambda: supabase.table("member_log").select("*").eq("chat_id", ch["chat_id"]).gte("date", cutoff).order("date").limit(2).execute())
            if len(res.data) >= 2:
                growth = res.data[-1]["count"] - res.data[0]["count"]
                report = f"📈 Daily Report {ch.get('title','Channel')}:\nMembers: {res.data[0]['count']} → {res.data[-1]['count']} ({growth:+d})"
                try:
                    await context.bot.send_message(ch["owner_id"], report)
                except:
                    pass
async def clear_old_report_keys(context: ContextTypes.DEFAULT_TYPE):
    sent_key = "daily_report_sent"
    if sent_key in context.bot_data:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        context.bot_data[sent_key].pop(yesterday, None)
async def click_handler(request):
    unique_id = request.match_info.get("unique_id")
    if not unique_id:
        return web.Response(text="Invalid link", status=400)
    res = await supabase_execute_async(lambda: supabase.table("link_clicks").select("target_url,clicks").eq("unique_id", unique_id).maybe_single().execute())
    if not res.data:
        return web.Response(text="Link not found", status=404)
    target_url = res.data["target_url"]
    try:
        await supabase_execute_async(lambda: supabase.rpc("increment_link_click", {"uid": unique_id}).execute())
    except:
        try:
            current = res.data.get("clicks", 0)
            await supabase_execute_async(lambda: supabase.table("link_clicks").update({"clicks": current + 1}).eq("unique_id", unique_id).execute())
        except Exception as e:
            logger.error(f"Click increment failed: {e}")
    raise web.HTTPFound(target_url)
async def health_check(request):
    return web.Response(text="OK")
async def webhook_handler(request, application: Application):
    if WEBHOOK_SECRET:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
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
    application.add_handler(MessageHandler(filters.TEXT & \
                                           filters.COMMAND, main_dispatcher))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, post_engagement_handler))
    application.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))
    app = web.Application()
    app.router.add_get("/healthz", health_check)
    app.router.add_get("/click/{unique_id}", click_handler)
    app.router.add_post("/webhook", lambda r: webhook_handler(r, application))
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
        await application.bot.set_webhook(url=webhook_url, secret_token=WEBHOOK_SECRET or None)
    except Exception as e:
        logger.error(f"Webhook set failed: {e}")
    await asyncio.Event().wait()
if __name__ == "__main__":
    asyncio.run(main())
