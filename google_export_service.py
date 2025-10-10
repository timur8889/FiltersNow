# google_export_service.py
import logging
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GoogleConfig

logger = logging.getLogger(__name__)

class GoogleExportService:
    """Сервис для экспорта данных из Google API"""
    
    def __init__(self, service_type='analytics'):
        """
        Инициализация сервиса
        
        Args:
            service_type (str): Тип Google сервиса
        """
        self.service_type = service_type
        self.credentials = GoogleConfig.get_credentials(service_type)
        self.service = self._build_service()
        
        # Валидация учетных данных при инициализации
        if not GoogleConfig.validate_credentials(self.credentials):
            raise ValueError("Невалидные учетные данные Google API")
    
    def _build_service(self):
        """Создание клиента для Google API"""
        try:
            if self.service_type == 'analytics':
                service = build('analytics', 'v3', credentials=self.credentials)
            elif self.service_type == 'bigquery':
                service = build('bigquery', 'v2', credentials=self.credentials)
            elif self.service_type == 'drive':
                service = build('drive', 'v3', credentials=self.credentials)
            else:
                raise ValueError(f"Неизвестный тип сервиса: {self.service_type}")
            
            logger.info(f"✅ Сервис {self.service_type} успешно инициализирован")
            return service
        except Exception as e:
            logger.error(f"❌ Ошибка создания сервиса {self.service_type}: {e}")
            raise
    
    def export_analytics_data(self, view_id, start_date, end_date, metrics, dimensions=None):
        """Экспорт данных из Google Analytics"""
        try:
            if self.service_type != 'analytics':
                raise ValueError("Сервис не инициализирован для Google Analytics")
            
            query = self.service.data().ga().get(
                ids=f'ga:{view_id}',
                start_date=start_date,
                end_date=end_date,
                metrics=metrics,
                dimensions=dimensions or 'ga:date'
            )
            
            response = query.execute()
            logger.info(f"✅ Успешно экспортировано данных: {len(response.get('rows', []))} строк")
            return response
            
        except HttpError as e:
            logger.error(f"❌ HTTP ошибка при экспорте: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка экспорта: {e}")
            raise
    
    def test_connection(self):
        """Тестирование подключения к Google API"""
        try:
            if self.service_type == 'analytics':
                # Простой запрос для проверки подключения
                accounts = self.service.management().accounts().list().execute()
                return len(accounts.get('items', [])) > 0
            elif self.service_type == 'bigquery':
                projects = self.service.projects().list().execute()
                return True
            elif self.service_type == 'drive':
                about = self.service.about().get(fields='user').execute()
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка тестирования подключения: {e}")
            return False
    
    def get_quota_info(self):
        """Получение информации о квотах"""
        try:
            if self.service_type == 'analytics':
                return {
                    'service': 'Google Analytics',
                    'credentials_valid': self.credentials.valid,
                    'token_expiry': self.credentials.expiry
                }
            return {
                'service': self.service_type,
                'credentials_valid': self.credentials.valid,
                'token_expiry': self.credentials.expiry
            }
        except Exception as e:
            logger.error(f"❌ Ошибка получения информации о квотах: {e}")
            return {}
