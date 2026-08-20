# ប្រើប្រាស់ Python ជំនាន់ស្រាលដើម្បីសន្សំទំហំម៉ាស៊ីន
FROM python:3.11-slim

# កំណត់ទីតាំងធ្វើការងារ
WORKDIR /app

# ដំឡើងកម្មវិធីប្រព័ន្ធ (OS Level) សម្រាប់ជំនួយដល់ Bot (FFmpeg និង Tesseract ខ្មែរ/អង់គ្លេស)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-khm \
    && rm -rf /var/lib/apt/lists/*

# ចម្លងឯកសារ Requirements និងដំឡើង Library របស់ Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ចម្លងកូដទាំងអស់ចូលទៅក្នុងម៉ាស៊ីន
COPY . .

# បញ្ជាឱ្យរត់កូដ main.py
CMD ["python", "main.py"]
