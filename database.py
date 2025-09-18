import sqlite3
import os
import logging
from contextlib import contextmanager
from typing import Optional, Tuple, Any
import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_TYPE, DATABASE_PATH, DATABASE_URL

logger = logging.getLogger(__name__)

def create_database() -> bool:
    """Создает базу данных и таблицы пользователей и приемов пищи"""
    try:
        if DATABASE_TYPE == "postgresql":
            return _create_postgresql_tables()
        else:
            return _create_sqlite_tables()
    except Exception as e:
        logger.error(f"Error creating database: {e}")
        return False

def _create_sqlite_tables() -> bool:
    """Создает таблицы в SQLite"""
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            
            # Создаем таблицу пользователей с указанными полями
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    gender TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    height REAL NOT NULL,
                    weight REAL NOT NULL,
                    activity_level TEXT NOT NULL,
                    daily_calories INTEGER NOT NULL,
                    subscription_type TEXT DEFAULT 'trial',
                    subscription_expires_at TIMESTAMP NULL,
                    is_premium BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Создаем таблицу приемов пищи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS meals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    meal_type TEXT NOT NULL,
                    meal_name TEXT NOT NULL,
                    dish_name TEXT NOT NULL,
                    calories INTEGER NOT NULL,
                    analysis_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                )
            ''')
            
            # Создаем таблицу для отслеживания использования функции "Узнать калории"
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS calorie_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    check_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                )
            ''')
            
            # Создаем индексы для быстрого поиска
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_telegram_id ON users(telegram_id)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_meals_telegram_id ON meals(telegram_id)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(created_at)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_meals_type ON meals(meal_type)
            ''')
            
            conn.commit()
        logger.info("SQLite database created successfully")
        return True
    except Exception as e:
        logger.error(f"Error creating SQLite database: {e}")
        return False

def _create_postgresql_tables() -> bool:
    """Создает таблицы в PostgreSQL"""
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            cursor = conn.cursor()
            
            # Создаем таблицу пользователей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    gender VARCHAR(50) NOT NULL,
                    age INTEGER NOT NULL,
                    height DECIMAL(5,2) NOT NULL,
                    weight DECIMAL(5,2) NOT NULL,
                    activity_level VARCHAR(100) NOT NULL,
                    daily_calories INTEGER NOT NULL,
                    subscription_type VARCHAR(50) DEFAULT 'trial',
                    subscription_expires_at TIMESTAMP NULL,
                    is_premium BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Создаем таблицу приемов пищи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS meals (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    meal_type VARCHAR(100) NOT NULL,
                    meal_name VARCHAR(255) NOT NULL,
                    dish_name VARCHAR(255) NOT NULL,
                    calories INTEGER NOT NULL,
                    analysis_type VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                )
            ''')
            
            # Создаем таблицу для отслеживания использования функции "Узнать калории"
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS calorie_checks (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    check_type VARCHAR(100) NOT NULL,
                    input_data TEXT NOT NULL,
                    result_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                )
            ''')
            
            # Создаем индексы для оптимизации
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_meals_telegram_id ON meals(telegram_id)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(created_at)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_meals_type ON meals(meal_type)
            ''')
            
            conn.commit()
            logger.info("PostgreSQL database created successfully")
            return True
    except Exception as e:
        logger.error(f"Error creating PostgreSQL database: {e}")
        return False

@contextmanager
def get_db_connection():
    """Контекстный менеджер для работы с базой данных с улучшенной обработкой ошибок"""
    conn = None
    try:
        if DATABASE_TYPE == "postgresql":
            conn = psycopg2.connect(DATABASE_URL)
            conn.autocommit = False
            yield conn
        else:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
            yield conn
    except (sqlite3.Error, psycopg2.Error) as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def get_user_by_telegram_id(telegram_id: int) -> Optional[Tuple[Any, ...]]:
    """Получает пользователя по telegram_id"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if DATABASE_TYPE == "postgresql":
                cursor.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
            else:
                cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            return cursor.fetchone()
    except Exception as e:
        logger.error(f"Error getting user by telegram_id {telegram_id}: {e}")
        return None

def create_user(telegram_id: int, name: str, gender: str, age: int, 
                height: float, weight: float, activity_level: str, 
                daily_calories: int) -> bool:
    """Создает нового пользователя"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (telegram_id, name, gender, age, height, weight, activity_level, daily_calories)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (telegram_id, name, gender, age, height, weight, activity_level, daily_calories))
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        logger.warning(f"User with telegram_id {telegram_id} already exists")
        return False
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return False

