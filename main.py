import os
import logging
import requests
import lyricsgenius as genius
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

load_dotenv()

# ---------- توکن‌ها ----------
BOT_TOKEN = os.getenv('BOT_TOKEN')
GENIUS_TOKEN = os.getenv('GENIUS_TOKEN')
# TheAudioDB نیازی به کلید ندارد (کلید 1 برای تست رایگان است)
AUDIODB_KEY = '1'

# ---------- تنظیمات لاگ ----------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ---------- دریافت متن از Genius ----------
def get_lyrics(song_name, artist_name=None):
    try:
        api = genius.Genius(GENIUS_TOKEN)
        api.verbose = False
        api.remove_section_headers = True
        if artist_name:
            song = api.search_song(song_name, artist_name)
        else:
            song = api.search_song(song_name)
        return song.lyrics if song else None
    except Exception as e:
        logging.error(f"Genius Error: {e}")
        return None

# ---------- دریافت ژانر از TheAudioDB ----------
def get_genre(song_name, artist_name):
    try:
        url = f"https://www.theaudiodb.com/api/v1/json/{AUDIODB_KEY}/searchtrack.php?s={artist_name}&t={song_name}"
        response = requests.get(url)
        data = response.json()
        if data.get('track'):
            return data['track'][0].get('strGenre', 'ناشناس')
        return None
    except Exception as e:
        logging.error(f"Genre Error: {e}")
        return None

# ---------- دستورات بات ----------
async def start(update: Update, context):
    await update.message.reply_text(
        "🎵 سلام! من بات پیداکننده آهنگم.\n"
        "اسم آهنگ و خواننده رو بفرست تا متن و ژانر رو برات پیدا کنم.\n"
        "مثال: `Imagine Dragons Believer`"
    )

async def search_song(update: Update, context):
    user_input = update.message.text
    await update.message.reply_text("🔍 در حال جستجو...")

    # تشخیص خواننده و آهنگ (با خط تیره جدا کن)
    if ' - ' in user_input:
        parts = user_input.split(' - ', 1)
        artist = parts[0].strip()
        song = parts[1].strip()
    else:
        artist = None
        song = user_input.strip()

    # دریافت متن
    lyrics = get_lyrics(song, artist)
    if not lyrics and artist:
        lyrics = get_lyrics(song)  # تلاش مجدد بدون خواننده

    # دریافت ژانر
    genre = "نام خواننده مشخص نیست"
    if artist:
        g = get_genre(song, artist)
        if g:
            genre = g
        else:
            genre = "پیدا نشد"

    # ساخت پاسخ نهایی
    if lyrics:
        if len(lyrics) > 4000:
            lyrics = lyrics[:4000] + "\n\n... (ادامه)"
        response = f"🎤 **{song}**\n"
        if artist:
            response += f"👤 {artist}\n"
        response += f"🏷️ ژانر: {genre}\n\n"
        response += f"📜 **متن:**\n{lyrics}"
    else:
        response = f"😞 آهنگ `{song}` پیدا نشد. اسم رو دقیق‌تر بفرست."

    await update.message.reply_text(response)

async def help_command(update: Update, context):
    await update.message.reply_text("اسم آهنگ رو با فرمت `خواننده - آهنگ` بفرست.")

# ---------- اجرای اصلی ----------
def main():
    if not BOT_TOKEN or not GENIUS_TOKEN:
        print("❌ خطا: BOT_TOKEN و GENIUS_TOKEN را در فایل .env تنظیم کن!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))

    print("🤖 بات روشن شد...")
    app.run_polling()

if __name__ == "__main__":
    main()
