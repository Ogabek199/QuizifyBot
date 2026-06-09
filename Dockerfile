FROM python:3.11-slim

WORKDIR /app

# System dependencies for pdfplumber
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpoppler-cpp-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/uploads

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]