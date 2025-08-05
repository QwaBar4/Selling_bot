import os
import logging
import logging.handlers
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, KeyboardButton, ReplyKeyboardMarkup
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
    SUBSCRIPTION_PRICE_USD,
    ADMIN_TELEGRAM_IDS
)
from app.database import (
    init_db,
    add_user,
    get_user,
    update_user_subscription,
    get_all_users,
    get_user_by_username
)
from app.payments import (
    create_freekassa_payment,
    create_kryptocloud_payment
)
from app.bot_logic import (
    grant_temp_config,
    cleanup_expired_configs,
    grant_subscription,
    deactivate_user_temp_config
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
SELECTING_ACTION, SELECTING_PAYMENT, FREEKASSA_VPN_CHOICE, SHOWING_INSTRUCTION, ALTERNATIVE_PAYMENT = range(5)
# Состояния для админской панели
ADMIN_MENU, ADMIN_GRANT_CONFIG, ADMIN_DELETE_CONFIG = range(10, 13)

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    return user_id in ADMIN_TELEGRAM_IDS

async def delete_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет сообщения пользователя"""
    try:
        if update.message:
            await update.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение: {e}")

async def handle_user_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает и удаляет сообщения пользователя"""
    await delete_user_message(update, context)
    return SELECTING_ACTION

async def delete_temp_config_message(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Удаляет сообщение с временным файлом конфига, но оставляет данные"""
    try:
        file_message_id = context.user_data.get(f'temp_file_message_{user_id}')
        if file_message_id:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=file_message_id)
                logger.info(f"Удалено сообщение с временным файлом для пользователя {user_id}")
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение с временным файлом: {e}")

        context.user_data.pop(f'temp_file_message_{user_id}', None)

    except Exception as e:
        logger.warning(f"Ошибка при удалении сообщения с временным конфигом для пользователя {user_id}: {e}")

async def delete_permanent_config_message(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Удаляет сообщение с постоянным файлом конфига, но оставляет данные"""
    try:
        perm_file_message_id = context.user_data.get(f'perm_file_message_{user_id}')
        if perm_file_message_id:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=perm_file_message_id)
                logger.info(f"Удалено сообщение с постоянным файлом для пользователя {user_id}")
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение с постоянным файлом: {e}")

        context.user_data.pop(f'perm_file_message_{user_id}', None)

    except Exception as e:
        logger.warning(f"Ошибка при удалении сообщения с постоянным конфигом для пользователя {user_id}: {e}")

async def delete_temp_notification_message(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Удаляет уведомление об отключении временного конфига"""
    try:
        notification_message_id = context.user_data.get(f'temp_notification_{user_id}')
        if notification_message_id:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=notification_message_id)
                logger.info(f"Удалено уведомление об отключении временного конфига для пользователя {user_id}")
            except Exception as e:
                logger.warning(f"Не удалось удалить уведомление: {e}")

        context.user_data.pop(f'temp_notification_{user_id}', None)

    except Exception as e:
        logger.warning(f"Ошибка при удалении уведомления для пользователя {user_id}: {e}")

async def delete_admin_notification_message(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Удаляет уведомление о выдаче подписки админом"""
    try:
        admin_notification_message_id = context.user_data.get(f'admin_notification_{user_id}')
        if admin_notification_message_id:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=admin_notification_message_id)
                logger.info(f"Удалено уведомление о выдаче подписки для пользователя {user_id}")
            except Exception as e:
                logger.warning(f"Не удалось удалить уведомление о выдаче подписки: {e}")

        context.user_data.pop(f'admin_notification_{user_id}', None)

    except Exception as e:
        logger.warning(f"Ошибка при удалении уведомления о выдаче подписки для пользователя {user_id}: {e}")

async def delete_menu_message(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Удаляет сообщение с кнопкой меню"""
    try:
        menu_message_id = context.user_data.get(f'menu_message_{user_id}')
        if menu_message_id:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=menu_message_id)
                logger.info(f"Удалено сообщение с кнопкой меню для пользователя {user_id}")
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение с кнопкой меню: {e}")

        context.user_data.pop(f'menu_message_{user_id}', None)

    except Exception as e:
        logger.warning(f"Ошибка при удалении сообщения с кнопкой меню для пользователя {user_id}: {e}")

async def clear_temp_config_data(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Полностью очищает все данные временного конфига из контекста"""
    try:
        await delete_temp_config_message(context, user_id)
        context.user_data.pop(f'temp_config_{user_id}', None)
        logger.info(f"Полностью очищены данные временного конфига для пользователя {user_id}")
    except Exception as e:
        logger.warning(f"Ошибка при полной очистке данных временного конфига для пользователя {user_id}: {e}")

async def periodic_cleanup(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая очистка истекших временных конфигов"""
    await cleanup_expired_configs(context.bot)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    add_user(user.id, user.username, user.first_name)
    logger.info(f"Пользователь {user.id} ({user.username}) запустил бота.")

    # Удаляем только сообщения с конфигами и уведомлениями (БЕЗ меню)
    await delete_temp_config_message(context, user.id)
    await delete_permanent_config_message(context, user.id)
    await delete_temp_notification_message(context, user.id)
    await delete_admin_notification_message(context, user.id)

    # Создаем кнопку "Меню" в меню пользователя
    keyboard_menu = ReplyKeyboardMarkup(
        [[KeyboardButton("📋 Меню")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    keyboard = [
        [InlineKeyboardButton("🛒 Купить подписку", callback_data='buy')],
        [InlineKeyboardButton("👤 Мой профиль", callback_data='profile')],
        [InlineKeyboardButton("♿️ Поддержка", url='https://t.me/')],
    ]
    
    # Добавляем кнопку админа для админов
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("🔧 Админ", callback_data='admin_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Используем HTML разметку вместо Markdown
    start_text = (
        f"👋 Привет, {user.first_name}! 🔥\n\n"
        "Ты нашел VPN, который позволит тебе:\n\n"
        "└ 🚀 Низкий пинг и высокую скорость интернета\n"
        "└ 📈 Неограниченное количество траффика\n"
        "└ 👥 Круглосуточную поддержку в чате\n"
        "└ 🖥 Туннель для всех ваших девайсов\n"
        "└ 🥷 Высокую анонимность\n\n"
        f"Подписка на {SUBSCRIPTION_DAYS} дней.\n"
        "Нажмите 'Купить подписку', чтобы начать.\n\n"
        "<i>Shallow by <a href='https://t.me/-'></a></i>\n\n"
    )

    try:
        if update.callback_query:
            # Если это callback query, редактируем сообщение
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                start_text, 
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        else:
            # Если это команда /start или кнопка "Меню"
            if update.message:
                await delete_user_message(update, context)
            
            # СНАЧАЛА удаляем старое сообщение с меню (если есть)
            await delete_menu_message(context, user.id)
            
            # Отправляем основное сообщение
            await context.bot.send_message(
                chat_id=user.id,
                text=start_text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            
            # Отправляем сообщение с кнопкой меню и сохраняем его ID для последующего удаления
            try:
                menu_message = await context.bot.send_message(
                    chat_id=user.id,
                    text="Используйте кнопку ниже для быстрого доступа к меню:",
                    reply_markup=keyboard_menu
                )
                # Сохраняем ID сообщения с меню для последующего удаления
                context.user_data[f'menu_message_{user.id}'] = menu_message.message_id
            except Exception as e:
                logger.warning(f"Не удалось отправить кнопку меню: {e}")

    except Exception as e:
        logger.error(f"Ошибка в функции start: {e}")
        # Резервное сообщение без форматирования
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=f"👋 Привет, {user.first_name}! Добро пожаловать в VPN бот!",
                reply_markup=reply_markup
            )
        except Exception as fallback_error:
            logger.error(f"Резервное сообщение также не удалось отправить: {fallback_error}")

    return SELECTING_ACTION

async def handle_start_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает нажатие кнопки 'Меню'"""
    if update.message and update.message.text == "📋 Меню":
        # Удаляем сообщение с меню ПЕРЕД вызовом start
        await delete_menu_message(context, update.effective_user.id)
        await delete_user_message(update, context)
        return await start(update, context)
    return SELECTING_ACTION

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user_data = get_user(user_id)

    # Удаляем сообщение с меню при переходе в профиль
    await delete_menu_message(context, user_id)
    # Удаляем остальные сообщения
    await delete_temp_config_message(context, user_id)
    await delete_permanent_config_message(context, user_id)
    await delete_temp_notification_message(context, user_id)
    await delete_admin_notification_message(context, user_id)

    if user_data and user_data.get('subscription_end_date'):
        sub_end_date = datetime.fromisoformat(user_data['subscription_end_date']).strftime('%d.%m.%Y %H:%M')
        message = (f"👤 **Ваш профиль**\n\n"
                  f"✅ **Подписка активна до:** `{sub_end_date}`\n\n"
                  "Ваш конфигурационный файл ниже. Просто импортируйте его в приложение WireGuard.")

        keyboard = [[InlineKeyboardButton("⬅ Назад в меню", callback_data='back_to_start')]]
        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        # Отправляем постоянный конфиг и сохраняем ID сообщения
        perm_file_message = await context.bot.send_document(
            chat_id=user_data['telegram_id'],
            document=bytes(user_data['wireguard_config'], 'utf-8'),
            filename=f"Sh1M_{str(user_data['telegram_id'])[:4]}.conf"
        )

        # Сохраняем ID сообщения с постоянным файлом для последующего удаления
        context.user_data[f'perm_file_message_{user_id}'] = perm_file_message.message_id

    else:
        message = "У вас еще нет активной подписки."
        keyboard = [
            [InlineKeyboardButton("🛒 Купить подписку", callback_data='buy')],
            [InlineKeyboardButton("⬅ Назад в меню", callback_data='back_to_start')]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    return SELECTING_ACTION

async def ask_for_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # Удаляем сообщение с меню при переходе к оплате
    await delete_menu_message(context, user_id)
    # Удаляем остальные сообщения
    await delete_temp_config_message(context, user_id)
    await delete_permanent_config_message(context, user_id)
    await delete_temp_notification_message(context, user_id)
    await delete_admin_notification_message(context, user_id)

    keyboard = [
        [InlineKeyboardButton(f"💳 Freekassa ({SUBSCRIPTION_PRICE_RUB} RUB)", callback_data='choose_freekassa')],
        [InlineKeyboardButton(f"💎 CryptoCloud ({SUBSCRIPTION_PRICE_USD} USD)", callback_data='pay_kryptocloud')],
        [InlineKeyboardButton("🔄 Альтернативные способы оплаты", callback_data='alternative_payment')],
        [InlineKeyboardButton("📲 Инструкция по установке", callback_data='show_instruction')],
        [InlineKeyboardButton("⬅ Назад", callback_data='back_to_start')]
    ]

    message = (
        "Доступные регионы 🌐 \n\n"
        "└ 🇳🇱 Нидерланды\n"
        "└ 🇫🇮 <s>Финляндия</s>\n"
        "└ 🇫🇷 <s>Франция</s>\n"
        "└ 🇹🇷 <s>Турция</s>\n"
        "└ 🇷🇺 <s>Россия</s>\n\n"
        "Стоимость подписки:\n"
        "1 мес. - 150 руб.\n"
        "<s>3 мес. - 300 руб.</s>\n"
        "<s>6 мес. - 500 руб.</s>\n"
        "<s>12 мес. - 900 руб.</s>\n\n"
        "Выберите удобный способ оплаты 👇"
    )

    await query.edit_message_text(
        text=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return SELECTING_PAYMENT

async def show_alternative_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает альтернативные способы оплаты"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # Удаляем сообщение с меню
    await delete_menu_message(context, user_id)
    # Удаляем остальные сообщения
    await delete_temp_config_message(context, user_id)
    await delete_permanent_config_message(context, user_id)
    await delete_temp_notification_message(context, user_id)
    await delete_admin_notification_message(context, user_id)

    keyboard = [
        [InlineKeyboardButton("👨‍💼 Админ 1", url='https://t.me/')],
        [InlineKeyboardButton("👨‍💼 Админ 2", url='https://t.me/')],
        [InlineKeyboardButton("⬅ Назад к оплате", callback_data='buy')]
    ]

    message = (
        "🔄 <b>Альтернативные способы оплаты</b>\n\n"
        "Если предложенные способы приобретения товара вам не подходят, "
        "у вас есть возможность обратиться к администраторам бота "
        "для дальнейшего приобретения без интерфейса бота.\n\n"
        "Свяжитесь с одним из администраторов:"
    )

    await query.edit_message_text(
        text=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return ALTERNATIVE_PAYMENT

async def show_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # Удаляем сообщение с меню
    await delete_menu_message(context, user_id)
    # Удаляем остальные сообщения
    await delete_temp_config_message(context, user_id)
    await delete_permanent_config_message(context, user_id)
    await delete_temp_notification_message(context, user_id)
    await delete_admin_notification_message(context, user_id)

    instruction_text = (
        "📲 <b>Инструкция по установке WireGuard</b>\n\n"
        "🔹 <b>Android, iOS</b>\n\n"
        "1️⃣ Скачай официальный клиент → <a href='https://play.google.com/store/apps/details?id=com.wireguard.android'>WireGuard в Google Play</a>, "
        "<a href='https://apps.apple.com/us/app/wireguard/id1441195209'>AppStore</a>\n\n"
        "2️⃣ Открой приложение → Нажми «+» → «Добавить туннель»\n\n"
        "3️⃣ Найдите файл *.conf и выберите его\n\n"
        "4️⃣ Проверь IP на 2ip.ru – должен отображаться IP сервера VPN ✅\n\n"
        "💻 <b>Windows</b>\n\n"
        "1️⃣ Скачай клиент → <a href='https://www.wireguard.com/install/'>Официальный сайт WireGuard</a>\n\n"
        "2️⃣ Загрузи конфиг из личного кабинета (файл *.conf)\n\n"
        "3️⃣ Открой клиент → «Добавить туннель» → «Импорт из файла»\n\n"
        "4️⃣ Выбери скачанный конфиг → «Подключить»\n\n"
        "Так ваш интернет станет быстрее и безопаснее! Наслаждайтесь свободным серфингом без ограничений."
    )

    keyboard = [
        [InlineKeyboardButton("⬅ Назад к оплате", callback_data='buy')]
    ]

    await query.edit_message_text(
        text=instruction_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML',
        disable_web_page_preview=True
    )
    return SHOWING_INSTRUCTION

# АДМИНСКАЯ ПАНЕЛЬ

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает админское меню"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа к админской панели", show_alert=True)
        return SELECTING_ACTION

    # Удаляем сообщение с меню при переходе в админку
    await delete_menu_message(context, user_id)
    # Удаляем остальные сообщения
    await delete_temp_config_message(context, user_id)
    await delete_permanent_config_message(context, user_id)
    await delete_temp_notification_message(context, user_id)
    await delete_admin_notification_message(context, user_id)

    keyboard = [
        [InlineKeyboardButton("➕ Выдать конфиг по тегу", callback_data='admin_grant_config')],
        [InlineKeyboardButton("👥 Список пользователей", callback_data='admin_user_list')],
        [InlineKeyboardButton("🗑 Удалить конфиг по тегу", callback_data='admin_delete_config')],
        [InlineKeyboardButton("⬅ Назад в меню", callback_data='back_to_start')]
    ]

    message = (
        "🔧 <b>Админская панель</b>\n\n"
        "Выберите действие:"
    )

    await query.edit_message_text(
        text=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return ADMIN_MENU

async def admin_request_username_for_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрашивает username для выдачи конфига"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not is_admin(user_id):
        return SELECTING_ACTION

    keyboard = [
        [InlineKeyboardButton("⬅ Назад в админку", callback_data='admin_menu')]
    ]

    message = (
        "➕ <b>Выдача конфига</b>\n\n"
        "Отправьте username пользователя (с @ или без):"
    )

    await query.edit_message_text(
        text=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return ADMIN_GRANT_CONFIG

async def admin_grant_config_by_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выдает конфиг по username"""
    if not is_admin(update.effective_user.id):
        return SELECTING_ACTION

    username = update.message.text.strip().replace('@', '')
    await delete_user_message(update, context)

    try:
        # Ищем пользователя по username
        user_data = get_user_by_username(username)
        
        if not user_data:
            await context.bot.send_message(
                update.effective_user.id,
                f"❌ Пользователь @{username} не найден в базе данных."
            )
            return ADMIN_GRANT_CONFIG

        target_user_id = user_data['telegram_id']
        
        # Проверяем, есть ли уже активная подписка
        if user_data.get('subscription_end_date'):
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end_date'])
                if end_date > datetime.now():
                    remaining_days = (end_date - datetime.now()).days
                    await context.bot.send_message(
                        update.effective_user.id,
                        f"⚠️ У пользователя @{username} уже есть активная подписка!\n"
                        f"Осталось дней: {remaining_days}"
                    )
                    return ADMIN_GRANT_CONFIG
            except:
                pass

        # Выдаем подписку
        await grant_subscription(target_user_id, context.bot, context)  # Передаем context
        
        # Уведомляем админа
        await context.bot.send_message(
            update.effective_user.id,
            f"✅ Конфиг успешно выдан пользователю @{username} (ID: {target_user_id})"
        )

        logger.info(f"Админ {update.effective_user.id} выдал конфиг пользователю @{username} ({target_user_id})")

    except Exception as e:
        logger.error(f"Ошибка при выдаче конфига админом: {e}")
        await context.bot.send_message(
            update.effective_user.id,
            f"❌ Произошла ошибка при выдаче конфига: {str(e)}"
        )

    return ADMIN_MENU

async def admin_show_user_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает список пользователей с работающей кнопкой назад"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        await query.edit_message_text("🚫 Доступ запрещен")
        return SELECTING_ACTION

    try:
        # Показываем индикатор загрузки
        await query.edit_message_text("⏳ Загружаю данные...")

        users = get_all_users()
        message = "📊 <b>Список пользователей</b>\n\n"
        
        if not users:
            message += "Пользователи не найдены"
        else:
            for i, user in enumerate(users[:20], 1):
                username = f"@{user['username']}" if user['username'] else "без username"
                status = "❌ неактивна"
                
                if user.get('subscription_end_date'):
                    try:
                        end_date = datetime.fromisoformat(user['subscription_end_date'])
                        if end_date > datetime.now():
                            days_left = (end_date - datetime.now()).days
                            status = f"✅ активна ({days_left} дн.)"
                    except:
                        pass
                
                message += f"{i}. {user.get('first_name', 'Без имени')} ({username})\nID: {user['telegram_id']} | Подписка: {status}\n\n"

            if len(users) > 20:
                message += f"Показано 20 из {len(users)} пользователей"

        # Клавиатура с работающей кнопкой "Назад"
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data='admin_user_list')],
            [InlineKeyboardButton("🔙 В админку", callback_data='admin_menu')]
        ]

        await query.edit_message_text(
            text=message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Ошибка в admin_show_user_list: {str(e)}")
        await query.edit_message_text(
            "❌ Ошибка загрузки списка",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 В админку", callback_data='admin_menu')]
            ])
        )

    return ADMIN_MENU  # Остаемся в состоянии ADMIN_MENU

async def admin_request_username_for_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрашивает username для удаления конфига"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not is_admin(user_id):
        return SELECTING_ACTION

    keyboard = [
        [InlineKeyboardButton("⬅ Назад в админку", callback_data='admin_menu')]
    ]

    message = (
        "🗑 <b>Удаление конфига</b>\n\n"
        "Отправьте username пользователя (с @ или без):"
    )

    await query.edit_message_text(
        text=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return ADMIN_DELETE_CONFIG

async def admin_delete_config_by_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Удаляет конфиг по username"""
    if not is_admin(update.effective_user.id):
        return SELECTING_ACTION

    username = update.message.text.strip().replace('@', '')
    await delete_user_message(update, context)

    try:
        # Ищем пользователя по username
        user_data = get_user_by_username(username)
        
        if not user_data:
            await context.bot.send_message(
                update.effective_user.id,
                f"❌ Пользователь @{username} не найден в базе данных."
            )
            return ADMIN_DELETE_CONFIG

        target_user_id = user_data['telegram_id']
        
        # Проверяем, есть ли активная подписка
        if not user_data.get('subscription_end_date'):
            await context.bot.send_message(
                update.effective_user.id,
                f"⚠️ У пользователя @{username} нет активной подписки."
            )
            return ADMIN_DELETE_CONFIG

        # Проверяем, не истекла ли подписка
        try:
            end_date = datetime.fromisoformat(user_data['subscription_end_date'])
            if end_date <= datetime.now():
                await context.bot.send_message(
                    update.effective_user.id,
                    f"⚠️ Подписка пользователя @{username} уже истекла."
                )
                return ADMIN_DELETE_CONFIG
        except:
            pass

        # Деактивируем временный конфиг если есть
        await deactivate_user_temp_config(target_user_id, context.bot, context)
        
        # Деактивируем основную подписку
        success = False
        if user_data.get('wg_easy_client_id'):
            # Импортируем wireguard здесь
            from app import wireguard
            from app.database import deactivate_user_subscription
            
            # Удаляем клиента из WG-Easy
            if wireguard.delete_client(user_data['wg_easy_client_id']):
                # ПОЛНОСТЬЮ деактивируем подписку в базе данных
                deactivate_user_subscription(target_user_id)
                success = True
                logger.info(f"Удален WG-Easy клиент {user_data['wg_easy_client_id']} для пользователя {target_user_id}")
            else:
                logger.error(f"Не удалось удалить WG-Easy клиента {user_data['wg_easy_client_id']}")
        
        if success:
            # Удаляем все сообщения с конфигами у пользователя
            await delete_temp_config_message(context, target_user_id)
            await delete_permanent_config_message(context, target_user_id)
            await delete_temp_notification_message(context, target_user_id)
            await delete_admin_notification_message(context, target_user_id)
            
            # Уведомляем админа
            await context.bot.send_message(
                update.effective_user.id,
                f"✅ Конфиг пользователя @{username} (ID: {target_user_id}) успешно удален"
            )
            
            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    target_user_id,
                    "⚠️ Ваша подписка была отключена администратором.\n\n"
                    "Для получения информации обратитесь в поддержку."
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить пользователя {target_user_id}: {e}")

            logger.info(f"Админ {update.effective_user.id} удалил конфиг пользователя @{username} ({target_user_id})")
        else:
            await context.bot.send_message(
                update.effective_user.id,
                f"❌ Не удалось удалить конфиг пользователя @{username}"
            )

    except Exception as e:
        logger.error(f"Ошибка при удалении конфига админом: {e}")
        await context.bot.send_message(
            update.effective_user.id,
            f"❌ Произошла ошибка при удалении конфига: {str(e)}"
        )

    return ADMIN_MENU

async def ask_freekassa_vpn_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # Удаляем сообщение с меню
    await delete_menu_message(context, user_id)
    # Удаляем остальные сообщения
    await delete_temp_config_message(context, user_id)
    await delete_permanent_config_message(context, user_id)
    await delete_temp_notification_message(context, user_id)
    await delete_admin_notification_message(context, user_id)

    keyboard = [
        [InlineKeyboardButton("✅ У меня есть VPN", callback_data='pay_freekassa_direct')],
        [InlineKeyboardButton("❌ У меня нет VPN", callback_data='need_temp_vpn')],
        [InlineKeyboardButton("⬅ Назад к оплате", callback_data='buy')]
    ]

    message = (
        "💳 <b>Оплата через Freekassa</b>\n\n"
        "Для доступа к сайту Freekassa может потребоваться VPN.\n\n"
        "<i>У вас есть рабочий VPN?</i>"
    )

    await query.edit_message_text(
        text=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return FREEKASSA_VPN_CHOICE

async def provide_temp_vpn_and_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выдает временный VPN и ссылку на оплату"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # Удаляем сообщение с меню
    await delete_menu_message(context, user_id)
    # Удаляем остальные сообщения
    await delete_temp_config_message(context, user_id)
    await delete_permanent_config_message(context, user_id)
    await delete_temp_notification_message(context, user_id)
    await delete_admin_notification_message(context, user_id)

    # Сначала показываем загрузку
    await query.edit_message_text(
        "⏳ Создаю временный VPN-конфиг...",
        reply_markup=None
    )

    # Получаем данные временного конфига без отправки файла
    temp_config_data = await grant_temp_config(user_id, context.bot, send_file=False, send_message=False)

    if not temp_config_data:
        await query.edit_message_text(
            "❌ Ошибка создания временного конфига. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅ Назад", callback_data='choose_freekassa')
            ]])
        )
        return FREEKASSA_VPN_CHOICE

    # Создаем ссылку на оплату
    payment_url, _ = create_freekassa_payment(user_id)

    if payment_url:
        keyboard = [
            [InlineKeyboardButton("📁 Скачать временный конфиг", callback_data=f'download_temp_{user_id}')],
            [InlineKeyboardButton("🔗 Перейти к оплате Freekassa", url=payment_url)],
            [InlineKeyboardButton("⬅ Назад к оплате", callback_data='buy')]
        ]

        status_text = "✅ **Временный VPN создан!**" if not temp_config_data['is_existing'] else "⏰ **У вас уже есть активный временный VPN!**"

        message = (
            f"{status_text}\n\n"
            f"🕐 **Действует:** {temp_config_data['remaining_minutes']} мин.\n\n"
            "**Инструкция:**\n"
            "1️⃣ Нажмите 'Скачать конфиг'\n"
            "2️⃣ Импортируйте в WireGuard\n"
            "3️⃣ Подключитесь к VPN\n"
            "4️⃣ Нажмите кнопку оплаты\n\n"
            "⚠️ После оплаты временный конфиг отключится"
        )

        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

        # Сохраняем данные конфига в контексте для последующего скачивания
        context.user_data[f'temp_config_{user_id}'] = temp_config_data
    else:
        await query.edit_message_text("Произошла ошибка при создании счета. Попробуйте позже.")

    return SELECTING_ACTION

async def download_temp_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отправляет файл временного конфига"""
    query = update.callback_query
    await query.answer("📁 Отправляю конфиг...")
    user_id = update.effective_user.id

    # СНАЧАЛА получаем данные конфига
    temp_config_data = context.user_data.get(f'temp_config_{user_id}')

    if temp_config_data:
        try:
            # Удаляем только предыдущие сообщения с файлами
            await delete_temp_config_message(context, user_id)
            await delete_permanent_config_message(context, user_id)
            await delete_temp_notification_message(context, user_id)
            await delete_admin_notification_message(context, user_id)

            # Отправляем файл и сохраняем ID сообщения с файлом
            file_message = await context.bot.send_document(
                user_id,
                document=bytes(temp_config_data['config'], 'utf-8'),
                filename=temp_config_data['filename'],
                caption="📁 Ваш временный VPN-конфиг\n\nИмпортируйте его в WireGuard и подключитесь перед оплатой."
            )

            # Сохраняем ID сообщения с файлом для последующего удаления
            context.user_data[f'temp_file_message_{user_id}'] = file_message.message_id

            logger.info(f"Временный конфиг отправлен пользователю {user_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки временного конфига пользователю {user_id}: {e}")
            await query.answer("❌ Ошибка отправки файла", show_alert=True)
    else:
        logger.warning(f"Временный конфиг не найден для пользователя {user_id}")
        await query.answer("❌ Конфиг не найден. Попробуйте создать заново.", show_alert=True)

    return SELECTING_ACTION

async def process_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    payment_system = query.data
    await query.answer()
    user_id = update.effective_user.id

    # Удаляем сообщение с меню
    await delete_menu_message(context, user_id)
    # Удаляем остальные сообщения
    await delete_temp_config_message(context, user_id)
    await delete_permanent_config_message(context, user_id)
    await delete_temp_notification_message(context, user_id)
    await delete_admin_notification_message(context, user_id)

    if payment_system == 'pay_freekassa_direct':
        payment_url, _ = create_freekassa_payment(user_id)
    elif payment_system == 'pay_kryptocloud':
        payment_url, _ = create_kryptocloud_payment(user_id)
    else:
        return SELECTING_ACTION

    if payment_url:
        keyboard = [
            [InlineKeyboardButton("🔗 Перейти к оплате", url=payment_url)],
            [InlineKeyboardButton("⬅ Назад к оплате", callback_data='buy')]
        ]

        message = ("Нажмите на кнопку ниже, чтобы перейти к оплате.\n\n"
                  "После успешной оплаты я автоматически выдам вам подписку. "
                  "Возвращаться и нажимать 'Проверить' не нужно.")
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text("Произошла ошибка при создании счета. Пожалуйста, попробуйте позже.")
        logger.error(f"Не удалось создать счет {payment_system} для {user_id}")

    return SELECTING_ACTION

def main():
    init_db()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(ask_for_payment_method, pattern='^buy$'),
                CallbackQueryHandler(show_profile, pattern='^profile$'),
                CallbackQueryHandler(start, pattern='^back_to_start$'),
                CallbackQueryHandler(show_admin_menu, pattern='^admin_menu$'),
                CallbackQueryHandler(download_temp_config, pattern=r'^download_temp_\d+$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_button),
            ],
            SELECTING_PAYMENT: [
                CallbackQueryHandler(ask_freekassa_vpn_choice, pattern='^choose_freekassa$'),
                CallbackQueryHandler(process_payment, pattern='^pay_kryptocloud$'),
                CallbackQueryHandler(show_alternative_payment, pattern='^alternative_payment$'),
                CallbackQueryHandler(show_instruction, pattern='^show_instruction$'),
                CallbackQueryHandler(start, pattern='^back_to_start$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_button),
            ],
            FREEKASSA_VPN_CHOICE: [
                CallbackQueryHandler(process_payment, pattern='^pay_freekassa_direct$'),
                CallbackQueryHandler(provide_temp_vpn_and_payment, pattern='^need_temp_vpn$'),
                CallbackQueryHandler(ask_for_payment_method, pattern='^buy$'),
                CallbackQueryHandler(download_temp_config, pattern=r'^download_temp_\d+$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_button),
            ],
            SHOWING_INSTRUCTION: [
                CallbackQueryHandler(ask_for_payment_method, pattern='^buy$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_button),
            ],
            ALTERNATIVE_PAYMENT: [
                CallbackQueryHandler(ask_for_payment_method, pattern='^buy$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_button),
            ],
            # Админские состояния
            ADMIN_MENU: [
                CallbackQueryHandler(admin_request_username_for_grant, pattern='^admin_grant_config$'),
                CallbackQueryHandler(admin_show_user_list, pattern='^admin_user_list$'),
                CallbackQueryHandler(admin_request_username_for_delete, pattern='^admin_delete_config$'),
                CallbackQueryHandler(show_admin_menu, pattern='^admin_menu$'),
                CallbackQueryHandler(start, pattern='^back_to_start$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_button),
            ],
            ADMIN_GRANT_CONFIG: [
                CallbackQueryHandler(show_admin_menu, pattern='^admin_menu$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_grant_config_by_username),
            ],
            ADMIN_DELETE_CONFIG: [
                CallbackQueryHandler(show_admin_menu, pattern='^admin_menu$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete_config_by_username),
            ],
        },
        fallbacks=[
            CommandHandler('start', start),
            MessageHandler(filters.TEXT, handle_start_button)
        ],
    )

    application.add_handler(conv_handler)

    # Добавляем периодическую задачу очистки каждые 5 минут
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

