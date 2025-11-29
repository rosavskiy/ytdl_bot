# YouTube Download Telegram Bot

A simple Telegram bot that downloads videos from YouTube and sends them directly to your chat.

## Features

- Download YouTube videos by simply sending a link
- Supports multiple YouTube URL formats:
  - `youtube.com/watch?v=...`
  - `youtu.be/...`
  - `youtube.com/shorts/...`
- Automatic quality adjustment for Telegram's 50MB file size limit
- Progress status updates during download

## Prerequisites

- Python 3.10 or higher
- A Telegram bot token (get one from [@BotFather](https://t.me/BotFather))
- FFmpeg (optional, for better format support)

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/rosavskiy/ytdl_bot.git
   cd ytdl_bot
   ```

2. Create a virtual environment (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Set up your Telegram bot token:
   ```bash
   export TELEGRAM_BOT_TOKEN="your_bot_token_here"
   ```

## Usage

1. Start the bot:
   ```bash
   python bot.py
   ```

2. Open your bot in Telegram

3. Send a YouTube link to download the video

## Commands

- `/start` - Welcome message and usage instructions
- `/help` - Display help information

## Limitations

- Maximum video size: 50MB (Telegram API limitation)
- Videos larger than 50MB will be downloaded in lower quality or rejected

## Dependencies

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Bot API wrapper
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - YouTube video downloader

## License

MIT License