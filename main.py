import os
import re
import json
import asyncio
import logging
import urllib.request
import urllib.error
from urllib.parse import urlencode
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

# ========== Optional Dependencies ==========
# BeautifulSoup (for Genius scraping)
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

# ---------- Tokens ----------
BOT_TOKEN = os.getenv('BOT_TOKEN')
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')
MUSIXMATCH_API_KEY = os.getenv('MUSIXMATCH_API_KEY')
GENIUS_TOKEN = os.getenv('GENIUS_TOKEN')  # Optional

# ---------- Logging ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- HTTP Helper (using urllib) ----------
def http_get(url, params=None, headers=None, timeout=10):
    """Send GET request using urllib and return parsed JSON or None."""
    if params:
        url = f"{url}?{urlencode(params)}"
    
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header('User-Agent', 'MusicBot/1.0 (Telegram Bot)')
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.getcode()
            if status != 200:
                logger.warning(f"HTTP {status} for {url}")
                return None
            data = response.read().decode('utf-8')
            return json.loads(data)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.info(f"HTTP 404 for {url}")
        else:
            logger.warning(f"HTTP error {e.code} for {url}")
        return None
    except Exception as e:
        logger.error(f"HTTP request failed: {e}")
        return None

def http_get_text(url, params=None, headers=None, timeout=10):
    """Send GET request and return raw text content."""
    if params:
        url = f"{url}?{urlencode(params)}"
    
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header('User-Agent', 'MusicBot/1.0 (Telegram Bot)')
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.getcode()
            if status != 200:
                return None
            return response.read().decode('utf-8')
    except Exception:
        return None

# ---------- Helper Functions ----------
def remove_timestamps(text):
    pattern = r'\[\d{2}:\d{2}(?:\.\d{2})?\]\s*'
    return re.sub(pattern, '', text)

def extract_metadata(file_path):
    """
    Extract artist and title from filename only.
    Since mutagen is not available in Pyodide, we rely on filename parsing.
    """
    filename = os.path.basename(file_path)
    # Remove extension
    name_part = os.path.splitext(filename)[0]
    
    artist = None
    title = None
    
    # Try to split by ' - ' (most common pattern: Artist - Title)
    if ' - ' in name_part:
        parts = name_part.rsplit(' - ', 1)
        artist = parts[0].strip()
        title = parts[1].strip()
    else:
        # If no separator, treat whole name as title
        title = name_part.strip()
    
    return artist, title

# ---------- Search on Last.fm ----------
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
        data = http_get(url, params=params)
        if not data:
            return None

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

# ---------- Get lyrics from multiple sources ----------
def get_lyrics_from_lyricsovh(song_name, artist_name):
    try:
        url = f"https://api.lyrics.ovh/v1/{artist_name.strip()}/{song_name.strip()}"
        data = http_get(url)
        if data and 'lyrics' in data:
            lyrics = data['lyrics']
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
        data = http_get(url, params=params)
        if data and 'plainLyrics' in data:
            lyrics = data['plainLyrics']
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
    """Get lyrics from Genius (if BeautifulSoup and token are available)."""
    if not BEAUTIFULSOUP_AVAILABLE:
        return None
    if not GENIUS_TOKEN:
        return None

    try:
        search_url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {GENIUS_TOKEN}"}
        params = {"q": f"{artist_name} {song_name}"}
        data = http_get(search_url, params=params, headers=headers)
        if not data:
            return None

        hits = data.get('response', {}).get('hits', [])
        if not hits:
            return None

        song_path = hits[0]['result']['path']
        song_url = f"https://genius.com{song_path}"

        page_html = http_get_text(song_url)
        if not page_html:
            return None

        soup = BeautifulSoup(page_html, 'html.parser')
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

