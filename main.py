# -*- coding: utf-8 -*-
import logging
import os
import sys
import asyncio
import ffmpeg
import zipfile
import tarfile
import shutil
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from PIL import Image
import pytesseract
from typing import Final

# នាំចូល Library ថ្មី
try:
    from PyPDF2 import PdfReader, PdfWriter, PdfMerger
    from pdf2image import convert_from_path
    from pdf2docx import Converter
    import yt_dlp
except ImportError:
    print("!!! កំហុស៖ សូមប្រាកដថាបានតម្លើង Library ទាំងអស់")
    sys.exit(1)

# --- ការកំណត់តម្លៃសំខាន់ៗសម្រាប់ Cloud Run ---
BOT_TOKEN: Final = os.environ.get("BOT_TOKEN", "") 
MAX_FILE_SIZE: Final = 50 * 1024 * 1024

WEBHOOK_URL: Final = os.environ.get("CLOUD_RUN_URL", "") 
PORT: Final = int(os.environ.get("PORT", "8080")) 

# បន្ថែមស្ថានភាព (States) ថ្មី
(SELECT_ACTION,
 WAITING_PDF_TO_IMG_FORMAT, WAITING_PDF_TO_IMG_FILE,
 WAITING_FOR_MERGE, WAITING_FOR_SPLIT_FILE, WAITING_FOR_SPLIT_RANGE,
 WAITING_FOR_COMPRESS, WAITING_FOR_PDF_TO_WORD,
 WAITING_FOR_IMG_TO_PDF, WAITING_FOR_IMG_TO_TEXT_FILE,
 SELECT_AUDIO_OUTPUT_FORMAT, WAITING_FOR_AUDIO_FILE,
 SELECT_VIDEO_OUTPUT_FORMAT, WAITING_FOR_VIDEO_FILE,
 SELECT_ARCHIVE_ACTION, WAITING_FOR_FILES_TO_ZIP, WAITING_FOR_ARCHIVE_TO_EXTRACT,
 WAITING_FOR_MEDIA_URL
) = range(18)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# ==========================================
# អនុគមន៍ថ្មី: បំប្លែង PDF ទៅជា Word
# ==========================================
async def pdf_to_word_task(chat_id, file_path, msg, context):
    output_path = f"converted_{chat_id}.docx"
    try:
        cv = Converter(file_path)
        cv.convert(output_path)
        cv.close()
        await context.bot.edit_message_text("បំប្លែង PDF ទៅជា Word បានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Document.docx")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែង។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)
        if msg: 
            try: await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            except Exception: pass

