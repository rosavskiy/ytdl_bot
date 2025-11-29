"""
YouTube Download Telegram Bot

A Telegram bot that downloads videos from YouTube and sends them to users.
"""

import logging
import os
import re
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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

# Maximum file size for Telegram (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "👋 Welcome to YouTube Download Bot!\n\n"
        "Send me a YouTube link and I'll download the video for you.\n\n"
        "Supported formats:\n"
        "• youtube.com/watch?v=...\n"
        "• youtu.be/...\n"
        "• youtube.com/shorts/...\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/help - Show help information"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "📖 How to use this bot:\n\n"
        "1. Copy a YouTube video link\n"
        "2. Paste it here\n"
        "3. Wait for the download to complete\n\n"
        "Note: Due to Telegram limitations, videos larger than 50MB "
        "will be downloaded in lower quality or as audio only."
    )


def extract_youtube_url(text: str) -> str | None:
    """Extract YouTube URL from text."""
    match = YOUTUBE_REGEX.search(text)
    return match.group(0) if match else None


async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download and send YouTube video."""
    message_text = update.message.text
    youtube_url = extract_youtube_url(message_text)

    if not youtube_url:
        return

    # Ensure URL has protocol
    if not youtube_url.startswith("http"):
        youtube_url = "https://" + youtube_url

    status_message = await update.message.reply_text("⏳ Processing your request...")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # First, get video info
            ydl_opts_info = {
                "quiet": True,
                "no_warnings": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                await status_message.edit_text("🔍 Fetching video information...")
                info = ydl.extract_info(youtube_url, download=False)
                video_title = info.get("title", "video")
                duration = info.get("duration", 0)

            # Download options for video (prefer formats under 50MB)
            output_path = Path(temp_dir) / "%(title)s.%(ext)s"
            ydl_opts = {
                "format": "best[filesize<50M]/best[height<=480]/worst",
                "outtmpl": str(output_path),
                "quiet": True,
                "no_warnings": True,
            }

            await status_message.edit_text(f"⬇️ Downloading: {video_title}...")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])

            # Find downloaded file
            downloaded_files = list(Path(temp_dir).glob("*"))
            if not downloaded_files:
                await status_message.edit_text("❌ Download failed. Please try again.")
                return

            video_file = downloaded_files[0]
            file_size = video_file.stat().st_size

            if file_size > MAX_FILE_SIZE:
                await status_message.edit_text(
                    f"⚠️ Video is too large ({file_size / (1024*1024):.1f}MB). "
                    "Attempting to download in lower quality..."
                )
                
                # Remove the large file before retry
                video_file.unlink()
                
                # Try to download in lower quality
                ydl_opts["format"] = "worst"
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([youtube_url])
                
                downloaded_files = list(Path(temp_dir).glob("*"))
                if not downloaded_files:
                    await status_message.edit_text("❌ Download failed. Please try again.")
                    return
                    
                video_file = downloaded_files[0]
                file_size = video_file.stat().st_size
                
                if file_size > MAX_FILE_SIZE:
                    await status_message.edit_text(
                        "❌ Video is too large to send via Telegram (max 50MB). "
                        "Please try a shorter video."
                    )
                    return

            await status_message.edit_text("📤 Uploading to Telegram...")

            with open(video_file, "rb") as f:
                await update.message.reply_video(
                    video=f,
                    caption=f"📹 {video_title}",
                    supports_streaming=True,
                )

            await status_message.delete()

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        await status_message.edit_text(
            "❌ Failed to download video. Please check if the URL is valid "
            "and the video is available."
        )
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await status_message.edit_text(
            "❌ An error occurred. Please try again later."
        )


def main() -> None:
    """Start the bot."""
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
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, download_video)
    )

    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
