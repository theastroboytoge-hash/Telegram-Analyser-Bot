import os
import re
import asyncio
import logging
import requests
from mutagen import File as MutagenFile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

# ========== وابستگی‌های اختیاری ==========
# BeautifulSoup (برای scraping Genius)
try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BeautifulSoup = None
    BEAUTIFULSOUP_AVAILABLE = False
    logging.warning("BeautifulSoup (bs4) not installed. Genius scraper disabled.")

# Musixmatch
try:
    import musixmatch
    MUSIXMATCH_AVAILABLE = True
except ImportError:
    musixmatch = None
    MUSIXMATCH_AVAILABLE = False
    logging.warning("musixmatch not installed. Install with: pip install musixmatch")

load_dotenv()

# ---------- توکن‌ها ----------
BOT_TOKEN = os.getenv('BOT_TOKEN')
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')
MUSIXMATCH_API_KEY = os.getenv('MUSIXMATCH_API_KEY')
GENIUS_TOKEN = os.getenv('GENIUS_TOKEN')  # اختیاری

# ---------- لاگینگ ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Session HTTP ----------
_session = requests.Session()
_session.headers.update({'User-Agent': 'MusicBot/1.0 (Telegram Bot)'})

# ---------- توابع کمکی ----------
def remove_timestamps(text):
    pattern = r'\[\d{2}:\d{2}(?:\.\d{2})?\]\s*'
    return re.sub(pattern, '', text)

def extract_metadata(file_path):
    """استخراج نام خواننده و عنوان آهنگ از فایل صوتی"""
    try:
        audio = MutagenFile(file_path)
        if audio is None:
            return None, None

        artist = None
        title = None

        # MP3 (ID3)
        if 'TPE1' in audio:
            artist = str(audio['TPE1'])
        elif 'artist' in audio:
            artist = str(audio['artist'][0]) if isinstance(audio['artist'], list) else str(audio['artist'])

        if 'TIT2' in audio:
            title = str(audio['TIT2'])
        elif 'title' in audio:
            title = str(audio['title'][0]) if isinstance(audio['title'], list) else str(audio['title'])

        if not artist or not title:
            filename = os.path.basename(file_path)
            if ' - ' in filename:
                parts = filename.rsplit(' - ', 1)
                artist = parts[0].strip()
                title = parts[1].split('.')[0].strip()
            else:
                title = os.path.splitext(filename)[0]

        return artist, title
    except Exception as e:
        logger.error(f"Metadata extraction error: {e}")
        return None, None

# ---------- جستجو در Last.fm ----------
def search_tracks_sync(query, limit=10):
    if not LASTFM_API_KEY:
        return None

    try:
        if not query or not query.strip():
            return None

        url = "https://ws.audioscrobbler.com/2.0/"
        params = {
            'method': 'track.search',
            'api_key': LASTFM_API_KEY,
            'track': query.strip(),
            'format': 'json',
            'limit': limit
        }
        response = _session.get(url, params=params, timeout=10)

        if not response.ok:
            logger.warning(f"Last.fm search HTTP error: {response.status_code}")
            return None

        data = response.json()
        results = data.get('results', {}).get('trackmatches', {}).get('track', [])
        
        if not results:
            return None

        tracks = []
        for track in results:
            tracks.append({
                'name': track.get('name', 'Unknown'),
                'artist': track.get('artist', 'Unknown'),
                'url': track.get('url', '')
            })
        return tracks

    except Exception as e:
        logger.error(f"Last.fm search error: {e}")
        return None

# ---------- دریافت متن از منابع مختلف ----------
def get_lyrics_from_lyricsovh(song_name, artist_name):
    try:
        url = f"https://api.lyrics.ovh/v1/{artist_name.strip()}/{song_name.strip()}"
        response = _session.get(url, timeout=10)
        
        if response.status_code == 404:
            logger.info(f"Lyrics.ovh: Not found for {song_name} by {artist_name}")
            return None
        elif not response.ok:
            logger.warning(f"Lyrics.ovh HTTP error: {response.status_code}")
            return None

        data = response.json()
        lyrics = data.get('lyrics')
        if lyrics:
            return remove_timestamps(lyrics).strip()
        return None

    except Exception as e:
        logger.error(f"Lyrics.ovh error: {e}")
        return None

