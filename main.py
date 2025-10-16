import logging
import sqlite3
import schedule
import time
import asyncio
from datetime import datetime, timedelta
from threading import Thread
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, Application
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyKeyboardMarkup, KeyboardButton
import os
from typing import Dict, List
import atexit
import signal
import sys
import redis
import pickle
import asyncpg
from redis import asyncio as aioredis
import jwt
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel, validator
from cryptography.fernet import Fernet
import hashlib
import hmac
from prometheus_client import Counter, Histogram, generate_latest, start_http_server
import json
from pathlib import Path
import importlib.util
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import smtplib
from email.mime.text import MimeText
from email.mime.multipart import MimeMultipart
from PIL import Image
import io
import openai
from transformers import pipeline
import speech_recognition as sr
import stripe
from twilio.rest import Client
import consul
from healthcheck import HealthCheck, HealthCheckMiddleware
import websockets
from sklearn.ensemble import RandomForestRegressor
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import gettext

# ==================== КОНФИГУРАЦИЯ ====================
load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME")
    CHANNEL_ID = os.getenv("CHANNEL_ID", "@timur_onion")
    ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "5024165375").split(",")]
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/bot")
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", Fernet.generate_key())
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# ==================== МЕТРИКИ PROMETHEUS ====================
POSTS_CREATED = Counter('posts_created_total', 'Total posts created')
POSTS_PUBLISHED = Counter('posts_published_total', 'Total posts published')
REQUEST_DURATION = Histogram('request_duration_seconds', 'Request duration')
FAILED_POSTS = Counter('failed_posts_total', 'Total failed posts')

# ==================== МОДЕЛИ PYDANTIC ====================
class PostCreate(BaseModel):
    text: str
    scheduled_time: datetime
    media_path: str = None
    media_type: str = None
    
    @validator('text')
    def validate_text_length(cls, v):
        if len(v) > 4096:
            raise ValueError('Text too long')
        return v

class UserCreate(BaseModel):
    user_id: int
    username: str
    role: str = "user"

# ==================== СИСТЕМА КЭШИРОВАНИЯ ====================
class Cache:
    def __init__(self):
        self.redis = aioredis.from_url(Config.REDIS_URL, decode_responses=False)
    
    async def set(self, key, value, expire=3600):
        await self.redis.setex(key, expire, pickle.dumps(value))
    
    async def get(self, key):
        data = await self.redis.get(key)
        return pickle.loads(data) if data else None
    
    async def delete(self, key):
        await self.redis.delete(key)

