"""
Утилиты для бота Calorigram
"""
import logging
from typing import Optional, Dict, Any
import re

logger = logging.getLogger(__name__)

def sanitize_input(text: str, max_length: int = 1000) -> str:
    """Очищает и обрезает пользовательский ввод"""
    if not text:
        return ""
    
    # Удаляем лишние пробелы
    text = re.sub(r'\s+', ' ', text.strip())
    
    # Обрезаем до максимальной длины
    if len(text) > max_length:
        text = text[:max_length]
        logger.warning(f"Input truncated to {max_length} characters")
    
    return text

def validate_telegram_id(telegram_id: int) -> bool:
    """Проверяет валидность Telegram ID"""
    return isinstance(telegram_id, int) and telegram_id > 0

def format_calories(calories: int) -> str:
    """Форматирует калории для отображения"""
    if calories < 1000:
        return f"{calories} ккал"
    else:
        return f"{calories:,} ккал".replace(',', ' ')

def format_weight(weight: float) -> str:
    """Форматирует вес для отображения"""
    if weight == int(weight):
        return f"{int(weight)} г"
    else:
        return f"{weight:.1f} г"

def safe_get_user_data(user_data: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Безопасно получает данные пользователя"""
    try:
        return user_data.get(key, default)
    except (KeyError, TypeError, AttributeError):
        logger.warning(f"Failed to get user data for key: {key}")
        return default

def is_valid_image_format(file_path: str) -> bool:
    """Проверяет, является ли файл изображением"""
    valid_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    return any(file_path.lower().endswith(ext) for ext in valid_extensions)

def is_valid_audio_format(file_path: str) -> bool:
    """Проверяет, является ли файл аудио"""
    valid_extensions = {'.ogg', '.mp3', '.wav', '.m4a', '.aac'}
    return any(file_path.lower().endswith(ext) for ext in valid_extensions)
