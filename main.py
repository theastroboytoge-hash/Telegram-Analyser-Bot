import os
import re
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

# ---------- Helper: Remove timestamps ----------
def remove_timestamps(text):
    """Remove timestamps like [00:12.15] or [00:12] from lyrics."""
    # Pattern for [mm:ss.xx] or [mm:ss]
    pattern = r'\[\d{2}:\d{2}(?:\.\d{2})?\]\s*'
    return re.sub(pattern, '', text)

# ---------- Sync Functions ----------
def get_lyrics_sync(song_name, artist_name):
    """Get lyrics from lyrics.ovh API and remove timestamps."""
    try:
        if not song_name or not song_name.strip() or not artist_name or not artist_name.strip():
            return None

        url = f"https://api.lyrics.ovh/v1/{artist_name.strip()}/{song_name.strip()}"
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
            # Remove timestamps
            cleaned = remove_timestamps(lyrics)
            return cleaned.strip()
        return None

    except requests.exceptions.Timeout:
        logger.error(f"Lyrics.ovh timeout for {song_name}")
        return None
    except Exception as e:
        logger.error(f"Lyrics.ovh error: {e}")
        return None

def get_genres_sync(song_name, artist_name):
    """Get genres from Last.fm."""
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

        # Return list of genre names (max 5)
        genre_list = [tag.get('name', 'Unknown') for tag in tags[:5] if isinstance(tag, dict)]
        return genre_list if genre_list else None

    except Exception as e:
        logger.error(f"Last.fm Error: {e}")
        return None

# ---------- Async Wrappers ----------
async def get_lyrics(song_name, artist_name):
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
        "Example: `Imagine Dragons - Believer`"
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

    if ' - ' not in raw:
        await update.message.reply_text(
            "❌ Please include artist name in this format:\n"
            "`Artist - Song`\n"
            "Example: `Imagine Dragons - Believer`"
        )
        return

    parts = raw.rsplit(' - ', 1)
    artist = parts[0].strip() if parts[0].strip() else None
    song = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None

    if not song or not artist:
        await update.message.reply_text(
            "❌ Please send a valid artist and song name.\n"
            "Example: `Imagine Dragons - Believer`"
        )
        return

    # Get lyrics
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

    # Get genres
    genre_list = None
    if artist and LASTFM_API_KEY:
        try:
            genre_list = await asyncio.wait_for(get_genres(song, artist), timeout=15.0)
        except asyncio.CancelledError:
            logger.info("Genres task cancelled")
        except asyncio.TimeoutError:
            logger.warning("Genres request timed out")
        except Exception as e:
            logger.error(f"Genres error: {e}", exc_info=True)

    # ---------- Build Response with HTML ----------
    if lyrics:
        # Build genre section
        genre_section = ""
        if genre_list:
            genre_lines = "\n".join([f"• {g}" for g in genre_list])
            genre_section = f"<b>🏷️ Genres:</b>\n<code>{genre_lines}</code>\n\n"
        elif LASTFM_API_KEY:
            genre_section = "<b>🏷️ Genres:</b> Not found\n\n"
        else:
            genre_section = "<b>🏷️ Genres:</b> Disabled (no API key)\n\n"

        # Build header
        header = f"<b>🎤 {song}</b>\n"
        header += f"<b>👤 {artist}</b>\n"

        # Build lyrics with <pre> for copy-paste
        lyrics_block = f"<pre>{lyrics}</pre>"

        # Combine
        full_message = header + "\n" + genre_section + lyrics_block

        # Truncate if needed (Telegram limit: 4096 characters)
        if len(full_message) > 4096:
            # Truncate lyrics part
            max_lyrics_len = 4096 - len(header) - len(genre_section) - len("<pre></pre>") - 50
            if max_lyrics_len < 100:
                max_lyrics_len = 100
            truncated_lyrics = lyrics[:max_lyrics_len] + "\n\n... (continued)"
            lyrics_block = f"<pre>{truncated_lyrics}</pre>"
            full_message = header + "\n" + genre_section + lyrics_block

        await update.message.reply_text(full_message, parse_mode='HTML')
    else:
        await update.message.reply_text(
            f"😞 Could not find lyrics for '{song}' by '{artist}'.\n"
            "Please check the spelling or try another song."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a song name in this format:\n"
        "`Artist - Song`\n\n"
        "Example: `Imagine Dragons - Believer`"
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

    logger.info("🤖 Bot is running with HTML formatting and timestamp removal...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
