import os
import logging
import logging.handlers
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from app.config import (
    TELEGRAM_BOT_TOKEN,
    LOG_FILE,
    SUBSCRIPTION_DAYS,
    SUBSCRIPTION_PRICE_RUB,
    SUBSCRIPTION_PRICE_USD
)
from app.database import (
    init_db,
    add_user,
    get_user,
    update_user_subscription
)
from app.payments import (
    create_freekassa_payment,
    create_kryptocloud_payment
)
from app.bot_logic import (
    grant_temp_config,
    cleanup_expired_configs
)

# Настройка логирования
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE,
    maxBytes=10*1024*1024,
    backupCount=5
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[log_handler, logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
SELECTING_ACTION, SELECTING_PAYMENT, FREEKASSA_VPN_CHOICE = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    add_user(user.id, user.username, user.first_name)
    logger.info(f"Пользователь {user.id} ({user.username}) запустил бота.")

    keyboard = [
        [InlineKeyboardButton("🛒 Купить подписку", callback_data='buy')],
        [InlineKeyboardButton("👤 Мой профиль", callback_data='profile')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    start_text = (f"👋 Привет, {user.first_name}!\n\n"
                "Я бот для продажи безопасных конфигураций WireGuard. "
                f"Подписка на {SUBSCRIPTION_DAYS} дней.\n\n"
                "Нажмите 'Купить подписку', чтобы начать.")
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(start_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(start_text, reply_markup=reply_markup)
        
    return SELECTING_ACTION

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_data = get_user(update.effective_user.id)

    if user_data and user_data.get('subscription_end_date'):
        sub_end_date = datetime.fromisoformat(user_data['subscription_end_date']).strftime('%d.%m.%Y %H:%M')
        message = (f"👤 **Ваш профиль**\n\n"
                 f"✅ **Подписка активна до:** `{sub_end_date}`\n\n"
                 "Ваш конфигурационный файл ниже. Просто импортируйте его в приложение WireGuard.")
        await query.edit_message_text(message, parse_mode='Markdown')
        await context.bot.send_document(
            chat_id=user_data['telegram_id'],
            document=bytes(user_data['wireguard_config'], 'utf-8'),
            filename=f"wg_{user_data['telegram_id']}.conf"
        )
    else:
        message = "У вас еще нет активной подписки."
        keyboard = [[InlineKeyboardButton("🛒 Купить подписку", callback_data='buy')]]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        
    return SELECTING_ACTION

async def ask_for_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton(f"💳 Freekassa ({SUBSCRIPTION_PRICE_RUB} RUB)", callback_data='choose_freekassa')],
        [InlineKeyboardButton(f"💎 CryptoCloud ({SUBSCRIPTION_PRICE_USD} USD)", callback_data='pay_kryptocloud')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_start')]
    ]
    await query.edit_message_text("Выберите удобный способ оплаты:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECTING_PAYMENT

async def ask_freekassa_vpn_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Спрашиваем у пользователя, есть ли у него VPN для доступа к Freekassa"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("✅ У меня есть VPN", callback_data='pay_freekassa_direct')],
        [InlineKeyboardButton("❌ У меня нет VPN", callback_data='need_temp_vpn')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='buy')]
    ]
    
    message = (
        "💳 **Оплата через Freekassa**\n\n"
        "Для доступа к сайту Freekassa может потребоваться VPN.\n\n"
        "У вас есть рабочий VPN?"
    )
    
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return FREEKASSA_VPN_CHOICE

async def provide_temp_vpn_and_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдает временный VPN и ссылку на оплату"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    # Выдаем временный конфиг
    await grant_temp_config(user_id, context.bot)
    
    # Создаем ссылку на оплату
    payment_url, _ = create_freekassa_payment(user_id)
    
    if payment_url:
        keyboard = [
            [InlineKeyboardButton("🔗 Перейти к оплате Freekassa", url=payment_url)],
            [InlineKeyboardButton("⬅️ Назад в меню", callback_data='back_to_start')]
        ]
        
        message = (
            "🕐 **Временный VPN выдан на 10 минут!**\n\n"
            "1️⃣ Импортируйте временный конфиг в WireGuard\n"
            "2️⃣ Подключитесь к VPN\n"
            "3️⃣ Нажмите кнопку ниже для оплаты\n\n"
            "⚠️ После успешной оплаты временный конфиг будет отключен, "
            "а вам будет выдан постоянный конфиг на весь период подписки."
        )
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await query.edit_message_text("Произошла ошибка при создании счета. Попробуйте позже.")
        
    return ConversationHandler.END

async def process_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    payment_system = query.data
    await query.answer()
    user_id = update.effective_user.id
    
    if payment_system == 'pay_freekassa_direct':
        payment_url, _ = create_freekassa_payment(user_id)
    elif payment_system == 'pay_kryptocloud':
        payment_url, _ = create_kryptocloud_payment(user_id)
    else:
        return SELECTING_ACTION

    if payment_url:
        message = ("Нажмите на кнопку ниже, чтобы перейти к оплате.\n\n"
                 "После успешной оплаты я автоматически выдам вам подписку. "
                 "Возвращаться и нажимать 'Проверить' не нужно.")
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Перейти к оплате", url=payment_url)]
        ]))
    else:
        await query.edit_message_text("Произошла ошибка при создании счета. Пожалуйста, попробуйте позже.")
        logger.error(f"Не удалось создать счет {payment_system} для {user_id}")
        
    return ConversationHandler.END

async def end_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Действие отменено. Чтобы начать заново, введите /start")
    return ConversationHandler.END

async def periodic_cleanup(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая очистка истекших временных конфигов"""
    await cleanup_expired_configs(context.bot)

def main():
    init_db()
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(ask_for_payment_method, pattern='^buy$'),
                CallbackQueryHandler(show_profile, pattern='^profile$'),
            ],
            SELECTING_PAYMENT: [
                CallbackQueryHandler(ask_freekassa_vpn_choice, pattern='^choose_freekassa$'),
                CallbackQueryHandler(process_payment, pattern='^pay_kryptocloud$'),
                CallbackQueryHandler(start, pattern='^back_to_start$'),
            ],
            FREEKASSA_VPN_CHOICE: [
                CallbackQueryHandler(process_payment, pattern='^pay_freekassa_direct$'),
                CallbackQueryHandler(provide_temp_vpn_and_payment, pattern='^need_temp_vpn$'),
                CallbackQueryHandler(ask_for_payment_method, pattern='^buy$'),
            ],
        },
        fallbacks=[CommandHandler('start', start), MessageHandler(filters.TEXT, end_conversation)],
    )

    application.add_handler(conv_handler)
    
    # Добавляем периодическую задачу очистки каждые 5 минут
    # Получаем job_queue после создания application
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(periodic_cleanup, interval=300, first=10)
        logger.info("Periodic cleanup job scheduled")
    else:
        logger.warning("Job queue not available, periodic cleanup disabled")
    
    logger.info("Бот запускается в режиме polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
