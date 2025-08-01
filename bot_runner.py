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
    update_user_subscription,
    get_next_available_ip
)
from app.payments import (
    create_freekassa_payment,
    create_kryptocloud_payment
)
from app.wireguard import (
    generate_client_keys,
    generate_wireguard_config,
    add_peer_to_server
)

# Настройка логирования
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE,  # Используем импортированную переменную
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
SELECTING_ACTION, SELECTING_PAYMENT = range(2)

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
        [InlineKeyboardButton(f"💳 Freekassa ({SUBSCRIPTION_PRICE_RUB} RUB)", callback_data='pay_freekassa')],
        [InlineKeyboardButton(f"💎 CryptoCloud ({SUBSCRIPTION_PRICE_USD} USD)", callback_data='pay_kryptocloud')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_start')]
    ]
    await query.edit_message_text("Выберите удобный способ оплаты:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECTING_PAYMENT

async def process_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    payment_system = query.data
    await query.answer()
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    
    if payment_system == 'pay_freekassa':
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
                CallbackQueryHandler(process_payment, pattern='^pay_freekassa$|^pay_kryptocloud$'),
                CallbackQueryHandler(start, pattern='^back_to_start$'),
            ],
        },
        fallbacks=[CommandHandler('start', start), MessageHandler(filters.TEXT, end_conversation)],
    )

    application.add_handler(conv_handler)
    logger.info("Бот запускается в режиме polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
