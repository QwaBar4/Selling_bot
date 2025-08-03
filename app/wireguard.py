import os
import re
import logging
import time
import requests
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from app.config import WG_SERVER_PUBLIC_KEY, WG_SERVER_ENDPOINT, WG_CLIENT_DNS

# Константы для WG-Easy
WG_EASY_URL = os.getenv("WG_EASY_URL", "http://localhost:51821")
WG_EASY_PASSWORD = os.getenv("WG_EASY_PASSWORD", "")
TEMP_CONFIG_DIR = "/tmp/wg_configs"

logger = logging.getLogger(__name__)

class WGEasyAPI:
    """Класс для работы с WG-Easy API"""
    
    def __init__(self, base_url: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.password = password
        self.session = requests.Session()
        self.session.timeout = 30
        self._authenticated = False
    
    def _authenticate(self) -> bool:
        """Аутентификация в WG-Easy"""
        try:
            # Получаем страницу логина для получения cookies
            login_page = self.session.get(f"{self.base_url}/")
            if login_page.status_code != 200:
                logger.error(f"Cannot access WG-Easy at {self.base_url}")
                return False
            
            # Отправляем пароль
            auth_data = {"password": self.password}
            auth_response = self.session.post(
                f"{self.base_url}/api/session", 
                json=auth_data,
                headers={"Content-Type": "application/json"}
            )
            
            # WG-Easy может возвращать 200 или 204 при успешной аутентификации
            if auth_response.status_code in [200, 204]:
                self._authenticated = True
                logger.info(f"Successfully authenticated with WG-Easy (status: {auth_response.status_code})")
                return True
            else:
                logger.error(f"WG-Easy authentication failed: {auth_response.status_code} - {auth_response.text}")
                return False
                
        except Exception as e:
            logger.error(f"WG-Easy authentication error: {e}")
            return False
    
    def _ensure_authenticated(self) -> bool:
        """Проверяет аутентификацию и переаутентифицируется при необходимости"""
        if not self._authenticated:
            return self._authenticate()
        
        # Проверяем, что сессия еще активна
        try:
            response = self.session.get(f"{self.base_url}/api/wireguard/client")
            if response.status_code == 401:
                logger.info("WG-Easy session expired, re-authenticating...")
                self._authenticated = False
                return self._authenticate()
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Session check failed: {e}, re-authenticating...")
            self._authenticated = False
            return self._authenticate()
    
    def create_client(self, name: str) -> Optional[Dict]:
        """Создает нового клиента в WG-Easy"""
        if not self._ensure_authenticated():
            logger.error("Failed to authenticate with WG-Easy")
            return None
        
        try:
            client_data = {"name": name}
            response = self.session.post(
                f"{self.base_url}/api/wireguard/client",
                json=client_data,
                headers={"Content-Type": "application/json"}
            )
            
            logger.info(f"Create client response: {response.status_code}, content: {response.text[:200]}")
            
            if response.status_code in [200, 201]:
                try:
                    if response.text.strip():  # Проверяем, что ответ не пустой
                        client_info = response.json()
                        logger.info(f"Created WG-Easy client: {name}, response: {client_info}")
                        return client_info
                    else:
                        # Если ответ пустой, пытаемся найти клиента по имени
                        logger.warning(f"Empty response when creating client {name}, searching by name...")
                        time.sleep(2)  # Даем время на создание
                        return self.find_client_by_name(name)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON response when creating client {name}: {e}")
                    # Пытаемся найти клиента по имени
                    time.sleep(2)
                    return self.find_client_by_name(name)
            else:
                logger.error(f"Failed to create WG-Easy client {name}: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating WG-Easy client {name}: {e}")
            return None
    
    def find_client_by_name(self, name: str) -> Optional[Dict]:
        """Находит клиента по имени"""
        try:
            clients = self.get_clients()
            for client in clients:
                if client.get('name') == name:
                    logger.info(f"Found client by name: {name}, id: {client.get('id')}")
                    return client
            logger.warning(f"Client not found by name: {name}")
            return None
        except Exception as e:
            logger.error(f"Error finding client by name {name}: {e}")
            return None
    
    def delete_client(self, client_id: str) -> bool:
        """Удаляет клиента из WG-Easy"""
        if not self._ensure_authenticated():
            return False
        
        try:
            response = self.session.delete(f"{self.base_url}/api/wireguard/client/{client_id}")
            
            if response.status_code in [200, 204]:
                logger.info(f"Deleted WG-Easy client: {client_id}")
                return True
            else:
                logger.error(f"Failed to delete WG-Easy client {client_id}: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error deleting WG-Easy client {client_id}: {e}")
            return False
    
    def get_clients(self) -> List[Dict]:
        """Получает список всех клиентов"""
        if not self._ensure_authenticated():
            return []
        
        try:
            response = self.session.get(f"{self.base_url}/api/wireguard/client")
            
            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    logger.error("Invalid JSON response when getting clients")
                    return []
            else:
                logger.error(f"Failed to get WG-Easy clients: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting WG-Easy clients: {e}")
            return []
    
    def get_client_config(self, client_id: str) -> Optional[str]:
        """Получает конфигурацию клиента по ID"""
        if not self._ensure_authenticated():
            return None
        
        try:
            response = self.session.get(f"{self.base_url}/api/wireguard/client/{client_id}/configuration")
            
            if response.status_code == 200:
                return response.text
            else:
                logger.error(f"Failed to get config for client {client_id}: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting config for client {client_id}: {e}")
            return None
    
    def enable_client(self, client_id: str) -> bool:
        """Включает клиента"""
        if not self._ensure_authenticated():
            return False
        
        try:
            response = self.session.post(f"{self.base_url}/api/wireguard/client/{client_id}/enable")
            return response.status_code in [200, 204]
        except Exception as e:
            logger.error(f"Error enabling client {client_id}: {e}")
            return False
    
    def disable_client(self, client_id: str) -> bool:
        """Отключает клиента"""
        if not self._ensure_authenticated():
            return False
        
        try:
            response = self.session.post(f"{self.base_url}/api/wireguard/client/{client_id}/disable")
            return response.status_code in [200, 204]
        except Exception as e:
            logger.error(f"Error disabling client {client_id}: {e}")
            return False

# Глобальный экземпляр API
wg_easy = WGEasyAPI(WG_EASY_URL, WG_EASY_PASSWORD)

def init_wireguard():
    """Инициализация директорий"""
    Path(TEMP_CONFIG_DIR).mkdir(parents=True, exist_ok=True, mode=0o700)
    # Проверяем подключение к WG-Easy при инициализации
    if wg_easy._authenticate():
        logger.info("WG-Easy connection established successfully")
    else:
        logger.error("Failed to establish WG-Easy connection")

def sanitize_client_name(name: str) -> str:
    """Очищает имя клиента для WG-Easy"""
    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if not clean_name:
        clean_name = f"client_{int(time.time())}"
    return clean_name[:50]  # Ограничиваем длину имени

def create_client(user_id: str, is_temp: bool = False) -> Optional[Dict[str, str]]:
    """Создает клиента через WG-Easy API и скачивает готовый конфиг"""
    prefix = "temp" if is_temp else "user"
    client_name = sanitize_client_name(f"{prefix}_{user_id}_{int(time.time())}")
    
    logger.info(f"Creating WG-Easy client: {client_name}")
    
    # Создаем клиента в WG-Easy
    client_info = wg_easy.create_client(client_name)
    if not client_info:
        logger.error(f"Failed to create WG-Easy client for user {user_id}")
        return None
    
    # Получаем ID клиента - он может быть в разных полях в зависимости от версии WG-Easy
    client_id = None
    if 'id' in client_info:
        client_id = client_info['id']
    elif 'publicKey' in client_info:
        # Если нет ID, используем публичный ключ как идентификатор
        client_id = client_info['publicKey']
    else:
        # В крайнем случае используем имя
        client_id = client_name
    
    logger.info(f"Using client ID: {client_id} for client: {client_name}")
    
    # Скачиваем готовый конфиг с сервера
    try:
        # Даем время серверу обновиться после создания клиента
        time.sleep(1)
        
        config_text = wg_easy.get_client_config(client_id)
        if not config_text:
            logger.error(f"Failed to download config for client {client_name} (ID: {client_id})")
            
            # Попробуем найти клиента по имени и получить его реальный ID
            found_client = wg_easy.find_client_by_name(client_name)
            if found_client and 'id' in found_client:
                real_client_id = found_client['id']
                logger.info(f"Found real client ID: {real_client_id}, retrying config download...")
                config_text = wg_easy.get_client_config(real_client_id)
                if config_text:
                    client_id = real_client_id  # Обновляем ID для дальнейшего использования
            
            if not config_text:
                logger.error(f"Still failed to download config, deleting client {client_name}")
                wg_easy.delete_client(client_id)
                return None
        
        client_ip = client_info['address'].split('/')[0]  # Убираем /32 если есть
        
        logger.info(f"Created client {client_name} with IP {client_ip}")
        
        return {
            'id': client_id,
            'name': client_name,
            'private_key': client_info['privateKey'],
            'public_key': client_info['publicKey'],
            'ip': client_ip,
            'config': config_text,  # Используем готовый конфиг с сервера
            'enabled': client_info.get('enabled', True)
        }
        
    except KeyError as e:
        logger.error(f"Missing required field in WG-Easy response: {e}")
        logger.error(f"Full response: {client_info}")
        # Удаляем созданного клиента при ошибке
        wg_easy.delete_client(client_id)
        return None
    except Exception as e:
        logger.error(f"Error getting config for client {client_name}: {e}")
        # Удаляем созданного клиента при ошибке
        wg_easy.delete_client(client_id)
        return None

def delete_client(client_id: str) -> bool:
    """Удаляет клиента через WG-Easy API"""
    return wg_easy.delete_client(client_id)

def enable_client(client_id: str) -> bool:
    """Включает клиента через WG-Easy API"""
    return wg_easy.enable_client(client_id)

def disable_client(client_id: str) -> bool:
    """Отключает клиента через WG-Easy API"""
    return wg_easy.disable_client(client_id)

def get_all_clients() -> List[Dict]:
    """Получает всех клиентов из WG-Easy"""
    return wg_easy.get_clients()

def find_client_by_name(name: str) -> Optional[Dict]:
    """Находит клиента по имени"""
    clients = get_all_clients()
    for client in clients:
        if client.get('name') == name:
            return client
    return None

def find_clients_by_user_id(user_id: str) -> List[Dict]:
    """Находит всех клиентов пользователя"""
    clients = get_all_clients()
    user_clients = []
    for client in clients:
        name = client.get('name', '')
        if f"user_{user_id}_" in name or f"temp_{user_id}_" in name:
            user_clients.append(client)
    return user_clients

def cleanup_user_clients(user_id: str, keep_latest: bool = True) -> int:
    """Очищает старых клиентов пользователя"""
    user_clients = find_clients_by_user_id(user_id)
    if not user_clients:
        return 0
    
    # Сортируем по времени создания
    user_clients.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
    
    # Оставляем последнего, если нужно
    clients_to_delete = user_clients[1:] if keep_latest else user_clients
    
    deleted_count = 0
    for client in clients_to_delete:
        if delete_client(client['id']):
            deleted_count += 1
    
    logger.info(f"Cleaned up {deleted_count} old clients for user {user_id}")
    return deleted_count

def get_client_by_public_key(public_key: str) -> Optional[Dict]:
    """Находит клиента по публичному ключу"""
    clients = get_all_clients()
    for client in clients:
        if client.get('publicKey') == public_key:
            return client
    return None

def is_client_active(client_id: str) -> bool:
    """Проверяет активность клиента"""
    clients = get_all_clients()
    for client in clients:
        if client.get('id') == client_id:
            # Проверяем, включен ли клиент
            if not client.get('enabled', False):
                return False
            
            # Проверяем недавний handshake
            latest_handshake = client.get('latestHandshakeAt')
            if latest_handshake:
                try:
                    # WG-Easy возвращает время в ISO формате
                    handshake_time = datetime.fromisoformat(latest_handshake.replace('Z', '+00:00'))
                    current_time = datetime.now(handshake_time.tzinfo)
                    return (current_time - handshake_time).total_seconds() < 180  # 3 минуты
                except:
                    pass
            
            return client.get('enabled', False)  # Если нет handshake, проверяем только enabled
    
    return False

def save_client_config(client_data: Dict[str, str], user_id: str) -> str:
    """Сохраняет конфиг клиента во временный файл"""
    safe_name = sanitize_client_name(f"client_{user_id}")
    config_path = f"{TEMP_CONFIG_DIR}/{safe_name}.conf"
    
    try:
        with open(config_path, 'w') as f:
            f.write(client_data['config'])
        
        os.chmod(config_path, 0o600)
        logger.info(f"Saved config to {config_path}")
        
        return config_path
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        raise

def diagnose_connection() -> Dict[str, any]:
    """Диагностика состояния WG-Easy"""
    try:
        # Проверяем аутентификацию
        auth_status = wg_easy._ensure_authenticated()
        
        clients = get_all_clients() if auth_status else []
        active_clients = [c for c in clients if c.get('enabled', False)]
        
        return {
            "wg_easy_status": "connected" if auth_status else "disconnected",
            "wg_easy_url": WG_EASY_URL,
            "authentication": "success" if auth_status else "failed",
            "total_clients": len(clients) if clients else 0,
            "active_clients": len(active_clients),
            "recent_clients": clients[:5] if clients else []  # Последние 5 для диагностики
        }
    except Exception as e:
        logger.error(f"WG-Easy diagnosis failed: {e}")
        return {
            "error": str(e), 
            "wg_easy_status": "error",
            "wg_easy_url": WG_EASY_URL,
            "authentication": "error"
        }

# Функции совместимости (минимальные, только для bot_logic.py)
def get_next_available_ip() -> str:
    """WG-Easy автоматически назначает IP"""
    return "auto"

def create_temp_client(user_id: str) -> Optional[Dict[str, str]]:
    """Создает временного клиента"""
    return create_client(user_id, is_temp=True)
