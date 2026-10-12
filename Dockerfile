FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Kutubxonalar; launcher.py requirements.txt o'zgarganini shu belgi orqali biladi
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && mkdir -p /usr/local/share/claude-bot \
 && sha256sum requirements.txt | cut -d' ' -f1 > /usr/local/share/claude-bot/requirements.sha256

COPY . .
RUN mkdir -p /app/data

# launcher.py botni ishga tushiradi, /yangilash dan keyin qayta ishga tushiradi,
# yangi versiya ishga tushmasa — eskisini avtomatik tiklaydi
CMD ["python", "launcher.py"]