# ==========================================
# អនុគមន៍ថ្មី: ទាញយកវីដេអូពី Social Media
# ==========================================
async def download_media_task(chat_id, url, msg, context):
    try:
        await context.bot.edit_message_text("កំពុងទាញយកទិន្នន័យពីតំណភ្ជាប់នេះ (អាចចំណាយពេលបន្តិច)...", chat_id=chat_id, message_id=msg.message_id)
        
        # កំណត់ជម្រើសសម្រាប់ yt-dlp (យកគុណភាពល្អបំផុត តែទំហំក្រោម 50MB)
        ydl_opts = {
            'outtmpl': f'downloaded_{chat_id}_%(title)s.%(ext)s',
            'format': 'best[filesize<50M]/best',
            'noplaylist': True,
            'quiet': True
        }
        
        # ការទាញយកនៅក្នុង Thread ផ្សេងដើម្បីកុំឱ្យ Block Bot
        def run_ytdlp():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        downloaded_file = await asyncio.to_thread(run_ytdlp)
        
        await context.bot.edit_message_text("ទាញយកជោគជ័យ! កំពុងផ្ញើឯកសារ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(downloaded_file, 'rb'))
        
        if os.path.exists(downloaded_file): os.remove(downloaded_file)
        
    except Exception as e:
        await context.bot.edit_message_text(f"មិនអាចទាញយកបានទេ (សូមប្រាកដថាវីដេអូមិន Private ឬលើស 50MB)។\nកំហុស: {str(e)[:100]}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if msg: 
            try: await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            except Exception: pass

# [អនុគមន៍ Background Tasks ចាស់ៗនៅរក្សាដដែល - ខ្ញុំកាត់បន្ថយការបង្ហាញទីនេះដើម្បីងាយស្រួលមើល ប៉ុន្តែបងត្រូវ copy កូដចាស់មកដាក់ត្រង់នេះ]
# -------------------------------------------------------------------------------------
# សូម Copy អនុគមន៍ pdf_to_img_task, merge_pdf_task, split_pdf_task, compress_pdf_task, 
# img_to_pdf_task, img_to_text_task, media_conversion_task, create_zip_task, extract_archive_task
# ពីកូដចាស់របស់បង យកមកដាក់ជំនួសចន្លោះនេះ។
# -------------------------------------------------------------------------------------

# ==========================================
# រៀបចំ Menu ប៊ូតុងនៅពេល /start
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        # ផ្នែក PDF
        [InlineKeyboardButton("📄 PDF ទៅជា រូបភាព", callback_data='pdf_to_img'),
         InlineKeyboardButton("📝 PDF ទៅជា Word", callback_data='pdf_to_word')],
        [InlineKeyboardButton("🖇️ បញ្ចូល PDF", callback_data='merge_pdf'),
         InlineKeyboardButton("✂️ បំបែក PDF", callback_data='split_pdf'),
         InlineKeyboardButton("📦 បន្ថយទំហំ PDF", callback_data='compress_pdf')],
        
        # ផ្នែក រូបភាព
        [InlineKeyboardButton("🖼️ រូបភាព ទៅជា PDF", callback_data='img_to_pdf'),
         InlineKeyboardButton("📖 ទាញអក្សរពីរូបភាព", callback_data='img_to_text')],
        
        # ផ្នែក Media
        [InlineKeyboardButton("🎵 បំប្លែងសម្លេង", callback_data='audio_converter'),
         InlineKeyboardButton("🎬 បំប្លែងវីដេអូ", callback_data='video_converter')],
        
        # ផ្នែកទាញយក និង Archive
        [InlineKeyboardButton("⬇️ ទាញយកវីដេអូ (Social Media)", callback_data='media_downloader')],
        [InlineKeyboardButton("🗜️ គ្រប់គ្រងឯកសារ Archive (ZIP/TAR)", callback_data='archive_manager')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = '👋 សួស្តី! ខ្ញុំជាជំនួយការឯកសាររបស់អ្នក។ សូមជ្រើសរើសមុខងារខាងក្រោម៖'
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SELECT_ACTION

# ==========================================
# Handler សម្រាប់មុខងារថ្មីទាំង ២
# ==========================================
async def start_pdf_to_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("✅ សូមផ្ញើឯកសារ PDF មួយដែលអ្នកចង់បំប្លែងទៅជា Word (DOCX)។\n(ទំហំមិនលើស 50MB)")
    return WAITING_FOR_PDF_TO_WORD

async def receive_pdf_for_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("❌ ឯកសារមានទំហំធំពេក។")
        return WAITING_FOR_PDF_TO_WORD
    
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបំប្លែងទៅជា Word...")
    
    asyncio.create_task(pdf_to_word_task(update.effective_chat.id, file_path, msg, context))
    return ConversationHandler.END

async def start_media_downloader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("✅ សូមផ្ញើតំណភ្ជាប់ (URL) នៃវីដេអូ (Youtube, TikTok, FB, IG...) មកឱ្យខ្ញុំ។\n\nចំណាំ៖ ខ្ញុំអាចទាញយកបានតែវីដេអូ Public ដែលមានទំហំក្រោម 50MB ប៉ុណ្ណោះ។")
    return WAITING_FOR_MEDIA_URL

async def receive_media_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text
    msg = await update.message.reply_text("✅ ទទួលបានតំណភ្ជាប់! កំពុងដំណើរការ...")
    asyncio.create_task(download_media_task(update.effective_chat.id, url, msg, context))
    return ConversationHandler.END

# [ដាក់ Handler ចាស់ៗ (start_pdf_to_img, start_merge, cancel... ត្រង់នេះដដែល]

# ==========================================
# ការរៀបចំប៊ូតុង Menu របស់ Telegram ស្វ័យប្រវត្តិ
# ==========================================
async def setup_commands(application: Application):
    commands = [
        BotCommand("start", "🏠 បើកផ្ទាំងបញ្ជាដើម (Main Menu)"),
        BotCommand("help", "ℹ️ ជំនួយ និងរបៀបប្រើប្រាស់"),
        BotCommand("cancel", "❌ បោះបង់ប្រតិបត្តិការបច្ចុប្បន្ន")
    ]
    await application.bot.set_my_commands(commands)
    logging.info("បានដំឡើងប៊ូតុង Menu រួចរាល់!")

# --- Main Application Runner ---
def main() -> None:
    if not BOT_TOKEN or not WEBHOOK_URL:
        print("!!! កំហុស៖ BOT_TOKEN ឬ CLOUD_RUN_URL មិនត្រូវបានកំណត់។")
        sys.exit(1)

    # បន្ថែម post_init ចូលក្នុង Builder ដើម្បី Setup Menu ស្វ័យប្រវត្តិ
    application = Application.builder().token(BOT_TOKEN).read_timeout(30).post_init(setup_commands).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [
                # មុខងារចាស់
                CallbackQueryHandler(start_pdf_to_img, pattern='^pdf_to_img$'),
                # ... (ដាក់ CallbackQueryHandler ចាស់ៗទាំងអស់មកវិញ) ...
                
                # មុខងារថ្មី
                CallbackQueryHandler(start_pdf_to_word, pattern='^pdf_to_word$'),
                CallbackQueryHandler(start_media_downloader, pattern='^media_downloader$'),
            ],
            # ... (ដាក់ MessageHandler ចាស់ៗទាំងអស់មកវិញ) ...
            
            WAITING_FOR_PDF_TO_WORD: [MessageHandler(filters.Document.PDF, receive_pdf_for_word)],
            WAITING_FOR_MEDIA_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_media_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    # ... (បន្តកូដ Webhook ដូចចាស់របស់បង) ...
    FULL_WEBHOOK_URL = f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}"
    application.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=FULL_WEBHOOK_URL)

if __name__ == "__main__":
    main()