def delete_user_by_telegram_id(telegram_id: int) -> bool:
    """Удаляет пользователя по telegram_id"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
            deleted_rows = cursor.rowcount
            conn.commit()
            return deleted_rows > 0
    except Exception as e:
        logger.error(f"Error deleting user with telegram_id {telegram_id}: {e}")
        return False

def add_meal(telegram_id: int, meal_type: str, meal_name: str, dish_name: str, 
             calories: int, analysis_type: str = "unknown") -> bool:
    """Добавляет запись о приеме пищи"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO meals (telegram_id, meal_type, meal_name, dish_name, calories, analysis_type)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (telegram_id, meal_type, meal_name, dish_name, calories, analysis_type))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding meal for telegram_id {telegram_id}: {e}")
        return False

def get_user_meals(telegram_id: int, date_from: str = None, date_to: str = None) -> list:
    """Получает приемы пищи пользователя за период"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if date_from and date_to:
                cursor.execute('''
                    SELECT * FROM meals 
                    WHERE telegram_id = ? AND DATE(created_at) BETWEEN ? AND ?
                    ORDER BY created_at DESC
                ''', (telegram_id, date_from, date_to))
            else:
                cursor.execute('''
                    SELECT * FROM meals 
                    WHERE telegram_id = ? 
                    ORDER BY created_at DESC
                ''', (telegram_id,))
            
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"Error getting meals for telegram_id {telegram_id}: {e}")
        return []

def get_daily_calories(telegram_id: int, date: str = None) -> dict:
    """Получает статистику калорий за день"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if date:
                cursor.execute('''
                    SELECT 
                        SUM(calories) as total_calories,
                        COUNT(*) as meals_count
                    FROM meals 
                    WHERE telegram_id = ? AND DATE(created_at) = ?
                ''', (telegram_id, date))
            else:
                cursor.execute('''
                    SELECT 
                        SUM(calories) as total_calories,
                        COUNT(*) as meals_count
                    FROM meals 
                    WHERE telegram_id = ? AND DATE(created_at) = DATE('now')
                ''', (telegram_id,))
            
            result = cursor.fetchone()
            if result:
                return {
                    'total_calories': result[0] or 0,
                    'meals_count': result[1] or 0
                }
            return {
                'total_calories': 0,
                'meals_count': 0
            }
    except Exception as e:
        logger.error(f"Error getting daily calories for telegram_id {telegram_id}: {e}")
        return {
            'total_calories': 0,
            'meals_count': 0
        }

def get_meal_statistics(telegram_id: int, days: int = 7) -> dict:
    """Получает статистику приемов пищи за последние N дней"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    DATE(created_at) as date,
                    SUM(calories) as daily_calories,
                    COUNT(*) as meals_count
                FROM meals 
                WHERE telegram_id = ? AND created_at >= DATE('now', '-{} days')
                GROUP BY DATE(created_at)
                ORDER BY date DESC
            '''.format(days), (telegram_id,))
            
            results = cursor.fetchall()
            return [dict(row) for row in results]
    except Exception as e:
        logger.error(f"Error getting meal statistics for telegram_id {telegram_id}: {e}")
        return []

def delete_meal(meal_id: int, telegram_id: int) -> bool:
    """Удаляет запись о приеме пищи"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM meals WHERE id = ? AND telegram_id = ?", (meal_id, telegram_id))
            deleted_rows = cursor.rowcount
            conn.commit()
            return deleted_rows > 0
    except Exception as e:
        logger.error(f"Error deleting meal {meal_id} for telegram_id {telegram_id}: {e}")
        return False

