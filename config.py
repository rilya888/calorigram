import os
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Конфигурация для телеграм бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "8272323776:AAGHpudMA5-he1af62No0UPbuFU6VsvWEWY")

# API ключи
API_KEYS = {
    "nebius_api": os.getenv("NEBIUS_API_KEY", "eyJhbGciOiJIUzI1NiIsImtpZCI6IlV6SXJWd1h0dnprLVRvdzlLZWstc0M1akptWXBvX1VaVkxUZlpnMDRlOFUiLCJ0eXAiOiJKV1QifQ.eyJzdWIiOiJnb29nbGUtb2F1dGgyfDEwNzkyNjYwMzM0NjczMTE1MjQ5NiIsInNjb3BlIjoib3BlbmlkIG9mZmxpbmVfYWNjZXNzIiwiaXNzIjoiYXBpX2tleV9pc3N1ZXIiLCJhdWQiOlsiaHR0cHM6Ly9uZWJpdXMtaW5mZXJlbmNlLmV1LmF1dGgwLmNvbS9hcGkvdjIvIl0sImV4cCI6MTkxNTI4NjgyOCwidXVpZCI6IjAxOTkzOTg3LWRhNTgtN2JkMS1hZWZkLWFhYTFiOGNiMWRkNCIsIm5hbWUiOiJjYWxvcmlncmFtIiwiZXhwaXJlc19hdCI6IjIwMzAtMDktMTBUMTY6MDc6MDgrMDAwMCJ9.biU76hT9EOn-jQelsN7yfPFVKBfz8-162cpBOerXDdk")
}

# API настройки
BASE_URL = "https://api.studio.nebius.ai/v1/"

# Настройки базы данных
import os

# Определяем тип базы данных на основе переменной окружения
DATABASE_TYPE = os.getenv("DATABASE_TYPE", "sqlite")

if DATABASE_TYPE == "postgresql":
    # PostgreSQL настройки для Railway
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is required for PostgreSQL")
else:
    # SQLite для локальной разработки
    DATABASE_PATH = "users.db"