# ---------- Get genres from Last.fm ----------
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
        data = http_get(url, params=params)
        if not data:
            return None

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
        "You can also send an audio file (MP3, etc.) and I'll search using its filename metadata."
    )

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    audio = update.message.audio or update.message.document
    if not audio:
        await update.message.reply_text("❌ No audio file found.")
        return

    file = await audio.get_file()
    file_extension = os.path.splitext(audio.file_name or '')[1] or '.mp3'
    file_path = f"temp_{audio.file_id}{file_extension}"
    await file.download_to_drive(file_path)

    try:
        artist, title = extract_metadata(file_path)
        if artist and title:
            query = f"{artist} - {title}"
            await update.message.reply_text(f"🎵 Extracted: {artist} - {title}\nSearching...")
            await search_and_show_results(update, context, query)
        else:
            await update.message.reply_text("❌ Could not extract artist and title. Please try searching manually.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def search_and_show_results(update, context, query):
    """Search and display results as inline buttons."""
    await update.message.reply_text("🔍 Searching...")

    if not query or not query.strip():
        await update.message.reply_text("❌ Please send a valid search query.")
        return

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
        logger.error(f"Search error: {e}")
        await update.message.reply_text("❌ Error occurred while searching.")
        return

    if not tracks:
        await update.message.reply_text(
            f"😞 No results found for '{query}'. Please try a different search."
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

    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        f"🎵 Found {len(tracks)} results for '{query}':\n"
        "Click on a song to get lyrics and genres:",
        reply_markup=reply_markup
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    await search_and_show_results(update, context, query)

# ---------- Callback Query Handler ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

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

            await query.edit_message_text(f"📥 Fetching lyrics for {song_name} by {artist_name}...")

            # Get lyrics (multi-source)
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
                logger.error(f"Lyrics error: {e}")

            # Get genres
            genre_list = None
            if artist_name and LASTFM_API_KEY:
                try:
                    genre_list = await asyncio.wait_for(get_genres(song_name, artist_name), timeout=15.0)
                except asyncio.CancelledError:
                    logger.info("Genres task was cancelled")
                except asyncio.TimeoutError:
                    logger.warning("Genres request timed out")
                except Exception as e:
                    logger.error(f"Genres error: {e}")

            # Build final message
            header = f"<b>🎤 {song_name}</b>\n"
            header += f"<b>👤 {artist_name}</b>\n\n"

            if genre_list:
                genre_lines = "\n".join([f"• {g}" for g in genre_list])
                genre_section = f"<b>🏷️ Genres:</b>\n<code>{genre_lines}</code>\n\n"
            elif LASTFM_API_KEY:
                genre_section = "<b>🏷️ Genres:</b> Not found\n\n"
            else:
                genre_section = "<b>🏷️ Genres:</b> Disabled\n\n"

            if lyrics:
                lyrics_block = f"<pre>{lyrics}</pre>"
            else:
                lyrics_block = "<b>📜 Lyrics:</b> Not found"

            full_message = header + genre_section + lyrics_block

            if len(full_message) > 4096:
                if lyrics and len(lyrics) > 2000:
                    truncated_lyrics = lyrics[:2000] + "\n\n... (continued)"
                    lyrics_block = f"<pre>{truncated_lyrics}</pre>"
                    full_message = header + genre_section + lyrics_block
                else:
                    full_message = header + genre_section + "<b>📜 Lyrics:</b> Too long to display."

            await query.message.reply_text(full_message, parse_mode='HTML')
            if lyrics:
                await query.message.reply_text("✅ You can copy lyrics by tapping on them above.")

        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            await query.edit_message_text("❌ An error occurred. Please try again.")

# ---------- Help Command ----------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 **Music Finder Bot**\n\n"
        "**How to use:**\n"
        "1. Send any search query (song name, artist, or part of lyrics)\n"
        "2. Or send an audio file (MP3, etc.) – metadata is extracted from filename\n"
        "3. Select from the results using buttons\n"
        "4. Get lyrics and genres for the chosen song\n\n"
        "**Examples:**\n"
        "• `Believer`\n"
        "• `Imagine Dragons`\n"
        "• `First things first`\n\n"
        "**Lyrics Sources (4):**\n"
        "• Lyrics.ovh\n"
        "• LRCLIB\n"
        "• Musixmatch (if key provided)\n"
        "• Genius (scraper)\n\n"
        "**Genres source:** Last.fm"
    )

# ---------- Main Execution ----------
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is not set!")
        return

    if not LASTFM_API_KEY:
        logger.warning("⚠️ LASTFM_API_KEY missing. Search and genre features will be disabled!")

    logger.info("✅ Active lyrics sources: Lyrics.ovh, LRCLIB")
    if MUSIXMATCH_API_KEY and MUSIXMATCH_AVAILABLE:
        logger.info("✅ Musixmatch source enabled.")
    else:
        logger.warning("⚠️ Musixmatch not enabled (no key or library missing).")

    if BEAUTIFULSOUP_AVAILABLE and GENIUS_TOKEN:
        logger.info("✅ Genius scraper enabled.")
    else:
        logger.warning("⚠️ Genius scraper disabled (BeautifulSoup or token missing).")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
    application.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL, handle_audio))
    application.add_handler(CallbackQueryHandler(button_callback))

    logger.info("🤖 Bot started successfully...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