def get_daily_meals_by_type(telegram_id: int, date: str = None) -> dict:
    """Получает калории по типам приемов пищи за день"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if date:
                cursor.execute('''
                    SELECT 
                        meal_type,
                        meal_name,
                        SUM(calories) as total_calories
                    FROM meals 
                    WHERE telegram_id = ? AND DATE(created_at) = ?
                    GROUP BY meal_type, meal_name
                    ORDER BY 
                        CASE meal_type
                            WHEN 'meal_breakfast' THEN 1
                            WHEN 'meal_lunch' THEN 2
                            WHEN 'meal_dinner' THEN 3
                            WHEN 'meal_snack' THEN 4
                            ELSE 5
                        END
                ''', (telegram_id, date))
            else:
                cursor.execute('''
                    SELECT 
                        meal_type,
                        meal_name,
                        SUM(calories) as total_calories
                    FROM meals 
                    WHERE telegram_id = ? AND DATE(created_at) = DATE('now')
                    GROUP BY meal_type, meal_name
                    ORDER BY 
                        CASE meal_type
                            WHEN 'meal_breakfast' THEN 1
                            WHEN 'meal_lunch' THEN 2
                            WHEN 'meal_dinner' THEN 3
                            WHEN 'meal_snack' THEN 4
                            ELSE 5
                        END
                ''', (telegram_id,))
            
            results = cursor.fetchall()
            meals_dict = {}
            
            for row in results:
                meal_type = row[0]
                meal_name = row[1]
                calories = row[2]
                meals_dict[meal_type] = {
                    'name': meal_name,
                    'calories': calories
                }
            
            return meals_dict
    except Exception as e:
        logger.error(f"Error getting daily meals by type for telegram_id {telegram_id}: {e}")
        return {}

def is_meal_already_added(telegram_id: int, meal_type: str, date: str = None) -> bool:
    """Проверяет, был ли уже добавлен прием пищи сегодня"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if date:
                cursor.execute('''
                    SELECT COUNT(*) FROM meals 
                    WHERE telegram_id = ? AND meal_type = ? AND DATE(created_at) = ?
                ''', (telegram_id, meal_type, date))
            else:
                cursor.execute('''
                    SELECT COUNT(*) FROM meals 
                    WHERE telegram_id = ? AND meal_type = ? AND DATE(created_at) = DATE('now')
                ''', (telegram_id, meal_type))
            
            count = cursor.fetchone()[0]
            return count > 0
    except Exception as e:
        logger.error(f"Error checking if meal already added for telegram_id {telegram_id}: {e}")
        return False

def get_weekly_meals_by_type(telegram_id: int) -> dict:
    """Получает калории по дням недели за последние 7 дней"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Получаем данные за последние 7 дней
            cursor.execute('''
                SELECT 
                    DATE(created_at) as date,
                    SUM(calories) as total_calories
                FROM meals 
                WHERE telegram_id = ? 
                AND DATE(created_at) >= DATE('now', '-6 days')
                AND DATE(created_at) <= DATE('now')
                GROUP BY DATE(created_at)
                ORDER BY DATE(created_at)
            ''', (telegram_id,))
            
            results = cursor.fetchall()
            
            # Создаем словарь с днями недели
            days_names = [
                'Понедельник', 'Вторник', 'Среда', 'Четверг', 
                'Пятница', 'Суббота', 'Воскресенье'
            ]
            
            week_stats = {}
            
            # Инициализируем все дни нулями
            for day in days_names:
                week_stats[day] = 0
            
            # Заполняем данные из базы
            for row in results:
                date_str = row[0]
                calories = row[1]
                
                # Получаем день недели
                cursor.execute('''
                    SELECT strftime('%w', ?)
                ''', (date_str,))
                day_of_week = cursor.fetchone()[0]
                
                # Преобразуем в индекс (0=воскресенье, 1=понедельник, ...)
                day_index = int(day_of_week)
                if day_index == 0:  # Воскресенье
                    day_index = 6
                else:
                    day_index -= 1
                
                if 0 <= day_index < 7:
                    week_stats[days_names[day_index]] = calories
            
            return week_stats
    except Exception as e:
        logger.error(f"Error getting weekly meals by type for telegram_id {telegram_id}: {e}")
        return {}

def delete_today_meals(telegram_id: int) -> bool:
    """Удаляет все приемы пищи за сегодняшний день"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Удаляем все приемы пищи за сегодня
            cursor.execute('''
                DELETE FROM meals 
                WHERE telegram_id = ? AND DATE(created_at) = DATE('now')
            ''', (telegram_id,))
            
            deleted_rows = cursor.rowcount
            conn.commit()
            
            logger.info(f"Deleted {deleted_rows} meals for user {telegram_id} for today")
            return deleted_rows > 0
    except Exception as e:
        logger.error(f"Error deleting today's meals for telegram_id {telegram_id}: {e}")
        return False