def get_lyrics_from_lrclib(song_name, artist_name):
    try:
        url = "https://lrclib.net/api/get"
        params = {
            'artist_name': artist_name.strip(),
            'track_name': song_name.strip()
        }
        response = _session.get(url, params=params, timeout=10)
        
        if response.status_code == 404:
            logger.info(f"LRCLIB: Not found for {song_name} by {artist_name}")
            return None
        elif not response.ok:
            logger.warning(f"LRCLIB HTTP error: {response.status_code}")
            return None

        data = response.json()
        lyrics = data.get('plainLyrics')
        if lyrics:
            return remove_timestamps(lyrics).strip()
        return None

    except Exception as e:
        logger.error(f"LRCLIB error: {e}")
        return None

def get_lyrics_from_musixmatch(song_name, artist_name):
    if not MUSIXMATCH_API_KEY or not MUSIXMATCH_AVAILABLE:
        return None

    try:
        client = musixmatch.Musixmatch(MUSIXMATCH_API_KEY)
        response = client.track_search(
            q_track=song_name.strip(),
            q_artist=artist_name.strip(),
            page_size=1,
            page=1,
            s_track_rating='desc',
            f_has_lyrics=1
        )
        
        if response['message']['header']['status_code'] != 200:
            return None

        track_list = response['message']['body']['track_list']
        if not track_list:
            return None

        track_id = track_list[0]['track']['track_id']
        lyrics_response = client.track_lyrics_get(track_id)
        
        if lyrics_response['message']['header']['status_code'] != 200:
            return None

        lyrics = lyrics_response['message']['body']['lyrics']['lyrics_body']
        if lyrics and lyrics != "..." and lyrics.strip():
            return remove_timestamps(lyrics).strip()
        return None

    except Exception as e:
        logger.error(f"Musixmatch error: {e}")
        return None

def get_lyrics_from_genius(song_name, artist_name):
    """دریافت متن از Genius (در صورت موجود بودن BeautifulSoup و توکن)"""
    if not BEAUTIFULSOUP_AVAILABLE:
        logger.info("BeautifulSoup not available, skipping Genius.")
        return None

    if not GENIUS_TOKEN:
        logger.info("GENIUS_TOKEN not set, skipping Genius.")
        return None

    try:
        # جستجو در Genius
        search_url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {GENIUS_TOKEN}"}
        params = {"q": f"{artist_name} {song_name}"}
        response = _session.get(search_url, headers=headers, params=params, timeout=10)
        if not response.ok:
            return None

        data = response.json()
        hits = data.get('response', {}).get('hits', [])
        if not hits:
            return None

        song_path = hits[0]['result']['path']
        song_url = f"https://genius.com{song_path}"

        page_response = _session.get(song_url, timeout=10)
        if not page_response.ok:
            return None

        soup = BeautifulSoup(page_response.text, 'html.parser')
        lyrics_div = soup.find('div', {'data-lyrics-container': 'true'})
        if not lyrics_div:
            lyrics_div = soup.find('div', class_='lyrics')
        if not lyrics_div:
            return None

        lyrics = lyrics_div.get_text(separator='\n').strip()
        return remove_timestamps(lyrics) if lyrics else None

    except Exception as e:
        logger.error(f"Genius scraper error: {e}")
        return None

def get_lyrics_sync(song_name, artist_name):
    if not song_name or not song_name.strip() or not artist_name or not artist_name.strip():
        return None

    sources = [
        ('Lyrics.ovh', get_lyrics_from_lyricsovh),
        ('LRCLIB', get_lyrics_from_lrclib),
        ('Musixmatch', get_lyrics_from_musixmatch),
        ('Genius', get_lyrics_from_genius)
    ]

    for source_name, source_func in sources:
        logger.info(f"Trying {source_name} for {song_name} by {artist_name}")
        try:
            lyrics = source_func(song_name, artist_name)
            if lyrics:
                logger.info(f"✅ {source_name}: Success for {song_name} by {artist_name}")
                return lyrics
        except Exception as e:
            logger.error(f"{source_name} crashed: {e}")

    logger.info(f"❌ No lyrics found for {song_name} by {artist_name} from any source")
    return None

