"""
YouTube Download Telegram Bot

A Telegram bot that downloads videos from YouTube and sends them to users.
"""

import logging
import os
import re
import tempfile
import shutil
import asyncio
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from aiohttp import web

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
import yt_dlp

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# YouTube URL regex pattern (video IDs can contain alphanumeric, underscore, and hyphen)
YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w_-]+"
)

# Maximum file size for Telegram (50 MB) - we'll warn but still try to send
MAX_FILE_SIZE = 50 * 1024 * 1024

# File storage configuration
STORAGE_DIR = Path("downloads")
STORAGE_DIR.mkdir(exist_ok=True)

# Server configuration (change for production VPS)
SERVER_HOST = os.environ.get("SERVER_HOST", "localhost")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
SERVER_URL = os.environ.get("SERVER_URL", f"http://{SERVER_HOST}:{SERVER_PORT}")

# File metadata storage: {file_id: {path, created_at, downloaded}}
file_storage = {}

# Thread pool for blocking operations
executor = ThreadPoolExecutor(max_workers=3)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "👋 Добро пожаловать в YouTube Download Bot!\n\n"
        "Отправьте мне ссылку на YouTube и я скачаю видео для вас.\n\n"
        "Поддерживаемые форматы:\n"
        "• youtube.com/watch?v=...\n"
        "• youtu.be/...\n"
        "• youtube.com/shorts/...\n\n"
        "Команды:\n"
        "/start - Показать это сообщение\n"
        "/help - Показать справку"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "📖 Как использовать этот бот:\n\n"
        "1. Скопируйте ссылку на YouTube видео\n"
        "2. Вставьте её сюда\n"
        "3. Дождитесь завершения загрузки\n\n"
        "Примечание: Из-за ограничений Telegram, видео больше 50МБ "
        "будут загружены в низком качестве."
    )


def extract_youtube_url(text: str) -> str | None:
    """Extract YouTube URL from text."""
    match = YOUTUBE_REGEX.search(text)
    return match.group(0) if match else None


async def cleanup_old_files():
    """Remove files older than 24 hours or already downloaded."""
    while True:
        try:
            now = datetime.now()
            to_delete = []
            
            for file_id, metadata in file_storage.items():
                age = now - metadata['created_at']
                # Delete if older than 24 hours or already downloaded
                if age > timedelta(hours=24) or metadata.get('downloaded', False):
                    file_path = metadata['path']
                    if file_path.exists():
                        file_path.unlink()
                        logger.info(f"Deleted file: {file_path.name}")
                    to_delete.append(file_id)
            
            for file_id in to_delete:
                del file_storage[file_id]
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        
        # Run cleanup every hour
        await asyncio.sleep(3600)


async def download_handler(request):
    """Handle file download requests."""
    file_id = request.match_info.get('file_id')
    
    if file_id not in file_storage:
        return web.Response(text="File not found or expired", status=404)
    
    metadata = file_storage[file_id]
    file_path = metadata['path']
    
    if not file_path.exists():
        del file_storage[file_id]
        return web.Response(text="File not found", status=404)
    
    # Mark as downloaded
    metadata['downloaded'] = True
    
    # Serve file
    return web.FileResponse(
        path=file_path,
        headers={
            'Content-Disposition': f'attachment; filename="{file_path.name}"'
        }
    )


async def start_file_server(app):
    """Start HTTP server for file downloads."""
    file_app = web.Application()
    file_app.router.add_get('/download/{file_id}', download_handler)
    
    runner = web.AppRunner(file_app)
    await runner.setup()
    site = web.TCPSite(runner, SERVER_HOST, SERVER_PORT)
    await site.start()
    logger.info(f"File server started at {SERVER_URL}")


