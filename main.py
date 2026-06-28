import os
import asyncio
import logging
import requests
import lyricsgenius as genius
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

load_dotenv()

# ---------- Tokens ----------
BOT_TOKEN = os.getenv('BOT_TOKEN')
GENIUS_TOKEN = os.getenv('GENIUS_TOKEN')
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')

# ---------- Logging ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Fallback for Python < 3.9 ----------
if not hasattr(asyncio, 'to_thread'):
    async def to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
else:
    to_thread = asyncio.to_thread

# ---------- Sync Functions ----------
def get_lyrics_sync(song_name, artist_name=None):
    try:
        if not song_name or not song_name.strip():
            return None

        # استفاده از lyriq برای دریافت متن
        lyrics_obj = get_lyrics_lyriq(song_name.strip(), artist_name.strip() if artist_name else None)
        
        if lyrics_obj and hasattr(lyrics_obj, 'plain_lyrics'):
            return lyrics_obj.plain_lyrics
        return None

    except Exception as e:
        logger.error(f"Lyriq Error: {e}")
        return None

def get_genres_sync(song_name, artist_name):
    try:
        if not song_name or not artist_name or not song_name.strip() or not artist_name.strip():
            return None

        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            'method': 'track.getInfo',
            'api_key': LASTFM_API_KEY,
            'artist': artist_name.strip(),
            'track': song_name.strip(),
            'format': 'json'
        }
        response = requests.get(url, params=params, timeout=10)

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

    except requests.exceptions.RequestException as e:
        logger.error(f"Last.fm Request Error: {e}")
        return None
    except Exception as e:
        logger.error(f"Last.fm Parse Error: {e}")
        return None

# ---------- Async Wrappers ----------
async def get_lyrics(song_name, artist_name=None):
    return await to_thread(get_lyrics_sync, song_name, artist_name)

async def get_genres(song_name, artist_name):
    return await to_thread(get_genres_sync, song_name, artist_name)

# ---------- Utility ----------
def split_message(text, max_length=4096):
    return [text[i:i+max_length] for i in range(0, len(text), max_length)]

# ---------- Bot Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>🎵 Hello! I'm a music finder bot.</b>\n\n"
        "Send me a song name (with artist if possible) and I'll find the lyrics and genres.\n\n"
        "<b>Examples:</b>\n"
        "<code>Imagine Dragons - Believer</code>\n"
        "<code>Bohemian Rhapsody</code> (will search without artist)",
        parse_mode='HTML'
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await update.message.reply_text("🔍 Searching...")

    if not user_input or not user_input.strip():
        await update.message.reply_text("❌ Please send a valid song name.")
        return

    # ✅ Clean input: take first line, remove extra spaces
    raw = user_input.strip().split('\n', 1)[0].strip()
    if not raw:
        await update.message.reply_text("❌ Please send a valid song name.")
        return

    # Parse Artist - Song
    if ' - ' in raw:
        parts = raw.split(' - ', 1)
        artist = parts[0].strip() if parts[0].strip() else None
        song = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    else:
        artist = None
        song = raw.strip()

    if not song:
        await update.message.reply_text(
            "❌ Please send a valid song name (e.g., <code>Artist - Song</code> or just <code>Song</code>).",
            parse_mode='HTML'
        )
        return

    try:
        lyrics = await asyncio.wait_for(get_lyrics(song, artist), timeout=20.0)
        if not lyrics and artist:
            lyrics = await asyncio.wait_for(get_lyrics(song), timeout=20.0)

        genre_text = "No artist specified"
        if artist:
            genres = await asyncio.wait_for(get_genres(song, artist), timeout=20.0)
            if genres:
                genre_text = ", ".join(genres)
            else:
                genre_text = "Not found"

    except asyncio.TimeoutError:
        await update.message.reply_text("⏰ Timeout: The request took too long. Please try again later.")
        return

    if lyrics:
        # ✅ Use HTML tags for formatting
        header = f"<b>🎤 {song}</b>\n"
        if artist:
            header += f"<b>👤 {artist}</b>\n"
        header += f"<b>🏷️ Genres:</b> {genre_text}\n\n"

        header_len = len(header)
        max_lyrics_len = 4096 - header_len - 50
        if max_lyrics_len < 100:
            max_lyrics_len = 100

        if len(lyrics) > max_lyrics_len:
            lyrics = lyrics[:max_lyrics_len] + "\n\n... (continued)"

        full_message = header + f"<b>📜 Lyrics:</b>\n{lyrics}"

        # Escape HTML special characters in lyrics
        # Telegram HTML parser needs escaping of <, >, & (but we do it globally)
        # We'll use a simple replace to be safe
        full_message = full_message.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        for chunk in split_message(full_message):
            await update.message.reply_text(chunk, parse_mode='HTML')
    else:
        await update.message.reply_text(
            f"😞 Song <code>{song}</code> not found. Please try with a more specific name or include the artist.",
            parse_mode='HTML'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a song name in this format:\n"
        "<code>Artist - Song Name</code>\n\n"
        "Or just send the song name alone.",
        parse_mode='HTML'
    )

# ---------- Main ----------
def main():
    if not BOT_TOKEN or not GENIUS_TOKEN or not LASTFM_API_KEY:
        logger.error("❌ Missing environment variables!")
        return

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))

    logger.info("🤖 Bot is running with HTML parsing and input cleaning...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
