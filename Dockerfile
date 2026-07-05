FROM python:3.11-slim

# Install system dependencies (build-essential/gcc is needed by some cryptography/cffi installs)
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip
RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py downloader.py sheets_logger.py env.txt ./
RUN mkdir -p downloads

# Mount local downloads folder
VOLUME ["/app/downloads"]

# Default run (overridden by docker-compose)
CMD ["python", "-u", "bot.py"]