# ---------- دریافت ژانر از Last.fm ----------
def get_genres_sync(song_name, artist_name):
    if not LASTFM_API_KEY:
        return None

    try:
        if not song_name or not artist_name or not song_name.strip() or not artist_name.strip():
            return None

        url = "https://ws.audioscrobbler.com/2.0/"
        params = {
            'method': 'track.getInfo',
            'api_key': LASTFM_API_KEY,
            'artist': artist_name.strip(),
            'track': song_name.strip(),
            'format': 'json'
        }
        response = _session.get(url, params=params, timeout=10)

        if not response.ok:
            logger.warning(f"Last.fm HTTP error: {response.status_code}")
            return None

        data = response.json()
        track = data.get('track')
        if not track:
            return None

        toptags = track.get('toptags')
        if not toptags:
            return None

        tags = toptags.get('tag')
        if not tags:
            return None

        if isinstance(tags, dict):
            tags = [tags]

        genre_list = [tag.get('name', 'Unknown') for tag in tags[:5] if isinstance(tag, dict)]
        return genre_list if genre_list else None

    except Exception as e:
        logger.error(f"Last.fm Error: {e}")
        return None

# ---------- توابع ناهمگام (Async) ----------
async def search_tracks(query, limit=10):
    return await asyncio.to_thread(search_tracks_sync, query, limit)

async def get_lyrics(song_name, artist_name):
    return await asyncio.to_thread(get_lyrics_sync, song_name, artist_name)

async def get_genres(song_name, artist_name):
    if not LASTFM_API_KEY:
        return None
    return await asyncio.to_thread(get_genres_sync, song_name, artist_name)

