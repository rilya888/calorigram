import logging
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from config import BOT_TOKEN, DATABASE_TYPE, DATABASE_URL
from database import create_database
from bot_functions import (
    start_command, help_command, register_command, profile_command, reset_command, dayreset_command, admin_command, add_command, addmeal_command, addphoto_command, addtext_command, addvoice_command, subscription_command,
    handle_text_input, handle_callback_query, handle_photo, handle_voice
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

def main():
    """Основная функция запуска бота"""
    try:
        logger.info("Starting Calorigram bot...")
        logger.info(f"Database type: {DATABASE_TYPE}")
        logger.info(f"Database URL: {DATABASE_URL[:20]}..." if DATABASE_URL else "DATABASE_URL is None")
        
        # Инициализируем базу данных
        if create_database():
            logger.info("Database initialized successfully")
        else:
            logger.error("Failed to initialize database")
            return
        
        # Создаем приложение
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Добавляем обработчики команд
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("register", register_command))
        application.add_handler(CommandHandler("profile", profile_command))
        application.add_handler(CommandHandler("subscription", subscription_command))
        application.add_handler(CommandHandler("reset", reset_command))
        application.add_handler(CommandHandler("dayreset", dayreset_command))
        application.add_handler(CommandHandler("admin", admin_command))
        application.add_handler(CommandHandler("add", add_command))
        application.add_handler(CommandHandler("addmeal", addmeal_command))
        application.add_handler(CommandHandler("addphoto", addphoto_command))
        application.add_handler(CommandHandler("addtext", addtext_command))
        application.add_handler(CommandHandler("addvoice", addvoice_command))
        
        # Добавляем обработчик callback запросов
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        
        # Добавляем обработчик текстовых сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))
        
        # Добавляем обработчик фотографий
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        
        # Добавляем обработчик голосовых сообщений
        application.add_handler(MessageHandler(filters.VOICE, handle_voice))
        
        # Запускаем бота
        logger.info("Bot started successfully")
        print("Бот запущен...")
        application.run_polling()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"Ошибка запуска бота: {e}")
        raise

if __name__ == '__main__':
    main()
