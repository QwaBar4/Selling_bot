import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import ContextTypes
from app import config, database as db, wireguard
from app.payments import create_freekassa_payment

logger = logging.getLogger(__name__)

async def handle_buy_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка покупки подписки"""
    user_id = update.effective_user.id
    
    # Проверяем, есть ли уже активная подписка
    user = db.get_user(user_id)
    if user and user.get('subscription_end_date'):
        try:
            end_date = datetime.fromisoformat(user['subscription_end_date'])
            if end_date > datetime.now():
                remaining_days = (end_date - datetime.now()).days
                await update.message.reply_text(
                    f"✅ У вас уже есть активная подписка!\n"
                    f"Осталось дней: {remaining_days}\n\n"
                    f"Используйте /status для получения конфига."
                )
                return
        except:
            pass
    
    # Создаем ссылку на оплату
    try:
        payment_url = create_payment_url(user_id)
        
        keyboard = [
            [InlineKeyboardButton("💳 Оплатить", url=payment_url)],
            [InlineKeyboardButton("🕐 Временный доступ", callback_data="temp_access")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"💰 Стоимость подписки: {config.PAYMENT_AMOUNT} {config.PAYMENT_CURRENCY}\n"
            f"📅 Срок: {config.SUBSCRIPTION_DAYS} дней\n\n"
            f"Для доступа к сайту оплаты можете получить временный VPN-конфиг.",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error creating payment URL: {e}")
        await update.message.reply_text("❌ Ошибка создания ссылки на оплату. Попробуйте позже.")

async def handle_status_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса подписки"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user:
        await update.message.reply_text("❌ Вы не зарегистрированы. Используйте /start")
        return
    
    # Проверяем активную подписку
    if user.get('subscription_end_date'):
        try:
            end_date = datetime.fromisoformat(user['subscription_end_date'])
            if end_date > datetime.now():
                remaining_days = (end_date - datetime.now()).days
                
                await update.message.reply_text(
                    f"✅ Ваша подписка активна!\n"
                    f"📅 Действует до: {end_date.strftime('%d.%m.%Y %H:%M')}\n"
                    f"⏰ Осталось дней: {remaining_days}\n"
                    f"🌐 IP адрес: {user.get('client_ip', 'не назначен')}"
                )
                
                # Отправляем конфиг
                if user.get('wireguard_config'):
                    await context.bot.send_document(
                        user_id,
                        document=bytes(user['wireguard_config'], 'utf-8'),
                        filename=f"wg_{user_id}.conf"
                    )
                return
        except:
            pass
    
    await update.message.reply_text(
        "❌ У вас нет активной подписки.\n\n"
        "Используйте /buy для покупки."
    )

async def handle_temp_config_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос временного конфига"""
    user_id = update.effective_user.id
    await grant_temp_config(user_id, context.bot)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка callback кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "temp_access":
        await grant_temp_config(query.from_user.id, context.bot)

async def grant_subscription(user_id: int, bot):
    """Выдает подписку пользователю через WG-Easy API"""
    logger.info(f"Начинаем процесс выдачи подписки для пользователя {user_id}")
    
    # Деактивируем временный конфиг если он есть
    await deactivate_user_temp_config(user_id, bot)
    
    try:
        # Очищаем старые конфиги пользователя
        wireguard.cleanup_user_clients(str(user_id), keep_latest=False)
        
        # Создаем нового клиента через WG-Easy
        client_data = wireguard.create_client(str(user_id), is_temp=False)
        
        if not client_data:
            logger.error(f"Не удалось создать WG-Easy клиента для пользователя {user_id}")
            await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Ошибка WG-Easy: не удалось создать клиента для {user_id}")
            await bot.send_message(user_id, "Произошла ошибка на сервере. Администратор уже уведомлен.")
            return

        # Сохраняем данные в БД
        db.update_user_subscription(
            user_id, 
            config.SUBSCRIPTION_DAYS, 
            client_data['config'], 
            client_data['ip'],
            wg_easy_client_id=client_data['id']
        )

        await bot.send_message(
            user_id,
            "✅ Оплата прошла успешно! Ваша подписка активирована.\n\n"
            "Вот ваш персональный конфигурационный файл. "
            "Импортируйте его в приложение WireGuard на вашем устройстве."
        )
        await bot.send_document(
            user_id,
            document=bytes(client_data['config'], 'utf-8'),
            filename=f"wg_{user_id}.conf"
        )
        logger.info(f"Конфиг успешно отправлен пользователю {user_id} (WG-Easy ID: {client_data['id']})")

    except Exception as e:
        logger.exception(f"Исключение при выдаче подписки {user_id}: {e}")
        await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Исключение при выдаче подписки {user_id}: {e}")

