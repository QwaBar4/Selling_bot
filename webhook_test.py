import asyncio
import logging
from telegram import Bot
from app.bot_logic import grant_subscription
from app.config import TELEGRAM_BOT_TOKEN, WG_SERVER_PORT

# Настройка логов для теста
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test():
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        user_id = 6773956229  # Замените на реальный ID вашего тестового пользователя
        logger.info(f"Запуск теста для user_id: {user_id}")
        logger.info(f"Используется порт: {WG_SERVER_PORT}")
        
        await grant_subscription(user_id, bot)
        logger.info("Тест успешно завершен!")
        
    except Exception as e:
        logger.error(f"Тест завершился ошибкой: {str(e)}", exc_info=True)

if __name__ == '__main__':
    asyncio.run(test())
