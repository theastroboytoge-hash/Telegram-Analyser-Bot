import os
import asyncio
import logging
import requests
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

# ---------- HTTP Session ----------
_session = requests.Session()
_session.headers.update({'User-Agent': 'MusicBot/1.0'})

# ---------- Sync Functions ----------
def get_lyrics_sync(song_name, artist_name=None):
    """Get lyrics from lyrics.ovh API."""
    try:
        if not song_name or not song_name.strip():
            return None

        # If artist is missing, try to search without artist (some APIs support it)
        if artist_name and artist_name.strip():
            url = f"https://api.lyrics.ovh/v1/{artist_name.strip()}/{song_name.strip()}"
        else:
            # Try with a placeholder artist (lyrics.ovh needs both)
            # So we'll try with artist = "Unknown" or we can try multiple common artists
            # Better: we can attempt with a generic search, but lyrics.ovh requires both
            # So if artist is missing, we return None and tell user to provide artist
            return None

        response = _session.get(url, timeout=10)
        
        if response.status_code == 404:
            logger.info(f"Lyrics not found for {song_name} by {artist_name}")
            return None
        elif not response.ok:
            logger.warning(f"Lyrics.ovh HTTP error: {response.status_code}")
            return None

        data = response.json()
        lyrics = data.get('lyrics')
        if lyrics:
            return lyrics.strip()
        return None

    except requests.exceptions.Timeout:
        logger.error(f"Lyrics.ovh timeout for {song_name}")
        return None
    except Exception as e:
        logger.error(f"Lyrics.ovh error: {e}")
        return None

def get_genres_sync(song_name, artist_name):
    """Get genres from Last.fm using Session."""
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
        "Send me a song name with artist in this format:\n"
        "`Artist - Song`\n\n"
        "Example: `Lana Del Rey - Summertime Sadness`"
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await update.message.reply_text("🔍 Searching...")

    if not user_input or not user_input.strip():
        await update.message.reply_text("❌ Please send a valid song name.")
        return

    raw = user_input.strip()
    if not raw:
        await update.message.reply_text("❌ Please send a valid song name.")
        return

    # Parse Artist - Song
    if ' - ' not in raw:
        await update.message.reply_text(
            "❌ Please include artist name in this format:\n"
            "`Artist - Song`\n"
            "Example: `Lana Del Rey - Summertime Sadness`"
        )
        return

    parts = raw.rsplit(' - ', 1)
    artist = parts[0].strip() if parts[0].strip() else None
    song = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None

    if not song or not artist:
        await update.message.reply_text(
            "❌ Please send a valid artist and song name.\n"
            "Example: `Lana Del Rey - Summertime Sadness`"
        )
        return

    # ✅ Get lyrics (separate try block)
    lyrics = None
    try:
        lyrics = await asyncio.wait_for(get_lyrics(song, artist), timeout=20.0)
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

    # ✅ Get genres (separate try block)
    genre_text = "Fetching..."
    if artist and LASTFM_API_KEY:
        try:
            genres = await asyncio.wait_for(get_genres(song, artist), timeout=15.0)
            if genres:
                genre_text = ", ".join(genres)
            else:
                genre_text = "Not found"
        except asyncio.CancelledError:
            genre_text = "⏹️ Cancelled"
        except asyncio.TimeoutError:
            genre_text = "⏰ Timeout"
        except Exception as e:
            logger.error(f"Genres error: {e}", exc_info=True)
            genre_text = "❌ Error"
    elif not LASTFM_API_KEY:
        genre_text = "⚠️ Disabled"

    # ✅ Build response
    if lyrics:
        prefix = "📜 Lyrics:\n"
        continuation = "\n\n... (continued)"
        header = f"🎤 {song}\n"
        header += f"👤 {artist}\n"
        header += f"🏷️ Genres: {genre_text}\n\n"
        
        header_len = len(header)
        max_lyrics_len = 4096 - header_len - len(prefix) - len(continuation)
        if max_lyrics_len < 100:
            max_lyrics_len = 100

        if len(lyrics) > max_lyrics_len:
            lyrics = lyrics[:max_lyrics_len] + continuation

        full_message = header + prefix + lyrics
        await update.message.reply_text(full_message)
    else:
        await update.message.reply_text(
            f"😞 Could not find lyrics for '{song}' by '{artist}'.\n"
            "Please check the spelling or try another song."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a song name in this format:\n"
        "`Artist - Song`\n\n"
        "Example: `Lana Del Rey - Summertime Sadness`"
    )

# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is missing!")
        return

    if not LASTFM_API_KEY:
        logger.warning("⚠️ LASTFM_API_KEY missing. Genre feature disabled.")

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))

    logger.info("🤖 Bot is running with lyrics.ovh API...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
