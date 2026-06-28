import os
import asyncio
import logging
import requests
from lyriq import get_lyrics as get_lyrics_lyriq
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

load_dotenv()

# ---------- Tokens ----------
BOT_TOKEN = os.getenv('BOT_TOKEN')
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')

# ---------- Logging ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- HTTP Session (reusable) ----------
_session = requests.Session()
_session.headers.update({
    'User-Agent': 'MusicBot/1.0 (Telegram Bot)'
})

# ---------- Sync Functions ----------
def get_lyrics_sync(song_name, artist_name=None):
    """Blocking function to get lyrics using lyriq."""
    try:
        if not song_name or not song_name.strip():
            return None
        
        # ✅ Check artist_name properly
        if artist_name and artist_name.strip():
            lyrics_obj = get_lyrics_lyriq(song_name.strip(), artist_name.strip())
        else:
            lyrics_obj = get_lyrics_lyriq(song_name.strip())
        
        if lyrics_obj and hasattr(lyrics_obj, 'plain_lyrics'):
            return lyrics_obj.plain_lyrics
        return None

    except Exception as e:
        logger.error(f"Lyriq Error: {e}")
        return None

def get_genres_sync(song_name, artist_name):
    """Blocking function to get genres from Last.fm using Session."""
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
        # ✅ Use session instead of creating new request
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

# ---------- Async Wrappers ----------
async def get_lyrics(song_name, artist_name=None):
    return await asyncio.to_thread(get_lyrics_sync, song_name, artist_name)

async def get_genres(song_name, artist_name):
    if not LASTFM_API_KEY:
        return None
    return await asyncio.to_thread(get_genres_sync, song_name, artist_name)

# ---------- Bot Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 Hello! I'm a music finder bot.\n\n"
        "Send me a song name (with artist if possible) and I'll find the lyrics and genres.\n\n"
        "Examples:\n"
        "Imagine Dragons - Believer\n"
        "Bohemian Rhapsody (will search without artist)"
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await update.message.reply_text("🔍 Searching...")

    # ✅ Clean input (keep full text, not just first line)
    if not user_input or not user_input.strip():
        await update.message.reply_text("❌ Please send a valid song name.")
        return

    raw = user_input.strip()
    if not raw:
        await update.message.reply_text("❌ Please send a valid song name.")
        return

    # ✅ Parse using rsplit (split from the end)
    if ' - ' in raw:
        parts = raw.rsplit(' - ', 1)
        artist = parts[0].strip() if parts[0].strip() else None
        song = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    else:
        artist = None
        song = raw.strip()

    if not song:
        await update.message.reply_text(
            "❌ Please send a valid song name (e.g., 'Artist - Song' or just 'Song')."
        )
        return

    # ✅ Get lyrics (separate try block with CancelledError handling)
    lyrics = None
    try:
        lyrics = await asyncio.wait_for(get_lyrics(song, artist), timeout=20.0)
        if not lyrics and artist:
            lyrics = await asyncio.wait_for(get_lyrics(song), timeout=20.0)
    except asyncio.CancelledError:
        logger.info("Lyrics task was cancelled")
        await update.message.reply_text("⏹️ Request was cancelled.")
        return
    except asyncio.TimeoutError:
        logger.warning("Lyrics request timed out")
        await update.message.reply_text("⏰ Timeout: Lyrics request took too long.")
        return
    except Exception as e:
        logger.error(f"Lyrics error: {e}", exc_info=True)
        await update.message.reply_text("❌ Error occurred while fetching lyrics.")
        return

    # ✅ Get genres (separate try block - doesn't affect lyrics)
    genre_text = "No artist specified"
    if artist:
        if LASTFM_API_KEY:
            try:
                genres = await asyncio.wait_for(get_genres(song, artist), timeout=15.0)
                if genres:
                    genre_text = ", ".join(genres)
                else:
                    genre_text = "Not found"
            except asyncio.CancelledError:
                logger.info("Genres task was cancelled")
                genre_text = "⏹️ Cancelled"
            except asyncio.TimeoutError:
                logger.warning("Genres request timed out")
                genre_text = "⏰ Timeout (genres)"
            except Exception as e:
                logger.error(f"Genres error: {e}", exc_info=True)
                genre_text = "❌ Error fetching genres"
        else:
            genre_text = "⚠️ Genre feature disabled (no API key)"

    # ✅ Build response (plain text)
    if lyrics:
        # ✅ Calculate prefix and continuation lengths dynamically
        prefix = "📜 Lyrics:\n"
        continuation = "\n\n... (continued)"
        header = f"🎤 {song}\n"
        if artist:
            header += f"👤 {artist}\n"
        header += f"🏷️ Genres: {genre_text}\n\n"
        
        header_len = len(header)
        # ✅ Account for continuation text if needed
        max_lyrics_len = 4096 - header_len - len(prefix) - len(continuation)
        if max_lyrics_len < 100:
            max_lyrics_len = 100

        # ✅ Truncate lyrics if needed
        if len(lyrics) > max_lyrics_len:
            lyrics = lyrics[:max_lyrics_len] + continuation

        full_message = header + prefix + lyrics

        # ✅ Send as plain text
        await update.message.reply_text(full_message)
    else:
        await update.message.reply_text(
            f"😞 Song '{song}' not found. Please try with a more specific name or include the artist."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a song name in this format:\n"
        "Artist - Song Name\n\n"
        "Or just send the song name alone."
    )

# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is missing! Bot cannot start.")
        return

    if not LASTFM_API_KEY:
        logger.warning("⚠️ LASTFM_API_KEY is missing. Genre feature will be disabled.")

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))

    logger.info("🤖 Bot is running with full error handling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
