# ប្រើប្រាស់ Python 3.12.1
FROM python:3.12.1-slim

# កំណត់ទីតាំងធ្វើការក្នុង Container
WORKDIR /app

# ដំឡើងកម្មវិធីប្រព័ន្ធ (FFmpeg និង Tesseract) ដែលមិនអាចខ្វះបាន
RUN apt-get update && apt-get install -y \
    ffmpeg \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# ចម្លង requirements.txt និងដំឡើង Library
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ចម្លងកូដទាំងអស់ (main.py) ចូលក្នុង Container
COPY . .

# បញ្ជាឱ្យដំណើរការ Cloud Run នឹងបញ្ជូន PORT មកដោយស្វ័យប្រវត្តិ
CMD ["python", "main.py"]