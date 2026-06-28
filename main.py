import os
import re
import asyncio
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

# Try to import optional libraries
try:
    import musixmatch
    MUSIXMATCH_AVAILABLE = True
except ImportError:
    MUSIXMATCH_AVAILABLE = False
    logging.warning("musixmatch not installed. Install with: pip install musixmatch-api")

load_dotenv()

# ---------- Tokens ----------
BOT_TOKEN = os.getenv('BOT_TOKEN')
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')
MUSIXMATCH_API_KEY = os.getenv('MUSIXMATCH_API_KEY')  # Optional

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
    pattern = r'\[\d{2}:\d{2}(?:\.\d{2})?\]\s*'
    return re.sub(pattern, '', text)

# ---------- Search tracks on Last.fm ----------
def search_tracks_sync(query, limit=10):
    """Search for tracks on Last.fm and return list of results."""
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

# ---------- Get lyrics from multiple sources (with fallback) ----------
def get_lyrics_from_lyricsovh(song_name, artist_name):
    """Get lyrics from lyrics.ovh API."""
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

    except requests.exceptions.Timeout:
        logger.error(f"Lyrics.ovh timeout for {song_name}")
        return None
    except Exception as e:
        logger.error(f"Lyrics.ovh error: {e}")
        return None

def get_lyrics_from_lrclib(song_name, artist_name):
    """Get lyrics from LRCLIB API (free, no key required)."""
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

    except requests.exceptions.Timeout:
        logger.error(f"LRCLIB timeout for {song_name}")
        return None
    except Exception as e:
        logger.error(f"LRCLIB error: {e}")
        return None

def get_lyrics_from_musixmatch(song_name, artist_name):
    """Get lyrics from Musixmatch API (requires key)."""
    if not MUSIXMATCH_API_KEY or not MUSIXMATCH_AVAILABLE:
        return None

    try:
        # Using musixmatch library
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
            logger.info(f"Musixmatch: Search failed for {song_name} by {artist_name}")
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

def get_lyrics_sync(song_name, artist_name):
    """Get lyrics from multiple sources with fallback."""
    if not song_name or not song_name.strip() or not artist_name or not artist_name.strip():
        return None

    sources = [
        ('Lyrics.ovh', get_lyrics_from_lyricsovh),
        ('LRCLIB', get_lyrics_from_lrclib),
        ('Musixmatch', get_lyrics_from_musixmatch)
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

# ---------- Get genres from Last.fm ----------
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

        genre_list = [tag.get('name', 'Unknown') for tag in tags[:5] if isinstance(tag, dict)]
        return genre_list if genre_list else None

    except Exception as e:
        logger.error(f"Last.fm Error: {e}")
        return None

# ---------- Async Wrappers ----------
async def search_tracks(query, limit=10):
    return await asyncio.to_thread(search_tracks_sync, query, limit)

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
        "Send me any search query (song name, artist, or even part of lyrics):\n"
        "Example: `Believer` or `Imagine Dragons` or `First things first`\n\n"
        "I'll show you matching songs with buttons. Just click on one!"
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    await update.message.reply_text("🔍 Searching...")

    if not query or not query.strip():
        await update.message.reply_text("❌ Please send a valid search query.")
        return

    # Search for tracks
    try:
        tracks = await asyncio.wait_for(search_tracks(query.strip()), timeout=15.0)
    except asyncio.CancelledError:
        logger.info("Search task was cancelled")
        await update.message.reply_text("⏹️ Search was cancelled.")
        return
    except asyncio.TimeoutError:
        logger.warning("Search request timed out")
        await update.message.reply_text("⏰ Timeout: Search took too long.")
        return
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        await update.message.reply_text("❌ Error occurred while searching.")
        return

    if not tracks:
        await update.message.reply_text(
            f"😞 No results found for '{query}'. Please try a different search."
        )
        return

    # Store search results in context.user_data for later use
    context.user_data['search_results'] = tracks
    context.user_data['last_query'] = query

    # Create inline buttons
    buttons = []
    for idx, track in enumerate(tracks[:10]):  # Max 10 results
        display_name = f"{track['artist']} - {track['name']}"
        if len(display_name) > 60:
            display_name = display_name[:57] + "..."
        buttons.append([InlineKeyboardButton(display_name, callback_data=f"track_{idx}")])

    # Add a cancel button
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        f"🎵 Found {len(tracks)} results for '{query}':\n"
        "Click on a song to get lyrics and genres:",
        reply_markup=reply_markup
    )