def delete_all_user_meals(telegram_id: int) -> bool:
    """Удаляет все приемы пищи пользователя за все время"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Удаляем все приемы пищи пользователя
            cursor.execute('''
                DELETE FROM meals 
                WHERE telegram_id = ?
            ''', (telegram_id,))
            
            deleted_rows = cursor.rowcount
            conn.commit()
            
            logger.info(f"Deleted {deleted_rows} meals for user {telegram_id} for all time")
            return deleted_rows > 0
    except Exception as e:
        logger.error(f"Error deleting all meals for telegram_id {telegram_id}: {e}")
        return False

def get_all_users() -> list:
    """Получает всех пользователей для админки"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT telegram_id, name, gender, age, height, weight, 
                       activity_level, daily_calories, created_at
                FROM users 
                ORDER BY created_at DESC
            ''')
            
            users = cursor.fetchall()
            return users
    except Exception as e:
        logger.error(f"Error getting all users: {e}")
        return []

def get_user_count() -> int:
    """Получает общее количество пользователей"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM users')
            count = cursor.fetchone()[0]
            return count
    except Exception as e:
        logger.error(f"Error getting user count: {e}")
        return 0

def get_meals_count() -> int:
    """Получает общее количество записей о приемах пищи"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM meals')
            count = cursor.fetchone()[0]
            return count
    except Exception as e:
        logger.error(f"Error getting meals count: {e}")
        return 0

def get_recent_meals(limit: int = 10) -> list:
    """Получает последние записи о приемах пищи"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT m.telegram_id, u.name, m.meal_name, m.dish_name, 
                       m.calories, m.analysis_type, m.created_at
                FROM meals m
                LEFT JOIN users u ON m.telegram_id = u.telegram_id
                ORDER BY m.created_at DESC
                LIMIT ?
            ''', (limit,))
            
            meals = cursor.fetchall()
            return meals
    except Exception as e:
        logger.error(f"Error getting recent meals: {e}")
        return []