# ==================== БАЗА ДАННЫХ POSTGRESQL ====================
class Database:
    def __init__(self):
        self.pool = None
        self.cache = Cache()
    
    async def connect(self):
        self.pool = await asyncpg.create_pool(Config.DATABASE_URL)
        await self.create_tables()
    
    async def create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT,
                    message_text TEXT,
                    media_path TEXT,
                    media_type TEXT,
                    scheduled_time TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    status VARCHAR(20) DEFAULT 'scheduled'
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    role VARCHAR(50) DEFAULT 'user',
                    points INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    action VARCHAR(255),
                    details JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS post_analytics (
                    post_id INTEGER REFERENCES scheduled_posts(id),
                    views INTEGER DEFAULT 0,
                    engagements INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

    async def add_scheduled_post(self, chat_id: int, message_text: str, scheduled_time: datetime, media_path: str = None, media_type: str = None):
        async with self.pool.acquire() as conn:
            post_id = await conn.fetchval('''
                INSERT INTO scheduled_posts (chat_id, message_text, media_path, media_type, scheduled_time)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
            ''', chat_id, message_text, media_path, media_type, scheduled_time)
            
            POSTS_CREATED.inc()
            await self.cache.delete('scheduled_posts')
            return post_id

    async def get_pending_posts(self):
        cache_key = 'pending_posts'
        cached = await self.cache.get(cache_key)
        if cached:
            return cached
        
        async with self.pool.acquire() as conn:
            posts = await conn.fetch('''
                SELECT * FROM scheduled_posts 
                WHERE scheduled_time <= NOW() AND status = 'scheduled'
                ORDER BY scheduled_time ASC
            ''')
            
            await self.cache.set(cache_key, posts, expire=60)
            return posts

    async def delete_scheduled_post(self, post_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM scheduled_posts WHERE id = $1', post_id)
            await self.cache.delete('scheduled_posts')

    async def get_all_scheduled_posts(self):
        cache_key = 'all_scheduled_posts'
        cached = await self.cache.get(cache_key)
        if cached:
            return cached
        
        async with self.pool.acquire() as conn:
            posts = await conn.fetch('SELECT * FROM scheduled_posts ORDER BY scheduled_time ASC')
            await self.cache.set(cache_key, posts, expire=300)
            return posts

    async def update_post_status(self, post_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE scheduled_posts SET status = $1 WHERE id = $2', status, post_id)
            await self.cache.delete('scheduled_posts')

# ==================== СИСТЕМА АУТЕНТИФИКАЦИИ ====================
class AuthManager:
    def __init__(self):
        self.secret_key = Config.SECRET_KEY
    
    def create_token(self, user_id: int) -> str:
        payload = {
            'user_id': user_id,
            'exp': datetime.utcnow() + timedelta(hours=24),
            'iat': datetime.utcnow()
        }
        return jwt.encode(payload, self.secret_key, algorithm='HS256')
    
    def verify_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=['HS256'])
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

# ==================== РОЛЕВАЯ МОДЕЛЬ ДОСТУПА ====================
class RBAC:
    ROLES = {
        'admin': ['create_post', 'delete_post', 'manage_users', 'view_analytics', 'schedule_post'],
        'moderator': ['create_post', 'edit_post', 'view_posts'],
        'user': ['view_posts']
    }
    
    def check_permission(self, user_role: str, permission: str) -> bool:
        return permission in self.ROLES.get(user_role, [])

# ==================== ШИФРОВАНИЕ ДАННЫХ ====================
class EncryptionService:
    def __init__(self):
        self.cipher = Fernet(Config.ENCRYPTION_KEY.encode())
    
    def encrypt(self, data: str) -> bytes:
        return self.cipher.encrypt(data.encode())
    
    def decrypt(self, encrypted_data: bytes) -> str:
        return self.cipher.decrypt(encrypted_data).decode()

# ==================== АУДИТ ДЕЙСТВИЙ ====================
class AuditLogger:
    def __init__(self, db: Database):
        self.db = db
    
    async def log_action(self, user_id: int, action: str, details: dict):
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO audit_log (user_id, action, details) VALUES ($1, $2, $3)",
                user_id, action, json.dumps(details)
            )

# ==================== АНАЛИТИКА ПОСТОВ ====================
class PostAnalytics:
    def __init__(self, db: Database):
        self.db = db
    
    async def track_post_metrics(self, post_id: int, message_id: int):
        async with self.db.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO post_analytics (post_id, views, engagements)
                VALUES ($1, 0, 0)
                ON CONFLICT (post_id) DO NOTHING
            ''', post_id)
    
    async def increment_views(self, post_id: int):
        async with self.db.pool.acquire() as conn:
            await conn.execute('''
                UPDATE post_analytics SET views = views + 1 WHERE post_id = $1
            ''', post_id)
    
    async def get_post_stats(self, post_id: int) -> dict:
        async with self.db.pool.acquire() as conn:
            stats = await conn.fetchrow('''
                SELECT views, engagements FROM post_analytics WHERE post_id = $1
            ''', post_id)
            return dict(stats) if stats else {'views': 0, 'engagements': 0}

# ==================== AI-ГЕНЕРАЦИЯ КОНТЕНТА ====================
class ContentGenerator:
    def __init__(self):
        self.client = openai.AsyncClient(api_key=Config.OPENAI_API_KEY) if Config.OPENAI_API_KEY else None
    
    async def generate_post(self, topic: str, tone: str = "professional") -> str:
        if not self.client:
            return f"Интересный пост о {topic}"
        
        try:
            prompt = f"Сгенерируй {tone} пост в социальных сетях на тему: {topic}"
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logging.error(f"OpenAI error: {e}")
            return f"🔥 Интересный контент о {topic}!"

# ==================== АВТОМОДЕРАЦИЯ ====================
class ContentModerator:
    def __init__(self):
        self.sentiment_analyzer = pipeline("sentiment-analysis")
    
    async def check_content(self, text: str) -> bool:
        # Простая проверка на запрещенные слова
        forbidden_words = ['спам', 'мошенничество', 'обман']
        if any(word in text.lower() for word in forbidden_words):
            return False
        
        # Анализ тональности
        try:
            result = self.sentiment_analyzer(text[:512])[0]
            return result['label'] == 'POSITIVE' or result['score'] < 0.7
        except:
            return True

# ==================== УМНЫЙ ПЛАНИРОВЩИК ====================
class SmartScheduler:
    def __init__(self, db: Database):
        self.db = db
        self.model = RandomForestRegressor()
        self.is_trained = False
    
    async def train_model(self):
        # Здесь должна быть логика обучения на исторических данных
        # Для примеси используем фиктивные данные
        X = np.random.rand(100, 5)
        y = np.random.rand(100)
        self.model.fit(X, y)
        self.is_trained = True
    
    async def suggest_optimal_time(self, target_audience: str = "general") -> datetime:
        now = datetime.now()
        
        # Эвристика: лучшее время для постов
        best_times = {
            "general": now.replace(hour=19, minute=0, second=0),  # 19:00
            "business": now.replace(hour=9, minute=0, second=0),   # 9:00
            "entertainment": now.replace(hour=20, minute=0, second=0)  # 20:00
        }
        
        return best_times.get(target_audience, now.replace(hour=19, minute=0, second=0))

# ==================== СИСТЕМА УВЕДОМЛЕНИЙ ====================
class NotificationManager:
    def __init__(self):
        self.email_enabled = bool(Config.SMTP_USERNAME)
        self.sms_enabled = bool(Config.TWILIO_ACCOUNT_SID)
    
    async def send_email(self, to_email: str, subject: str, body: str):
        if not self.email_enabled:
            return
        
        try:
            msg = MimeMultipart()
            msg['From'] = Config.SMTP_USERNAME
            msg['To'] = to_email
            msg['Subject'] = subject
            
            msg.attach(MimeText(body, 'html'))
            
            with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
                server.starttls()
                server.login(Config.SMTP_USERNAME, Config.SMTP_PASSWORD)
                server.send_message(msg)
        except Exception as e:
            logging.error(f"Email sending failed: {e}")
    
    async def send_sms(self, to_phone: str, message: str):
        if not self.sms_enabled:
            return
        
        try:
            client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
            client.messages.create(
                body=message,
                from_=os.getenv("TWILIO_PHONE_NUMBER"),
                to=to_phone
            )
        except Exception as e:
            logging.error(f"SMS sending failed: {e}")

# ==================== ОПТИМИЗАЦИЯ МЕДИА ====================
class MediaOptimizer:
    @staticmethod
    async def optimize_image(image_data: bytes, max_size: tuple = (1200, 1200), quality: int = 85) -> bytes:
        try:
            image = Image.open(io.BytesIO(image_data))
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            output = io.BytesIO()
            if image.mode in ('RGBA', 'LA'):
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            
            image.save(output, format='JPEG', quality=quality, optimize=True)
            return output.getvalue()
        except Exception as e:
            logging.error(f"Image optimization failed: {e}")
            return image_data

# ==================== СИСТЕМА ПЛАГИНОВ ====================
class PluginManager:
    def __init__(self):
        self.plugins = {}
    
    def load_plugins(self, plugins_dir: str = "plugins"):
        plugins_path = Path(plugins_dir)
        if not plugins_path.exists():
            return
        
        for plugin_file in plugins_path.glob("*.py"):
            try:
                spec = importlib.util.spec_from_file_location(plugin_file.stem, plugin_file)
                plugin_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(plugin_module)
                
                if hasattr(plugin_module, 'register'):
                    plugin_module.register(self)
                    self.plugins[plugin_file.stem] = plugin_module
                    logging.info(f"Plugin loaded: {plugin_file.stem}")
            except Exception as e:
                logging.error(f"Failed to load plugin {plugin_file}: {e}")

# ==================== МУЛЬТИЯЗЫЧНОСТЬ ====================
class Localization:
    def __init__(self):
        self.translations = {}
        self.load_translations()
    
    def load_translations(self):
        locales_dir = Path("locales")
        if not locales_dir.exists():
            return
        
        for lang_dir in locales_dir.iterdir():
            if lang_dir.is_dir():
                try:
                    self.translations[lang_dir.name] = gettext.translation(
                        'bot', localedir=locales_dir, languages=[lang_dir.name]
                    )
                except FileNotFoundError:
                    continue
    
    def gettext(self, text: str, lang: str = 'ru') -> str:
        if lang in self.translations:
            return self.translations[lang].gettext(text)
        return text

# ==================== ОСНОВНОЙ КЛАСС БОТА ====================
class AdvancedChannelBot:
    def __init__(self):
        self.config = Config()
        self.application = Application.builder().token(self.config.BOT_TOKEN).build()
        self.dispatcher = self.application
        
        # Инициализация компонентов
        self.db = Database()
        self.cache = Cache()
        self.auth = AuthManager()
        self.rbac = RBAC()
        self.encryption = EncryptionService()
        self.analytics = PostAnalytics(self.db)
        self.content_generator = ContentGenerator()
        self.moderator = ContentModerator()
        self.smart_scheduler = SmartScheduler(self.db)
        self.notifier = NotificationManager()
        self.plugin_manager = PluginManager()
        self.localization = Localization()
        
        self.audit_logger = AuditLogger(self.db)
        self.media_optimizer = MediaOptimizer()
        
        self.setup_handlers()
        self.running = True

    async def initialize(self):
        """Асинхронная инициализация"""
        await self.db.connect()
        await self.smart_scheduler.train_model()
        self.plugin_manager.load_plugins()
        
        # Запуск метрик Prometheus
        start_http_server(8000)

    def setup_handlers(self):
        """Настройка обработчиков"""
        # Основные команды
        self.dispatcher.add_handler(CommandHandler("start", self.start))
        self.dispatcher.add_handler(CommandHandler("help", self.help_command))
        self.dispatcher.add_handler(CommandHandler("post", self.post_to_channel))
        self.dispatcher.add_handler(CommandHandler("schedule", self.schedule_post))
        self.dispatcher.add_handler(CommandHandler("posts_list", self.list_scheduled_posts))
        self.dispatcher.add_handler(CommandHandler("stats", self.show_stats))
        self.dispatcher.add_handler(CommandHandler("menu", self.show_main_menu))
        self.dispatcher.add_handler(CommandHandler("cancel", self.cancel_action))
        self.dispatcher.add_handler(CommandHandler("delete_post", self.delete_post_command))
        self.dispatcher.add_handler(CommandHandler("generate_content", self.generate_content))
        self.dispatcher.add_handler(CommandHandler("analytics", self.show_analytics))
        
        # Обработчики кнопок
        self.dispatcher.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Обработчики сообщений
        self.dispatcher.add_handler(
            MessageHandler(Filters.TEXT & ~Filters.COMMAND, self.handle_message)
        )
        self.dispatcher.add_handler(
            MessageHandler(Filters.PHOTO, self.handle_photo)
        )
        self.dispatcher.add_handler(
            MessageHandler(Filters.DOCUMENT, self.handle_document)
        )
        self.dispatcher.add_handler(
            MessageHandler(Filters.VOICE, self.handle_voice)
        )

    async def start(self, update: Update, context):
        """Улучшенный обработчик команды /start"""
        user = update.effective_user
        
        # Логируем действие
        await self.audit_logger.log_action(user.id, "start", {"username": user.username})
        
        welcome_text = self.localization.gettext(
            f"""🎉 Добро пожаловать, {user.first_name}!

🤖 Я - ваш помощник в управлении каналом {self.config.CHANNEL_ID}

✨ <b>Что я умею:</b>
• 📝 Создавать и публиковать посты
• 🧠 Генерировать контент с помощью AI
• 📅 Умное планирование публикаций
• 🖼️ Работать с фото и документами
• 📊 Детальная аналитика
• 🔒 Безопасность и аудит

👇 Используйте кнопки ниже для навигации:""",
            user.language_code or 'ru'
        )
        
        if self.is_admin(update):
            await update.message.reply_text(
                welcome_text,
                reply_markup=self.get_admin_keyboard(),
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                self.localization.gettext("👋 Привет! Этот бот предназначен для администраторов канала.", 
                                       user.language_code or 'ru'),
                reply_markup=self.get_main_keyboard()
            )

    async def generate_content(self, update: Update, context):
        """Генерация контента с помощью AI"""
        if not self.is_admin(update):
            await update.message.reply_text("❌ Эта команда только для администраторов!")
            return
        
        if not context.args:
            await update.message.reply_text(
                "🤖 Использование: /generate_content <тема> [тон]\n"
                "Пример: /generate_content технологии профессиональный"
            )
            return
        
        topic = context.args[0]
        tone = context.args[1] if len(context.args) > 1 else "professional"
        
        await update.message.reply_text("🧠 Генерирую контент...")
        
        try:
            generated_content = await self.content_generator.generate_post(topic, tone)
            await update.message.reply_text(
                f"📝 Сгенерированный контент:\n\n{generated_content}\n\n"
                f"Используйте /post чтобы опубликовать"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка генерации: {e}")

    async def show_analytics(self, update: Update, context):
        """Показать расширенную аналитику"""
        if not self.is_admin(update):
            await update.message.reply_text("❌ Эта команда только для администраторов!")
            return
        
        posts = await self.db.get_all_scheduled_posts()
        now = datetime.now()
        
        upcoming = [p for p in posts if p['scheduled_time'] > now]
        published = [p for p in posts if p['scheduled_time'] <= now]
        
        # Агрегированная статистика
        total_views = sum([await self.analytics.get_post_stats(p['id'])['views'] for p in published])
        total_engagements = sum([await self.analytics.get_post_stats(p['id'])['engagements'] for p in published])
        
        analytics_text = (
            f"📊 <b>Расширенная аналитика</b>\n\n"
            f"• 📈 Всего постов: {len(posts)}\n"
            f"• ⏳ Ожидают публикации: {len(upcoming)}\n"
            f"• ✅ Опубликовано: {len(published)}\n"
            f"• 👁️ Всего просмотров: {total_views}\n"
            f"• 💬 Всего взаимодействий: {total_engagements}\n"
            f"• 📅 Эффективность: {total_engagements/max(total_views, 1)*100:.1f}%\n\n"
            f"<i>Данные обновляются в реальном времени</i>"
        )
        
        await update.message.reply_text(analytics_text, parse_mode="HTML")

    async def handle_voice(self, update: Update, context):
        """Обработка голосовых сообщений"""
        if not self.is_admin(update):
            return
        
        voice = update.message.voice
        voice_file = await voice.get_file()
        
        # Скачиваем и конвертируем голосовое в текст
        try:
            recognizer = sr.Recognizer()
            with sr.AudioFile(voice_file.file_path) as source:
                audio = recognizer.record(source)
                text = recognizer.recognize_google(audio, language="ru-RU")
                
            await update.message.reply_text(
                f"🎤 Распознанный текст:\n{text}\n\n"
                f"Используйте /post чтобы опубликовать"
            )
            
            # Сохраняем для дальнейшего использования
            context.user_data['last_voice_text'] = text
            
        except Exception as e:
            await update.message.reply_text("❌ Не удалось распознать голосовое сообщение")

    async def schedule_post(self, update: Update, context):
        """Улучшенное планирование поста с валидацией"""
        if not self.is_admin(update):
            await update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        if not context.args:
            await update.message.reply_text(
                "⏰ Формат: /schedule \"текст сообщения\" HH:MM DD.MM.YYYY\n"
                "Пример: /schedule \"Привет мир!\" 14:30 25.12.2024"
            )
            return

        try:
            message_parts = " ".join(context.args).split('"')
            if len(message_parts) < 3:
                raise ValueError("Неверный формат сообщения. Используйте кавычки для текста.")
            
            message_text = message_parts[1]
            time_date = message_parts[2].strip().split()
            
            if len(time_date) < 2:
                raise ValueError("Укажите время и дату")
            
            time_str = time_date[0]
            date_str = time_date[1] if len(time_date) > 1 else datetime.now().strftime("%d.%m.%Y")
            
            scheduled_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            
            # Валидация контента
            if not await self.moderator.check_content(message_text):
                await update.message.reply_text("❌ Контент не прошел модерацию")
                return
            
            # Умное планирование
            optimal_time = await self.smart_scheduler.suggest_optimal_time()
            time_diff = (scheduled_time - optimal_time).total_seconds() / 3600
            
            post_id = await self.db.add_scheduled_post(
                update.effective_chat.id,
                message_text,
                scheduled_time
            )
            
            # Отправляем уведомление
            await self.notifier.send_email(
                "admin@example.com",
                "Новый пост запланирован",
                f"Пост {post_id} запланирован на {scheduled_time}"
            )
            
            response_text = (
                f"✅ Пост запланирован на {scheduled_time.strftime('%d.%m.%Y %H:%M')}\n"
                f"🆔 ID поста: {post_id}\n"
            )
            
            if abs(time_diff) > 2:
                response_text += f"💡 Совет: оптимальное время для публикации - {optimal_time.strftime('%H:%M')}\n"
            
            await update.message.reply_text(response_text)
            
        except ValueError as e:
            await update.message.reply_text(f"❌ Ошибка формата: {e}")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def process_pending_posts(self):
        """Фоновая обработка отложенных постов"""
        while self.running:
            try:
                posts = await self.db.get_pending_posts()
                for post in posts:
                    try:
                        await self.send_scheduled_post(post)
                        await self.db.update_post_status(post['id'], 'published')
                        POSTS_PUBLISHED.inc()
                        
                        # Логируем успешную публикацию
                        await self.audit_logger.log_action(
                            post['chat_id'], 
                            "post_published", 
                            {"post_id": post['id'], "channel": self.config.CHANNEL_ID}
                        )
                        
                    except Exception as e:
                        FAILED_POSTS.inc()
                        logging.error(f"Error sending scheduled post {post['id']}: {e}")
                        
                        # Уведомление об ошибке
                        await self.notifier.send_sms(
                            "+1234567890", 
                            f"Ошибка публикации поста {post['id']}: {e}"
                        )
                
                await asyncio.sleep(60)  # Проверка каждую минуту
            except Exception as e:
                logging.error(f"Error in post processor: {e}")
                await asyncio.sleep(60)

    async def send_scheduled_post(self, post):
        """Улучшенная отправка поста с оптимизацией медиа"""
        try:
            media_path = post['media_path']
            message_text = post['message_text']
            
            if media_path and os.path.exists(media_path):
                with open(media_path, 'rb') as media_file:
                    media_data = media_file.read()
                
                # Оптимизация изображений
                if media_path.lower().endswith(('.jpg', '.jpeg', '.png')):
                    optimized_data = await self.media_optimizer.optimize_image(media_data)
                    
                    await self.application.bot.send_photo(
                        chat_id=self.config.CHANNEL_ID,
                        photo=optimized_data,
                        caption=message_text,
                        parse_mode="HTML"
                    )
                else:
                    await self.application.bot.send_document(
                        chat_id=self.config.CHANNEL_ID,
                        document=media_data,
                        caption=message_text,
                        parse_mode="HTML"
                    )
                
                # Очистка временного файла
                os.remove(media_path)
            else:
                await self.application.bot.send_message(
                    chat_id=self.config.CHANNEL_ID,
                    text=message_text,
                    parse_mode="HTML"
                )
            
            # Трек analytics
            await self.analytics.track_post_metrics(post['id'], post['id'])
            
        except Exception as e:
            logging.error(f"Error sending scheduled post: {e}")
            raise

    def get_admin_keyboard(self):
        """Расширенная админ клавиатура"""
        keyboard = [
            [
                KeyboardButton("📝 Быстрый пост"), 
                KeyboardButton("🧠 AI Генерация")
            ],
            [
                KeyboardButton("📅 Умный планировщик"), 
                KeyboardButton("📊 Аналитика")
            ],
            [
                KeyboardButton("🖼️ Медиа меню"), 
                KeyboardButton("⚡ Инструменты")
            ],
            [
                KeyboardButton("🔐 Безопасность"), 
                KeyboardButton("❓ Помощь")
            ]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)

    # Остальные методы (handle_message, button_handler, etc.) остаются аналогичными,
    # но адаптируются под асинхронную работу и новые функции

    def is_admin(self, update: Update) -> bool:
        """Проверка прав администратора"""
        user_id = update.effective_user.id
        return user_id in self.config.ADMIN_IDS

    async def stop(self):
        """Корректная остановка бота"""
        self.running = False
        await self.application.stop()
        await self.application.shutdown()
        if self.db.pool:
            await self.db.pool.close()

    async def run(self):
        """Запуск бота"""
        await self.initialize()
        
        # Запуск фоновых задач
        asyncio.create_task(self.process_pending_posts())
        
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        
        logging.info("🤖 Продвинутый бот запущен!")

# ==================== FASTAPI SERVER ====================
app = FastAPI(title="Telegram Bot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health checks
health = HealthCheck()
app.add_middleware(HealthCheckMiddleware, health_check=health)

# Глобальный инстанс бота
bot_instance = None

@app.on_event("startup")
async def startup_event():
    global bot_instance
    bot_instance = AdvancedChannelBot()
    asyncio.create_task(bot_instance.run())

@app.get("/")
async def root():
    return {"status": "Bot is running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/metrics")
async def metrics():
    return generate_latest()

@app.post("/api/v1/schedule")
async def api_schedule_post(post: PostCreate, token: str = Depends(lambda: None)):
    """API для планирования постов"""
    # Здесь должна быть проверка JWT токена
    post_id = await bot_instance.db.add_scheduled_post(
        0,  # system user
        post.text,
        post.scheduled_time,
        post.media_path,
        post.media_type
    )
    return {"post_id": post_id, "status": "scheduled"}

@app.get("/api/v1/analytics")
async def get_analytics():
    """API для получения аналитики"""
    posts = await bot_instance.db.get_all_scheduled_posts()
    return {
        "total_posts": len(posts),
        "scheduled": len([p for p in posts if p['scheduled_time'] > datetime.now()]),
        "published": len([p for p in posts if p['scheduled_time'] <= datetime.now()])
    }

# ==================== ЗАПУСК ====================
async def main():
    bot = AdvancedChannelBot()
    
    # Обработчики сигналов для graceful shutdown
    def signal_handler(signum, frame):
        print(f"\n🛑 Получен сигнал {signum}. Останавливаем бота...")
        asyncio.create_task(bot.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await bot.run()
    except Exception as e:
        logging.error(f"Ошибка при запуске бота: {e}")
        await bot.stop()

if __name__ == "__main__":
    # Запуск как standalone бот
    asyncio.run(main())
