import os
import re
import asyncio
import logging
import requests
from bs4 import BeautifulSoup
from mutagen import File as MutagenFile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

# Try to import optional libraries
try:
    import musixmatch
    MUSIXMATCH_AVAILABLE = True
except ImportError:
    MUSIXMATCH_AVAILABLE = False
    logging.warning("musixmatch not installed. Install with: pip install musixmatch")

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
_session.headers.update({'User-Agent': 'MusicBot/1.0 (Telegram Bot)'})

# ---------- Helper: Remove timestamps ----------
def remove_timestamps(text):
    pattern = r'\[\d{2}:\d{2}(?:\.\d{2})?\]\s*'
    return re.sub(pattern, '', text)

# ---------- Helper: Extract metadata from audio file ----------
def extract_metadata(file_path):
    """Extract artist and title from audio file using mutagen."""
    try:
        audio = MutagenFile(file_path)
        if audio is None:
            return None, None

        # Try common tags
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

        # If not found, try filename
        if not artist or not title:
            filename = os.path.basename(file_path)
            # Try to parse "Artist - Title.mp3"
            if ' - ' in filename:
                parts = filename.rsplit(' - ', 1)
                artist = parts[0].strip()
                title = parts[1].split('.')[0].strip()
            else:
                # Use filename without extension as title
                title = os.path.splitext(filename)[0]

        return artist, title
    except Exception as e:
        logger.error(f"Metadata extraction error: {e}")
        return None, None

# ---------- Search tracks on Last.fm ----------
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

# ---------- Get lyrics from multiple sources (with fallback) ----------
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
    """Scrape lyrics from Genius website."""
    try:
        # Search on Genius
        search_url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {os.getenv('GENIUS_TOKEN', '')}"}
        if not os.getenv('GENIUS_TOKEN'):
            # If no token, use a fallback search via HTML
            # But we skip because we don't have a public token
            return None

        params = {"q": f"{artist_name} {song_name}"}
        response = _session.get(search_url, headers=headers, params=params, timeout=10)
        if not response.ok:
            return None

        data = response.json()
        hits = data.get('response', {}).get('hits', [])
        if not hits:
            return None

        # Find the best match (first hit)
        song_path = hits[0]['result']['path']
        song_url = f"https://genius.com{song_path}"

        # Get the page
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
    """Get lyrics from multiple sources with fallback."""
    if not song_name or not song_name.strip() or not artist_name or not artist_name.strip():
        return None

    # Define sources in order of preference
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
        "You can also send me an audio file (MP3, etc.) and I'll search using its metadata!"
    )

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle audio file uploads."""
    audio = update.message.audio or update.message.document
    if not audio:
        await update.message.reply_text("❌ No audio file found.")
        return

    # Download the file
    file = await audio.get_file()
    file_path = f"temp_{audio.file_id}.mp3"
    await file.download_to_drive(file_path)

    try:
        artist, title = extract_metadata(file_path)
        if artist and title:
            # Build search query
            query = f"{artist} - {title}"
            await update.message.reply_text(f"🎵 Found metadata: {artist} - {title}\nSearching...")
            # Reuse search logic
            await search_and_show_results(update, context, query)
        else:
            await update.message.reply_text("❌ Could not extract artist and title from the file. Please try searching manually.")
    finally:
        # Clean up temp file
        if os.path.exists(file_path):
            os.remove(file_path)

async def search_and_show_results(update, context, query):
    """Helper to search and display results."""
    await update.message.reply_text("🔍 Searching...")

    if not query or not query.strip():
        await update.message.reply_text("❌ Please send a valid search query.")
        return

    try:
        tracks = await asyncio.wait_for(search_tracks(query.strip()), timeout=15.0)
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
            except Exception as e:
                logger.error(f"Lyrics error: {e}")

            # Get genres
            genre_list = None
            if artist_name and LASTFM_API_KEY:
                try:
                    genre_list = await asyncio.wait_for(get_genres(song_name, artist_name), timeout=15.0)
                except Exception as e:
                    logger.error(f"Genres error: {e}")

            # Build response with separate handling
            header = f"<b>🎤 {song_name}</b>\n"
            header += f"<b>👤 {artist_name}</b>\n\n"

            # Genre section
            if genre_list:
                genre_lines = "\n".join([f"• {g}" for g in genre_list])
                genre_section = f"<b>🏷️ Genres:</b>\n<code>{genre_lines}</code>\n\n"
            elif LASTFM_API_KEY:
                genre_section = "<b>🏷️ Genres:</b> Not found\n\n"
            else:
                genre_section = "<b>🏷️ Genres:</b> Disabled\n\n"

            # Lyrics section
            if lyrics:
                lyrics_block = f"<pre>{lyrics}</pre>"
            else:
                lyrics_block = "<b>📜 Lyrics:</b> Not found"

            full_message = header + genre_section + lyrics_block

            # Truncate if needed
            if len(full_message) > 4096:
                # Remove some parts to fit
                if lyrics and len(lyrics) > 2000:
                    truncated_lyrics = lyrics[:2000] + "\n\n... (continued)"
                    lyrics_block = f"<pre>{truncated_lyrics}</pre>"
                    full_message = header + genre_section + lyrics_block

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
        "2. Or send an audio file (MP3, etc.)\n"
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

# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is missing!")
        return

    if not LASTFM_API_KEY:
        logger.warning("⚠️ LASTFM_API_KEY missing. Search and genre features will be disabled!")

    # Show active sources
    logger.info("✅ Active lyrics sources: Lyrics.ovh, LRCLIB, Genius (scraper)")
    if MUSIXMATCH_API_KEY and MUSIXMATCH_AVAILABLE:
        logger.info("✅ Musixmatch source enabled.")
    else:
        logger.warning("⚠️ Musixmatch not enabled (no key or library missing).")

    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
    application.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL, handle_audio))
    application.add_handler(CallbackQueryHandler(button_callback))

    logger.info("🤖 Bot is running with multi-source lyrics, audio support, and separate error handling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
