FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright — опционально, устанавливаем отдельно
RUN pip install --no-cache-dir playwright && \
    playwright install chromium --with-deps || \
    echo "Playwright install failed — continuing without browser automation"

COPY . .
CMD ["python", "bot.py"]
