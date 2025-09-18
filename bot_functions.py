import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import API_KEYS, BASE_URL, BOT_TOKEN
from database import (get_db_connection, get_user_by_telegram_id, create_user, delete_user_by_telegram_id, 
                     add_meal, get_user_meals, get_daily_calories, get_meal_statistics, delete_meal, get_daily_meals_by_type, is_meal_already_added, get_weekly_meals_by_type, delete_today_meals, delete_all_user_meals,
                     get_all_users, get_user_count, get_meals_count, get_recent_meals, get_daily_stats,
                     check_user_subscription, activate_premium_subscription, get_daily_calorie_checks_count, add_calorie_check)
from constants import (
    MIN_AGE, MAX_AGE, MIN_HEIGHT, MAX_HEIGHT, MIN_WEIGHT, MAX_WEIGHT,
    ERROR_MESSAGES, SUCCESS_MESSAGES, ACTIVITY_LEVELS, GENDERS, CALLBACK_DATA,
    ADMIN_IDS, ADMIN_CALLBACKS
)
from utils import sanitize_input, validate_telegram_id, format_calories, format_weight
import re

# Логирование уже настроено в main.py
logger = logging.getLogger(__name__)

def extract_calories_from_analysis(analysis_text: str) -> Optional[int]:
    """Извлекает общую калорийность блюда из текста анализа"""
    try:
        # Ищем паттерны для общей калорийности (не на 100г)
        patterns = [
            r'Общая калорийность:\s*(\d+)\s*ккал',
            r'Общее количество калорий:\s*(\d+)\s*ккал',
            r'Калорийность блюда:\s*(\d+)\s*ккал',
            r'Калорийность:\s*(\d+)\s*ккал\s*$',  # В конце строки
            r'(\d+)\s*ккал\s*$',  # Просто число ккал в конце
            r'калорийность:\s*(\d+)',
            r'калорий:\s*(\d+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, analysis_text, re.IGNORECASE | re.MULTILINE)
            if match:
                calories = int(match.group(1))
                # Проверяем разумность значения (от 10 до 10000 калорий)
                if 10 <= calories <= 10000:
                    logger.info(f"Extracted calories: {calories} from pattern: {pattern}")
                    return calories
        
        # Если не нашли общую калорийность, ищем любую калорийность
        fallback_patterns = [
            r'(\d+)\s*ккал',
            r'калорийность:\s*(\d+)',
            r'калорий:\s*(\d+)'
        ]
        
        for pattern in fallback_patterns:
            match = re.search(pattern, analysis_text, re.IGNORECASE)
            if match:
                calories = int(match.group(1))
                if 10 <= calories <= 10000:
                    logger.info(f"Extracted calories (fallback): {calories} from pattern: {pattern}")
                    return calories
        
        return None
    except Exception as e:
        logger.error(f"Error extracting calories from analysis: {e}")
        return None

def extract_dish_name_from_analysis(analysis_text: str) -> Optional[str]:
    """Извлекает название блюда из текста анализа"""
    try:
        # Ищем паттерн "Название: [название]"
        pattern = r'Название:\s*([^\n]+)'
        match = re.search(pattern, analysis_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    except Exception as e:
        logger.error(f"Error extracting dish name from analysis: {e}")
        return None

def parse_quantity_from_description(description: str) -> Tuple[float, str]:
    """Парсит количество и единицу измерения из описания блюда"""
    try:
        description = description.lower().strip()
        
        # Паттерны для поиска количества и единиц измерения
        patterns = [
            # Килограммы
            (r'(\d+(?:\.\d+)?)\s*кг', lambda x: float(x) * 1000, 'г'),
            (r'(\d+(?:\.\d+)?)\s*килограмм', lambda x: float(x) * 1000, 'г'),
            (r'(\d+(?:\.\d+)?)\s*kg', lambda x: float(x) * 1000, 'г'),
            
            # Граммы
            (r'(\d+(?:\.\d+)?)\s*г', lambda x: float(x), 'г'),
            (r'(\d+(?:\.\d+)?)\s*грамм', lambda x: float(x), 'г'),
            (r'(\d+(?:\.\d+)?)\s*g', lambda x: float(x), 'г'),
            
            # Литры
            (r'(\d+(?:\.\d+)?)\s*л', lambda x: float(x) * 1000, 'мл'),
            (r'(\d+(?:\.\d+)?)\s*литр', lambda x: float(x) * 1000, 'мл'),
            (r'(\d+(?:\.\d+)?)\s*l', lambda x: float(x) * 1000, 'мл'),
            
            # Миллилитры
            (r'(\d+(?:\.\d+)?)\s*мл', lambda x: float(x), 'мл'),
            (r'(\d+(?:\.\d+)?)\s*миллилитр', lambda x: float(x), 'мл'),
            (r'(\d+(?:\.\d+)?)\s*ml', lambda x: float(x), 'мл'),
            
            # Штуки (приблизительно по 100г)
            (r'(\d+)\s*шт', lambda x: float(x) * 100, 'г'),
            (r'(\d+)\s*штук', lambda x: float(x) * 100, 'г'),
            (r'(\d+)\s*штуки', lambda x: float(x) * 100, 'г'),
            (r'(\d+)\s*pc', lambda x: float(x) * 100, 'г'),
            
            # Порции (приблизительно 200г)
            (r'(\d+)\s*порц', lambda x: float(x) * 200, 'г'),
            (r'(\d+)\s*порции', lambda x: float(x) * 200, 'г'),
            (r'(\d+)\s*порция', lambda x: float(x) * 200, 'г'),
            
            # Стаканы (приблизительно 250г)
            (r'(\d+)\s*стакан', lambda x: float(x) * 250, 'г'),
            (r'(\d+)\s*стакана', lambda x: float(x) * 250, 'г'),
            (r'(\d+)\s*стаканов', lambda x: float(x) * 250, 'г'),
            
            # Ложки столовые (приблизительно 15г)
            (r'(\d+)\s*ст\.\s*л\.', lambda x: float(x) * 15, 'г'),
            (r'(\d+)\s*столовых ложек', lambda x: float(x) * 15, 'г'),
            (r'(\d+)\s*столовые ложки', lambda x: float(x) * 15, 'г'),
            
            # Ложки чайные (приблизительно 5г)
            (r'(\d+)\s*ч\.\s*л\.', lambda x: float(x) * 5, 'г'),
            (r'(\d+)\s*чайных ложек', lambda x: float(x) * 5, 'г'),
            (r'(\d+)\s*чайные ложки', lambda x: float(x) * 5, 'г'),
        ]
        
        for pattern, converter, unit in patterns:
            match = re.search(pattern, description)
            if match:
                quantity = converter(match.group(1))
                logger.info(f"Parsed quantity: {quantity}{unit} from '{description}'")
                return quantity, unit
        
        # Если не нашли количество, возвращаем стандартную порцию
        logger.info(f"No quantity found in '{description}', using default 100g")
        return 100.0, 'г'
        
    except Exception as e:
        logger.error(f"Error parsing quantity from description '{description}': {e}")
        return 100.0, 'г'

def is_valid_analysis(analysis_text: str) -> bool:
    """Проверяет, является ли анализ валидным (содержит калории)"""
    calories = extract_calories_from_analysis(analysis_text)
    return calories is not None and calories > 0

def clean_markdown_text(text: str) -> str:
    """Очищает текст от проблемных символов Markdown для Telegram"""
    try:
        # Экранируем проблемные символы
        text = text.replace('*', '\\*')
        text = text.replace('_', '\\_')
        text = text.replace('[', '\\[')
        text = text.replace(']', '\\]')
        text = text.replace('`', '\\`')
        text = text.replace('~', '\\~')
        text = text.replace('>', '\\>')
        text = text.replace('#', '\\#')
        text = text.replace('+', '\\+')
        text = text.replace('-', '\\-')
        text = text.replace('=', '\\=')
        text = text.replace('|', '\\|')
        text = text.replace('{', '\\{')
        text = text.replace('}', '\\}')
        text = text.replace('.', '\\.')
        text = text.replace('!', '\\!')
        return text
    except Exception as e:
        logger.error(f"Error cleaning markdown text: {e}")
        return text

def remove_explanations_from_analysis(text: str) -> str:
    """Удаляет пояснения и дополнительные расчеты из анализа ИИ"""
    try:
        # Ищем раздел "Пояснение расчетов" и обрезаем его
        explanation_patterns = [
            r'### Пояснение расчетов:.*$',
            r'## Пояснение расчетов:.*$',
            r'# Пояснение расчетов:.*$',
            r'Пояснение расчетов:.*$',
            r'Таким образом.*$',
            r'Итак.*$',
            r'В итоге.*$',
            r'Итого.*$'
        ]
        
        for pattern in explanation_patterns:
            text = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Убираем лишние переносы строк в конце
        text = text.rstrip('\n')
        
        return text
    except Exception as e:
        logger.error(f"Error removing explanations from analysis: {e}")
        return text

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    return user_id in ADMIN_IDS

def check_subscription_access(telegram_id: int) -> dict:
    """Проверяет доступ пользователя к функциям бота"""
    try:
        subscription = check_user_subscription(telegram_id)
        
        if subscription['is_active']:
            return {
                'has_access': True,
                'subscription_type': subscription['type'],
                'expires_at': subscription['expires_at']
            }
        else:
            return {
                'has_access': False,
                'subscription_type': subscription['type'],
                'expires_at': subscription['expires_at']
            }
    except Exception as e:
        logger.error(f"Error checking subscription access: {e}")
        return {'has_access': False, 'subscription_type': 'error', 'expires_at': None}

def get_subscription_message(access_info: dict) -> str:
    """Возвращает сообщение о статусе подписки"""
    if access_info['has_access']:
        if access_info['subscription_type'] == 'trial':
            return f"🆓 **Триальный период**\n\nДоступен до: {access_info['expires_at']}\n\nПосле истечения триального периода потребуется подписка для продолжения использования бота."
        elif access_info['subscription_type'] == 'premium':
            return f"⭐ **Премиум подписка**\n\nДействует до: {access_info['expires_at'] or 'Бессрочно'}\n\nСпасибо за поддержку!"
    else:
        if access_info['subscription_type'] == 'trial_expired':
            return "❌ **Триальный период истек**\n\nДля продолжения использования бота необходимо оформить подписку.\n\n💰 **Тарифы:**\n• 1 месяц - 299₽\n• 3 месяца - 799₽ (скидка 11%)\n• 6 месяцев - 1499₽ (скидка 17%)\n• 12 месяцев - 2799₽ (скидка 22%)\n\n💳 Для оформления подписки обратитесь к администратору."
        else:
            return "❌ **Нет активной подписки**\n\nДля использования бота необходимо оформить подписку.\n\n💰 **Тарифы:**\n• 1 месяц - 299₽\n• 3 месяца - 799₽ (скидка 11%)\n• 6 месяцев - 1499₽ (скидка 17%)\n• 12 месяцев - 2799₽ (скидка 22%)\n\n💳 Для оформления подписки обратитесь к администратору."

def validate_age(age: str) -> Optional[int]:
    """Валидация возраста"""
    try:
        age_int = int(age)
        if MIN_AGE <= age_int <= MAX_AGE:
            return age_int
        return None
    except ValueError:
        return None

def validate_height(height: str) -> Optional[float]:
    """Валидация роста"""
    try:
        height_float = float(height)
        if MIN_HEIGHT <= height_float <= MAX_HEIGHT:
            return height_float
        return None
    except ValueError:
        return None

def validate_weight(weight: str) -> Optional[float]:
    """Валидация веса"""
    try:
        weight_float = float(weight)
        if MIN_WEIGHT <= weight_float <= MAX_WEIGHT:
            return weight_float
        return None
    except ValueError:
        return None

async def check_user_registration(user_id: int) -> Optional[Tuple[Any, ...]]:
    """Проверяет, зарегистрирован ли пользователь"""
    return get_user_by_telegram_id(user_id)

async def send_not_registered_message(update, context):
    """Отправляет сообщение о том, что пользователь не зарегистрирован"""
    message = ERROR_MESSAGES['user_not_registered']
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(message)
    elif hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.message.reply_text(message)

def get_main_menu_keyboard():
    """Создает клавиатуру главного меню"""
    keyboard = [
        [InlineKeyboardButton("🍽️ Добавить блюдо", callback_data="add_dish")],
        [InlineKeyboardButton("🔍 Узнать калории", callback_data="check_calories")],
        [InlineKeyboardButton("📊 Статистика", callback_data="statistics")],
        [InlineKeyboardButton("⭐ Подписка", callback_data="subscription")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    welcome_message = f"""
Привет, {user.first_name}! 👋

Добро пожаловать в Calorigram - бот для подсчета калорий!

Я помогу тебе:
• Рассчитать суточную норму калорий
• Отслеживать твой прогресс
• Давать рекомендации по питанию

Для начала работы используй команду /register
    """
    
    keyboard = [
        [InlineKeyboardButton("📝 Регистрация", callback_data="register")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📋 Доступные команды:

/start - Начать работу с ботом
/register - Регистрация в системе
/profile - Посмотреть профиль
/add - Добавить блюдо
/addmeal - Анализ блюда (фото/текст/голос)
/addphoto - Анализ фото еды ИИ
/addtext - Анализ описания блюда ИИ
/addvoice - Анализ голосового описания ИИ
/reset - Удалить все данные регистрации
/help - Показать эту справку

🔧 Функции бота:
• Расчет суточной нормы калорий
• Отслеживание прогресса
• Рекомендации по питанию
• Добавление блюд по приемам пищи
• Анализ фотографий еды с помощью ИИ
• Анализ текстового описания блюд
• Анализ голосовых сообщений
• Безопасное удаление данных
    """
    
    # Проверяем, это команда или callback запрос
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(help_text, reply_markup=get_main_menu_keyboard())
    elif hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.message.reply_text(help_text, reply_markup=get_main_menu_keyboard())

async def subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /subscription"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        user_data = await check_user_registration(user.id)
        if not user_data:
            # Проверяем, это команда или callback запрос
            if hasattr(update, 'message') and update.message:
                await update.message.reply_text(
                    "❌ Вы не зарегистрированы в системе!\n"
                    "Используйте /register для регистрации.",
                    reply_markup=get_main_menu_keyboard()
                )
            elif hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.message.reply_text(
                    "❌ Вы не зарегистрированы в системе!\n"
                    "Используйте /register для регистрации.",
                    reply_markup=get_main_menu_keyboard()
                )
            return
        
        # Получаем информацию о подписке
        access_info = check_subscription_access(user.id)
        subscription_msg = get_subscription_message(access_info)
        
        # Проверяем, это команда или callback запрос
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text(
                subscription_msg,
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
        elif hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.message.reply_text(
                subscription_msg,
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
        
    except Exception as e:
        logger.error(f"Error in subscription_command: {e}")
        # Проверяем, это команда или callback запрос
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text(
                "❌ Произошла ошибка при проверке подписки. Попробуйте позже.",
                reply_markup=get_main_menu_keyboard()
            )
        elif hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.message.reply_text(
                "❌ Произошла ошибка при проверке подписки. Попробуйте позже.",
                reply_markup=get_main_menu_keyboard()
            )

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /register"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        existing_user = await check_user_registration(user.id)
        
        if existing_user:
            await update.message.reply_text("Вы уже зарегистрированы! Используйте /profile для просмотра данных.")
            return
        
        # Сохраняем состояние регистрации
        context.user_data['registration_step'] = 'name'
        context.user_data['user_data'] = {'telegram_id': user.id}
        
        await update.message.reply_text(
            "Давайте зарегистрируем вас в системе!\n\n"
            "Введите ваше имя:"
        )
    except Exception as e:
        logger.error(f"Error in register_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
        )

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений для регистрации и анализа блюд"""
    # Проверяем, ожидается ли ввод Telegram ID для админки
    if context.user_data.get('admin_waiting_for_telegram_id', False):
        await handle_admin_telegram_id_input(update, context)
        return
    
    # Проверяем, ожидается ли текстовое описание блюда
    if context.user_data.get('waiting_for_text', False) or context.user_data.get('waiting_for_check_text', False):
        await handle_food_text_analysis(update, context)
        return
    
    # Обработка регистрации
    if 'registration_step' not in context.user_data:
        await update.message.reply_text("Используйте /start для начала работы с ботом")
        return
    
    text = update.message.text
    step = context.user_data['registration_step']
    user_data = context.user_data['user_data']
    
    if step == 'name':
        user_data['name'] = text
        context.user_data['registration_step'] = 'gender'
        
        keyboard = [
            [InlineKeyboardButton("👨 Мужской", callback_data="gender_male")],
            [InlineKeyboardButton("👩 Женский", callback_data="gender_female")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "Выберите ваш пол:",
            reply_markup=reply_markup
        )
        
    elif step == 'age':
        age = validate_age(text)
        if age is None:
            await update.message.reply_text(f"Пожалуйста, введите корректный возраст ({MIN_AGE}-{MAX_AGE}):")
            return
        user_data['age'] = age
        context.user_data['registration_step'] = 'height'
        await update.message.reply_text("Введите ваш рост в см:")
            
    elif step == 'height':
        height = validate_height(text)
        if height is None:
            await update.message.reply_text(f"Пожалуйста, введите корректный рост ({MIN_HEIGHT}-{MAX_HEIGHT} см):")
            return
        user_data['height'] = height
        context.user_data['registration_step'] = 'weight'
        await update.message.reply_text("Введите ваш вес в кг:")
            
    elif step == 'weight':
        weight = validate_weight(text)
        if weight is None:
            await update.message.reply_text(f"Пожалуйста, введите корректный вес ({MIN_WEIGHT}-{MAX_WEIGHT} кг):")
            return
        user_data['weight'] = weight
        context.user_data['registration_step'] = 'activity'
        
        keyboard = [
            [InlineKeyboardButton("🛌 Минимальная", callback_data="activity_minimal")],
            [InlineKeyboardButton("🏃 Легкая", callback_data="activity_light")],
            [InlineKeyboardButton("💪 Умеренная", callback_data="activity_moderate")],
            [InlineKeyboardButton("🔥 Высокая", callback_data="activity_high")],
            [InlineKeyboardButton("⚡ Очень высокая", callback_data="activity_very_high")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "Выберите ваш уровень активности:",
            reply_markup=reply_markup
        )

async def handle_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора уровня активности"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('activity_'):
        # Проверяем, есть ли данные пользователя
        if 'user_data' not in context.user_data:
            await query.message.reply_text(
                "❌ Ошибка: данные регистрации не найдены.\n"
                "Пожалуйста, начните регистрацию заново с помощью /register"
            )
            return
            
        activity_levels = {
            'activity_minimal': 'Минимальная',
            'activity_light': 'Легкая',
            'activity_moderate': 'Умеренная',
            'activity_high': 'Высокая',
            'activity_very_high': 'Очень высокая'
        }
        
        user_data = context.user_data['user_data']
        user_data['activity_level'] = activity_levels[query.data]
        
        # Получаем имя пользователя
        name = user_data.get('name', 'Пользователь')
        
        # Рассчитываем суточную норму калорий (упрощенная формула)
        daily_calories = calculate_daily_calories(
            user_data['age'],
            user_data['height'],
            user_data['weight'],
            user_data['gender'],
            user_data['activity_level']
        )
        user_data['daily_calories'] = daily_calories
        
        # Сохраняем пользователя в базу данных
        success = create_user(
            user_data['telegram_id'],
            user_data['name'],
            user_data['gender'],
            user_data['age'],
            user_data['height'],
            user_data['weight'],
            user_data['activity_level'],
            user_data['daily_calories']
        )
        
        if not success:
            await query.message.reply_text(
                "❌ Произошла ошибка при сохранении данных. Попробуйте регистрацию заново."
            )
            return
        
        # Очищаем данные регистрации
        context.user_data.pop('registration_step', None)
        context.user_data.pop('user_data', None)
        
        # Создаем кнопки для главного меню
        keyboard = [
            [InlineKeyboardButton("🍽️ Добавить блюдо", callback_data="add_dish")],
            [InlineKeyboardButton("📋 Меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Получаем информацию о подписке
        access_info = check_subscription_access(user_data['telegram_id'])
        subscription_msg = get_subscription_message(access_info)
        
        await query.message.reply_text(
            f"Привет {name}, ✅ **Регистрация завершена!**\n\n"
            f"Ваша суточная норма калорий: **{daily_calories} ккал**\n\n"
            f"{subscription_msg}\n\n"
            f"Выберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

def calculate_daily_calories(age, height, weight, gender: str, activity_level: str) -> int:
    """Рассчитывает суточную норму калорий по формуле Миффлин-Сен Жеор"""
    try:
        # Преобразуем данные в нужные типы
        age = int(age)
        height = float(height)
        weight = float(weight)
        
        logger.info(f"Calculating calories for: age={age}, height={height}, weight={weight}, gender={gender}, activity={activity_level}")
        
        # Формула Миффлин-Сен Жеор (более точная)
        if gender == 'Мужской':
            # BMR для мужчин = (10 * weight) + (6.25 * height) - (5 * age) + 5
            bmr = (10 * weight) + (6.25 * height) - (5 * age) + 5
        else:  # Женский
            # BMR для женщин = (10 * weight) + (6.25 * height) - (5 * age) - 161
            bmr = (10 * weight) + (6.25 * height) - (5 * age) - 161
        
        # Коэффициенты активности
        multiplier = ACTIVITY_LEVELS.get(activity_level, 1.55)
        daily_calories = int(bmr * multiplier)
        
        logger.info(f"Calculated BMR: {bmr}, multiplier: {multiplier}, daily_calories: {daily_calories}")
        
        # Проверяем разумность результата
        if daily_calories < 800 or daily_calories > 5000:
            logger.warning(f"Unusual daily calories calculated: {daily_calories} for user with age={age}, height={height}, weight={weight}, gender={gender}, activity={activity_level}")
        
        return daily_calories
        
    except Exception as e:
        logger.error(f"Error calculating daily calories: {e}, types: age={type(age)}, height={type(height)}, weight={type(weight)}")
        # Возвращаем среднее значение в случае ошибки
        return 2000

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /profile"""
    user = update.effective_user
    logger.info(f"Profile command called by user {user.id}")
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        user_data = await check_user_registration(user.id)
        
        if not user_data:
            await send_not_registered_message(update, context)
            return
        
        # Получаем информацию о подписке
        subscription_info = check_user_subscription(user.id)
        logger.info(f"Subscription info for user {user.id}: {subscription_info}")
        
        # Формируем текст о подписке
        subscription_text = ""
        if subscription_info['is_active']:
            if subscription_info['type'] == 'trial':
                subscription_text = f"🆓 Триальный период\nДоступен до: {subscription_info['expires_at']}"
            elif subscription_info['type'] == 'premium':
                if subscription_info['expires_at']:
                    subscription_text = f"⭐ Премиум подписка\nДействует до: {subscription_info['expires_at']}"
                else:
                    subscription_text = "⭐ Премиум подписка\nБез ограничений"
        else:
            if subscription_info['type'] == 'trial_expired':
                subscription_text = f"❌ Триальный период истек\nИстек: {subscription_info['expires_at']}"
            elif subscription_info['type'] == 'premium_expired':
                subscription_text = f"❌ Премиум подписка истекла\nИстекла: {subscription_info['expires_at']}"
            else:
                subscription_text = "❌ Нет активной подписки"
        
        profile_text = f"""
👤 Ваш профиль:

📝 Имя: {user_data[2]}
👤 Пол: {user_data[3]}
🎂 Возраст: {user_data[4]} лет
📏 Рост: {user_data[5]} см
⚖️ Вес: {user_data[6]} кг
🏃 Уровень активности: {user_data[7]}
🔥 Суточная норма калорий: {user_data[8]} ккал
📅 Дата регистрации: {user_data[9]}

{subscription_text}
        """
        
        await update.message.reply_text(profile_text, reply_markup=get_main_menu_keyboard())
    except Exception as e:
        logger.error(f"Error in profile_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при получении данных профиля. Попробуйте позже."
        )

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /reset"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        existing_user = await check_user_registration(user.id)
        
        if not existing_user:
            await send_not_registered_message(update, context)
            return
    except Exception as e:
        logger.error(f"Error in reset_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
        )
        return
    
    # Показываем предупреждение с кнопками подтверждения
    warning_text = """
⚠️ **ВНИМАНИЕ!** ⚠️

Вы собираетесь удалить ВСЕ ваши данные:
• Данные регистрации (имя, пол, возраст, рост, вес, уровень активности)
• Суточная норма калорий
• ВСЕ данные о приемах пищи за все время
• Статистика и история питания

🗑️ **УДАЛЕНИЕ БЕЗВОЗВРАТНО!**

Вы уверены, что хотите продолжить?
    """
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить все данные", callback_data="reset_confirm")],
        [InlineKeyboardButton("🔙 Вернуться в меню", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(warning_text, reply_markup=reply_markup, parse_mode='Markdown')

async def dayreset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /dayreset"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        user_data = await check_user_registration(user.id)
        if not user_data:
            await update.message.reply_text(
                "❌ Вы не зарегистрированы в системе!\n"
                "Используйте /register для регистрации."
            )
            return
        
        # Удаляем все приемы пищи за сегодня
        success = delete_today_meals(user.id)
        
        if success:
            await update.message.reply_text(
                "✅ **Данные за сегодня удалены!**\n\n"
                "Все приемы пищи за сегодняшний день были удалены.\n"
                "Теперь вы можете снова добавлять завтрак, обед и ужин.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "ℹ️ **Нет данных для удаления**\n\n"
                "У вас нет записей о приемах пищи за сегодняшний день.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error in dayreset command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при удалении данных. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard()
        )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /admin"""
    user = update.effective_user
    
    # Проверяем, является ли пользователь админом
    if not is_admin(user.id):
        await update.message.reply_text(
            "❌ У вас нет прав доступа к админ панели!",
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    await show_admin_panel(update, context)

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает админ панель"""
    try:
        # Получаем общую статистику
        user_count = get_user_count()
        meals_count = get_meals_count()
        daily_stats = get_daily_stats()
        
        admin_text = f"""
🔧 **Админ панель**

📊 **Общая статистика:**
• Всего пользователей: {user_count}
• Всего записей о еде: {meals_count}

📈 **За сегодня:**
• Активных пользователей: {daily_stats['active_users']}
• Записей о еде: {daily_stats['meals_today']}
• Общих калорий: {daily_stats['total_calories']}

Выберите действие:
        """
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data=ADMIN_CALLBACKS['admin_stats'])],
            [InlineKeyboardButton("👥 Пользователи", callback_data=ADMIN_CALLBACKS['admin_users'])],
            [InlineKeyboardButton("🍽️ Последние приемы пищи", callback_data=ADMIN_CALLBACKS['admin_meals'])],
            [InlineKeyboardButton("⭐ Управление подписками", callback_data=ADMIN_CALLBACKS['admin_subscriptions'])],
            [InlineKeyboardButton("📢 Рассылка", callback_data=ADMIN_CALLBACKS['admin_broadcast'])],
            [InlineKeyboardButton("🔙 Главное меню", callback_data=ADMIN_CALLBACKS['admin_back'])]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text(admin_text, reply_markup=reply_markup, parse_mode='Markdown')
        elif hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.message.reply_text(admin_text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error showing admin panel: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при загрузке админ панели. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard()
        )

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /add"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        existing_user = await check_user_registration(user.id)
        
        if not existing_user:
            await send_not_registered_message(update, context)
            return
    except Exception as e:
        logger.error(f"Error in add_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
        )
        return
    
    # Создаем подменю для выбора приема пищи
    keyboard = [
        [InlineKeyboardButton("🌅 Завтрак", callback_data="addmeal")],
        [InlineKeyboardButton("☀️ Обед", callback_data="addmeal")],
        [InlineKeyboardButton("🌙 Ужин", callback_data="addmeal")],
        [InlineKeyboardButton("🍎 Перекус", callback_data="addmeal")],
        [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🍽️ **Добавить блюдо**\n\n"
        "Выберите прием пищи:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def addmeal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /addmeal"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        existing_user = await check_user_registration(user.id)
        
        if not existing_user:
            await send_not_registered_message(update, context)
            return
    except Exception as e:
        logger.error(f"Error in addmeal_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
        )
        return
    
    # Создаем подменю для анализа блюда
    keyboard = [
        [InlineKeyboardButton("📷 Анализ по фото", callback_data="analyze_photo")],
        [InlineKeyboardButton("📝 Анализ по тексту", callback_data="analyze_text")],
        [InlineKeyboardButton("🎤 Анализ по голосовому", callback_data="analyze_voice")],
        [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🍽️ **Анализ блюда**\n\n"
        "Выберите способ анализа:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_addmeal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback запроса для addmeal"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        existing_user = await check_user_registration(user.id)
        
        if not existing_user:
            await query.message.reply_text(
                "❌ Вы не зарегистрированы в системе!\n"
                "Используйте /register для регистрации."
            )
            return
    except Exception as e:
        logger.error(f"Error in handle_addmeal_callback: {e}")
        await query.message.reply_text(
            "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
        )
        return
    
    # Создаем подменю для анализа блюда
    keyboard = [
        [InlineKeyboardButton("📷 Анализ по фото", callback_data="analyze_photo")],
        [InlineKeyboardButton("📝 Анализ по тексту", callback_data="analyze_text")],
        [InlineKeyboardButton("🎤 Анализ по голосовому", callback_data="analyze_voice")],
        [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "🍽️ **Анализ блюда**\n\n"
        "Выберите способ анализа:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def addphoto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /addphoto"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        existing_user = await check_user_registration(user.id)
        
        if not existing_user:
            await send_not_registered_message(update, context)
            return
        
        # Устанавливаем состояние ожидания фото
        context.user_data['waiting_for_photo'] = True
        
        await update.message.reply_text(
            "📸 **Анализ фотографии еды**\n\n"
            "Пришлите мне фото блюда, калорийность которого вы хотите оценить.\n\n"
            "⚠️ **Для более точного расчета на фото должны присутствовать якорные объекты:**\n"
            "• Вилка\n"
            "• Ложка\n"
            "• Рука\n"
            "• Монета\n"
            "• Другие объекты для масштаба\n\n"
            "Модель проанализирует фото и вернет:\n"
            "• Название блюда\n"
            "• Ориентировочный вес\n"
            "• Калорийность\n"
            "• Раскладку по БЖУ",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in addphoto_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
        )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback запросов"""
    query = update.callback_query
    
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Failed to answer callback query: {e}")
        # Продолжаем обработку даже если не удалось ответить на callback
    
    # Добавляем отладочную информацию
    logger.info(f"Callback query received: {query.data}")
    
    if query.data == "register":
        # Для callback query нужно использовать query.message.reply_text вместо update.message.reply_text
        user = update.effective_user
        
        # Проверяем, зарегистрирован ли пользователь
        try:
            existing_user = await check_user_registration(user.id)
            
            if existing_user:
                await query.message.reply_text(
                    "Вы уже зарегистрированы! Используйте /profile для просмотра данных."
                )
                return
        except Exception as e:
            logger.error(f"Error checking user registration: {e}")
            await query.message.reply_text(
                "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
            )
            return
        
        # Сохраняем состояние регистрации
        context.user_data['registration_step'] = 'name'
        context.user_data['user_data'] = {'telegram_id': user.id}
        
        await query.message.reply_text(
            "Давайте зарегистрируем вас в системе!\n\n"
            "Введите ваше имя:"
        )
    elif query.data == "help":
        await help_command(update, context)
    elif query.data == "subscription":
        await subscription_command(update, context)
    elif query.data.startswith('gender_'):
        await handle_gender_callback(update, context)
    elif query.data.startswith('activity_'):
        await handle_activity_callback(update, context)
    elif query.data == "reset_confirm":
        await handle_reset_confirm(update, context)
    elif query.data == "add_dish":
        await handle_add_dish(update, context)
    elif query.data == "check_calories":
        await handle_check_calories(update, context)
    elif query.data == "addmeal":
        await handle_addmeal_callback(update, context)
    elif query.data == "menu":
        await handle_menu(update, context)
    elif query.data == "profile":
        await handle_profile_callback(update, context)
    elif query.data == "back_to_main":
        await handle_back_to_main(update, context)
    elif query.data.startswith('meal_'):
        await handle_meal_selection(update, context)
    elif query.data == "analyze_photo":
        await handle_analyze_photo_callback(update, context)
    elif query.data == "analyze_text":
        await handle_analyze_text_callback(update, context)
    elif query.data == "analyze_voice":
        await handle_analyze_voice_callback(update, context)
    elif query.data == "check_photo":
        await handle_check_photo_callback(update, context)
    elif query.data == "check_text":
        await handle_check_text_callback(update, context)
    elif query.data == "check_voice":
        await handle_check_voice_callback(update, context)
    elif query.data == "statistics":
        await handle_statistics_callback(update, context)
    elif query.data == "stats_today":
        await handle_stats_today_callback(update, context)
    elif query.data == "stats_yesterday":
        await handle_stats_yesterday_callback(update, context)
    elif query.data == "stats_week":
        await handle_stats_week_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_stats']:
        await handle_admin_stats_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_users']:
        await handle_admin_users_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_meals']:
        await handle_admin_meals_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_broadcast']:
        await handle_admin_broadcast_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_subscriptions']:
        await handle_admin_subscriptions_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_check_subscription']:
        await handle_admin_check_subscription_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_manage_subscription']:
        await handle_admin_manage_subscription_callback(update, context)
    elif query.data.startswith(ADMIN_CALLBACKS['admin_activate_trial'] + ':'):
        await handle_admin_activate_trial_callback(update, context)
    elif query.data.startswith(ADMIN_CALLBACKS['admin_activate_premium'] + ':'):
        await handle_admin_activate_premium_callback(update, context)
    elif query.data.startswith(ADMIN_CALLBACKS['admin_deactivate_subscription'] + ':'):
        await handle_admin_deactivate_subscription_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_back']:
        await handle_admin_back_callback(update, context)
    elif query.data == ADMIN_CALLBACKS['admin_panel']:
        await show_admin_panel(update, context)
    else:
        # Если callback data не распознан
        logger.warning(f"Unknown callback data: {query.data}")
        await query.message.reply_text(
            "❌ Неизвестная команда. Попробуйте снова.",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора пола"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('gender_'):
        # Проверяем, есть ли данные пользователя
        if 'user_data' not in context.user_data:
            await query.message.reply_text(
                "❌ Ошибка: данные регистрации не найдены.\n"
                "Пожалуйста, начните регистрацию заново с помощью /register"
            )
            return
            
        gender_map = {
            'gender_male': 'Мужской',
            'gender_female': 'Женский'
        }
        
        user_data = context.user_data['user_data']
        user_data['gender'] = gender_map[query.data]
        context.user_data['registration_step'] = 'age'
        
        await query.message.reply_text("Введите ваш возраст:")

async def handle_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик подтверждения сброса данных"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    try:
        # Удаляем данные регистрации
        user_deleted = delete_user_by_telegram_id(user.id)
        
        # Удаляем все данные о приемах пищи
        meals_deleted = delete_all_user_meals(user.id)
        
        if user_deleted:
            # Очищаем данные пользователя из контекста
            context.user_data.clear()
            
            # Создаем кнопку для регистрации
            keyboard = [
                [InlineKeyboardButton("📝 Регистрация", callback_data="register")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Формируем сообщение о результатах удаления
            message = "✅ **Данные успешно удалены!**\n\n"
            message += "• Данные регистрации удалены\n"
            if meals_deleted:
                message += "• Все данные о приемах пищи удалены\n"
            else:
                message += "• Данные о приемах пищи не найдены\n"
            message += "\nВсе ваши данные были безвозвратно удалены."
            
            await query.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await query.message.reply_text(
                "❌ Ошибка: данные не найдены для удаления или произошла ошибка при удалении"
            )
    except Exception as e:
        logger.error(f"Error in handle_reset_confirm: {e}")
        await query.message.reply_text(
            "❌ Произошла ошибка при удалении данных. Попробуйте позже."
        )


async def handle_add_dish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Добавить блюдо'"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем подписку
    access_info = check_subscription_access(user.id)
    if not access_info['has_access']:
        subscription_msg = get_subscription_message(access_info)
        await query.message.reply_text(
            subscription_msg,
            reply_markup=get_main_menu_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    # Проверяем, какие приемы пищи уже добавлены сегодня
    breakfast_added = is_meal_already_added(user.id, 'meal_breakfast')
    lunch_added = is_meal_already_added(user.id, 'meal_lunch')
    dinner_added = is_meal_already_added(user.id, 'meal_dinner')
    
    # Создаем подменю для выбора приема пищи
    keyboard = []
    
    # Завтрак - только если не добавлен
    if not breakfast_added:
        keyboard.append([InlineKeyboardButton("🌅 Завтрак", callback_data="meal_breakfast")])
    
    # Обед - только если не добавлен
    if not lunch_added:
        keyboard.append([InlineKeyboardButton("☀️ Обед", callback_data="meal_lunch")])
    
    # Ужин - только если не добавлен
    if not dinner_added:
        keyboard.append([InlineKeyboardButton("🌙 Ужин", callback_data="meal_dinner")])
    
    # Перекус - всегда доступен
    keyboard.append([InlineKeyboardButton("🍎 Перекус", callback_data="meal_snack")])
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Формируем сообщение
    message_text = "🍽️ **Добавить блюдо**\n\n"
    message_text += "Выберите прием пищи:\n\n"
    message_text += "🍎 Перекус можно добавлять неограниченное количество раз"
    
    await query.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Меню'"""
    query = update.callback_query
    await query.answer()
    
    # Создаем главное меню с кнопками
    keyboard = [
        [InlineKeyboardButton("🍽️ Добавить блюдо", callback_data="add_dish")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "📋 **Главное меню**\n\n"
        "Выберите нужную функцию:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Профиль' из главного меню"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user.id,))
            user_data = cursor.fetchone()
        
        if not user_data:
            await query.message.reply_text(
                "❌ Вы не зарегистрированы в системе!\n"
                "Используйте /register для регистрации."
            )
            return
    except Exception as e:
        logger.error(f"Error in handle_profile_callback: {e}")
        await query.message.reply_text(
            "❌ Произошла ошибка при получении данных профиля. Попробуйте позже."
        )
        return
    
    # Получаем информацию о подписке
    subscription_info = check_user_subscription(user.id)
    logger.info(f"Profile callback - Subscription info for user {user.id}: {subscription_info}")
    
    # Формируем текст о подписке
    subscription_text = ""
    if subscription_info['is_active']:
        if subscription_info['type'] == 'trial':
            subscription_text = f"🆓 **Триальный период**\nДоступен до: {subscription_info['expires_at']}"
        elif subscription_info['type'] == 'premium':
            if subscription_info['expires_at']:
                subscription_text = f"⭐ **Премиум подписка**\nДействует до: {subscription_info['expires_at']}"
            else:
                subscription_text = "⭐ **Премиум подписка**\nБез ограничений"
    else:
        if subscription_info['type'] == 'trial_expired':
            subscription_text = f"❌ **Триальный период истек**\nИстек: {subscription_info['expires_at']}"
        elif subscription_info['type'] == 'premium_expired':
            subscription_text = f"❌ **Премиум подписка истекла**\nИстекла: {subscription_info['expires_at']}"
        else:
            subscription_text = "❌ **Нет активной подписки**"
    
    profile_text = f"""
👤 **Ваш профиль:**

📝 **Имя:** {user_data[2]}
👤 **Пол:** {user_data[3]}
🎂 **Возраст:** {user_data[4]} лет
📏 **Рост:** {user_data[5]} см
⚖️ **Вес:** {user_data[6]} кг
🏃 **Уровень активности:** {user_data[7]}
🔥 **Суточная норма калорий:** {user_data[8]} ккал
📅 **Дата регистрации:** {user_data[9]}

{subscription_text}
    """
    
    # Добавляем кнопку "Назад в меню"
    keyboard = [
        [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        profile_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фотографий"""
    is_for_adding = context.user_data.get('waiting_for_photo', False)
    is_for_checking = context.user_data.get('waiting_for_check_photo', False)
    
    if not (is_for_adding or is_for_checking):
        return
    
    user = update.effective_user
    photo = update.message.photo[-1]  # Берем фото в наилучшем качестве
    
    # Сбрасываем состояние ожидания
    context.user_data['waiting_for_photo'] = False
    context.user_data['waiting_for_check_photo'] = False
    
    # Отправляем сообщение о начале обработки
    processing_msg = await update.message.reply_text(
        "🔄 **Обрабатываю фотографию...**\n\n"
        "Анализирую изображение с помощью ИИ модели...",
        parse_mode='Markdown'
    )
    
    try:
        # Получаем файл фотографии
        file = await context.bot.get_file(photo.file_id)
        file_url = file.file_path
        
        logger.info(f"Downloading photo from: {file_url}")
        
        # Скачиваем изображение
        if file_url.startswith('https://'):
            response = requests.get(file_url)
        else:
            response = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_url}")
        
        logger.info(f"Photo download response: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"Failed to download photo: {response.status_code} - {response.text}")
            await processing_msg.edit_text(
                f"❌ Ошибка при загрузке фотографии\n\n"
                f"Код ошибки: {response.status_code}\n"
                f"URL: {file_url}\n"
                f"Попробуйте отправить фото еще раз или используйте команду /addphoto"
            )
            return
        
        # Конвертируем в base64
        image_data = base64.b64encode(response.content).decode('utf-8')
        
        # Отправляем запрос к языковой модели
        logger.info("Starting food photo analysis...")
        analysis_result = await analyze_food_photo(image_data)
        logger.info(f"Analysis result: {analysis_result is not None}")
        
        # Получаем информацию о выбранном приеме пищи
        selected_meal = context.user_data.get('selected_meal_name', 'Прием пищи')
        
        if analysis_result and is_valid_analysis(analysis_result):
            # Удаляем пояснения из анализа
            analysis_result = remove_explanations_from_analysis(analysis_result)
            
            # Парсим результат анализа для извлечения калорий
            calories = extract_calories_from_analysis(analysis_result)
            dish_name = extract_dish_name_from_analysis(analysis_result) or "Блюдо по фото"
            
            # Проверяем режим - добавление или проверка калорий
            is_check_mode = context.user_data.get('check_mode', False)
            
            if is_check_mode:
                # Режим проверки калорий - только показываем результат
                # Записываем использование функции
                add_calorie_check(user.id, 'photo')
                
                cleaned_result = clean_markdown_text(analysis_result)
                result_text = f"🔍 **Анализ калорий**\n\n{cleaned_result}\n\nℹ️ **Данные НЕ сохранены в статистику**"
                
                await processing_msg.edit_text(
                    result_text, 
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад к выбору", callback_data="check_calories")]
                    ]), 
                    parse_mode='Markdown'
                )
                # Сбрасываем режим проверки
                context.user_data['check_mode'] = False
            else:
                # Режим добавления блюда - сохраняем в базу
                meal_info = f"**🍽️ {selected_meal}**\n\n{analysis_result}"
                
                # Сохраняем данные о приеме пищи в базу данных
                try:
                    meal_type = context.user_data.get('selected_meal', 'meal_breakfast')
                    
                    # Сохраняем в базу данных
                    success = add_meal(
                        telegram_id=user.id,
                        meal_type=meal_type,
                        meal_name=selected_meal,
                        dish_name=dish_name,
                        calories=calories,
                        analysis_type="photo"
                    )
                    
                    if success:
                        logger.info(f"Meal saved successfully for user {user.id}")
                        cleaned_meal_info = clean_markdown_text(meal_info)
                        await processing_msg.edit_text(cleaned_meal_info, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')
                    else:
                        logger.warning(f"Failed to save meal for user {user.id}")
                        await processing_msg.edit_text(
                            "❌ Ошибка сохранения\n\n"
                            "Не удалось сохранить данные о приеме пищи. Попробуйте еще раз.",
                            reply_markup=get_main_menu_keyboard()
                        )
                    
                except Exception as e:
                    logger.error(f"Error saving meal to database: {e}")
                    await processing_msg.edit_text(
                        "❌ Ошибка сохранения\n\n"
                        "Не удалось сохранить данные о приеме пищи. Попробуйте еще раз.",
                        reply_markup=get_main_menu_keyboard()
                )
        elif analysis_result:
            # ИИ вернул результат, но не смог определить калории
            await processing_msg.edit_text(
                "❌ **Анализ не удался**\n\n"
                "ИИ не смог определить калорийность блюда на фотографии.\n\n"
                "**Возможные причины:**\n"
                "• На фото нет еды или еда не видна\n"
                "• Слишком темное или размытое изображение\n"
                "• Отсутствуют якорные объекты для масштаба\n\n"
                "**Рекомендации:**\n"
                "• Убедитесь, что на фото четко видна еда\n"
                "• Добавьте вилку, ложку или руку для масштаба\n"
                "• Сделайте фото при хорошем освещении\n\n"
                "Попробуйте отправить другое фото или используйте команду /addtext для текстового описания.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
        else:
            # API не работает
            await processing_msg.edit_text(
                "❌ **Ошибка анализа**\n\n"
                "Не удалось проанализировать фотографию. Попробуйте:\n"
                "• Убедиться, что на фото изображена еда\n"
                "• Добавить якорные объекты (вилка, ложка, рука)\n"
                "• Сделать фото в лучшем качестве\n\n"
                "Попробуйте команду /addphoto снова.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error processing photo: {e}")
        await processing_msg.edit_text(
            "❌ Произошла ошибка\n\n"
            "Не удалось обработать фотографию. Попробуйте позже или используйте команду /addphoto снова.",
            reply_markup=get_main_menu_keyboard()
        )

async def addtext_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /addtext"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        existing_user = await check_user_registration(user.id)
        
        if not existing_user:
            await send_not_registered_message(update, context)
            return
        
        # Устанавливаем состояние ожидания текстового описания
        context.user_data['waiting_for_text'] = True
        
        await update.message.reply_text(
            "📝 **Анализ описания блюда**\n\n"
            "Опишите блюдо, калорийность которого вы хотите оценить.\n\n"
            "**Примеры описаний:**\n"
            "• \"Большая тарелка борща с мясом и сметаной\"\n"
            "• \"2 куска пиццы Маргарита среднего размера\"\n"
            "• \"Салат Цезарь с курицей и сыром пармезан\"\n"
            "• \"Порция жареной картошки с луком\"\n\n"
            "**Укажите:**\n"
            "• Название блюда\n"
            "• Примерный размер порции\n"
            "• Основные ингредиенты\n\n"
            "Модель проанализирует описание и вернет:\n"
            "• Название блюда\n"
            "• Ориентировочный вес\n"
            "• Калорийность\n"
            "• Раскладку по БЖУ",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in addtext_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
        )

async def addvoice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /addvoice"""
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        existing_user = await check_user_registration(user.id)
        
        if not existing_user:
            await send_not_registered_message(update, context)
            return
        
        # Устанавливаем состояние ожидания голосового сообщения
        context.user_data['waiting_for_voice'] = True
        
        await update.message.reply_text(
            "🎤 **Анализ голосового описания блюда**\n\n"
            "Отправьте голосовое сообщение с описанием блюда, калорийность которого вы хотите оценить.\n\n"
            "**Примеры описаний:**\n"
            "• \"Большая тарелка борща с мясом и сметаной\"\n"
            "• \"Два куска пиццы Маргарита среднего размера\"\n"
            "• \"Салат Цезарь с курицей и сыром пармезан\"\n"
            "• \"Порция жареной картошки с луком\"\n\n"
            "**Укажите в голосовом сообщении:**\n"
            "• Название блюда\n"
            "• Примерный размер порции\n"
            "• Основные ингредиенты\n\n"
            "Модель проанализирует голосовое сообщение и вернет:\n"
            "• Название блюда\n"
            "• Ориентировочный вес\n"
            "• Калорийность\n"
            "• Раскладку по БЖУ",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in addvoice_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при проверке регистрации. Попробуйте позже."
        )

async def handle_food_text_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик анализа текстового описания блюда"""
    user = update.effective_user
    description = update.message.text
    
    # Сбрасываем состояние ожидания
    context.user_data['waiting_for_text'] = False
    context.user_data['waiting_for_check_text'] = False
    
    # Отправляем сообщение о начале обработки
    processing_msg = await update.message.reply_text(
        "🔄 **Анализирую описание блюда...**\n\n"
        "Обрабатываю текст с помощью ИИ модели...",
        parse_mode='Markdown'
    )
    
    try:
        # Отправляем запрос к языковой модели
        analysis_result = await analyze_food_text(description)
        
        if analysis_result and is_valid_analysis(analysis_result):
            # Удаляем пояснения из анализа
            analysis_result = remove_explanations_from_analysis(analysis_result)
            
            # Парсим результат анализа для извлечения калорий
            calories = extract_calories_from_analysis(analysis_result)
            dish_name = extract_dish_name_from_analysis(analysis_result) or description[:50]
            
            # Проверяем режим - добавление или проверка калорий
            is_check_mode = context.user_data.get('check_mode', False)
            
            if is_check_mode:
                # Режим проверки калорий - только показываем результат
                # Записываем использование функции
                add_calorie_check(user.id, 'text')
                
                cleaned_result = clean_markdown_text(analysis_result)
                result_text = f"🔍 **Анализ калорий**\n\n{cleaned_result}\n\nℹ️ **Данные НЕ сохранены в статистику**"
                
                await processing_msg.edit_text(
                    result_text, 
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад к выбору", callback_data="check_calories")]
                    ]), 
                    parse_mode='Markdown'
                )
                # Сбрасываем режим проверки
                context.user_data['check_mode'] = False
            else:
                # Режим добавления блюда - сохраняем в базу
                try:
                    meal_type = context.user_data.get('selected_meal', 'meal_breakfast')
                    selected_meal = context.user_data.get('selected_meal_name', 'Прием пищи')
                    
                    # Сохраняем в базу данных
                    success = add_meal(
                        telegram_id=user.id,
                        meal_type=meal_type,
                        meal_name=selected_meal,
                        dish_name=dish_name,
                        calories=calories,
                        analysis_type="text"
                    )
                    
                    if success:
                        logger.info(f"Meal saved successfully for user {user.id}")
                        cleaned_result = clean_markdown_text(analysis_result)
                        await processing_msg.edit_text(cleaned_result, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')
                    else:
                        logger.warning(f"Failed to save meal for user {user.id}")
                        await processing_msg.edit_text(
                            "❌ Ошибка сохранения\n\n"
                            "Не удалось сохранить данные о приеме пищи. Попробуйте еще раз.",
                            reply_markup=get_main_menu_keyboard()
                        )
                    
                except Exception as e:
                    logger.error(f"Error saving meal to database: {e}")
                    await processing_msg.edit_text(
                        "❌ Ошибка сохранения\n\n"
                        "Не удалось сохранить данные о приеме пищи. Попробуйте еще раз.",
                        reply_markup=get_main_menu_keyboard()
                )
        elif analysis_result:
            # ИИ вернул результат, но не смог определить калории
            await processing_msg.edit_text(
                "❌ **Анализ не удался**\n\n"
                "ИИ не смог определить калорийность блюда по описанию.\n\n"
                "**Возможные причины:**\n"
                "• Описание слишком краткое или неясное\n"
                "• Не указан размер порции\n"
                "• Отсутствуют основные ингредиенты\n\n"
                "**Рекомендации:**\n"
                "• Укажите точные ингредиенты и их количество\n"
                "• Добавьте размер порции (например, 'большая тарелка', '2 куска')\n"
                "• Опишите способ приготовления\n\n"
                "Попробуйте дать более подробное описание или используйте команду /addphoto для анализа фото.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
        else:
            # API не работает
            await processing_msg.edit_text(
                "❌ **Ошибка анализа**\n\n"
                "Не удалось проанализировать описание блюда. Попробуйте:\n"
                "• Указать более подробное описание\n"
                "• Включить размер порции\n"
                "• Перечислить основные ингредиенты\n\n"
                "Попробуйте команду /addtext снова.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error processing text description: {e}")
        await processing_msg.edit_text(
            "❌ Произошла ошибка\n\n"
            "Не удалось обработать описание блюда. Попробуйте позже или используйте команду /addtext снова.",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик голосовых сообщений"""
    is_for_adding = context.user_data.get('waiting_for_voice', False)
    is_for_checking = context.user_data.get('waiting_for_check_voice', False)
    
    if not (is_for_adding or is_for_checking):
        return
    
    user = update.effective_user
    voice = update.message.voice
    
    # Сбрасываем состояние ожидания
    context.user_data['waiting_for_voice'] = False
    context.user_data['waiting_for_check_voice'] = False
    
    # Отправляем сообщение о начале обработки
    processing_msg = await update.message.reply_text(
        "🔄 **Обрабатываю голосовое сообщение...**\n\n"
        "Преобразую речь в текст и анализирую с помощью ИИ...",
        parse_mode='Markdown'
    )
    
    try:
        # Получаем файл голосового сообщения
        file = await context.bot.get_file(voice.file_id)
        file_url = file.file_path
        
        logger.info(f"Downloading voice from: {file_url}")
        
        # Скачиваем аудиофайл
        if file_url.startswith('https://'):
            response = requests.get(file_url)
        else:
            response = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_url}")
        
        logger.info(f"Voice download response: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"Failed to download voice: {response.status_code} - {response.text}")
            await processing_msg.edit_text(
                f"❌ Ошибка при загрузке голосового сообщения\n\n"
                f"Код ошибки: {response.status_code}\n"
                f"URL: {file_url}\n"
                f"Попробуйте отправить голосовое сообщение еще раз или используйте команду /addvoice"
            )
            return
        
        # Конвертируем в base64
        audio_data = base64.b64encode(response.content).decode('utf-8')
        
        # Отправляем запрос к языковой модели для распознавания речи
        transcription_result = await transcribe_voice(audio_data)
        
        if not transcription_result:
            await processing_msg.edit_text(
                "❌ Ошибка распознавания речи\n\n"
                "Не удалось распознать голосовое сообщение. Попробуйте:\n"
                "• Говорить четче и медленнее\n"
                "• Убедиться, что микрофон работает\n"
                "• Использовать команду /addtext для текстового описания\n\n"
                "Попробуйте команду /addvoice снова."
            )
            return
        
        # Анализируем распознанный текст
        analysis_result = await analyze_food_text(transcription_result)
        
        if analysis_result and is_valid_analysis(analysis_result):
            # Удаляем пояснения из анализа
            analysis_result = remove_explanations_from_analysis(analysis_result)
            
            # Парсим результат анализа для извлечения калорий
            calories = extract_calories_from_analysis(analysis_result)
            dish_name = extract_dish_name_from_analysis(analysis_result) or transcription_result[:50]
            
            # Проверяем режим - добавление или проверка калорий
            is_check_mode = context.user_data.get('check_mode', False)
            
            if is_check_mode:
                # Режим проверки калорий - только показываем результат
                # Записываем использование функции
                add_calorie_check(user.id, 'voice')
                
                cleaned_result = clean_markdown_text(analysis_result)
                result_text = f"🔍 **Анализ калорий**\n\n{cleaned_result}\n\nℹ️ **Данные НЕ сохранены в статистику**"
                
                await processing_msg.edit_text(
                    result_text, 
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад к выбору", callback_data="check_calories")]
                    ]), 
                    parse_mode='Markdown'
                )
                # Сбрасываем режим проверки
                context.user_data['check_mode'] = False
            else:
                # Режим добавления блюда - сохраняем в базу
                try:
                    meal_type = context.user_data.get('selected_meal', 'meal_breakfast')
                    selected_meal = context.user_data.get('selected_meal_name', 'Прием пищи')
                    
                    # Сохраняем в базу данных
                    success = add_meal(
                        telegram_id=user.id,
                        meal_type=meal_type,
                        meal_name=selected_meal,
                        dish_name=dish_name,
                        calories=calories,
                        analysis_type="voice"
                    )
                    
                    if success:
                        logger.info(f"Meal saved successfully for user {user.id}")
                        # Добавляем информацию о распознанном тексте
                        cleaned_result = clean_markdown_text(analysis_result)
                        result_with_transcription = f"**🎤 Распознанный текст:** {transcription_result}\n\n{cleaned_result}"
                        await processing_msg.edit_text(result_with_transcription, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')
                    else:
                        logger.warning(f"Failed to save meal for user {user.id}")
                        await processing_msg.edit_text(
                            "❌ Ошибка сохранения\n\n"
                            "Не удалось сохранить данные о приеме пищи. Попробуйте еще раз.",
                            reply_markup=get_main_menu_keyboard()
                        )
                    
                except Exception as e:
                    logger.error(f"Error saving meal to database: {e}")
                    await processing_msg.edit_text(
                        "❌ Ошибка сохранения\n\n"
                        "Не удалось сохранить данные о приеме пищи. Попробуйте еще раз.",
                        reply_markup=get_main_menu_keyboard()
                )
        elif analysis_result:
            # ИИ вернул результат, но не смог определить калории
            await processing_msg.edit_text(
                f"**🎤 Распознанный текст:** {transcription_result}\n\n"
                "❌ **Анализ не удался**\n\n"
                "ИИ не смог определить калорийность блюда по описанию.\n\n"
                "**Возможные причины:**\n"
                "• Описание слишком краткое или неясное\n"
                "• Не указан размер порции\n"
                "• Отсутствуют основные ингредиенты\n\n"
                "**Рекомендации:**\n"
                "• Укажите точные ингредиенты и их количество\n"
                "• Добавьте размер порции (например, 'большая тарелка', '2 куска')\n"
                "• Опишите способ приготовления\n\n"
                "Попробуйте дать более подробное описание или используйте команду /addphoto для анализа фото.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
        else:
            await processing_msg.edit_text(
                f"**🎤 Распознанный текст:** {transcription_result}\n\n"
                "❌ **Ошибка анализа**\n\n"
                "Не удалось проанализировать описание блюда. Попробуйте:\n"
                "• Указать более подробное описание\n"
                "• Включить размер порции\n"
                "• Перечислить основные ингредиенты\n\n"
                "Попробуйте команду /addvoice снова.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error processing voice: {e}")
        await processing_msg.edit_text(
            "❌ Произошла ошибка\n\n"
            "Не удалось обработать голосовое сообщение. Попробуйте позже или используйте команду /addvoice снова.",
            reply_markup=get_main_menu_keyboard()
        )

# ==================== АДМИН ФУНКЦИИ ====================

async def handle_admin_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Статистика' в админке"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if not is_admin(user.id):
        await query.message.reply_text("❌ У вас нет прав доступа!")
        return
    
    try:
        # Получаем детальную статистику
        user_count = get_user_count()
        meals_count = get_meals_count()
        daily_stats = get_daily_stats()
        
        # Получаем статистику за последние 7 дней
        week_stats = {}
        for i in range(7):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            # Здесь можно добавить функцию для получения статистики по дням
            week_stats[date] = 0  # Заглушка
        
        stats_text = f"""
📊 **Детальная статистика**

👥 **Пользователи:**
• Всего зарегистрировано: {user_count}
• Активных сегодня: {daily_stats['active_users']}

🍽️ **Приемы пищи:**
• Всего записей: {meals_count}
• За сегодня: {daily_stats['meals_today']}
• Общих калорий сегодня: {daily_stats['total_calories']}

📈 **Активность за неделю:**
• Понедельник: 0 записей
• Вторник: 0 записей  
• Среда: 0 записей
• Четверг: 0 записей
• Пятница: 0 записей
• Суббота: 0 записей
• Воскресенье: {daily_stats['meals_today']} записей
        """
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(stats_text, reply_markup=reply_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error showing admin stats: {e}")
        await query.message.reply_text(
            "❌ Ошибка при получении статистики. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
            ])
        )

async def handle_admin_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Пользователи' в админке"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if not is_admin(user.id):
        await query.message.reply_text("❌ У вас нет прав доступа!")
        return
    
    try:
        # Получаем список пользователей
        users = get_all_users()
        
        if not users:
            await query.message.reply_text(
                "👥 **Пользователи**\n\n"
                "Пользователи не найдены.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
                ])
            )
            return
        
        # Формируем список пользователей (показываем только первые 10)
        users_text = "👥 **Пользователи**\n\n"
        for i, user_data in enumerate(users[:10], 1):
            users_text += f"{i}. **{user_data[1]}** (ID: {user_data[0]})\n"
            users_text += f"   Пол: {user_data[2]}, Возраст: {user_data[3]}\n"
            users_text += f"   Рост: {user_data[4]}см, Вес: {user_data[5]}кг\n"
            users_text += f"   Норма калорий: {user_data[7]} ккал\n"
            users_text += f"   Регистрация: {user_data[8][:10]}\n\n"
        
        if len(users) > 10:
            users_text += f"... и еще {len(users) - 10} пользователей"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(users_text, reply_markup=reply_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error showing admin users: {e}")
        await query.message.reply_text(
            "❌ Ошибка при получении списка пользователей. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
            ])
        )

async def handle_admin_meals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Последние приемы пищи' в админке"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if not is_admin(user.id):
        await query.message.reply_text("❌ У вас нет прав доступа!")
        return
    
    try:
        # Получаем последние записи о приемах пищи
        meals = get_recent_meals(10)
        
        if not meals:
            await query.message.reply_text(
                "🍽️ **Последние приемы пищи**\n\n"
                "Записи о приемах пищи не найдены.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
                ])
            )
            return
        
        meals_text = "🍽️ **Последние приемы пищи**\n\n"
        for i, meal in enumerate(meals, 1):
            user_name = meal[1] or f"ID: {meal[0]}"
            meals_text += f"{i}. **{user_name}**\n"
            meals_text += f"   {meal[2]}: {meal[3]} ({meal[4]} ккал)\n"
            meals_text += f"   Тип: {meal[5]}, Время: {meal[6][:16]}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(meals_text, reply_markup=reply_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error showing admin meals: {e}")
        await query.message.reply_text(
            "❌ Ошибка при получении записей о приемах пищи. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
            ])
        )

async def handle_admin_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Рассылка' в админке"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if not is_admin(user.id):
        await query.message.reply_text("❌ У вас нет прав доступа!")
        return
    
    await query.message.reply_text(
        "📢 **Рассылка**\n\n"
        "Функция рассылки находится в разработке.\n"
        "В будущих версиях здесь будет возможность отправлять сообщения всем пользователям бота.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
        ]),
        parse_mode='Markdown'
    )

async def handle_admin_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Главное меню' в админке"""
    query = update.callback_query
    await query.answer()
    
    await query.message.reply_text(
        "🏠 **Главное меню**\n\n"
        "Выберите нужную функцию:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='Markdown'
    )

# ==================== ФУНКЦИИ УПРАВЛЕНИЯ ПОДПИСКАМИ ====================

async def handle_admin_subscriptions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Управление подписками' в админке"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if user.id not in ADMIN_IDS:
        await query.message.reply_text("❌ У вас нет прав администратора!")
        return
    
    subscriptions_text = """
⭐ **Управление подписками**

Выберите действие:
    """
    
    keyboard = [
        [InlineKeyboardButton("🔍 Проверить подписку", callback_data=ADMIN_CALLBACKS['admin_check_subscription'])],
        [InlineKeyboardButton("🔙 Назад в админку", callback_data=ADMIN_CALLBACKS['admin_panel'])]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        subscriptions_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_admin_check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Проверить подписку' в админке"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if user.id not in ADMIN_IDS:
        await query.message.reply_text("❌ У вас нет прав администратора!")
        return
    
    # Сохраняем состояние ожидания ввода Telegram ID
    context.user_data['admin_waiting_for_telegram_id'] = True
    
    await query.message.reply_text(
        "🔍 **Проверка подписки**\n\n"
        "Введите Telegram ID пользователя для проверки подписки:",
        parse_mode='Markdown'
    )

async def handle_admin_manage_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик управления подпиской конкретного пользователя"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if user.id not in ADMIN_IDS:
        await query.message.reply_text("❌ У вас нет прав администратора!")
        return
    
    # Получаем Telegram ID из callback data
    if ':' in query.data:
        telegram_id = int(query.data.split(':')[1])
    else:
        await query.message.reply_text("❌ Ошибка: не удалось получить ID пользователя")
        return
    
    # Получаем информацию о пользователе
    user_data = get_user_by_telegram_id(telegram_id)
    if not user_data:
        await query.message.reply_text("❌ Пользователь не найден в базе данных!")
        return
    
    # Получаем информацию о подписке
    subscription_info = check_user_subscription(telegram_id)
    
    # Формируем текст о подписке
    subscription_text = ""
    if subscription_info['is_active']:
        if subscription_info['type'] == 'trial':
            subscription_text = f"🆓 **Триальный период**\nДоступен до: {subscription_info['expires_at']}"
        elif subscription_info['type'] == 'premium':
            if subscription_info['expires_at']:
                subscription_text = f"⭐ **Премиум подписка**\nДействует до: {subscription_info['expires_at']}"
            else:
                subscription_text = "⭐ **Премиум подписка**\nБез ограничений"
    else:
        if subscription_info['type'] == 'trial_expired':
            subscription_text = f"❌ **Триальный период истек**\nИстек: {subscription_info['expires_at']}"
        elif subscription_info['type'] == 'premium_expired':
            subscription_text = f"❌ **Премиум подписка истекла**\nИстекла: {subscription_info['expires_at']}"
        else:
            subscription_text = "❌ **Нет активной подписки**"
    
    manage_text = f"""
👤 **Управление подпиской пользователя**

📝 **Имя:** {user_data[2]}
🆔 **Telegram ID:** {telegram_id}
📅 **Дата регистрации:** {user_data[9]}

{subscription_text}

Выберите действие:
    """
    
    keyboard = [
        [InlineKeyboardButton("🆓 Активировать триал (1 день)", callback_data=f"{ADMIN_CALLBACKS['admin_activate_trial']}:{telegram_id}")],
        [InlineKeyboardButton("⭐ Активировать премиум (30 дней)", callback_data=f"{ADMIN_CALLBACKS['admin_activate_premium']}:{telegram_id}")],
        [InlineKeyboardButton("❌ Деактивировать подписку", callback_data=f"{ADMIN_CALLBACKS['admin_deactivate_subscription']}:{telegram_id}")],
        [InlineKeyboardButton("🔙 Назад к управлению подписками", callback_data=ADMIN_CALLBACKS['admin_subscriptions'])]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        manage_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_admin_activate_trial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик активации триального периода"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if user.id not in ADMIN_IDS:
        await query.message.reply_text("❌ У вас нет прав администратора!")
        return
    
    # Получаем Telegram ID из callback data
    if ':' in query.data:
        telegram_id = int(query.data.split(':')[1])
    else:
        await query.message.reply_text("❌ Ошибка: не удалось получить ID пользователя")
        return
    
    # Активируем триальный период
    success = activate_premium_subscription(telegram_id, 1)  # 1 день триала
    
    if success:
        await query.message.reply_text(
            f"✅ **Триальный период активирован!**\n\n"
            f"👤 Пользователь: {telegram_id}\n"
            f"🆓 Период: 1 день\n"
            f"📅 Истекает: завтра",
            parse_mode='Markdown'
        )
    else:
        await query.message.reply_text(
            f"❌ **Ошибка активации триального периода!**\n\n"
            f"Пользователь {telegram_id} не найден или произошла ошибка базы данных.",
            parse_mode='Markdown'
        )

async def handle_admin_activate_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик активации премиум подписки"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if user.id not in ADMIN_IDS:
        await query.message.reply_text("❌ У вас нет прав администратора!")
        return
    
    # Получаем Telegram ID из callback data
    if ':' in query.data:
        telegram_id = int(query.data.split(':')[1])
    else:
        await query.message.reply_text("❌ Ошибка: не удалось получить ID пользователя")
        return
    
    # Активируем премиум подписку
    success = activate_premium_subscription(telegram_id, 30)  # 30 дней премиум
    
    if success:
        await query.message.reply_text(
            f"✅ **Премиум подписка активирована!**\n\n"
            f"👤 Пользователь: {telegram_id}\n"
            f"⭐ Период: 30 дней\n"
            f"📅 Истекает: через 30 дней",
            parse_mode='Markdown'
        )
    else:
        await query.message.reply_text(
            f"❌ **Ошибка активации премиум подписки!**\n\n"
            f"Пользователь {telegram_id} не найден или произошла ошибка базы данных.",
            parse_mode='Markdown'
        )

async def handle_admin_deactivate_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик деактивации подписки"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем права админа
    if user.id not in ADMIN_IDS:
        await query.message.reply_text("❌ У вас нет прав администратора!")
        return
    
    # Получаем Telegram ID из callback data
    if ':' in query.data:
        telegram_id = int(query.data.split(':')[1])
    else:
        await query.message.reply_text("❌ Ошибка: не удалось получить ID пользователя")
        return
    
    # Деактивируем подписку (устанавливаем как истекшую)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET subscription_type = 'trial_expired',
                    is_premium = 0,
                    subscription_expires_at = datetime('now', '-1 day')
                WHERE telegram_id = ?
            ''', (telegram_id,))
            conn.commit()
            
            if cursor.rowcount > 0:
                await query.message.reply_text(
                    f"✅ **Подписка деактивирована!**\n\n"
                    f"👤 Пользователь: {telegram_id}\n"
                    f"❌ Статус: Подписка отменена",
                    parse_mode='Markdown'
                )
            else:
                await query.message.reply_text(
                    f"❌ **Ошибка деактивации подписки!**\n\n"
                    f"Пользователь {telegram_id} не найден.",
                    parse_mode='Markdown'
                )
    except Exception as e:
        logger.error(f"Error deactivating subscription: {e}")
        await query.message.reply_text(
            f"❌ **Ошибка деактивации подписки!**\n\n"
            f"Произошла ошибка базы данных.",
            parse_mode='Markdown'
        )

async def handle_admin_telegram_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода Telegram ID для админки"""
    user = update.effective_user
    
    # Проверяем права админа
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав администратора!")
        return
    
    text = update.message.text.strip()
    
    try:
        # Пытаемся преобразовать в число
        telegram_id = int(text)
        
        # Проверяем, что это положительное число
        if telegram_id <= 0:
            await update.message.reply_text("❌ Telegram ID должен быть положительным числом!")
            return
        
        # Проверяем, существует ли пользователь
        user_data = get_user_by_telegram_id(telegram_id)
        if not user_data:
            await update.message.reply_text(
                f"❌ **Пользователь не найден!**\n\n"
                f"🆔 Telegram ID: {telegram_id}\n"
                f"Пользователь не зарегистрирован в боте.",
                parse_mode='Markdown'
            )
            # Сбрасываем состояние ожидания
            context.user_data['admin_waiting_for_telegram_id'] = False
            return
        
        # Сбрасываем состояние ожидания
        context.user_data['admin_waiting_for_telegram_id'] = False
        
        # Показываем меню управления подпиской
        await show_admin_manage_subscription_menu(update, context, telegram_id, user_data)
        
    except ValueError:
        await update.message.reply_text(
            "❌ **Неверный формат Telegram ID!**\n\n"
            "Пожалуйста, введите числовой ID пользователя (например: 123456789)",
            parse_mode='Markdown'
        )

async def show_admin_manage_subscription_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, telegram_id: int, user_data):
    """Показывает меню управления подпиской для конкретного пользователя"""
    # Получаем информацию о подписке
    subscription_info = check_user_subscription(telegram_id)
    
    # Формируем текст о подписке
    subscription_text = ""
    if subscription_info['is_active']:
        if subscription_info['type'] == 'trial':
            subscription_text = f"🆓 **Триальный период**\nДоступен до: {subscription_info['expires_at']}"
        elif subscription_info['type'] == 'premium':
            if subscription_info['expires_at']:
                subscription_text = f"⭐ **Премиум подписка**\nДействует до: {subscription_info['expires_at']}"
            else:
                subscription_text = "⭐ **Премиум подписка**\nБез ограничений"
    else:
        if subscription_info['type'] == 'trial_expired':
            subscription_text = f"❌ **Триальный период истек**\nИстек: {subscription_info['expires_at']}"
        elif subscription_info['type'] == 'premium_expired':
            subscription_text = f"❌ **Премиум подписка истекла**\nИстекла: {subscription_info['expires_at']}"
        else:
            subscription_text = "❌ **Нет активной подписки**"
    
    manage_text = f"""
👤 **Управление подпиской пользователя**

📝 **Имя:** {user_data[2]}
🆔 **Telegram ID:** {telegram_id}
📅 **Дата регистрации:** {user_data[9]}

{subscription_text}

Выберите действие:
    """
    
    keyboard = [
        [InlineKeyboardButton("🆓 Активировать триал (1 день)", callback_data=f"{ADMIN_CALLBACKS['admin_activate_trial']}:{telegram_id}")],
        [InlineKeyboardButton("⭐ Активировать премиум (30 дней)", callback_data=f"{ADMIN_CALLBACKS['admin_activate_premium']}:{telegram_id}")],
        [InlineKeyboardButton("❌ Деактивировать подписку", callback_data=f"{ADMIN_CALLBACKS['admin_deactivate_subscription']}:{telegram_id}")],
        [InlineKeyboardButton("🔙 Назад к управлению подписками", callback_data=ADMIN_CALLBACKS['admin_subscriptions'])]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        manage_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ==================== ФУНКЦИИ "УЗНАТЬ КАЛОРИИ" (БЕЗ СОХРАНЕНИЯ) ====================

async def handle_check_calories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Узнать калории'"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        user_data = await check_user_registration(user.id)
        if not user_data:
            await query.message.reply_text(
                "❌ Вы не зарегистрированы в системе!\n"
                "Используйте /register для регистрации.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Проверяем подписку
        access_info = check_subscription_access(user.id)
        
        # Если подписка неактивна, проверяем лимит использований
        if not access_info['has_access']:
            daily_checks = get_daily_calorie_checks_count(user.id)
            if daily_checks >= 3:
                subscription_msg = get_subscription_message(access_info)
                limit_msg = f"❌ **Лимит использований исчерпан**\n\n"
                limit_msg += f"Вы использовали функцию 'Узнать калории' {daily_checks}/3 раз сегодня.\n\n"
                limit_msg += f"{subscription_msg}"
                
                await query.message.reply_text(
                    limit_msg,
                    reply_markup=get_main_menu_keyboard(),
                    parse_mode='Markdown'
                )
                return
        
        # Создаем подменю для выбора типа анализа
        keyboard = [
            [InlineKeyboardButton("📷 Анализ по фото", callback_data="check_photo")],
            [InlineKeyboardButton("📝 Анализ по тексту", callback_data="check_text")],
            [InlineKeyboardButton("🎤 Анализ по голосу", callback_data="check_voice")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = "🔍 **Узнать калории**\n\n"
        message_text += "Выберите способ анализа:\n\n"
        message_text += "ℹ️ **Результат будет показан, но НЕ сохранится в вашу статистику**"
        
        # Показываем информацию о лимите для пользователей без подписки
        if not access_info['has_access']:
            daily_checks = get_daily_calorie_checks_count(user.id)
            message_text += f"\n\n🆓 **Осталось использований: {3 - daily_checks}/3**"
            message_text += f"\n\n⏰ **Счетчик сбрасывается в полночь**"
        
        await query.message.reply_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in handle_check_calories: {e}")
        await query.message.reply_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_check_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Анализ по фото' для проверки калорий"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем подписку и лимит использований
    access_info = check_subscription_access(user.id)
    if not access_info['has_access']:
        daily_checks = get_daily_calorie_checks_count(user.id)
        if daily_checks >= 3:
            subscription_msg = get_subscription_message(access_info)
            limit_msg = f"❌ **Лимит использований исчерпан**\n\n"
            limit_msg += f"Вы использовали функцию 'Узнать калории' {daily_checks}/3 раз сегодня.\n\n"
            limit_msg += f"{subscription_msg}"
            
            await query.message.reply_text(
                limit_msg,
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
            return
    
    await query.message.reply_text(
        "📷 **Анализ по фото**\n\n"
        "Отправьте фотографию еды для анализа калорий.\n\n"
        "ℹ️ **Результат будет показан, но НЕ сохранится в статистику**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data="check_calories")]
        ]),
        parse_mode='Markdown'
    )
    
    # Устанавливаем состояние ожидания фото для проверки
    context.user_data['waiting_for_check_photo'] = True
    context.user_data['check_mode'] = True

async def handle_check_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Анализ по тексту' для проверки калорий"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем подписку и лимит использований
    access_info = check_subscription_access(user.id)
    if not access_info['has_access']:
        daily_checks = get_daily_calorie_checks_count(user.id)
        if daily_checks >= 3:
            subscription_msg = get_subscription_message(access_info)
            limit_msg = f"❌ **Лимит использований исчерпан**\n\n"
            limit_msg += f"Вы использовали функцию 'Узнать калории' {daily_checks}/3 раз сегодня.\n\n"
            limit_msg += f"{subscription_msg}"
            
            await query.message.reply_text(
                limit_msg,
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
            return
    
    await query.message.reply_text(
        "📝 **Анализ по тексту**\n\n"
        "Опишите блюдо для анализа калорий.\n\n"
        "ℹ️ **Результат будет показан, но НЕ сохранится в статистику**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data="check_calories")]
        ]),
        parse_mode='Markdown'
    )
    
    # Устанавливаем состояние ожидания текста для проверки
    context.user_data['waiting_for_check_text'] = True
    context.user_data['check_mode'] = True

async def handle_check_voice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Анализ по голосу' для проверки калорий"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем подписку и лимит использований
    access_info = check_subscription_access(user.id)
    if not access_info['has_access']:
        daily_checks = get_daily_calorie_checks_count(user.id)
        if daily_checks >= 3:
            subscription_msg = get_subscription_message(access_info)
            limit_msg = f"❌ **Лимит использований исчерпан**\n\n"
            limit_msg += f"Вы использовали функцию 'Узнать калории' {daily_checks}/3 раз сегодня.\n\n"
            limit_msg += f"{subscription_msg}"
            
            await query.message.reply_text(
                limit_msg,
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
            return
    
    await query.message.reply_text(
        "🎤 **Анализ по голосу**\n\n"
        "Отправьте голосовое сообщение с описанием блюда для анализа калорий.\n\n"
        "ℹ️ **Результат будет показан, но НЕ сохранится в статистику**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data="check_calories")]
        ]),
        parse_mode='Markdown'
    )
    
    # Устанавливаем состояние ожидания голоса для проверки
    context.user_data['waiting_for_check_voice'] = True
    context.user_data['check_mode'] = True

async def analyze_food_photo(image_data):
    """Анализирует фотографию еды с помощью Qwen2.5-VL-72B-Instruct"""
    try:
        logger.info("Preparing API request for food photo analysis...")
        
        # Подготавливаем запрос к Qwen API
        prompt = """
        Проанализируй фотографию еды и определи:
        1. Название блюда
        2. Ориентировочный вес (используя якорные объекты как вилка, ложка, рука для масштаба)
        3. Калорийность на 100г
        4. Раскладку по белкам, жирам и углеводам на 100г
        5. ОБЩУЮ калорийность блюда (для всего видимого количества)
        6. Общее количество БЖУ в блюде (для всего видимого количества)
        
        ВАЖНО: Рассчитай калорийность для ВСЕГО видимого количества еды на фото, а не только для 100г!
        
        Ответ должен быть ТОЛЬКО в формате:
        **🍽️ Анализ блюда:**
        
        **Название:** [название блюда]
        **Вес:** [общий вес блюда]г
        **Калорийность:** [ОБЩАЯ калорийность для всего количества] ккал
        
        **📊 БЖУ на 100г:**
        • Белки: [количество]г
        • Жиры: [количество]г  
        • Углеводы: [количество]г
        
        **📈 Общее БЖУ в блюде:**
        • Белки: [общее количество]г
        • Жиры: [общее количество]г
        • Углеводы: [общее количество]г
        
        НЕ добавляй никаких дополнительных пояснений, расчетов или объяснений!
        """
        
        # Отправляем запрос к API Nebius с Qwen2.5-VL-72B-Instruct
        api_data = {
            "model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        
        logger.info("Sending API request...")
        # Используем правильный endpoint для Nebius API
        response = await make_api_request("chat/completions", api_data, "POST")
        
        if response and 'choices' in response and len(response['choices']) > 0:
            result = response['choices'][0]['message']['content']
            logger.info(f"API analysis successful, result length: {len(result)}")
            return result
        else:
            logger.error(f"Unexpected API response: {response}")
            return None
            
    except Exception as e:
        logger.error(f"Error in food photo analysis: {e}")
        return None

async def analyze_food_text(description):
    """Анализирует текстовое описание блюда с помощью Qwen2.5-VL-72B-Instruct"""
    try:
        # Парсим количество из описания
        quantity, unit = parse_quantity_from_description(description)
        
        # Подготавливаем запрос к Qwen API
        prompt = f"""
        Проанализируй следующее описание блюда и определи:
        1. Название блюда
        2. Ориентировочный вес порции (учитывая указанное количество)
        3. Калорийность на 100г
        4. Раскладку по белкам, жирам и углеводам на 100г
        5. ОБЩУЮ калорийность блюда (для всего указанного количества)
        6. Общее количество БЖУ в блюде (для всего указанного количества)
        
        Описание блюда: "{description}"
        Примерный вес: {quantity}{unit}
        
        ВАЖНО: Рассчитай калорийность для ВСЕГО указанного количества, а не только для 100г!
        Например, если указано "3 яблока", рассчитай калорийность для 3 яблок, а не для 100г яблок.
        
        Ответ должен быть ТОЛЬКО в формате:
        **🍽️ Анализ блюда:**
        
        **Название:** [название блюда]
        **Вес:** [общий вес блюда]г
        **Калорийность:** [ОБЩАЯ калорийность для всего количества] ккал
        
        **📊 БЖУ на 100г:**
        • Белки: [количество]г
        • Жиры: [количество]г  
        • Углеводы: [количество]г
        
        **📈 Общее БЖУ в блюде:**
        • Белки: [общее количество]г
        • Жиры: [общее количество]г
        • Углеводы: [общее количество]г
        
        НЕ добавляй никаких дополнительных пояснений, расчетов или объяснений!
        """
        
        # Отправляем запрос к API Nebius с Qwen2.5-VL-72B-Instruct
        api_data = {
            "model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        
        # Используем правильный endpoint для Nebius API
        response = await make_api_request("chat/completions", api_data, "POST")
        
        if response and 'choices' in response and len(response['choices']) > 0:
            return response['choices'][0]['message']['content']
        else:
            logger.error(f"Unexpected API response: {response}")
            return None
            
    except Exception as e:
        logger.error(f"Error in food text analysis: {e}")
        return None

async def transcribe_voice(audio_data):
    """Распознает речь из аудиофайла с помощью Qwen2.5-VL-72B-Instruct"""
    try:
        # Подготавливаем запрос к Qwen API для распознавания речи
        prompt = """
        Распознай речь из аудиосообщения и верни только текст без дополнительных комментариев.
        Если в аудио есть описание еды, верни его точно как сказано.
        """
        
        # Отправляем запрос к API Nebius с Qwen2.5-VL-72B-Instruct
        api_data = {
            "model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "audio_url",
                            "audio_url": {
                                "url": f"data:audio/ogg;base64,{audio_data}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 500,
            "temperature": 0.1
        }
        
        # Используем правильный endpoint для Nebius API
        response = await make_api_request("chat/completions", api_data, "POST")
        
        if response and 'choices' in response and len(response['choices']) > 0:
            return response['choices'][0]['message']['content'].strip()
        else:
            logger.error(f"Unexpected API response for transcription: {response}")
            return None
            
    except Exception as e:
        logger.error(f"Error in voice transcription: {e}")
        return None

async def make_api_request(endpoint: str, data: Optional[Dict[str, Any]] = None, method: str = "GET") -> Optional[Dict[str, Any]]:
    """Выполняет запрос к API Nebius с улучшенной обработкой ошибок"""
    try:
        headers = {
            "Authorization": f"Bearer {API_KEYS['nebius_api']}",
            "Content-Type": "application/json"
        }
        
        url = f"{BASE_URL}{endpoint}"
        logger.info(f"Making {method} request to {url}")
        
        # Выполняем запрос в отдельном потоке для избежания блокировки
        loop = asyncio.get_event_loop()
        
        if method == "GET":
            response = await loop.run_in_executor(None, lambda: requests.get(url, headers=headers))
        elif method == "POST":
            response = await loop.run_in_executor(None, lambda: requests.post(url, headers=headers, json=data))
        elif method == "PUT":
            response = await loop.run_in_executor(None, lambda: requests.put(url, headers=headers, json=data))
        elif method == "DELETE":
            response = await loop.run_in_executor(None, lambda: requests.delete(url, headers=headers))
        else:
            logger.error(f"Unsupported HTTP method: {method}")
            return None
        
        # Проверяем статус ответа
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 401:
            logger.error("API authentication failed - check API key")
            return None
        elif response.status_code == 429:
            logger.error("API rate limit exceeded")
            return None
        elif response.status_code >= 500:
            logger.error(f"API server error: {response.status_code}")
            return None
        else:
            logger.error(f"API request failed with status {response.status_code}: {response.text}")
            return None
        
    except requests.exceptions.Timeout:
        logger.error("API request timeout")
        return None
    except requests.exceptions.ConnectionError:
        logger.error("API connection error")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in API request: {e}")
        return None

async def handle_meal_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора приема пищи"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Определяем тип приема пищи
    meal_types = {
        'meal_breakfast': '🌅 Завтрак',
        'meal_lunch': '☀️ Обед', 
        'meal_dinner': '🌙 Ужин',
        'meal_snack': '🍎 Перекус'
    }
    
    meal_name = meal_types.get(query.data, 'Прием пищи')
    
    # Сохраняем выбранный прием пищи в контексте
    context.user_data['selected_meal'] = query.data
    context.user_data['selected_meal_name'] = meal_name
    
    # Создаем меню для выбора способа анализа
    keyboard = [
        [InlineKeyboardButton("📷 Анализ по фото", callback_data="analyze_photo")],
        [InlineKeyboardButton("📝 Анализ по тексту", callback_data="analyze_text")],
        [InlineKeyboardButton("🎤 Анализ по голосовому", callback_data="analyze_voice")],
        [InlineKeyboardButton("🔙 Назад к приемам пищи", callback_data="add_dish")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        f"🍽️ **{meal_name}**\n\n"
        "Выберите способ анализа блюда:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_analyze_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Анализ по фото'"""
    query = update.callback_query
    await query.answer()
    
    # Устанавливаем состояние ожидания фото
    context.user_data['waiting_for_photo'] = True
    
    # Получаем информацию о выбранном приеме пищи
    selected_meal = context.user_data.get('selected_meal_name', 'Прием пищи')
    
    await query.message.reply_text(
        f"📸 **Анализ фотографии еды - {selected_meal}**\n\n"
        "Пришлите мне фото блюда, калорийность которого вы хотите оценить.\n\n"
        "⚠️ **Для более точного расчета на фото должны присутствовать якорные объекты:**\n"
        "• Вилка\n"
        "• Ложка\n"
        "• Рука\n"
        "• Монета\n"
        "• Другие объекты для масштаба\n\n"
        "Модель проанализирует фото и вернет:\n"
        "• Название блюда\n"
        "• Ориентировочный вес\n"
        "• Калорийность\n"
        "• Раскладку по БЖУ",
        parse_mode='Markdown'
    )

async def handle_analyze_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Анализ по тексту'"""
    query = update.callback_query
    await query.answer()
    
    # Устанавливаем состояние ожидания текстового описания
    context.user_data['waiting_for_text'] = True
    
    # Получаем информацию о выбранном приеме пищи
    selected_meal = context.user_data.get('selected_meal_name', 'Прием пищи')
    
    await query.message.reply_text(
        f"📝 **Анализ описания блюда - {selected_meal}**\n\n"
        "Опишите блюдо, калорийность которого вы хотите оценить.\n\n"
        "**Примеры описаний:**\n"
        "• \"Большая тарелка борща с мясом и сметаной\"\n"
        "• \"2 куска пиццы Маргарита среднего размера\"\n"
        "• \"Салат Цезарь с курицей и сыром пармезан\"\n"
        "• \"Порция жареной картошки с луком\"\n\n"
        "**Укажите:**\n"
        "• Название блюда\n"
        "• Примерный размер порции\n"
        "• Основные ингредиенты\n\n"
        "Модель проанализирует описание и вернет:\n"
        "• Название блюда\n"
        "• Ориентировочный вес\n"
        "• Калорийность\n"
        "• Раскладку по БЖУ",
        parse_mode='Markdown'
    )

async def handle_analyze_voice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Анализ по голосовому'"""
    query = update.callback_query
    await query.answer()
    
    # Устанавливаем состояние ожидания голосового сообщения
    context.user_data['waiting_for_voice'] = True
    
    # Получаем информацию о выбранном приеме пищи
    selected_meal = context.user_data.get('selected_meal_name', 'Прием пищи')
    
    await query.message.reply_text(
        f"🎤 **Анализ голосового описания блюда - {selected_meal}**\n\n"
        "Отправьте голосовое сообщение с описанием блюда, калорийность которого вы хотите оценить.\n\n"
        "**Примеры описаний:**\n"
        "• \"Большая тарелка борща с мясом и сметаной\"\n"
        "• \"Два куска пиццы Маргарита среднего размера\"\n"
        "• \"Салат Цезарь с курицей и сыром пармезан\"\n"
        "• \"Порция жареной картошки с луком\"\n\n"
        "**Укажите в голосовом сообщении:**\n"
        "• Название блюда\n"
        "• Примерный размер порции\n"
        "• Основные ингредиенты\n\n"
        "Модель проанализирует голосовое сообщение и вернет:\n"
        "• Название блюда\n"
        "• Ориентировочный вес\n"
        "• Калорийность\n"
        "• Раскладку по БЖУ",
        parse_mode='Markdown'
    )


async def handle_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Назад в меню'"""
    query = update.callback_query
    await query.answer()
    
    await query.message.reply_text(
        "🏠 **Главное меню**\n\n"
        "Выберите нужную функцию:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='Markdown'
    )

async def handle_statistics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Статистика'"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    # Проверяем подписку
    access_info = check_subscription_access(user.id)
    if not access_info['has_access']:
        subscription_msg = get_subscription_message(access_info)
        await query.message.reply_text(
            subscription_msg,
            reply_markup=get_main_menu_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    try:
        # Получаем информацию о пользователе
        user_data = await check_user_registration(user.id)
        if not user_data:
            await query.message.reply_text(
                "❌ Вы не зарегистрированы в системе!\n"
                "Используйте /register для регистрации.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Создаем подменю для выбора периода
        keyboard = [
            [InlineKeyboardButton("📅 За сегодня", callback_data="stats_today")],
            [InlineKeyboardButton("📅 За вчера", callback_data="stats_yesterday")],
            [InlineKeyboardButton("📅 За неделю", callback_data="stats_week")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            "📊 **Статистика**\n\n"
            "Выберите период для просмотра статистики:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing statistics menu: {e}")
        await query.message.reply_text(
            "❌ Произошла ошибка при получении статистики. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_stats_today_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'За сегодня'"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    try:
        # Получаем статистику по приемам пищи за сегодня
        daily_meals = get_daily_meals_by_type(user.id)
        
        # Формируем сообщение со статистикой
        stats_text = "📊 **Ваша статистика за сегодня:**\n\n"
        
        # Определяем порядок приемов пищи
        meal_order = [
            ('meal_breakfast', '🌅 Завтрак'),
            ('meal_lunch', '☀️ Обед'),
            ('meal_dinner', '🌙 Ужин'),
            ('meal_snack', '🍎 Перекус')
        ]
        
        total_calories = 0
        
        for meal_type, meal_name in meal_order:
            if meal_type in daily_meals:
                calories = daily_meals[meal_type]['calories']
                total_calories += calories
                stats_text += f"{meal_name} - {calories} калорий\n"
            else:
                stats_text += f"{meal_name} - 0 калорий\n"
        
        stats_text += f"\n🔥 **Всего за день:** {total_calories} калорий"
        
        # Добавляем процент от суточной нормы
        try:
            # Получаем данные пользователя для расчета суточной нормы
            user_data = get_user_by_telegram_id(user.id)
            if user_data:
                daily_norm = calculate_daily_calories(
                    user_data['age'], 
                    user_data['height'], 
                    user_data['weight'], 
                    user_data['gender'], 
                    user_data['activity_level']
                )
                percentage = round((total_calories / daily_norm) * 100, 1)
                stats_text += f"\n📊 **Процент от суточной нормы:** {percentage}%"
        except Exception as e:
            logger.error(f"Error calculating daily percentage: {e}")
        
        # Создаем клавиатуру
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к статистике", callback_data="statistics")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing today's statistics: {e}")
        await query.message.reply_text(
            "❌ Произошла ошибка при получении статистики. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к статистике", callback_data="statistics")]
            ])
        )

async def handle_stats_yesterday_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'За вчера'"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    try:
        # Получаем дату вчера
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Получаем статистику по приемам пищи за вчера
        daily_meals = get_daily_meals_by_type(user.id, yesterday)
        
        # Формируем сообщение со статистикой
        stats_text = "📊 **Ваша статистика за вчера:**\n\n"
        
        # Определяем порядок приемов пищи
        meal_order = [
            ('meal_breakfast', '🌅 Завтрак'),
            ('meal_lunch', '☀️ Обед'),
            ('meal_dinner', '🌙 Ужин'),
            ('meal_snack', '🍎 Перекус')
        ]
        
        total_calories = 0
        
        for meal_type, meal_name in meal_order:
            if meal_type in daily_meals:
                calories = daily_meals[meal_type]['calories']
                total_calories += calories
                stats_text += f"{meal_name} - {calories} калорий\n"
            else:
                stats_text += f"{meal_name} - 0 калорий\n"
        
        stats_text += f"\n🔥 **Всего за день:** {total_calories} калорий"
        
        # Добавляем процент от суточной нормы
        try:
            # Получаем данные пользователя для расчета суточной нормы
            user_data = get_user_by_telegram_id(user.id)
            if user_data:
                daily_norm = calculate_daily_calories(
                    user_data['age'], 
                    user_data['height'], 
                    user_data['weight'], 
                    user_data['gender'], 
                    user_data['activity_level']
                )
                percentage = round((total_calories / daily_norm) * 100, 1)
                stats_text += f"\n📊 **Процент от суточной нормы:** {percentage}%"
        except Exception as e:
            logger.error(f"Error calculating daily percentage: {e}")
        
        # Создаем клавиатуру
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к статистике", callback_data="statistics")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing yesterday's statistics: {e}")
        await query.message.reply_text(
            "❌ Произошла ошибка при получении статистики. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к статистике", callback_data="statistics")]
            ])
        )

async def handle_stats_week_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'За неделю'"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    try:
        # Получаем статистику за неделю
        week_stats = get_weekly_meals_by_type(user.id)
        
        # Формируем сообщение со статистикой
        stats_text = "📊 **Ваша статистика за неделю:**\n\n"
        
        # Определяем порядок дней недели
        days_order = [
            'Понедельник', 'Вторник', 'Среда', 'Четверг', 
            'Пятница', 'Суббота', 'Воскресенье'
        ]
        
        total_week_calories = 0
        
        for day in days_order:
            if day in week_stats:
                calories = week_stats[day]
                total_week_calories += calories
                stats_text += f"{day} - {calories} калорий\n"
            else:
                stats_text += f"{day} - 0 калорий\n"
        
        stats_text += f"\n🔥 **Всего за неделю:** {total_week_calories} калорий"
        
        # Создаем клавиатуру
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к статистике", callback_data="statistics")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing week's statistics: {e}")
        await query.message.reply_text(
            "❌ Произошла ошибка при получении статистики. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к статистике", callback_data="statistics")]
            ])
        )

async def show_meal_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику приемов пищи пользователя"""
    user = update.effective_user
    
    try:
        # Получаем статистику за сегодня
        daily_stats = get_daily_calories(user.id)
        
        # Получаем статистику за последние 7 дней
        weekly_stats = get_meal_statistics(user.id, 7)
        
        # Получаем информацию о пользователе
        user_data = await check_user_registration(user.id)
        if not user_data:
            await update.message.reply_text(
                "❌ Вы не зарегистрированы в системе!\n"
                "Используйте /register для регистрации."
            )
            return
        
        daily_calories = user_data[8]  # Суточная норма калорий
        consumed_calories = daily_stats['total_calories']
        remaining_calories = daily_calories - consumed_calories
        progress_percent = (consumed_calories / daily_calories * 100) if daily_calories > 0 else 0
        
        # Формируем сообщение со статистикой
        stats_text = f"""
📊 **Ваша статистика питания**

📅 **Сегодня ({daily_stats['meals_count']} приемов пищи):**
🔥 **Съедено:** {consumed_calories} ккал
🎯 **Норма:** {daily_calories} ккал
📈 **Осталось:** {remaining_calories} ккал
📊 **Прогресс:** {progress_percent:.1f}%

🍽️ **БЖУ за день:**
• Белки: {daily_stats['total_protein']:.1f}г
• Жиры: {daily_stats['total_fat']:.1f}г
• Углеводы: {daily_stats['total_carbs']:.1f}г

📈 **Статистика за неделю:**
"""
        
        # Добавляем статистику по дням
        for day_stat in weekly_stats[:5]:  # Показываем только последние 5 дней
            date_str = day_stat['date']
            day_calories = day_stat['daily_calories']
            meals_count = day_stat['meals_count']
            stats_text += f"• {date_str}: {day_calories} ккал ({meals_count} приемов)\n"
        
        if not weekly_stats:
            stats_text += "• Данных за неделю пока нет\n"
        
        # Создаем клавиатуру
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing meal statistics: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при получении статистики. Попробуйте позже."
        )