async def grant_temp_config(user_id: int, bot):
    """Выдает временный конфиг на 10 минут через WG-Easy API с именем ShallowTemp[1-128]"""
    logger.info(f"Выдача временного конфига для пользователя {user_id}")
    
    try:
        # Проверяем, есть ли уже временный конфиг
        existing_temp = db.get_temp_config(user_id)
        random_suffix = random.randint(1, 128)
        if existing_temp:
            expires_at = datetime.fromisoformat(existing_temp['expires_at'])
            if expires_at > datetime.now():
                remaining_minutes = int((expires_at - datetime.now()).total_seconds() / 60)
                await bot.send_message(
                    user_id,
                    f"⏰ У вас уже есть активный временный конфиг!\n"
                    f"Осталось времени: {remaining_minutes} мин.\n\n"
                    f"Используйте его для доступа к сайту оплаты."
                )
                await bot.send_document(
                    user_id,
                    document=bytes(existing_temp['config_text'], 'utf-8'),
                    filename=f"ShallowTemp{random_suffix}.conf"
                )
                return
            else:
                # Конфиг истек, удаляем его из WG-Easy и БД
                logger.info(f"Удаляем истекший временный конфиг для пользователя {user_id}")
                if existing_temp['wg_easy_client_id']:
                    try:
                        wireguard.delete_client(existing_temp['wg_easy_client_id'])
                    except Exception as e:
                        logger.warning(f"Не удалось удалить WG-Easy клиента {existing_temp['wg_easy_client_id']}: {e}")
                
                db.remove_temp_config(user_id)

        # Генерируем случайное имя для клиента
        
        client_name = f"ShallowTemp{random_suffix}"

        # Создаем нового временного клиента через WG-Easy
        client_data = wireguard.create_client(client_name, is_temp=True)
        
        if not client_data:
            logger.error(f"Не удалось создать временного WG-Easy клиента для {user_id}")
            await bot.send_message(user_id, "Ошибка создания временного конфига. Попробуйте позже.")
            return

        # Сохраняем временный конфиг в БД
        try:
            db.add_temp_config(
                user_id, 
                client_data['config'],  # Используем готовый конфиг с сервера
                client_data['ip'], 
                client_data['public_key'],
                wg_easy_client_id=client_data['id']
            )
        except Exception as e:
            logger.error(f"Ошибка сохранения временного конфига для {user_id}: {e}")
            # Удаляем созданного клиента из WG-Easy
            try:
                wireguard.delete_client(client_data['id'])
                logger.info(f"Удален WG-Easy клиент {client_data['id']} из-за ошибки сохранения в БД")
            except Exception as del_e:
                logger.error(f"Не удалось удалить WG-Easy клиента {client_data['id']}: {del_e}")
            
            await bot.send_message(user_id, "Ошибка сохранения конфига. Попробуйте позже.")
            return

        await bot.send_message(
            user_id,
            "🕐 Временный VPN-конфиг выдан на 10 минут!\n\n"
            "Используйте его для доступа к сайту оплаты Freekassa.\n"
            "После оплаты этот конфиг будет автоматически отключен, "
            "а вам будет выдан постоянный конфиг."
        )
        await bot.send_document(
            user_id,
            document=bytes(client_data['config'], 'utf-8'),
            filename=f"ShallowTemp{random_suffix}.conf"
        )
        logger.info(f"Временный конфиг выдан пользователю {user_id} (Имя: {client_name}, WG-Easy ID: {client_data['id']})")

    except Exception as e:
        logger.exception(f"Ошибка при выдаче временного конфига {user_id}: {e}")
        await bot.send_message(user_id, "Произошла ошибка. Попробуйте позже.")

