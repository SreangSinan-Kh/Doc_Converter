# -*- coding: utf-8 -*-
import logging
import os
import sys
import asyncio
import ffmpeg
import zipfile
import tarfile
import shutil
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
from PIL import Image
import pytesseract
from typing import Final

# នាំចូល Library មូលដ្ឋាន
try:
    from PyPDF2 import PdfReader, PdfWriter, PdfMerger
    from pdf2image import convert_from_path
    from pdf2docx import Converter
    import yt_dlp
    from gtts import gTTS
    from deep_translator import GoogleTranslator
except ImportError:
    print("!!! កំហុស៖ សូមប្រាកដថាបានតម្លើង Library ទាំងអស់")
    sys.exit(1)

BOT_TOKEN: Final = os.environ.get("BOT_TOKEN", "") 
MAX_FILE_SIZE: Final = 50 * 1024 * 1024 # 50 MB
WEBHOOK_URL: Final = os.environ.get("CLOUD_RUN_URL", "") 
PORT: Final = int(os.environ.get("PORT", "8080")) 

# កំណត់ 'ស្ថានភាព' (States) ទាំងអស់
(SELECT_ACTION,
 WAITING_PDF_TO_IMG_FORMAT, WAITING_PDF_TO_IMG_FILE,
 WAITING_FOR_MERGE, WAITING_FOR_SPLIT_FILE, WAITING_FOR_SPLIT_RANGE,
 WAITING_FOR_COMPRESS, WAITING_FOR_PDF_TO_WORD,
 WAITING_FOR_IMG_TO_PDF, WAITING_FOR_IMG_TO_TEXT_FILE,
 SELECT_AUDIO_OUTPUT_FORMAT, WAITING_FOR_AUDIO_FILE,
 SELECT_VIDEO_OUTPUT_FORMAT, WAITING_FOR_VIDEO_FILE,
 SELECT_ARCHIVE_ACTION, WAITING_FOR_FILES_TO_ZIP, WAITING_FOR_ARCHIVE_TO_EXTRACT,
 WAITING_FOR_MEDIA_URL,
 WAITING_FOR_TTS, WAITING_FOR_TRANSLATE, WAITING_FOR_REMOVE_BG
) = range(21)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# ==========================================
# អនុគមន៍បំពេញការងារ (Background Tasks)
# ==========================================
async def text_to_speech_task(chat_id, text, msg, context):
    out_file = f"audio_narration_{chat_id}.mp3"
    try:
        await context.bot.edit_message_text("កំពុងបង្កើតសំឡេងអាន (សូមរង់ចាំ)... 🎙️", chat_id=chat_id, message_id=msg.message_id)
        def create_audio():
            tts = gTTS(text=text, lang='km')
            tts.save(out_file)
        await asyncio.to_thread(create_audio)
        await context.bot.edit_message_text("បង្កើតសំឡេងបានជោគជ័យ! កំពុងផ្ញើ... ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_audio(chat_id=chat_id, audio=open(out_file, 'rb'), title="Audio Narration")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបង្កើតសំឡេង។\nកំហុស: {str(e)[:100]}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(out_file): os.remove(out_file)

async def translate_text_task(chat_id, text, msg, context):
    try:
        await context.bot.edit_message_text("កំពុងបកប្រែអត្ថបទ... 🔄", chat_id=chat_id, message_id=msg.message_id)
        translated = await asyncio.to_thread(GoogleTranslator(source='auto', target='km').translate, text)
        await context.bot.edit_message_text("បកប្រែជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_message(chat_id=chat_id, text=f"**អត្ថបទបកប្រែជាភាសាខ្មែរ៖**\n\n{translated}", parse_mode='Markdown')
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបកប្រែ។\nកំហុស: {str(e)[:100]}", chat_id=chat_id, message_id=msg.message_id)

async def remove_bg_task(chat_id, file_path, msg, context):
    out_file = f"nobg_{chat_id}.png"
    try:
        await context.bot.edit_message_text("កំពុងលុបផ្ទៃខាងក្រោយដោយប្រើ AI (អាចប្រើពេលបន្តិច)... ✂️", chat_id=chat_id, message_id=msg.message_id)
        from rembg import remove
        def process_image():
            with open(file_path, 'rb') as i:
                input_data = i.read()
            output_data = remove(input_data)
            with open(out_file, 'wb') as o:
                o.write(output_data)
        await asyncio.to_thread(process_image)
        await context.bot.edit_message_text("លុបផ្ទៃខាងក្រោយបានជោគជ័យ! កំពុងផ្ញើ... ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(out_file, 'rb'), filename="Removed_Background.png")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការលុបផ្ទៃខាងក្រោយ។\nកំហុស: {str(e)[:100]}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(out_file): os.remove(out_file)

async def pdf_to_word_task(chat_id, file_path, msg, context):
    output_path = f"converted_{chat_id}.docx"
    try:
        cv = Converter(file_path)
        cv.convert(output_path)
        cv.close()
        await context.bot.edit_message_text("បំប្លែង PDF ទៅជា Word បានជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Document.docx")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែង។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def download_media_task(chat_id, url, msg, context):
    try:
        await context.bot.edit_message_text("កំពុងទាញយកទិន្នន័យពីតំណភ្ជាប់នេះ (អាចចំណាយពេលបន្តិច)... ⬇️", chat_id=chat_id, message_id=msg.message_id)
        ydl_opts = {
            'outtmpl': f'downloaded_{chat_id}_%(title)s.%(ext)s',
            'format': 'best[filesize<50M]/best',
            'noplaylist': True,
            'quiet': True
        }
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'

        def run_ytdlp():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        downloaded_file = await asyncio.to_thread(run_ytdlp)
        await context.bot.edit_message_text("ទាញយកជោគជ័យ! កំពុងផ្ញើឯកសារ... ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(downloaded_file, 'rb'))
        if os.path.exists(downloaded_file): os.remove(downloaded_file)
    except Exception as e:
        await context.bot.edit_message_text(f"មិនអាចទាញយកបានទេ (សូមប្រាកដថាវីដេអូមិន Private ឬលើស 50MB)។\nកំហុស: {str(e)[:100]}", chat_id=chat_id, message_id=msg.message_id)

async def pdf_to_img_task(chat_id, file_path, msg, context, fmt):
    try:
        images = convert_from_path(file_path, dpi=200, fmt=fmt)
        await context.bot.edit_message_text(f"បំប្លែងបាន {len(images)} ទំព័រ។ កំពុងផ្ញើរូបភាព... ✅", chat_id=chat_id, message_id=msg.message_id)
        for i, image in enumerate(images):
            out_path = f"page_{i+1}_{chat_id}.{fmt}"
            image.save(out_path, fmt.upper())
            await context.bot.send_photo(chat_id=chat_id, photo=open(out_path, 'rb'))
            os.remove(out_path)
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែង PDF ទៅជារូបភាព។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def merge_pdf_task(chat_id, file_paths, msg, context):
    output_path = f"merged_{chat_id}.pdf"
    try:
        merger = PdfMerger()
        for path in file_paths: merger.append(path)
        merger.write(output_path)
        merger.close()
        await context.bot.edit_message_text("បញ្ចូលឯកសារបានជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Merged.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបញ្ចូលឯកសារ។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def split_pdf_task(chat_id, file_path, page_range_str, msg, context):
    output_path = f"split_{chat_id}.pdf"
    try:
        writer = PdfWriter()
        reader = PdfReader(file_path)
        pages_to_extract = set()
        parts = page_range_str.split(',')
        for part in parts:
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                for i in range(start, end + 1): pages_to_extract.add(i-1)
            else:
                pages_to_extract.add(int(part)-1)
        for i in sorted(list(pages_to_extract)):
            if 0 <= i < len(reader.pages): writer.add_page(reader.pages[i])
        if not writer.pages: raise ValueError("ទំព័រមិនត្រឹមត្រូវ")
        writer.write(output_path)
        await context.bot.edit_message_text("បំបែកឯកសារបានជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Split.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំបែក។\nសូមប្រាកដថាទម្រង់លេខទំព័រត្រឹមត្រូវ។", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def compress_pdf_task(chat_id, file_path, msg, context):
    output_path = f"compressed_{chat_id}.pdf"
    try:
        reader = PdfReader(file_path)
        writer = PdfWriter()
        for page in reader.pages:
            page.compress_content_streams()
            writer.add_page(page)
        with open(output_path, "wb") as f: writer.write(f)
        await context.bot.edit_message_text("បន្ថយទំហំឯកសារបានជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Compressed.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបន្ថយទំហំឯកសារ។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def img_to_pdf_task(chat_id, file_paths, msg, context):
    output_path = f"converted_from_img_{chat_id}.pdf"
    try:
        image_list = [Image.open(path).convert('RGB') for path in file_paths]
        image_list[0].save(output_path, "PDF", resolution=100.0, save_all=True, append_images=image_list[1:])
        await context.bot.edit_message_text("បំប្លែងរូបភាពទៅជា PDF បានជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Image_to_PDF.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def img_to_text_task(chat_id, file_path, msg, context):
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image, lang='khm+eng')
        await context.bot.edit_message_text("បំប្លែងរូបភាពទៅជាអក្សរបានជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        if not text.strip():
            await context.bot.send_message(chat_id=chat_id, text="មិនអាចរកឃើញអក្សរនៅក្នុងរូបភាពនេះទេ ឬរូបភាពគ្មានគុណភាពល្អ។")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"**លទ្ធផលដែលបានបំប្លែង៖**\n\n```\n{text}\n```", parse_mode='Markdown')
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែងរូបភាពទៅជាអក្សរ។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def media_conversion_task(chat_id, file_path, output_format, msg, context, media_type='audio'):
    output_path = f"converted_{chat_id}.{output_format}"
    try:
        await context.bot.edit_message_text(f"កំពុងបំប្លែងទៅជា {output_format.upper()}...", chat_id=chat_id, message_id=msg.message_id)
        ffmpeg.input(file_path).output(output_path, preset='ultrafast', threads=2).run(overwrite_output=True)
        await context.bot.edit_message_text("បំប្លែងបានជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        if media_type == 'audio':
            await context.bot.send_audio(chat_id=chat_id, audio=open(output_path, 'rb'))
        else:
            await context.bot.send_video(chat_id=chat_id, video=open(output_path, 'rb'))
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែង។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def create_zip_task(chat_id, file_paths, msg, context):
    output_path = f"archive_{chat_id}.zip"
    try:
        await context.bot.edit_message_text("កំពុងបង្កើតឯកសារ ZIP...", chat_id=chat_id, message_id=msg.message_id)
        with zipfile.ZipFile(output_path, 'w') as zipf:
            for file_path in file_paths:
                zipf.write(file_path, os.path.basename(file_path))
        await context.bot.edit_message_text("បង្កើតឯកសារ ZIP បានជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="archive.zip")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការបង្កើត ZIP: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def extract_archive_task(chat_id, file_path, msg, context):
    extract_dir = f"extracted_{chat_id}"
    try:
        await context.bot.edit_message_text("កំពុងពន្លាឯកសារ...", chat_id=chat_id, message_id=msg.message_id)
        os.makedirs(extract_dir, exist_ok=True)
        if file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref: zip_ref.extractall(extract_dir)
        elif file_path.endswith('.tar.gz') or file_path.endswith('.tgz'):
            with tarfile.open(file_path, 'r:gz') as tar_ref: tar_ref.extractall(extract_dir)
        elif file_path.endswith('.tar'):
            with tarfile.open(file_path, 'r:') as tar_ref: tar_ref.extractall(extract_dir)
        else:
            raise ValueError("មិនគាំទ្រទ្រង់ទ្រាយឯកសារនេះទេ។")
        
        extracted_files = os.listdir(extract_dir)
        await context.bot.edit_message_text(f"ពន្លាបាន {len(extracted_files)} ឯកសារ។ កំពុងផ្ញើ... ✅", chat_id=chat_id, message_id=msg.message_id)
        for filename in extracted_files:
            full_path = os.path.join(extract_dir, filename)
            if os.path.isfile(full_path):
                 await context.bot.send_document(chat_id=chat_id, document=open(full_path, 'rb'))
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.isdir(extract_dir): shutil.rmtree(extract_dir)

# ==========================================
# UI / UX និង Menu Start
# ==========================================
async def setup_commands(application: Application):
    commands = [
        BotCommand("start", "🏠 បើកផ្ទាំងបញ្ជាដើម (Main Menu)"),
        BotCommand("help", "ℹ️ ជំនួយ និងរបៀបប្រើប្រាស់"),
        BotCommand("cancel", "❌ បោះបង់ប្រតិបត្តិការបច្ចុប្បន្ន")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("📄 PDF ទៅជា រូបភាព", callback_data='pdf_to_img'),
         InlineKeyboardButton("📝 PDF ទៅជា Word", callback_data='pdf_to_word')],
        [InlineKeyboardButton("🖇️ បញ្ចូល PDF", callback_data='merge_pdf'),
         InlineKeyboardButton("✂️ បំបែក PDF", callback_data='split_pdf'),
         InlineKeyboardButton("📦 បន្ថយទំហំ PDF", callback_data='compress_pdf')],
        [InlineKeyboardButton("🖼️ រូបភាព ទៅជា PDF", callback_data='img_to_pdf'),
         InlineKeyboardButton("📖 ទាញអក្សរពីរូបភាព", callback_data='img_to_text')],
        [InlineKeyboardButton("🎵 បំប្លែងសម្លេង", callback_data='audio_converter'),
         InlineKeyboardButton("🎬 បំប្លែងវីដេអូ", callback_data='video_converter')],
        [InlineKeyboardButton("⬇️ ទាញយកវីដេអូ", callback_data='media_downloader'),
         InlineKeyboardButton("🗜️ គ្រប់គ្រង Archive", callback_data='archive_manager')],
        [InlineKeyboardButton("🎙️ អត្ថបទ ទៅជា សំឡេង", callback_data='text_to_speech'),
         InlineKeyboardButton("🌍 បកប្រែអត្ថបទ (En->Km)", callback_data='translate_text')],
        [InlineKeyboardButton("✂️ លុបផ្ទៃខាងក្រោយរូបភាព (Remove BG)", callback_data='remove_bg')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = '👋 **សួស្តី! ខ្ញុំជាជំនួយការឯកសារឆ្លាតវៃរបស់អ្នក។**\n\nតើថ្ងៃនេះអ្នកចង់ឱ្យខ្ញុំជួយរៀបចំអ្វីដែរ? សូមជ្រើសរើសមុខងារខាងក្រោម៖'
    LOGO_URL = "https://cdn-icons-png.flaticon.com/512/2721/2721265.png"
    
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.message.delete()
        except Exception: pass
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=LOGO_URL, caption=text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await update.message.reply_photo(photo=LOGO_URL, caption=text, parse_mode='Markdown', reply_markup=reply_markup)
    return SELECT_ACTION

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
🛠 **របៀបប្រើប្រាស់ប្រព័ន្ធជំនួយការឯកសារ** 🛠

📄 **ផ្នែក PDF**
• បំប្លែង PDF ទៅជារូបភាព ឬ Word
• បញ្ចូល, បំបែក និងបន្ថយទំហំ PDF

🖼 **ផ្នែករូបភាព & ឆ្លាតវៃ (AI)**
• ទាញអក្សរពីរូបភាព (OCR)
• លុបផ្ទៃខាងក្រោយរូបភាពស្វ័យប្រវត្តិ (Remove BG) ✂️

🎬 **ផ្នែក Media & Social**
• បំប្លែង Format សម្លេង និង វីដេអូ
• ទាញយកវីដេអូពី Social Media ⬇️

🌍 **ផ្នែកអត្ថបទ**
• បកប្រែអត្ថបទ (អង់គ្លេស មក ខ្មែរ) ស្វ័យប្រវត្តិ 
• បំប្លែងអត្ថបទទៅជាសំឡេង (Audio Narration) 🎙️

⚠️ **កំណត់សម្គាល់៖** ឯកសារត្រូវមានទំហំមិនលើសពី **50MB** ឡើយ។ វាយបញ្ជា `/cancel` ដើម្បីបោះបង់សកម្មភាព។
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

# ==========================================
# Handlers សម្រាប់ចាប់ផ្ដើមមុខងារនីមួយៗ (បានជួសជុលការលុបរូបភាព)
# ==========================================
async def start_tts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete() 
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមវាយ ឬ Copy អត្ថបទ បញ្ចូលមកទីនេះ ខ្ញុំនឹងអានវាជាសំឡេងជូនអ្នក។")
    return WAITING_FOR_TTS

async def receive_tts_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    msg = await update.message.reply_text("✅ ទទួលបានអត្ថបទ! កំពុងរៀបចំ...")
    asyncio.create_task(text_to_speech_task(update.effective_chat.id, text, msg, context))
    return ConversationHandler.END

async def start_translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមវាយ ឬ Copy អត្ថបទ បញ្ចូលមកទីនេះ ដើម្បីបកប្រែមកជាភាសាខ្មែរ។")
    return WAITING_FOR_TRANSLATE

async def receive_translate_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    msg = await update.message.reply_text("✅ ទទួលបានអត្ថបទ! កំពុងរៀបចំ...")
    asyncio.create_task(translate_text_task(update.effective_chat.id, text, msg, context))
    return ConversationHandler.END

async def start_remove_bg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើរូបភាពមួយមកឱ្យខ្ញុំ ខ្ញុំនឹងកាត់យកតែរូបភាព និងលុបផ្ទៃខាងក្រោយចោល។")
    return WAITING_FOR_REMOVE_BG

async def receive_remove_bg_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_obj = update.message.photo[-1] if update.message.photo else update.message.document
    if not file_obj:
        await update.message.reply_text("សូមផ្ញើរូបភាពជា File ឬ Photo។")
        return WAITING_FOR_REMOVE_BG
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}.jpg"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានរូបភាព! កំពុងរៀបចំ...")
    asyncio.create_task(remove_bg_task(update.effective_chat.id, file_path, msg, context))
    return ConversationHandler.END

async def start_pdf_to_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារ PDF មួយដែលអ្នកចង់បំប្លែងទៅជា Word (DOCX)។")
    return WAITING_FOR_PDF_TO_WORD

async def receive_pdf_for_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបំប្លែងទៅជា Word...")
    asyncio.create_task(pdf_to_word_task(update.effective_chat.id, file_path, msg, context))
    return ConversationHandler.END

async def start_media_downloader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើតំណភ្ជាប់ (URL) នៃវីដេអូ (Youtube, TikTok, FB, IG...) មកឱ្យខ្ញុំ។")
    return WAITING_FOR_MEDIA_URL

async def receive_media_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text
    msg = await update.message.reply_text("✅ ទទួលបានតំណភ្ជាប់! កំពុងដំណើរការ...")
    asyncio.create_task(download_media_task(update.effective_chat.id, url, msg, context))
    return ConversationHandler.END

async def start_pdf_to_img(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    keyboard = [[InlineKeyboardButton("➡️ JPG", callback_data='fmt_jpeg'),
                 InlineKeyboardButton("➡️ PNG", callback_data='fmt_png')],
                [InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data='main_menu')]]
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="សូមជ្រើសរើសប្រភេទរូបភាព៖", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_ACTION

async def start_conversion_with_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data['format'] = "jpeg" if query.data == 'fmt_jpeg' else "png"
    await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ បានជ្រើសរើស {context.user_data['format'].upper()}។ សូមផ្ញើឯកសារ PDF មួយមកឱ្យខ្ញុំ។")
    return WAITING_PDF_TO_IMG_FILE

async def receive_pdf_for_img(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    fmt = context.user_data.get('format', 'jpeg')
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបំប្លែង...")
    asyncio.create_task(pdf_to_img_task(update.effective_chat.id, file_path, msg, context, fmt))
    return ConversationHandler.END

async def start_merge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data['merge_files'] = []
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារ PDF ម្ដងមួយៗ។ ពេលរួចរាល់ សូមវាយ /done ។")
    return WAITING_FOR_MERGE

async def receive_pdf_for_merge(update, context):
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    if 'merge_files' not in context.user_data: context.user_data['merge_files'] = []
    context.user_data['merge_files'].append(file_path)
    await update.message.reply_text(f"បានទទួលឯកសារទី {len(context.user_data['merge_files'])}។ ផ្ញើបន្ថែម ឬវាយ /done ។")
    return WAITING_FOR_MERGE

async def done_merging(update, context):
    if 'merge_files' not in context.user_data or len(context.user_data['merge_files']) < 2:
        await update.message.reply_text("សូមផ្ញើឯកសារ PDF យ៉ាងហោចណាស់ ២ មុននឹងវាយ /done ។")
        return WAITING_FOR_MERGE
    msg = await update.message.reply_text("យល់ព្រម! កំពុងបញ្ចូលឯកសារ...")
    asyncio.create_task(merge_pdf_task(update.effective_chat.id, context.user_data['merge_files'], msg, context))
    context.user_data.clear()
    return ConversationHandler.END

async def start_split(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារ PDF មួយដែលអ្នកចង់បំបែក។")
    return WAITING_FOR_SPLIT_FILE

async def receive_pdf_for_split(update, context):
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    context.user_data['split_file_path'] = file_path
    await update.message.reply_text("✅ ទទួលបានឯកសារ។ សូមវាយបញ្ចូលលេខទំព័រ (ឧ. '2-5' ឬ '1,3,8')។")
    return WAITING_FOR_SPLIT_RANGE

async def receive_split_range(update, context):
    page_range = update.message.text
    file_path = context.user_data.get('split_file_path')
    msg = await update.message.reply_text("យល់ព្រម! កំពុងបំបែកឯកសារ...")
    asyncio.create_task(split_pdf_task(update.effective_chat.id, file_path, page_range, msg, context))
    context.user_data.clear()
    return ConversationHandler.END

async def start_compress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារ PDF មួយដែលអ្នកចង់បន្ថយទំហំ។")
    return WAITING_FOR_COMPRESS

async def receive_pdf_for_compress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបន្ថយទំហំ...")
    asyncio.create_task(compress_pdf_task(update.effective_chat.id, file_path, msg, context))
    return ConversationHandler.END

async def start_img_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data['img_to_pdf_files'] = []
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើរូបភាពម្ដងមួយៗ។ ពេលរួចរាល់ សូមវាយ /done ។")
    return WAITING_FOR_IMG_TO_PDF

async def receive_img_for_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_obj = update.message.photo[-1] if update.message.photo else update.message.document
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}.jpg"
    await file.download_to_drive(file_path)
    if 'img_to_pdf_files' not in context.user_data: context.user_data['img_to_pdf_files'] = []
    context.user_data['img_to_pdf_files'].append(file_path)
    await update.message.reply_text(f"បានទទួលរូបភាពទី {len(context.user_data['img_to_pdf_files'])}។ ផ្ញើបន្ថែម ឬវាយ /done ។")
    return WAITING_FOR_IMG_TO_PDF

async def done_img_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if 'img_to_pdf_files' not in context.user_data or len(context.user_data['img_to_pdf_files']) < 1:
        await update.message.reply_text("សូមផ្ញើរូបភាពយ៉ាងហោចណាស់ ១ មុននឹងវាយ /done ។")
        return WAITING_FOR_IMG_TO_PDF
    msg = await update.message.reply_text("យល់ព្រម! កំពុងបំប្លែងរូបភាពទៅជា PDF...")
    asyncio.create_task(img_to_pdf_task(update.effective_chat.id, context.user_data['img_to_pdf_files'], msg, context))
    context.user_data.clear()
    return ConversationHandler.END

async def start_img_to_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើរូបភាពមួយមកឱ្យខ្ញុំ ដើម្បីបំប្លែងទៅជាអក្សរ។")
    return WAITING_FOR_IMG_TO_TEXT_FILE

async def receive_img_for_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_obj = update.message.photo[-1] if update.message.photo else update.message.document
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}.jpg"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានរូបភាព! កំពុងបំប្លែងទៅជាអក្សរ...")
    asyncio.create_task(img_to_text_task(update.effective_chat.id, file_path, msg, context))
    return ConversationHandler.END

def create_format_buttons(formats, prefix, columns=3):
    buttons = [InlineKeyboardButton(f"{fmt.upper()}", callback_data=f"{prefix}_{fmt.lower()}") for fmt in formats]
    keyboard = [buttons[i:i + columns] for i in range(0, len(buttons), columns)]
    keyboard.append([InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data='main_menu')])
    return keyboard

async def start_audio_converter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    audio_formats = ['AAC', 'FLAC', 'M4A', 'MP3', 'OGG', 'WAV']
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="សូមជ្រើសរើសទ្រង់ទ្រាយសម្លេង៖", reply_markup=InlineKeyboardMarkup(create_format_buttons(audio_formats, "audio")))
    return SELECT_ACTION

async def select_audio_output(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data['output_format'] = query.data.split('_')[1]
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ បានជ្រើសរើស {context.user_data['output_format'].upper()}។ សូមផ្ញើឯកសារសម្លេង។")
    return WAITING_FOR_AUDIO_FILE

async def receive_audio_for_conversion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_obj = update.message.audio or update.message.voice or update.message.document
    if not file_obj:
        await update.message.reply_text("សូមផ្ញើឯកសារសម្លេងត្រឹមត្រូវ។")
        return WAITING_FOR_AUDIO_FILE
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}"
    await file.download_to_drive(file_path)
    output_format = context.user_data.get('output_format', 'mp3')
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបំប្លែង...")
    asyncio.create_task(media_conversion_task(update.effective_chat.id, file_path, output_format, msg, context, media_type='audio'))
    return ConversationHandler.END

async def start_video_converter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    video_formats = ['AVI', 'MKV', 'MOV', 'MP4', 'WEBM']
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="សូមជ្រើសរើសទ្រង់ទ្រាយវីដេអូ៖", reply_markup=InlineKeyboardMarkup(create_format_buttons(video_formats, "video")))
    return SELECT_ACTION

async def select_video_output(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data['output_format'] = query.data.split('_')[1]
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ បានជ្រើសរើស {context.user_data['output_format'].upper()}។ សូមផ្ញើឯកសារវីដេអូ។")
    return WAITING_FOR_VIDEO_FILE

async def receive_video_for_conversion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_obj = update.message.video or update.message.video_note or update.message.document
    if not file_obj:
        await update.message.reply_text("សូមផ្ញើឯកសារវីដេអូត្រឹមត្រូវ។")
        return WAITING_FOR_VIDEO_FILE
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}"
    await file.download_to_drive(file_path)
    output_format = context.user_data.get('output_format', 'mp4')
    msg = await update.message.reply_text("✅ ទទួលបានវីដេអូ! កំពុងបំប្លែង...")
    asyncio.create_task(media_conversion_task(update.effective_chat.id, file_path, output_format, msg, context, media_type='video'))
    return ConversationHandler.END

async def start_archive_manager(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    keyboard = [
        [InlineKeyboardButton("➕ បង្កើតឯកសារ ZIP", callback_data='archive_create')],
        [InlineKeyboardButton("➖ ពន្លាឯកសារ Archive", callback_data='archive_extract')],
        [InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data='main_menu')]
    ]
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="សូមជ្រើសរើសសកម្មភាពសម្រាប់ Archive៖", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_ACTION

async def start_create_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data['zip_files'] = []
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារម្ដងមួយៗ។ ពេលរួចរាល់ សូមវាយ /done ។")
    return WAITING_FOR_FILES_TO_ZIP

async def receive_file_for_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text("សូមផ្ញើជាឯកសារ (Document)។")
        return WAITING_FOR_FILES_TO_ZIP
    file = await doc.get_file()
    file_path = f"temp_{file.file_unique_id}_{doc.file_name}"
    await file.download_to_drive(file_path)
    if 'zip_files' not in context.user_data: context.user_data['zip_files'] = []
    context.user_data['zip_files'].append(file_path)
    await update.message.reply_text(f"បានទទួលឯកសារទី {len(context.user_data['zip_files'])}។ ផ្ញើបន្ថែម ឬវាយ /done ។")
    return WAITING_FOR_FILES_TO_ZIP

async def done_zipping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'zip_files' not in context.user_data or not context.user_data['zip_files']:
        await update.message.reply_text("សូមផ្ញើឯកសារយ៉ាងហោចណាស់ ១ មុននឹងវាយ /done ។")
        return WAITING_FOR_FILES_TO_ZIP
    msg = await update.message.reply_text("យល់ព្រម! កំពុងបង្កើតឯកសារ ZIP...")
    asyncio.create_task(create_zip_task(update.effective_chat.id, context.user_data['zip_files'], msg, context))
    context.user_data.clear()
    return ConversationHandler.END

async def start_extract_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារ Archive (ZIP, TAR) ដែលអ្នកចង់ពន្លា។")
    return WAITING_FOR_ARCHIVE_TO_EXTRACT

async def receive_archive_to_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text("សូមផ្ញើជាឯកសារ Archive ។")
        return WAITING_FOR_ARCHIVE_TO_EXTRACT
    file = await doc.get_file()
    file_path = f"temp_{file.file_unique_id}_{doc.file_name}"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងពន្លា...")
    asyncio.create_task(extract_archive_task(update.effective_chat.id, file_path, msg, context))
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.message.delete()
        except Exception: pass
        await context.bot.send_message(chat_id=update.effective_chat.id, text="ប្រតិបត្តិការត្រូវបានបោះបង់។ វាយ /start ដើម្បីចាប់ផ្តើមថ្មី។")
    else:
        await update.message.reply_text("ប្រតិបត្តិការត្រូវបានបោះបង់។ វាយ /start ដើម្បីចាប់ផ្តើមថ្មី។")
    return ConversationHandler.END

# --- Main Application Runner ---
def main() -> None:
    if not BOT_TOKEN or not WEBHOOK_URL:
        print("!!! កំហុស៖ BOT_TOKEN ឬ CLOUD_RUN_URL មិនត្រូវបានកំណត់។")
        sys.exit(1)

    application = Application.builder().token(BOT_TOKEN).read_timeout(30).post_init(setup_commands).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [
                CallbackQueryHandler(start_pdf_to_img, pattern='^pdf_to_img$'),
                CallbackQueryHandler(start_conversion_with_format, pattern='^fmt_'),
                CallbackQueryHandler(start_merge, pattern='^merge_pdf$'),
                CallbackQueryHandler(start_split, pattern='^split_pdf$'),
                CallbackQueryHandler(start_compress, pattern='^compress_pdf$'),
                CallbackQueryHandler(start_img_to_pdf, pattern='^img_to_pdf$'),
                CallbackQueryHandler(start_img_to_text, pattern='^img_to_text$'),
                CallbackQueryHandler(start_audio_converter, pattern='^audio_converter$'),
                CallbackQueryHandler(select_audio_output, pattern='^audio_'),
                CallbackQueryHandler(start_video_converter, pattern='^video_converter$'),
                CallbackQueryHandler(select_video_output, pattern='^video_'),
                CallbackQueryHandler(start_archive_manager, pattern='^archive_manager$'),
                CallbackQueryHandler(start_create_zip, pattern='^archive_create$'),
                CallbackQueryHandler(start_extract_archive, pattern='^archive_extract$'),
                CallbackQueryHandler(start_pdf_to_word, pattern='^pdf_to_word$'),
                CallbackQueryHandler(start_media_downloader, pattern='^media_downloader$'),
                CallbackQueryHandler(start_tts, pattern='^text_to_speech$'),
                CallbackQueryHandler(start_translate, pattern='^translate_text$'),
                CallbackQueryHandler(start_remove_bg, pattern='^remove_bg$'),
                CallbackQueryHandler(start, pattern='^main_menu$')
            ],
            WAITING_PDF_TO_IMG_FILE: [MessageHandler(filters.Document.PDF, receive_pdf_for_img)],
            WAITING_FOR_MERGE: [MessageHandler(filters.Document.PDF, receive_pdf_for_merge), CommandHandler('done', done_merging)],
            WAITING_FOR_SPLIT_FILE: [MessageHandler(filters.Document.PDF, receive_pdf_for_split)],
            WAITING_FOR_SPLIT_RANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_split_range)],
            WAITING_FOR_COMPRESS: [MessageHandler(filters.Document.PDF, receive_pdf_for_compress)],
            WAITING_FOR_IMG_TO_PDF: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_img_for_pdf), CommandHandler('done', done_img_to_pdf)],
            WAITING_FOR_IMG_TO_TEXT_FILE: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_img_for_text)],
            WAITING_FOR_AUDIO_FILE: [MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.ALL, receive_audio_for_conversion)],
            WAITING_FOR_VIDEO_FILE: [MessageHandler(filters.VIDEO | filters.VIDEO_NOTE | filters.Document.ALL, receive_video_for_conversion)],
            WAITING_FOR_FILES_TO_ZIP: [MessageHandler(filters.Document.ALL, receive_file_for_zip), CommandHandler('done', done_zipping)],
            WAITING_FOR_ARCHIVE_TO_EXTRACT: [MessageHandler(filters.Document.ALL, receive_archive_to_extract)],
            WAITING_FOR_PDF_TO_WORD: [MessageHandler(filters.Document.PDF, receive_pdf_for_word)],
            WAITING_FOR_MEDIA_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_media_url)],
            WAITING_FOR_TTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_tts_text)],
            WAITING_FOR_TRANSLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_translate_text)],
            WAITING_FOR_REMOVE_BG: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_remove_bg_image)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    
    FULL_WEBHOOK_URL = f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}"
    
    print(f">>> Bot កំពុងដំណើរការដោយ Webhook នៅលើ Port: {PORT}")
    application.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=FULL_WEBHOOK_URL)

if __name__ == "__main__":
    main()
