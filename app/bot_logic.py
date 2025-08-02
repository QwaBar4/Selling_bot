import logging
from datetime import datetime, timedelta
from telegram import Bot
from app import config, database as db, wireguard

logger = logging.getLogger(__name__)

async def grant_subscription(user_id: int, bot: Bot):
    """
    Выдает подписку пользователю: генерирует конфиг,
    добавляет пира на сервер и отправляет файл пользователю.
    """
    logger.info(f"Начинаем процесс выдачи подписки для пользователя {user_id}")
    
    # Деактивируем временный конфиг если он есть
    await deactivate_user_temp_config(user_id, bot)
    
    try:
        client_ip = db.get_next_available_ip()
        if not client_ip:
            logger.error(f"Не удалось выделить IP-адрес для пользователя {user_id}.")
            await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Закончились IP-адреса! Не удалось выдать подписку {user_id}.")
            await bot.send_message(user_id, "Произошла ошибка на сервере. Администратор уже уведомлен.")
            return

        client_private_key, client_public_key = wireguard.generate_client_keys()
        if not client_private_key:
            await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Не удалось сгенерировать ключи для {user_id}.")
            return

        if wireguard.add_peer_to_server(client_public_key, client_ip):
            # Генерируем конфиг с новым портом
            config_text = wireguard.generate_wireguard_config(
                client_private_key, 
                client_ip, 
                port=config.WG_SERVER_PORT
            )
            db.update_user_subscription(user_id, config.SUBSCRIPTION_DAYS, config_text, client_ip)

            await bot.send_message(
                user_id,
                "✅ Оплата прошла успешно! Ваша подписка активирована.\n\n"
                "Вот ваш персональный конфигурационный файл. "
                "Импортируйте его в приложение WireGuard на вашем устройстве."
            )
            await bot.send_document(
                user_id,
                document=bytes(config_text, 'utf-8'),
                filename=f"wg_{user_id}.conf"
            )
            logger.info(f"Конфиг успешно отправлен пользователю {user_id}.")
        else:
            logger.error(f"Не удалось добавить пира на сервер для пользователя {user_id}")
            await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Ошибка WG: не удалось добавить пира для {user_id}.")
            await bot.send_message(user_id, "Произошла критическая ошибка. Администратор уведомлен.")

    except Exception as e:
        logger.exception(f"Исключение при выдаче подписки {user_id}: {e}")
        await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Исключение при выдаче подписки {user_id}: {e}")

async def grant_temp_config(user_id: int, bot: Bot):
    """
    Выдает временный конфиг на 10 минут для доступа к Freekassa
    """
    logger.info(f"Выдача временного конфига для пользователя {user_id}")
    
    try:
        # Проверяем, есть ли уже активный временный конфиг
        existing_temp = db.get_temp_config(user_id)
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
                    filename=f"shallow{user_id}.conf"
                )
                return

        # Получаем свободный IP
        client_ip = db.get_next_available_ip()
        if not client_ip:
            logger.error(f"Не удалось выделить IP-адрес для временного конфига {user_id}.")
            await bot.send_message(user_id, "Временно нет доступных IP-адресов. Попробуйте позже.")
            return

        # Генерируем ключи
        client_private_key, client_public_key = wireguard.generate_client_keys()
        if not client_private_key:
            await bot.send_message(user_id, "Ошибка генерации ключей. Попробуйте позже.")
            return

        # Добавляем пира на сервер
        if wireguard.add_peer_to_server(client_public_key, client_ip):
            # Генерируем временный конфиг
            config_text = wireguard.generate_wireguard_config(
                client_private_key, 
                client_ip, 
                port=getattr(config, 'WG_SERVER_PORT', 51820)
            )
            
            # Сохраняем временный конфиг в БД
            db.add_temp_config(user_id, config_text, client_ip, client_public_key)

            await bot.send_message(
                user_id,
                "🕐 Временный VPN-конфиг выдан на 10 минут!\n\n"
                "Используйте его для доступа к сайту оплаты Freekassa.\n"
                "После оплаты этот конфиг будет автоматически отключен, "
                "а вам будет выдан постоянный конфиг."
            )
            await bot.send_document(
                user_id,
                document=bytes(config_text, 'utf-8'),
                filename=f"temp_wg_{user_id}.conf"
            )
            logger.info(f"Временный конфиг выдан пользователю {user_id} с IP {client_ip}.")
        else:
            logger.error(f"Не удалось добавить временного пира для {user_id}")
            await bot.send_message(user_id, "Ошибка создания временного конфига. Попробуйте позже.")

    except Exception as e:
        logger.exception(f"Ошибка при выдаче временного конфига {user_id}: {e}")
        await bot.send_message(user_id, "Произошла ошибка. Попробуйте позже.")

async def deactivate_user_temp_config(user_id: int, bot: Bot):
    """
    Деактивирует временный конфиг пользователя
    """
    temp_config = db.get_temp_config(user_id)
    if temp_config and temp_config['is_active']:
        # Удаляем пира с сервера
        if wireguard.remove_peer_from_server(temp_config['public_key']):
            db.deactivate_temp_config(user_id)
            logger.info(f"Временный конфиг пользователя {user_id} деактивирован.")
            
            try:
                await bot.send_message(
                    user_id,
                    "🔄 Ваш временный VPN-конфиг отключен, так как вы получили постоянную подписку."
                )
            except:
                pass  # Игнорируем ошибки отправки сообщения

async def cleanup_expired_configs(bot: Bot):
    """
    Очищает истекшие временные конфиги
    """
    expired_keys = db.cleanup_expired_temp_configs()
    for public_key in expired_keys:
        wireguard.remove_peer_from_server(public_key)
    
    if expired_keys:
        logger.info(f"Очищено {len(expired_keys)} истекших временных конфигов.")