async def grant_permanent_config(user_id: int, bot):
    """Выдает постоянный конфиг после оплаты"""
    logger.info(f"Выдача постоянного конфига для пользователя {user_id}")
    
    try:
        # Удаляем временный конфиг если есть
        temp_config = db.get_temp_config(user_id)
        if temp_config and temp_config['wg_easy_client_id']:
            try:
                wireguard.delete_client(temp_config['wg_easy_client_id'])
                db.remove_temp_config(user_id)
                logger.info(f"Удален временный конфиг для пользователя {user_id}")
            except Exception as e:
                logger.warning(f"Ошибка удаления временного конфига для {user_id}: {e}")

        # Создаем постоянного клиента
        client_data = wireguard.create_client(str(user_id), is_temp=False)
        
        if not client_data:
            logger.error(f"Не удалось создать постоянного WG-Easy клиента для {user_id}")
            await bot.send_message(user_id, "Ошибка создания постоянного конфига. Обратитесь в поддержку.")
            return

        # Обновляем пользователя в БД
        db.update_user_config(
            user_id,
            client_data['config'],  # Используем готовый конфиг с сервера
            client_data['ip'],
            client_data['id']
        )

        await bot.send_message(
            user_id,
            "🎉 Ваш постоянный VPN-конфиг готов!\n\n"
            "Временный конфиг отключен. Используйте новый конфиг для постоянного доступа."
        )
        await bot.send_document(
            user_id,
            document=bytes(client_data['config'], 'utf-8'),
            filename=f"wireguard_{user_id}.conf"
        )
        logger.info(f"Постоянный конфиг выдан пользователю {user_id} (WG-Easy ID: {client_data['id']})")

    except Exception as e:
        logger.exception(f"Ошибка при выдаче постоянного конфига {user_id}: {e}")
        await bot.send_message(user_id, "Произошла ошибка при создании конфига. Обратитесь в поддержку.")

async def deactivate_user_temp_config(user_id: int, bot):
    """Деактивирует временный конфиг пользователя через WG-Easy"""
    temp_config = db.get_temp_config(user_id)
    if temp_config and temp_config['is_active']:
        wg_easy_client_id = temp_config.get('wg_easy_client_id')
        
        if wg_easy_client_id:
            # Удаляем клиента из WG-Easy
            if wireguard.delete_client(wg_easy_client_id):
                db.deactivate_temp_config(user_id)
                logger.info(f"Временный конфиг пользователя {user_id} деактивирован (WG-Easy ID: {wg_easy_client_id})")
                
                try:
                    await bot.send_message(
                        user_id,
                        "🔄 Ваш временный VPN-конфиг отключен, так как вы получили постоянную подписку."
                    )
                except:
                    pass
            else:
                logger.error(f"Failed to delete WG-Easy client {wg_easy_client_id} for user {user_id}")

async def cleanup_expired_configs(bot: Bot):
    """Очищает истекшие временные конфиги и подписки"""
    logger.info("Запущена очистка истекших конфигураций")
    
    # Очистка временных конфигов
    expired_temp_configs = db.get_expired_temp_configs()
    for config in expired_temp_configs:
        try:
            if config['wg_easy_client_id']:
                if wireguard.delete_client(config['wg_easy_client_id']):
                    db.deactivate_temp_config(config['user_id'])
                    logger.info(f"Удален временный конфиг пользователя {config['user_id']}")
                    
                    # Уведомляем пользователя
                    try:
                        await bot.send_message(
                            config['user_id'],
                            "⏳ Ваш временный VPN-конфиг истек и был отключен.\n\n"
                            "Если вы уже оплатили подписку, постоянный конфиг остается активным."
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось уведомить пользователя {config['user_id']}: {e}")
        except Exception as e:
            logger.error(f"Ошибка при удалении временного конфига {config['user_id']}: {e}")

    # Очистка истекших подписок
    expired_subscriptions = db.get_expired_subscriptions()
    for user in expired_subscriptions:
        try:
            if user['wg_easy_client_id']:
                if wireguard.delete_client(user['wg_easy_client_id']):
                    db.deactivate_user_subscription(user['telegram_id'])
                    logger.info(f"Подписка пользователя {user['telegram_id']} истекла и была отключена")
                    
                    # Уведомляем пользователя
                    try:
                        await bot.send_message(
                            user['telegram_id'],
                            "⏳ Ваша подписка истекла!\n\n"
                            "Для продолжения использования VPN приобретите новую подписку через /start"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось уведомить пользователя {user['telegram_id']}: {e}")
        except Exception as e:
            logger.error(f"Ошибка при отключении истекшей подписки {user['telegram_id']}: {e}")
