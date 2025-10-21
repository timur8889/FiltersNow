FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

VOLUME /app/backups
VOLUME /app/data

CMD ["python", "bot.py"]