# ---------- دستورات ربات ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 سلام! من یک ربات پیداکننده موسیقی هستم.\n\n"
        "هر جستجویی (نام آهنگ، خواننده یا حتی قسمتی از متن) بفرستید:\n"
        "مثال: `Believer` یا `Imagine Dragons` یا `First things first`\n\n"
        "همچنین می‌توانید یک فایل صوتی (MP3 و غیره) ارسال کنید تا متادیتا استخراج شود."
    )

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    audio = update.message.audio or update.message.document
    if not audio:
        await update.message.reply_text("❌ فایل صوتی پیدا نشد.")
        return

    file = await audio.get_file()
    file_path = f"temp_{audio.file_id}.mp3"
    await file.download_to_drive(file_path)

    try:
        artist, title = extract_metadata(file_path)
        if artist and title:
            query = f"{artist} - {title}"
            await update.message.reply_text(f"🎵 متادیتا: {artist} - {title}\nدر حال جستجو...")
            await search_and_show_results(update, context, query)
        else:
            await update.message.reply_text("❌ امکان استخراج نام خواننده و عنوان وجود ندارد. لطفاً به‌صورت دستی جستجو کنید.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def search_and_show_results(update, context, query):
    """نمایش نتایج جستجو به‌صورت دکمه‌ای"""
    await update.message.reply_text("🔍 در حال جستجو...")

    if not query or not query.strip():
        await update.message.reply_text("❌ لطفاً یک عبارت معتبر ارسال کنید.")
        return

    try:
        tracks = await asyncio.wait_for(search_tracks(query.strip()), timeout=15.0)
    except Exception as e:
        logger.error(f"Search error: {e}")
        await update.message.reply_text("❌ خطا در جستجو.")
        return

    if not tracks:
        await update.message.reply_text(
            f"😞 نتیجه‌ای برای '{query}' پیدا نشد. لطفاً عبارت دیگری امتحان کنید."
        )
        return

    context.user_data['search_results'] = tracks
    context.user_data['last_query'] = query

    buttons = []
    for idx, track in enumerate(tracks[:10]):
        display_name = f"{track['artist']} - {track['name']}"
        if len(display_name) > 60:
            display_name = display_name[:57] + "..."
        buttons.append([InlineKeyboardButton(display_name, callback_data=f"track_{idx}")])

    buttons.append([InlineKeyboardButton("❌ لغو", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        f"🎵 {len(tracks)} نتیجه برای '{query}':\n"
        "برای دریافت متن و ژانر روی آهنگ مورد نظر کلیک کنید:",
        reply_markup=reply_markup
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    await search_and_show_results(update, context, query)

# ---------- مدیریت کلیک روی دکمه‌ها ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_data = context.user_data

    if data == "cancel":
        await query.edit_message_text("❌ جستجو لغو شد.")
        return

    if data.startswith("track_"):
        try:
            idx = int(data.split("_")[1])
            tracks = user_data.get('search_results', [])
            
            if idx >= len(tracks):
                await query.edit_message_text("❌ نتیجه پیدا نشد. لطفاً دوباره جستجو کنید.")
                return

            track = tracks[idx]
            song_name = track['name']
            artist_name = track['artist']

            await query.edit_message_text(f"📥 دریافت متن برای {song_name} از {artist_name}...")

            lyrics = None
            try:
                lyrics = await asyncio.wait_for(get_lyrics(song_name, artist_name), timeout=25.0)
            except Exception as e:
                logger.error(f"Lyrics error: {e}")

            genre_list = None
            if artist_name and LASTFM_API_KEY:
                try:
                    genre_list = await asyncio.wait_for(get_genres(song_name, artist_name), timeout=15.0)
                except Exception as e:
                    logger.error(f"Genres error: {e}")

            # ساخت پیام نهایی
            header = f"<b>🎤 {song_name}</b>\n"
            header += f"<b>👤 {artist_name}</b>\n\n"

            if genre_list:
                genre_lines = "\n".join([f"• {g}" for g in genre_list])
                genre_section = f"<b>🏷️ ژانرها:</b>\n<code>{genre_lines}</code>\n\n"
            elif LASTFM_API_KEY:
                genre_section = "<b>🏷️ ژانرها:</b> یافت نشد\n\n"
            else:
                genre_section = "<b>🏷️ ژانرها:</b> غیرفعال\n\n"

            if lyrics:
                lyrics_block = f"<pre>{lyrics}</pre>"
            else:
                lyrics_block = "<b>📜 متن:</b> یافت نشد"

            full_message = header + genre_section + lyrics_block

            if len(full_message) > 4096:
                if lyrics and len(lyrics) > 2000:
                    truncated_lyrics = lyrics[:2000] + "\n\n... (ادامه)"
                    lyrics_block = f"<pre>{truncated_lyrics}</pre>"
                    full_message = header + genre_section + lyrics_block
                else:
                    full_message = header + genre_section + "<b>📜 متن:</b> خیلی طولانی است."

            await query.message.reply_text(full_message, parse_mode='HTML')
            if lyrics:
                await query.message.reply_text("✅ می‌توانید متن را با کلیک روی آن کپی کنید.")

        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            await query.edit_message_text("❌ خطا رخ داد. لطفاً دوباره تلاش کنید.")

# ---------- دستور راهنما ----------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 **ربات پیداکننده موسیقی**\n\n"
        "**نحوه استفاده:**\n"
        "1. عبارت جستجو (نام آهنگ، خواننده یا قسمتی از متن) را ارسال کنید\n"
        "2. یا یک فایل صوتی ارسال کنید\n"
        "3. از بین نتایج، گزینه مورد نظر را انتخاب کنید\n"
        "4. متن و ژانر آهنگ نمایش داده می‌شود\n\n"
        "**مثال:**\n"
        "• `Believer`\n"
        "• `Imagine Dragons`\n"
        "• `First things first`\n\n"
        "**منابع متن (۴ منبع):**\n"
        "• Lyrics.ovh\n"
        "• LRCLIB\n"
        "• Musixmatch (در صورت وجود کلید)\n"
        "• Genius (در صورت نصب BeautifulSoup و وجود توکن)\n\n"
        "**منبع ژانر:** Last.fm"
    )

# ---------- اجرای اصلی ----------
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN تنظیم نشده است!")
        return

    if not LASTFM_API_KEY:
        logger.warning("⚠️ LASTFM_API_KEY وجود ندارد. جستجو و ژانر غیرفعال خواهد بود!")

    logger.info("✅ منابع فعال متن: Lyrics.ovh, LRCLIB")
    if MUSIXMATCH_API_KEY and MUSIXMATCH_AVAILABLE:
        logger.info("✅ منبع Musixmatch فعال است.")
    else:
        logger.warning("⚠️ Musixmatch غیرفعال است (کلید یا کتابخانه موجود نیست).")

    if BEAUTIFULSOUP_AVAILABLE and GENIUS_TOKEN:
        logger.info("✅ منبع Genius (اسکرپر) فعال است.")
    else:
        logger.warning("⚠️ Genius غیرفعال است (BeautifulSoup یا توکن موجود نیست).")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
    application.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL, handle_audio))
    application.add_handler(CallbackQueryHandler(button_callback))

    logger.info("🤖 ربات با موفقیت راه‌اندازی شد...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