def get_daily_stats() -> dict:
    """Получает статистику за сегодня"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Количество пользователей, добавивших еду сегодня
            cursor.execute('''
                SELECT COUNT(DISTINCT telegram_id) 
                FROM meals 
                WHERE DATE(created_at) = DATE('now')
            ''')
            active_users = cursor.fetchone()[0]
            
            # Общее количество калорий за сегодня
            cursor.execute('''
                SELECT SUM(calories) 
                FROM meals 
                WHERE DATE(created_at) = DATE('now')
            ''')
            total_calories = cursor.fetchone()[0] or 0
            
            # Количество записей за сегодня
            cursor.execute('''
                SELECT COUNT(*) 
                FROM meals 
                WHERE DATE(created_at) = DATE('now')
            ''')
            meals_today = cursor.fetchone()[0]
            
            return {
                'active_users': active_users,
                'total_calories': total_calories,
                'meals_today': meals_today
            }
    except Exception as e:
        logger.error(f"Error getting daily stats: {e}")
        return {'active_users': 0, 'total_calories': 0, 'meals_today': 0}

def migrate_database() -> bool:
    """Мигрирует существующую базу данных, добавляя новые таблицы"""
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            
            # Проверяем, существует ли таблица meals
            cursor.execute('''
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='meals'
            ''')
            
            if not cursor.fetchone():
                # Создаем таблицу приемов пищи
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS meals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_id INTEGER NOT NULL,
                        meal_type TEXT NOT NULL,
                        meal_name TEXT NOT NULL,
                        dish_name TEXT NOT NULL,
                        calories INTEGER NOT NULL,
                        analysis_type TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                    )
                ''')
                
                # Создаем индексы для новой таблицы
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_meals_telegram_id ON meals(telegram_id)
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(created_at)
                ''')
                
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_meals_type ON meals(meal_type)
                ''')
                
                conn.commit()
                logger.info("Meals table created successfully")
                return True
            else:
                # Проверяем, есть ли старые колонки
                cursor.execute("PRAGMA table_info(meals)")
                columns = [column[1] for column in cursor.fetchall()]
                
                if 'protein' in columns or 'fat' in columns or 'carbs' in columns or 'weight' in columns:
                    # Создаем новую таблицу без ненужных колонок
                    cursor.execute('''
                        CREATE TABLE meals_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            telegram_id INTEGER NOT NULL,
                            meal_type TEXT NOT NULL,
                            meal_name TEXT NOT NULL,
                            dish_name TEXT NOT NULL,
                            calories INTEGER NOT NULL,
                            analysis_type TEXT NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                        )
                    ''')
                    
                    # Копируем данные из старой таблицы
                    cursor.execute('''
                        INSERT INTO meals_new (id, telegram_id, meal_type, meal_name, dish_name, calories, analysis_type, created_at)
                        SELECT id, telegram_id, meal_type, meal_name, dish_name, calories, analysis_type, created_at
                        FROM meals
                    ''')
                    
                    # Удаляем старую таблицу
                    cursor.execute('DROP TABLE meals')
                    
                    # Переименовываем новую таблицу
                    cursor.execute('ALTER TABLE meals_new RENAME TO meals')
                    
                    # Создаем индексы
                    cursor.execute('''
                        CREATE INDEX IF NOT EXISTS idx_meals_telegram_id ON meals(telegram_id)
                    ''')
                    
                    cursor.execute('''
                        CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(created_at)
                    ''')
                    
                    cursor.execute('''
                        CREATE INDEX IF NOT EXISTS idx_meals_type ON meals(meal_type)
                    ''')
                    
                    conn.commit()
                    logger.info("Meals table migrated successfully - removed protein, fat, carbs, weight columns")
                else:
                    logger.info("Meals table already exists and is up to date")
            
            # Проверяем и добавляем поля подписки в таблицу users
            cursor.execute("PRAGMA table_info(users)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'subscription_type' not in columns:
                logger.info("Adding subscription fields to users table...")
                cursor.execute('ALTER TABLE users ADD COLUMN subscription_type TEXT DEFAULT "trial"')
                cursor.execute('ALTER TABLE users ADD COLUMN subscription_expires_at TIMESTAMP NULL')
                cursor.execute('ALTER TABLE users ADD COLUMN is_premium BOOLEAN DEFAULT 0')
                
                # Устанавливаем триальный период для существующих пользователей
                cursor.execute('''
                    UPDATE users 
                    SET subscription_expires_at = datetime(created_at, '+1 day')
                    WHERE subscription_type = 'trial' AND subscription_expires_at IS NULL
                ''')
                
                conn.commit()
                logger.info("Added subscription fields to users table")
            else:
                logger.info("Subscription fields already exist in users table")
            
            # Проверяем, существует ли таблица calorie_checks
            cursor.execute('''
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='calorie_checks'
            ''')
            
            if not cursor.fetchone():
                # Создаем таблицу для отслеживания использования функции "Узнать калории"
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS calorie_checks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_id INTEGER NOT NULL,
                        check_type TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                    )
                ''')
                
                conn.commit()
                logger.info("Calorie checks table created successfully")
            else:
                logger.info("Calorie checks table already exists")
            
            return True
    except Exception as e:
        logger.error(f"Error migrating database: {e}")
        return False

def check_user_subscription(telegram_id: int) -> dict:
    """Проверяет статус подписки пользователя"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT subscription_type, subscription_expires_at, is_premium, created_at
                FROM users 
                WHERE telegram_id = ?
            ''', (telegram_id,))
            
            result = cursor.fetchone()
            if not result:
                return {'is_active': False, 'type': 'none', 'expires_at': None}
            
            subscription_type, expires_at, is_premium, created_at = result
            
            # Если это триальный период
            if subscription_type == 'trial':
                if expires_at:
                    # Проверяем, не истек ли триальный период
                    cursor.execute('''
                        SELECT datetime('now') > ?
                    ''', (expires_at,))
                    is_expired = cursor.fetchone()[0]
                    
                    if is_expired:
                        return {'is_active': False, 'type': 'trial_expired', 'expires_at': expires_at}
                    else:
                        return {'is_active': True, 'type': 'trial', 'expires_at': expires_at}
                else:
                    # Если нет даты истечения, устанавливаем триальный период
                    cursor.execute('''
                        UPDATE users 
                        SET subscription_expires_at = datetime(created_at, '+1 day')
                        WHERE telegram_id = ?
                    ''', (telegram_id,))
                    conn.commit()
                    
                    cursor.execute('''
                        SELECT datetime(created_at, '+1 day')
                        FROM users 
                        WHERE telegram_id = ?
                    ''', (telegram_id,))
                    expires_at = cursor.fetchone()[0]
                    
                    return {'is_active': True, 'type': 'trial', 'expires_at': expires_at}
            
            # Если это премиум подписка
            elif subscription_type == 'premium' and is_premium:
                if expires_at:
                    cursor.execute('''
                        SELECT datetime('now') > ?
                    ''', (expires_at,))
                    is_expired = cursor.fetchone()[0]
                    
                    if is_expired:
                        return {'is_active': False, 'type': 'premium_expired', 'expires_at': expires_at}
                    else:
                        return {'is_active': True, 'type': 'premium', 'expires_at': expires_at}
                else:
                    return {'is_active': True, 'type': 'premium', 'expires_at': None}
            
            return {'is_active': False, 'type': 'none', 'expires_at': None}
            
    except Exception as e:
        logger.error(f"Error checking user subscription: {e}")
        return {'is_active': False, 'type': 'error', 'expires_at': None}

def activate_premium_subscription(telegram_id: int, days: int = 30) -> bool:
    """Активирует премиум подписку для пользователя"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Устанавливаем премиум подписку
            cursor.execute('''
                UPDATE users 
                SET subscription_type = 'premium',
                    is_premium = 1,
                    subscription_expires_at = datetime('now', '+{} days')
                WHERE telegram_id = ?
            '''.format(days), (telegram_id,))
            
            conn.commit()
            logger.info(f"Activated premium subscription for user {telegram_id} for {days} days")
            return True
            
    except Exception as e:
        logger.error(f"Error activating premium subscription: {e}")
        return False

def get_daily_calorie_checks_count(telegram_id: int) -> int:
    """Получает количество использований функции 'Узнать калории' за сегодня"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) FROM calorie_checks 
                WHERE telegram_id = ? AND DATE(created_at) = DATE('now')
            ''', (telegram_id,))
            return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"Error getting daily calorie checks count: {e}")
        return 0

def add_calorie_check(telegram_id: int, check_type: str) -> bool:
    """Добавляет запись об использовании функции 'Узнать калории'"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO calorie_checks (telegram_id, check_type) 
                VALUES (?, ?)
            ''', (telegram_id, check_type))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding calorie check: {e}")
        return False

# Создаем базу данных при импорте модуля
if not os.path.exists(DATABASE_PATH):
    if not create_database():
        logger.error("Failed to create database")
else:
    # Мигрируем существующую базу данных
    if not migrate_database():
        logger.error("Failed to migrate database")
