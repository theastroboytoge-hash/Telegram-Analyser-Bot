import os
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

# ---------- Get Lyrics from Genius ----------
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
        logger.error(f"Genius Error: {e}")
        return None

# ---------- Get Genres from Last.fm ----------
def get_genres(song_name, artist_name):
    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            'method': 'track.getInfo',
            'api_key': LASTFM_API_KEY,
            'artist': artist_name,
            'track': song_name,
            'format': 'json'
        }
        response = requests.get(url, params=params)
        data = response.json()
        
        if 'track' in data and 'toptags' in data['track']:
            tags = data['track']['toptags'].get('tag', [])
            if tags:
                # Get top 5 genres
                genre_list = [tag['name'] for tag in tags[:5]]
                return genre_list
        return None
    except Exception as e:
        logger.error(f"Last.fm Error: {e}")
        return None

# ---------- Bot Commands (Async) ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 Hello! I'm a music finder bot.\n"
        "Send me a song name (with artist if possible) and I'll find the lyrics and genres.\n\n"
        "Examples:\n"
        "`Imagine Dragons - Believer`\n"
        "`Bohemian Rhapsody` (will search without artist)"
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await update.message.reply_text("🔍 Searching...")

    # Detect artist and song (separated by ' - ')
    if ' - ' in user_input:
        parts = user_input.split(' - ', 1)
        artist = parts[0].strip()
        song = parts[1].strip()
    else:
        artist = None
        song = user_input.strip()

    # Get lyrics
    lyrics = get_lyrics(song, artist)
    if not lyrics and artist:
        lyrics = get_lyrics(song)  # Retry without artist

    # Get genres from Last.fm
    genre_text = "No artist specified"
    if artist:
        genres = get_genres(song, artist)
        if genres:
            genre_text = ", ".join(genres)
        else:
            genre_text = "Not found"

    # Build final response
    if lyrics:
        if len(lyrics) > 4000:
            lyrics = lyrics[:4000] + "\n\n... (continued)"
        
        response = f"🎤 **{song}**\n"
        if artist:
            response += f"👤 {artist}\n"
        response += f"🏷️ Genres: {genre_text}\n\n"
        response += f"📜 **Lyrics:**\n{lyrics}"
    else:
        response = f"😞 Song `{song}` not found. Please try with a more specific name or include the artist."

    await update.message.reply_text(response)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a song name in this format:\n"
        "`Artist - Song Name`\n\n"
        "Or just send the song name alone."
    )

# ---------- Main Execution ----------
def main():
    # Check if all tokens are set
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is not set!")
        return
    if not GENIUS_TOKEN:
        logger.error("❌ GENIUS_TOKEN is not set!")
        return
    if not LASTFM_API_KEY:
        logger.error("❌ LASTFM_API_KEY is not set!")
        return

    # Create Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))

    # Start polling
    logger.info("🤖 Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