async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show quality selection buttons for YouTube video."""
    message_text = update.message.text
    youtube_url = extract_youtube_url(message_text)

    if not youtube_url:
        return

    # Ensure URL has protocol
    if not youtube_url.startswith("http"):
        youtube_url = "https://" + youtube_url

    # Store URL in user context
    context.user_data['youtube_url'] = youtube_url

    # Create inline keyboard with quality options
    keyboard = [
        [
            InlineKeyboardButton("🎬 HD качество", callback_data="quality_hd"),
            InlineKeyboardButton("📺 SD качество", callback_data="quality_sd"),
        ],
        [
            InlineKeyboardButton("🎵 Только аудио", callback_data="quality_audio"),
        ],
        [
            InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🎯 Выберите качество для загрузки:",
        reply_markup=reply_markup
    )


async def handle_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quality selection button callbacks."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Загрузка отменена.")
        return

    youtube_url = context.user_data.get('youtube_url')
    if not youtube_url:
        await query.edit_message_text("❌ Ошибка: URL не найден. Отправьте ссылку снова.")
        return

    # Determine quality format based on selection
    quality_formats = {
        "quality_hd": "best[height<=1080]/best",
        "quality_sd": "best[height<=480]/worst",
        "quality_audio": "bestaudio/best",
    }
    
    selected_format = quality_formats.get(query.data, "best")
    quality_name = {
        "quality_hd": "HD",
        "quality_sd": "SD",
        "quality_audio": "аудио"
    }.get(query.data, "стандарт")

    status_message = await query.edit_message_text(f"⏳ Загружаю в качестве {quality_name}...")

    # Progress tracking variables
    context.user_data['download_progress'] = {
        'percent': 0,
        'status': 'downloading',
        'last_update': datetime.now()
    }

    def progress_hook(d):
        """Hook to track download progress."""
        if d['status'] == 'downloading':
            if 'total_bytes' in d:
                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
                context.user_data['download_progress']['percent'] = percent
            elif 'total_bytes_estimate' in d:
                percent = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                context.user_data['download_progress']['percent'] = percent
        elif d['status'] == 'finished':
            context.user_data['download_progress']['status'] = 'finished'

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # First, get video info
            ydl_opts_info = {
                "quiet": True,
                "no_warnings": True,
                "cookiefile": None,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            }

            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                await status_message.edit_text("🔍 Получаю информацию о видео...")
                info = ydl.extract_info(youtube_url, download=False)
                video_title = info.get("title", "video")
                duration = info.get("duration", 0)
                description = info.get("description", "")
                tags = info.get("tags", [])
                uploader = info.get("uploader", "")
                view_count = info.get("view_count", 0)

            # Determine format strategy based on FFmpeg availability
            ffmpeg_available = shutil.which("ffmpeg") is not None

            # Always prefer MP4 format for Telegram compatibility
            # Progressive MP4 formats (no merging) to avoid FFmpeg requirement
            progressive_mp4 = (
                "best[ext=mp4][vcodec!=none][acodec!=none]"
                "[protocol!=m3u8][protocol!=dash]/"
                "best[ext=mp4][protocol!=m3u8][protocol!=dash]/"
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "best"
            )

            # Apply quality preference
            if query.data == "quality_audio":
                chosen_format = "bestaudio[ext=m4a]/bestaudio/best"
            elif query.data == "quality_hd":
                chosen_format = (
                    "best[ext=mp4][height<=1080]/"
                    "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/"
                    + progressive_mp4
                )
            else:  # SD quality
                chosen_format = (
                    "best[ext=mp4][height<=480]/"
                    "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/"
                    "worst[ext=mp4]/worst"
                )

            # Download options for video (prefer formats under 50MB)
            output_path = Path(temp_dir) / "%(title)s.%(ext)s"
            ydl_opts = {
                "format": chosen_format,
                "outtmpl": str(output_path),
                "quiet": True,
                "no_warnings": True,
                "cookiefile": None,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
                "progress_hooks": [progress_hook],
            }
            
            # If FFmpeg available, convert to MP4 for better Telegram compatibility
            if ffmpeg_available and query.data != "quality_audio":
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }]
                ydl_opts["merge_output_format"] = "mp4"

            # Start progress updater task
            async def update_progress():
                """Update progress message every 5 seconds."""
                last_percent = -1
                while context.user_data['download_progress']['status'] == 'downloading':
                    await asyncio.sleep(5)
                    if context.user_data['download_progress']['status'] == 'downloading':
                        percent = context.user_data['download_progress']['percent']
                        if percent > 0 and abs(percent - last_percent) > 1:  # Update only if changed
                            last_percent = percent
                            bar_length = 20
                            filled = int(bar_length * percent / 100)
                            bar = '█' * filled + '░' * (bar_length - filled)
                            try:
                                await status_message.edit_text(
                                    f"⬇️ Загружаю: {video_title}\n\n"
                                    f"{bar} {percent:.1f}%"
                                )
                            except Exception as e:
                                logger.debug(f"Progress update error: {e}")

            progress_task = asyncio.create_task(update_progress())

            # Run download in thread pool to not block async loop
            loop = asyncio.get_event_loop()
            
            def download_sync():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([youtube_url])
            
            await status_message.edit_text(f"⬇️ Загружаю: {video_title}...")
            await loop.run_in_executor(executor, download_sync)

            # Stop progress updater
            context.user_data['download_progress']['status'] = 'finished'
            await progress_task

            # Find downloaded file
            downloaded_files = list(Path(temp_dir).glob("*"))
            if not downloaded_files:
                await status_message.edit_text("❌ Ошибка загрузки. Попробуйте снова.")
                return

            video_file = downloaded_files[0]
            file_size = video_file.stat().st_size

            # Check file size - Telegram bot API has strict 50MB limit
            if file_size > MAX_FILE_SIZE:
                size_mb = file_size / (1024*1024)
                
                # Generate unique file ID and move to storage
                file_id = str(uuid.uuid4())
                stored_filename = f"{file_id}_{video_file.name}"
                stored_path = STORAGE_DIR / stored_filename
                
                # Copy file to storage
                shutil.copy2(video_file, stored_path)
                
                # Store metadata
                file_storage[file_id] = {
                    'path': stored_path,
                    'created_at': datetime.now(),
                    'downloaded': False,
                    'original_name': video_file.name
                }
                
                download_url = f"{SERVER_URL}/download/{file_id}"
                
                await status_message.edit_text(
                    f"📦 Видео готово к скачиванию ({size_mb:.1f}МБ)\n\n"
                    f"⚠️ Файл слишком большой для отправки через Telegram (лимит 50МБ).\n\n"
                    f"🔗 Скачать: {download_url}\n\n"
                    f"ℹ️ Файл будет доступен 24 часа и удалится после первого скачивания."
                )
                return

            await status_message.edit_text("📤 Отправляю в Telegram...")

            # Prepare caption with metadata
            caption_parts = [f"📹 {video_title}"]
            
            if uploader:
                caption_parts.append(f"\n👤 {uploader}")
            
            if view_count:
                caption_parts.append(f"\n👁 {view_count:,} просмотров")
            
            if duration:
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                caption_parts.append(f"\n⏱ {minutes}:{seconds:02d}")
            
            # Add description (limited to avoid Telegram caption limit)
            if description:
                # Telegram caption limit is 1024 characters
                desc_preview = description[:200].strip()
                if len(description) > 200:
                    desc_preview += "..."
                caption_parts.append(f"\n\n📝 {desc_preview}")
            
            # Add tags (limited)
            if tags:
                tags_text = " #" + " #".join(tags[:5])  # First 5 tags
                # Ensure total caption doesn't exceed limit
                full_caption = "".join(caption_parts) + f"\n\n{tags_text}"
                if len(full_caption) <= 1024:
                    caption_parts.append(f"\n\n{tags_text}")
            
            caption = "".join(caption_parts)

            # Send as audio if audio-only was selected
            if query.data == "quality_audio":
                with open(video_file, "rb") as f:
                    await query.message.reply_audio(
                        audio=f,
                        caption=caption,
                        read_timeout=60,
                        write_timeout=60,
                        connect_timeout=60,
                    )
            else:
                with open(video_file, "rb") as f:
                    await query.message.reply_video(
                        video=f,
                        caption=caption,
                        supports_streaming=True,
                        read_timeout=60,
                        write_timeout=60,
                        connect_timeout=60,
                    )

            await status_message.delete()

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        await status_message.edit_text(
            "❌ Не удалось загрузить видео. Проверьте правильность ссылки "
            "и доступность видео."
        )
    except TimeoutError as e:
        logger.error(f"Timeout error: {e}")
        # Don't edit message - video might still be uploading
        logger.info("Video upload may still complete despite timeout")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await status_message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже."
        )


def main() -> None:
    """Start the bot."""
    # Load environment variables from .env file
    load_dotenv()
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN environment variable is not set. "
            "Please set it to your Telegram bot token from @BotFather."
        )

    application = Application.builder().token(token).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(handle_quality_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, download_video)
    )

    # Start cleanup task
    loop = asyncio.get_event_loop()
    loop.create_task(cleanup_old_files())
    
    # Start file server
    loop.create_task(start_file_server(application))

    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
