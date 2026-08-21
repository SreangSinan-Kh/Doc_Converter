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
import urllib.request
import urllib.parse
import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
from PIL import Image
import pytesseract
from typing import Final

# ==========================================
# នាំចូល Library មូលដ្ឋាន
# ==========================================
try:
    from PyPDF2 import PdfReader, PdfWriter, PdfMerger
    from pdf2image import convert_from_path
    from pdf2docx import Converter
    import yt_dlp
    from gtts import gTTS
except Exception as e:
    print(f"!!! កំហុសពេលទាញយក Library: {e}")
    sys.exit(1)

BOT_TOKEN: Final = os.environ.get("BOT_TOKEN", "") 
MAX_FILE_SIZE: Final = 50 * 1024 * 1024 # 50 MB

if not BOT_TOKEN:
    print(f"!!! កំហុស៖ បាត់ BOT_TOKEN ។ សូមពិនិត្យមើលការកំណត់ម្តងទៀត។")
    sys.exit(1)

# ==========================================
# កំណត់ State សម្រាប់ការសន្ទនា
# ==========================================
(SELECT_ACTION, WAITING_PDF_TO_IMG_FORMAT, WAITING_PDF_TO_IMG_FILE,
 WAITING_FOR_MERGE, WAITING_FOR_SPLIT_FILE, WAITING_FOR_SPLIT_RANGE,
 WAITING_FOR_COMPRESS, WAITING_FOR_PDF_TO_WORD, WAITING_FOR_IMG_TO_PDF,
 WAITING_FOR_IMG_TO_TEXT_FILE, SELECT_AUDIO_OUTPUT_FORMAT, WAITING_FOR_AUDIO_FILE,
 SELECT_VIDEO_OUTPUT_FORMAT, WAITING_FOR_VIDEO_FILE, SELECT_ARCHIVE_ACTION,
 WAITING_FOR_FILES_TO_ZIP, WAITING_FOR_ARCHIVE_TO_EXTRACT, WAITING_FOR_MEDIA_URL,
 WAITING_FOR_TTS, WAITING_FOR_TRANSLATE, WAITING_FOR_REMOVE_BG) = range(21)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# ==========================================
# ប្រព័ន្ធខែលការពារ Telegram Timeout (Anti-Duplicate)
# ==========================================
PROCESSED_UPDATES = set()

def is_duplicate(update: Update) -> bool:
    if not update: return False
    update_id = update.update_id
    if update_id in PROCESSED_UPDATES: return True 
    PROCESSED_UPDATES.add(update_id)
    if len(PROCESSED_UPDATES) > 1000: PROCESSED_UPDATES.clear()
    return False

# ==========================================
# ប្រព័ន្ធកម្តៅម៉ាស៊ីន (AI Pre-Warming)
# ==========================================
def warmup_services():
    try:
        logging.info(">>> កំពុងកម្តៅម៉ាស៊ីន (Warm-up)...")
        from rembg import remove 
        _ = urllib.parse.quote("warmup") 
        logging.info(">>> កម្តៅម៉ាស៊ីនរួចរាល់! លឿនដូចហោះ!")
    except SystemExit:
        logging.error("!!! រកមិនឃើញកញ្ចប់ rembg[cpu] ទេ។ សូមពិនិត្យ requirements.txt ម្តងទៀត។")
    except Exception as e:
        logging.error(f"Warm-up Error: {e}")

