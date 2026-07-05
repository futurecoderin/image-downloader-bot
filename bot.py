import os
import shutil
import logging
from dotenv import load_dotenv
import telebot
import downloader
import sheets_logger

# Load env config
script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, "env.txt"))

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if not TOKEN:
    print("❌ ERROR: TELEGRAM_BOT_TOKEN is not set in env.txt!")
    import sys
    sys.exit(0)

bot = telebot.TeleBot(TOKEN)

# Temporary downloads directory
DOWNLOADS_DIR = os.path.join(script_dir, "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "📸 **Welcome to High-Quality Image Downloader Bot!** 🖼️\n\n"
        "Send me any web page link or direct image URL, and I will download and send you the highest quality version available.\n\n"
        "👉 **Examples of supported links:**\n"
        "• Pinterest, Imgur, Twitter/X, Instagram, Flickr\n"
        "• Direct image links (.jpg, .png, .webp)\n"
        "• Standard web blogs & news articles\n\n"
        "🔗 _Developed & maintained by_ [abhishekvigyan.com](https://abhishekvigyan.com)"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown", disable_web_page_preview=True)
    sheets_logger.log_image_downloader(message.from_user, "/start", "Opened Bot", "OK")

@bot.message_handler(func=lambda message: True)
def handle_download_request(message):
    url = message.text.strip()
    
    # Simple URL regex validation
    if not (url.startswith("http://") or url.startswith("https://")):
        bot.reply_to(message, "⚠️ Please send a valid website URL starting with `http://` or `https://`.")
        return

    logger.info(f"Processing download request from {message.from_user.id}: {url}")
    sheets_logger.log_image_downloader(message.from_user, "URL Received", url, "PENDING")

    # Send progress message
    status_msg = bot.reply_to(message, "⚡ _Analyzing link and fetching high-resolution image..._", parse_mode="Markdown")

    # Create unique session folder to prevent concurrent files mixup
    session_id = f"session_{message.message_id}_{message.from_user.id}"
    session_dir = os.path.join(DOWNLOADS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    downloaded_paths = []
    try:
        # Download images
        downloaded_paths = downloader.download_image(url, session_dir)
        
        if not downloaded_paths:
            raise ValueError("No download paths returned by downloader engine.")

        # Cap galleries to maximum 5 images to prevent spam/limits
        max_images = 5
        total_found = len(downloaded_paths)
        if total_found > max_images:
            bot.send_message(
                message.chat.id, 
                f"ℹ️ Found a gallery with {total_found} images. Sending the first {max_images} to prevent flooding..."
            )
            downloaded_paths = downloaded_paths[:max_images]

        # Send each image
        for idx, filepath in enumerate(downloaded_paths):
            if not os.path.exists(filepath):
                continue
                
            file_size = os.path.getsize(filepath)
            
            # File size check (50MB limit)
            if file_size > 50 * 1024 * 1024:
                bot.send_message(message.chat.id, f"⚠️ File {idx+1} is too large ({file_size / (1024*1024):.1f} MB) and exceeds Telegram's 50MB limit.")
                sheets_logger.log_image_downloader(message.from_user, "Size Check Fail", f"size={file_size}", "TOO_LARGE")
                continue

            caption = f"🖼️ Image {idx+1}/{len(downloaded_paths)} (High Quality)\n\n🔗 Developed by abhishekvigyan.com" if len(downloaded_paths) > 1 else "🖼️ High-Quality Image\n\n🔗 Developed by abhishekvigyan.com"

            # 1. Send as Photo
            try:
                with open(filepath, 'rb') as photo:
                    bot.send_photo(message.chat.id, photo, caption=caption)
            except Exception as pe:
                logger.error(f"Failed to send photo: {pe}")

        # Delete status loading message
        bot.delete_message(message.chat.id, status_msg.message_id)
        sheets_logger.log_image_downloader(message.from_user, "Download Success", f"count={len(downloaded_paths)}", "SUCCESS")

    except Exception as e:
        logger.error(f"Failed to process download: {e}")
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=f"❌ **Failed to download image from the URL.**\n\nCould not extract high-resolution image files. Please verify the URL and try again."
        )
        sheets_logger.log_image_downloader(message.from_user, "Download Error", str(e), "FAILED")

    finally:
        # Cleanup files from local session dir
        if os.path.exists(session_dir):
            try:
                shutil.rmtree(session_dir)
            except Exception as clean_err:
                logger.error(f"Failed cleaning directory {session_dir}: {clean_err}")

if __name__ == "__main__":
    logger.info("Starting Image Downloader Bot polling locally...")
    print("Bot is running in polling mode... Press Ctrl+C to stop.")
    try:
        bot.remove_webhook()
        
        # Set description
        try:
            bot.set_my_description(
                "📸 Download high-quality images from any web URL!\n\n"
                "Send any direct image link or website URL (Pinterest, Twitter, Flickr, etc.) to get the image.\n\n"
                "🔗 Developed & maintained by https://abhishekvigyan.com"
            )
            bot.set_my_short_description(
                "Download images from any URL. Developed by abhishekvigyan.com"
            )
            logger.info("Bot description updated successfully.")
        except Exception as _de:
            logger.warning(f"Could not set bot description: {_de}")
            
        bot.infinity_polling()
    except Exception as e:
        logger.error(f"Error occurred: {e}")