# ---------- Callback Query Handler ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    data = query.data
    user_data = context.user_data

    if data == "cancel":
        await query.edit_message_text("❌ Search cancelled.")
        return

    if data.startswith("track_"):
        try:
            idx = int(data.split("_")[1])
            tracks = user_data.get('search_results', [])
            
            if idx >= len(tracks):
                await query.edit_message_text("❌ Result not found. Please search again.")
                return

            track = tracks[idx]
            song_name = track['name']
            artist_name = track['artist']

            # Show loading message
            await query.edit_message_text(f"📥 Fetching lyrics for {song_name} by {artist_name}...")

            # Get lyrics (with multi-source fallback)
            lyrics = None
            try:
                lyrics = await asyncio.wait_for(get_lyrics(song_name, artist_name), timeout=25.0)
            except asyncio.CancelledError:
                logger.info("Lyrics task was cancelled")
                await query.edit_message_text("⏹️ Request was cancelled.")
                return
            except asyncio.TimeoutError:
                logger.warning("Lyrics request timed out")
                await query.edit_message_text("⏰ Timeout: Lyrics request took too long.")
                return
            except Exception as e:
                logger.error(f"Lyrics error: {e}", exc_info=True)
                await query.edit_message_text("❌ Error occurred while fetching lyrics.")
                return

            # Get genres
            genre_list = None
            if artist_name and LASTFM_API_KEY:
                try:
                    genre_list = await asyncio.wait_for(get_genres(song_name, artist_name), timeout=15.0)
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
                header = f"<b>🎤 {song_name}</b>\n"
                header += f"<b>👤 {artist_name}</b>\n\n"

                # Build lyrics with <pre> for copy-paste
                lyrics_block = f"<pre>{lyrics}</pre>"

                # Combine
                full_message = header + genre_section + lyrics_block

                # Truncate if needed (Telegram limit: 4096 characters)
                if len(full_message) > 4096:
                    max_lyrics_len = 4096 - len(header) - len(genre_section) - len("<pre></pre>") - 50
                    if max_lyrics_len < 100:
                        max_lyrics_len = 100
                    truncated_lyrics = lyrics[:max_lyrics_len] + "\n\n... (continued)"
                    lyrics_block = f"<pre>{truncated_lyrics}</pre>"
                    full_message = header + genre_section + lyrics_block

                # Send result as new message (not edit) to avoid formatting issues
                await query.message.reply_text(full_message, parse_mode='HTML')
                await query.message.reply_text("✅ You can copy lyrics by tapping on them above.")
            else:
                await query.message.reply_text(
                    f"😞 Could not find lyrics for '{song_name}' by '{artist_name}'.\n"
                    "Please try another song or search again."
                )

        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            await query.edit_message_text("❌ An error occurred. Please try again.")

# ---------- Help Command ----------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 **Music Finder Bot**\n\n"
        "**How to use:**\n"
        "1. Send any search query (song name, artist, or part of lyrics)\n"
        "2. Select from the results using buttons\n"
        "3. Get lyrics and genres for the chosen song\n\n"
        "**Examples:**\n"
        "• `Believer`\n"
        "• `Imagine Dragons`\n"
        "• `First things first`\n\n"
        "**Features:**\n"
        "• Search by song, artist, or any text\n"
        "• Results shown as buttons\n"
        "• Lyrics without timestamps\n"
        "• Genre tags displayed\n"
        "• Copy lyrics with one tap\n\n"
        "**Lyrics Sources:**\n"
        "• Lyrics.ovh (primary)\n"
        "• LRCLIB (fallback)\n"
        "• Musixmatch (fallback, if key provided)"
    )

# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is missing!")
        return

    if not LASTFM_API_KEY:
        logger.warning("⚠️ LASTFM_API_KEY missing. Search and genre features will be disabled!")

    if MUSIXMATCH_API_KEY and MUSIXMATCH_AVAILABLE:
        logger.info("✅ Musixmatch API key found and library available.")
    else:
        logger.warning("⚠️ Musixmatch not available (no key or library missing).")

    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
    application.add_handler(CallbackQueryHandler(button_callback))

    logger.info("🤖 Bot is running with multi-source lyrics search...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
