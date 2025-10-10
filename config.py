# config.py
import os
import json
import logging
from google.oauth2 import service_account
from google.auth.exceptions import GoogleAuthError

logger = logging.getLogger(__name__)

class GoogleConfig:
    """Конфигурация для Google API с защитой ключей"""
    
    # Scopes для разных сервисов Google
    SCOPES = {
        'analytics': ['https://www.googleapis.com/auth/analytics.readonly'],
        'bigquery': ['https://www.googleapis.com/auth/bigquery'],
        'drive': ['https://www.googleapis.com/auth/drive.readonly']
    }
    
    @staticmethod
    def get_credentials(service_type='analytics'):
        """
        Безопасное получение учетных данных Google API
        
        Args:
            service_type (str): Тип сервиса ('analytics', 'bigquery', 'drive')
        
        Returns:
            service_account.Credentials: Объект учетных данных
        """
        scope = GoogleConfig.SCOPES.get(service_type, GoogleConfig.SCOPES['analytics'])
        
        # Способ 1: Переменная окружения (ПРИОРИТЕТ - для продакшена)
        env_key = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        if env_key:
            try:
                service_account_info = json.loads(env_key)
                logger.info("✅ Учетные данные загружены из переменной окружения")
                return service_account.Credentials.from_service_account_info(
                    service_account_info,
                    scopes=scope
                )
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга JSON из переменной окружения: {e}")
                raise ValueError("Неверный формат JSON в GOOGLE_SERVICE_ACCOUNT_JSON")
        
        # Способ 2: Файл через переменную окружения (для разработки)
        key_file_path = os.environ.get('GOOGLE_KEY_FILE_PATH')
        if key_file_path:
            try:
                logger.info(f"🔑 Загружаем ключ из файла: {key_file_path}")
                return service_account.Credentials.from_service_account_file(
                    key_file_path,
                    scopes=scope
                )
            except FileNotFoundError:
                logger.error(f"❌ Файл ключа не найден: {key_file_path}")
                raise FileNotFoundError(f"Файл ключа не найден: {key_file_path}")
        
        # Способ 3: Стандартный путь (только для локальной разработки)
        try:
            default_path = 'service-account-key.json'
            logger.info(f"🔑 Попытка загрузить ключ из стандартного пути: {default_path}")
            return service_account.Credentials.from_service_account_file(
                default_path,
                scopes=scope
            )
        except FileNotFoundError:
            logger.error("❌ Не найден файл с ключом сервисного аккаунта")
            raise Exception(
                "Не найден ключ сервисного аккаунта. Используйте один из способов:\n"
                "1. Установите переменную окружения GOOGLE_SERVICE_ACCOUNT_JSON\n"
                "2. Установите GOOGLE_KEY_FILE_PATH на путь к файлу ключа\n"
                "3. Положите файл service-account-key.json в корень проекта"
            )
    
    @staticmethod
    def validate_credentials(credentials):
        """Проверка валидности учетных данных"""
        try:
            # Попытка обновить токен
            credentials.refresh(credentials._request)
            logger.info("✅ Учетные данные валидны")
            return True
        except Exception as e:
            logger.error(f"❌ Невалидные учетные данные: {e}")
            return False