# ==========================================
# អនុគមន៍កម្រិតកំពូល (Background Multi-Threaded Tasks)
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
        await context.bot.edit_message_text(f"កំហុសការបង្កើតសំឡេង: {str(e)[:100]}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(out_file): os.remove(out_file)

async def translate_text_task(chat_id, text, msg, context):
    try:
        await context.bot.edit_message_text("កំពុងបកប្រែអត្ថបទ... 🔄", chat_id=chat_id, message_id=msg.message_id)
        def do_translate():
            encoded_text = urllib.parse.quote(text)
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=km&dt=t&q={encoded_text}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                return "".join([item[0] for item in data[0]])
        translated = await asyncio.to_thread(do_translate)
        await context.bot.edit_message_text("បកប្រែជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_message(chat_id=chat_id, text=f"**អត្ថបទបកប្រែ៖**\n\n{translated}", parse_mode='Markdown')
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសប្រព័ន្ធបកប្រែ: {str(e)[:100]}", chat_id=chat_id, message_id=msg.message_id)

async def remove_bg_task(chat_id, file_path, msg, context):
    out_file = f"nobg_{chat_id}.png"
    try:
        await context.bot.edit_message_text("កំពុងលុបផ្ទៃខាងក្រោយដោយ AI... ✂️", chat_id=chat_id, message_id=msg.message_id)
        from rembg import remove
        def process_image():
            with open(file_path, 'rb') as i: input_data = i.read()
            output_data = remove(input_data)
            with open(out_file, 'wb') as o: o.write(output_data)
        await asyncio.to_thread(process_image)
        await context.bot.edit_message_text("លុបផ្ទៃខាងក្រោយជោគជ័យ! កំពុងផ្ញើ... ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(out_file, 'rb'), filename="Removed_Background.png")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការលុបផ្ទៃខាងក្រោយ: {str(e)[:100]}\n(សូមប្រាកដថាបានដាក់ rembg[cpu] ក្នុង requirements.txt)", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(out_file): os.remove(out_file)

async def pdf_to_word_task(chat_id, file_path, msg, context):
    output_path = f"converted_{chat_id}.docx"
    try:
        await context.bot.edit_message_text("កំពុងបំប្លែង PDF ទៅ Word (កម្លាំង CPU ពេញ)... 📝", chat_id=chat_id, message_id=msg.message_id)
        def do_pdf_word():
            cv = Converter(file_path)
            cv.convert(output_path, multi_processing=True, cpu_count=2)
            cv.close()
        await asyncio.to_thread(do_pdf_word)
        await context.bot.edit_message_text("បំប្លែងជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Document.docx")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការបំប្លែង: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def download_media_task(chat_id, url, msg, context):
    try:
        await context.bot.edit_message_text("កំពុងទាញយកទិន្នន័យ (Multi-thread)... ⬇️", chat_id=chat_id, message_id=msg.message_id)
        ydl_opts = {
            'outtmpl': f'downloaded_{chat_id}_%(title)s.%(ext)s',
            'format': 'best[filesize<50M]/best',
            'noplaylist': True,
            'quiet': True,
            'concurrent_fragment_downloads': 5
        }
        if os.path.exists('cookies.txt'): ydl_opts['cookiefile'] = 'cookies.txt'

        def run_ytdlp():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        downloaded_file = await asyncio.to_thread(run_ytdlp)
        await context.bot.edit_message_text("ទាញយកជោគជ័យ! កំពុងផ្ញើ... ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(downloaded_file, 'rb'))
        if os.path.exists(downloaded_file): os.remove(downloaded_file)
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការទាញយក: {str(e)[:100]}", chat_id=chat_id, message_id=msg.message_id)

async def pdf_to_img_task(chat_id, file_path, msg, context, fmt):
    try:
        await context.bot.edit_message_text("កំពុងបំប្លែងជា រូបភាព (Multi-thread)... 🖼️", chat_id=chat_id, message_id=msg.message_id)
        def do_pdf_img():
            return convert_from_path(file_path, dpi=150, fmt=fmt, thread_count=2)
        images = await asyncio.to_thread(do_pdf_img)
        
        await context.bot.edit_message_text(f"បំប្លែងបាន {len(images)} ទំព័រ។ កំពុងផ្ញើ... ✅", chat_id=chat_id, message_id=msg.message_id)
        for i, image in enumerate(images):
            out_path = f"page_{i+1}_{chat_id}.{fmt}"
            image.save(out_path, fmt.upper())
            await context.bot.send_photo(chat_id=chat_id, photo=open(out_path, 'rb'))
            os.remove(out_path)
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការបំប្លែងរូបភាព: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def merge_pdf_task(chat_id, file_paths, msg, context):
    output_path = f"merged_{chat_id}.pdf"
    try:
        await context.bot.edit_message_text("កំពុងបញ្ចូលឯកសារ... 🖇️", chat_id=chat_id, message_id=msg.message_id)
        def do_merge():
            merger = PdfMerger()
            for path in file_paths: merger.append(path)
            merger.write(output_path)
            merger.close()
        await asyncio.to_thread(do_merge)
        
        await context.bot.edit_message_text("បញ្ចូលជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Merged.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def split_pdf_task(chat_id, file_path, page_range_str, msg, context):
    output_path = f"split_{chat_id}.pdf"
    try:
        await context.bot.edit_message_text("កំពុងបំបែកឯកសារ... ✂️", chat_id=chat_id, message_id=msg.message_id)
        def do_split():
            writer = PdfWriter()
            reader = PdfReader(file_path)
            pages_to_extract = set()
            for part in page_range_str.split(','):
                part = part.strip()
                if '-' in part:
                    start, end = map(int, part.split('-'))
                    for i in range(start, end + 1): pages_to_extract.add(i-1)
                else: pages_to_extract.add(int(part)-1)
            for i in sorted(list(pages_to_extract)):
                if 0 <= i < len(reader.pages): writer.add_page(reader.pages[i])
            if not writer.pages: raise ValueError("ទំព័រមិនត្រឹមត្រូវ")
            writer.write(output_path)
        await asyncio.to_thread(do_split)

        await context.bot.edit_message_text("បំបែកជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Split.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការបំបែក: ទម្រង់លេខខុស។", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def compress_pdf_task(chat_id, file_path, msg, context):
    output_path = f"compressed_{chat_id}.pdf"
    try:
        await context.bot.edit_message_text("កំពុងបន្ថយទំហំឯកសារ... 📦", chat_id=chat_id, message_id=msg.message_id)
        def do_compress():
            reader = PdfReader(file_path)
            writer = PdfWriter()
            for page in reader.pages:
                page.compress_content_streams()
                writer.add_page(page)
            with open(output_path, "wb") as f: writer.write(f)
        await asyncio.to_thread(do_compress)
        
        await context.bot.edit_message_text("បន្ថយទំហំជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Compressed.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការបន្ថយទំហំ: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def img_to_pdf_task(chat_id, file_paths, msg, context):
    output_path = f"converted_from_img_{chat_id}.pdf"
    try:
        await context.bot.edit_message_text("កំពុងបំប្លែងរូបភាពទៅជា PDF... 📄", chat_id=chat_id, message_id=msg.message_id)
        def do_img_pdf():
            image_list = [Image.open(path).convert('RGB') for path in file_paths]
            image_list[0].save(output_path, "PDF", resolution=100.0, save_all=True, append_images=image_list[1:])
        await asyncio.to_thread(do_img_pdf)
        
        await context.bot.edit_message_text("បំប្លែងជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Image_to_PDF.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def img_to_text_task(chat_id, file_path, msg, context):
    try:
        await context.bot.edit_message_text("កំពុងអានអក្សរពីរូបភាព... 📖", chat_id=chat_id, message_id=msg.message_id)
        def do_ocr():
            image = Image.open(file_path)
            custom_config = r'--oem 3 --psm 3'
            return pytesseract.image_to_string(image, lang='khm+eng', config=custom_config)
        text = await asyncio.to_thread(do_ocr)
        
        await context.bot.edit_message_text("បំប្លែងជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        if not text.strip():
            await context.bot.send_message(chat_id=chat_id, text="មិនអាចរកឃើញអក្សរនៅក្នុងរូបភាពនេះទេ។")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"**លទ្ធផល៖**\n\n```\n{text}\n```", parse_mode='Markdown')
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការបំប្លែង: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def media_conversion_task(chat_id, file_path, output_format, msg, context, media_type='audio'):
    output_path = f"converted_{chat_id}.{output_format}"
    try:
        await context.bot.edit_message_text(f"កំពុងបំប្លែងទៅ {output_format.upper()} (Max Speed)... 🎬", chat_id=chat_id, message_id=msg.message_id)
        def do_convert():
            ffmpeg.input(file_path).output(output_path, preset='ultrafast', threads=0).run(overwrite_output=True)
        await asyncio.to_thread(do_convert)
        
        await context.bot.edit_message_text("បំប្លែងជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        if media_type == 'audio': await context.bot.send_audio(chat_id=chat_id, audio=open(output_path, 'rb'))
        else: await context.bot.send_video(chat_id=chat_id, video=open(output_path, 'rb'))
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសការបំប្លែង: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def create_zip_task(chat_id, file_paths, msg, context):
    output_path = f"archive_{chat_id}.zip"
    try:
        await context.bot.edit_message_text("កំពុងបង្កើត ZIP ផ្លេកបន្ទោរ... 📦", chat_id=chat_id, message_id=msg.message_id)
        def do_zip():
            with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zipf:
                for p in file_paths: zipf.write(p, os.path.basename(p))
        await asyncio.to_thread(do_zip)
        
        await context.bot.edit_message_text("បង្កើត ZIP ជោគជ័យ! ✅", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="archive.zip")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុស ZIP: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def extract_archive_task(chat_id, file_path, msg, context):
    extract_dir = f"extracted_{chat_id}"
    try:
        await context.bot.edit_message_text("កំពុងពន្លាឯកសារ... 📂", chat_id=chat_id, message_id=msg.message_id)
        def do_extract():
            os.makedirs(extract_dir, exist_ok=True)
            if file_path.endswith('.zip'):
                with zipfile.ZipFile(file_path, 'r') as zip_ref: zip_ref.extractall(extract_dir)
            elif file_path.endswith('.tar.gz') or file_path.endswith('.tgz'):
                with tarfile.open(file_path, 'r:gz') as tar_ref: tar_ref.extractall(extract_dir)
            elif file_path.endswith('.tar'):
                with tarfile.open(file_path, 'r:') as tar_ref: tar_ref.extractall(extract_dir)
            else: raise ValueError("មិនគាំទ្រឯកសារនេះ។")
        await asyncio.to_thread(do_extract)
        
        extracted_files = os.listdir(extract_dir)
        await context.bot.edit_message_text(f"ពន្លាបាន {len(extracted_files)} ឯកសារ។ កំពុងផ្ញើ... ✅", chat_id=chat_id, message_id=msg.message_id)
        for filename in extracted_files:
            full_path = os.path.join(extract_dir, filename)
            if os.path.isfile(full_path): await context.bot.send_document(chat_id=chat_id, document=open(full_path, 'rb'))
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសពន្លាឯកសារ: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.isdir(extract_dir): shutil.rmtree(extract_dir)

# ==========================================
# UI / UX និង Menu Start (គ្រប់មុខងារទាំងអស់)
# ==========================================
async def setup_commands(application: Application):
    commands = [
        BotCommand("start", "🏠 បើកផ្ទាំងបញ្ជាដើម"),
        BotCommand("help", "ℹ️ ជំនួយការប្រើប្រាស់"),
        BotCommand("cancel", "❌ បោះបង់សកម្មភាព")
    ]
    await application.bot.set_my_commands(commands)
    asyncio.create_task(asyncio.to_thread(warmup_services))

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
    text = '👋 **សួស្តី! ខ្ញុំជាជំនួយការឯកសារឆ្លាតវៃ។**\n\nតើថ្ងៃនេះអ្នកចង់ឱ្យខ្ញុំជួយរៀបចំអ្វីដែរ?'
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
    help_text = "🛠 **របៀបប្រើប្រាស់ប្រព័ន្ធជំនួយការឯកសារ** 🛠\n\n⚠️ **កំណត់សម្គាល់៖** ឯកសារត្រូវមានទំហំមិនលើសពី **50MB** ឡើយ។ វាយបញ្ជា `/cancel` ដើម្បីបោះបង់សកម្មភាពដែលគាំង។"
    await update.message.reply_text(help_text, parse_mode='Markdown')

# ==========================================
# Handlers (ការចាប់យកសកម្មភាពអ្នកប្រើប្រាស់គ្រប់មុខងារទាំងអស់)
# ==========================================

# --- Text to Speech ---
async def start_tts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete() 
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមវាយ ឬ Copy អត្ថបទ បញ្ចូលមកទីនេះ ខ្ញុំនឹងអានវាជាសំឡេងជូនអ្នក។")
    return WAITING_FOR_TTS

async def receive_tts_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_duplicate(update): return ConversationHandler.END
    text = update.message.text
    msg = await update.message.reply_text("✅ ទទួលបានអត្ថបទ! កំពុងរៀបចំ...")
    await text_to_speech_task(update.effective_chat.id, text, msg, context)
    return ConversationHandler.END

# --- Translate ---
async def start_translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមវាយ ឬ Copy អត្ថបទ បញ្ចូលមកទីនេះ ដើម្បីបកប្រែមកជាភាសាខ្មែរ។")
    return WAITING_FOR_TRANSLATE

async def receive_translate_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_duplicate(update): return ConversationHandler.END
    text = update.message.text
    msg = await update.message.reply_text("✅ ទទួលបានអត្ថបទ! កំពុងរៀបចំ...")
    await translate_text_task(update.effective_chat.id, text, msg, context)
    return ConversationHandler.END

# --- Remove BG ---
async def start_remove_bg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើរូបភាពមួយមកឱ្យខ្ញុំ ខ្ញុំនឹងលុបផ្ទៃខាងក្រោយចោល។")
    return WAITING_FOR_REMOVE_BG

async def receive_remove_bg_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_duplicate(update): return ConversationHandler.END
    file_obj = update.message.photo[-1] if update.message.photo else update.message.document
    if not file_obj:
        await update.message.reply_text("សូមផ្ញើរូបភាពជា File ឬ Photo។")
        return WAITING_FOR_REMOVE_BG
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}.jpg"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានរូបភាព! កំពុងរៀបចំ...")
    await remove_bg_task(update.effective_chat.id, file_path, msg, context)
    return ConversationHandler.END

# --- PDF to Word ---
async def start_pdf_to_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារ PDF មួយដែលអ្នកចង់បំប្លែងទៅជា Word (DOCX)។")
    return WAITING_FOR_PDF_TO_WORD

async def receive_pdf_for_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_duplicate(update): return ConversationHandler.END
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបំប្លែងទៅជា Word...")
    await pdf_to_word_task(update.effective_chat.id, file_path, msg, context)
    return ConversationHandler.END

# --- Media Downloader ---
async def start_media_downloader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើតំណភ្ជាប់ (URL) នៃវីដេអូ (Youtube, TikTok, FB, IG...) មកឱ្យខ្ញុំ។")
    return WAITING_FOR_MEDIA_URL

async def receive_media_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_duplicate(update): return ConversationHandler.END
    url = update.message.text
    msg = await update.message.reply_text("✅ ទទួលបានតំណភ្ជាប់! កំពុងដំណើរការ...")
    await download_media_task(update.effective_chat.id, url, msg, context)
    return ConversationHandler.END

# --- PDF to Image ---
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
    if is_duplicate(update): return ConversationHandler.END
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    fmt = context.user_data.get('format', 'jpeg')
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបំប្លែង...")
    await pdf_to_img_task(update.effective_chat.id, file_path, msg, context, fmt)
    return ConversationHandler.END

# --- Merge PDF ---
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
    if is_duplicate(update): return ConversationHandler.END
    if 'merge_files' not in context.user_data or len(context.user_data['merge_files']) < 2:
        await update.message.reply_text("សូមផ្ញើឯកសារ PDF យ៉ាងហោចណាស់ ២ មុននឹងវាយ /done ។")
        return WAITING_FOR_MERGE
    msg = await update.message.reply_text("យល់ព្រម! កំពុងបញ្ចូលឯកសារ...")
    await merge_pdf_task(update.effective_chat.id, context.user_data['merge_files'], msg, context)
    context.user_data.clear()
    return ConversationHandler.END

# --- Split PDF ---
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
    if is_duplicate(update): return ConversationHandler.END
    page_range = update.message.text
    file_path = context.user_data.get('split_file_path')
    msg = await update.message.reply_text("យល់ព្រម! កំពុងបំបែកឯកសារ...")
    await split_pdf_task(update.effective_chat.id, file_path, page_range, msg, context)
    context.user_data.clear()
    return ConversationHandler.END

# --- Compress PDF ---
async def start_compress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារ PDF មួយដែលអ្នកចង់បន្ថយទំហំ។")
    return WAITING_FOR_COMPRESS

async def receive_pdf_for_compress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_duplicate(update): return ConversationHandler.END
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបន្ថយទំហំ...")
    await compress_pdf_task(update.effective_chat.id, file_path, msg, context)
    return ConversationHandler.END

# --- Image to PDF ---
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
    if is_duplicate(update): return ConversationHandler.END
    if 'img_to_pdf_files' not in context.user_data or len(context.user_data['img_to_pdf_files']) < 1:
        await update.message.reply_text("សូមផ្ញើរូបភាពយ៉ាងហោចណាស់ ១ មុននឹងវាយ /done ។")
        return WAITING_FOR_IMG_TO_PDF
    msg = await update.message.reply_text("យល់ព្រម! កំពុងបំប្លែងរូបភាពទៅជា PDF...")
    await img_to_pdf_task(update.effective_chat.id, context.user_data['img_to_pdf_files'], msg, context)
    context.user_data.clear()
    return ConversationHandler.END

# --- Image to Text (OCR) ---
async def start_img_to_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើរូបភាពមួយមកឱ្យខ្ញុំ ដើម្បីបំប្លែងទៅជាអក្សរ។")
    return WAITING_FOR_IMG_TO_TEXT_FILE

async def receive_img_for_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_duplicate(update): return ConversationHandler.END
    file_obj = update.message.photo[-1] if update.message.photo else update.message.document
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}.jpg"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានរូបភាព! កំពុងអានអក្សរ (OCR)...")
    await img_to_text_task(update.effective_chat.id, file_path, msg, context)
    return ConversationHandler.END

# --- Audio Converter ---
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
    if is_duplicate(update): return ConversationHandler.END
    file_obj = update.message.audio or update.message.voice or update.message.document
    if not file_obj:
        await update.message.reply_text("សូមផ្ញើឯកសារសម្លេងត្រឹមត្រូវ។")
        return WAITING_FOR_AUDIO_FILE
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}"
    await file.download_to_drive(file_path)
    output_format = context.user_data.get('output_format', 'mp3')
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងបំប្លែង...")
    await media_conversion_task(update.effective_chat.id, file_path, output_format, msg, context, media_type='audio')
    return ConversationHandler.END

# --- Video Converter ---
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
    if is_duplicate(update): return ConversationHandler.END
    file_obj = update.message.video or update.message.video_note or update.message.document
    if not file_obj:
        await update.message.reply_text("សូមផ្ញើឯកសារវីដេអូត្រឹមត្រូវ។")
        return WAITING_FOR_VIDEO_FILE
    file = await file_obj.get_file()
    file_path = f"temp_{file.file_id}"
    await file.download_to_drive(file_path)
    output_format = context.user_data.get('output_format', 'mp4')
    msg = await update.message.reply_text("✅ ទទួលបានវីដេអូ! កំពុងបំប្លែង...")
    await media_conversion_task(update.effective_chat.id, file_path, output_format, msg, context, media_type='video')
    return ConversationHandler.END

# --- Archive Manager ---
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
    if is_duplicate(update): return ConversationHandler.END
    if 'zip_files' not in context.user_data or not context.user_data['zip_files']:
        await update.message.reply_text("សូមផ្ញើឯកសារយ៉ាងហោចណាស់ ១ មុននឹងវាយ /done ។")
        return WAITING_FOR_FILES_TO_ZIP
    msg = await update.message.reply_text("យល់ព្រម! កំពុងបង្កើតឯកសារ ZIP...")
    await create_zip_task(update.effective_chat.id, context.user_data['zip_files'], msg, context)
    context.user_data.clear()
    return ConversationHandler.END

async def start_extract_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    try: await query.message.delete()
    except Exception: pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ សូមផ្ញើឯកសារ Archive (ZIP, TAR) ដែលអ្នកចង់ពន្លា។")
    return WAITING_FOR_ARCHIVE_TO_EXTRACT

async def receive_archive_to_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate(update): return ConversationHandler.END
    doc = update.message.document
    if not doc:
        await update.message.reply_text("សូមផ្ញើជាឯកសារ Archive ។")
        return WAITING_FOR_ARCHIVE_TO_EXTRACT
    file = await doc.get_file()
    file_path = f"temp_{file.file_unique_id}_{doc.file_name}"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("✅ ទទួលបានឯកសារ! កំពុងពន្លា...")
    await extract_archive_task(update.effective_chat.id, file_path, msg, context)
    return ConversationHandler.END

# --- Cancel Command ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.message.delete()
        except Exception: pass
        await context.bot.send_message(chat_id=update.effective_chat.id, text="ប្រតិបត្តិការត្រូវបានបោះបង់។ វាយ /start ដើម្បីបញ្ជាជាថ្មី។")
    else:
        await update.message.reply_text("ប្រតិបត្តិការត្រូវបានបោះបង់។ វាយ /start ដើម្បីបញ្ជាជាថ្មី។")
    return ConversationHandler.END

# ==========================================
# Main Application Runner (Polling Mode សម្រាប់ VM)
# ==========================================
def main() -> None:
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
    
    print(">>> Bot កំពុងដំណើរការដោយប្រព័ន្ធ Polling លើម៉ាស៊ីន VM...")
    application.run_polling
if __name__ == "__main__":
    main()
