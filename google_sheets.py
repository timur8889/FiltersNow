import gspread
import pandas as pd
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

class GoogleSheetsManager:
    def __init__(self, credentials_file: str = 'credentials.json'):
        self.credentials_file = credentials_file
        self.client = None
        self.spreadsheet = None
        self.worksheet = None
        self.drive_service = None
        self._setup_client()
    
    def _setup_client(self):
        """Настройка клиента Google Sheets и Drive"""
        try:
            # Определяем scope
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets"
            ]
            
            # Создаем credentials
            creds = Credentials.from_service_account_file(
                self.credentials_file, scopes=scope
            )
            
            # Создаем клиенты
            self.client = gspread.authorize(creds)
            self.drive_service = build('drive', 'v3', credentials=creds)
            
            logging.info("✅ Google Sheets клиент успешно настроен")
            
        except Exception as e:
            logging.error(f"❌ Ошибка настройки Google Sheets: {e}")
            raise
    
    def create_spreadsheet(self, title: str) -> str:
        """Создание новой таблицы"""
        try:
            self.spreadsheet = self.client.create(title)
            
            # Даем доступ для чтения/записи
            self.spreadsheet.share(None, perm_type='anyone', role='writer')
            
            logging.info(f"📊 Создана таблица: {title}")
            return self.spreadsheet.url
            
        except Exception as e:
            logging.error(f"❌ Ошибка создания таблицы: {e}")
            raise
    
    def open_spreadsheet(self, spreadsheet_id: str):
        """Открытие существующей таблицы"""
        try:
            self.spreadsheet = self.client.open_by_key(spreadsheet_id)
            logging.info(f"📊 Таблица открыта: {self.spreadsheet.title}")
        except Exception as e:
            logging.error(f"❌ Ошибка открытия таблицы: {e}")
            raise
    
    def setup_worksheet(self, sheet_name: str = "Фильтры"):
        """Настройка листа с заголовками и форматированием"""
        try:
            # Пытаемся получить лист, если нет - создаем
            try:
                self.worksheet = self.spreadsheet.worksheet(sheet_name)
                # Очищаем старые данные кроме заголовков
                if self.worksheet.row_count > 1:
                    self.worksheet.delete_rows(2, self.worksheet.row_count)
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(
                    title=sheet_name, rows=1000, cols=15
                )
            
            # Устанавливаем заголовки
            headers = [
                "ID", "Тип фильтра", "Местоположение", 
                "Дата последней замены", "Дата истечения срока",
                "Осталось дней", "Статус", "Иконка статуса",
                "Срок службы (дни)", "Дата создания", "Последнее обновление",
                "User ID", "Telegram Username", "Телефон", "Email"
            ]
            
            self.worksheet.update('A1:O1', [headers])
            
            # Применяем форматирование заголовков
            self._apply_header_formatting()
            
            # Настраиваем ширину колонок
            self._auto_resize_columns()
            
            logging.info(f"📝 Лист '{sheet_name}' настроен")
            
        except Exception as e:
            logging.error(f"❌ Ошибка настройки листа: {e}")
            raise
    
    def _apply_header_formatting(self):
        """Применение форматирования к заголовкам"""
        try:
            header_format = {
                "backgroundColor": {
                    "red": 0.2, "green": 0.4, "blue": 0.6
                },
                "textFormat": {
                    "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    "bold": True,
                    "fontSize": 11
                },
                "horizontalAlignment": "CENTER"
            }
            
            # Обновляем формат через batch_update
            requests = [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": self.worksheet.id,
                            "startRowIndex": 0,
                            "endRowIndex": 1
                        },
                        "cell": {
                            "userEnteredFormat": header_format
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
                    }
                }
            ]
            
            self.spreadsheet.batch_update({"requests": requests})
            
        except Exception as e:
            logging.error(f"❌ Ошибка форматирования заголовков: {e}")
    
    def _auto_resize_columns(self):
        """Автоматическая настройка ширины колонок"""
        try:
            requests = []
            
            # Устанавливаем оптимальную ширину для каждой колонки
            column_widths = {
                0: 50,   # ID
                1: 120,  # Тип фильтра
                2: 100,  # Местоположение
                3: 110,  # Дата замены
                4: 110,  # Дата истечения
                5: 90,   # Осталось дней
                6: 80,   # Статус
                7: 80,   # Иконка
                8: 90,   # Срок службы
                9: 110,  # Дата создания
                10: 110, # Последнее обновление
                11: 80,  # User ID
                12: 100, # Username
                13: 100, # Телефон
                14: 120  # Email
            }
            
            for col_index, width in column_widths.items():
                requests.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": self.worksheet.id,
                            "dimension": "COLUMNS",
                            "startIndex": col_index,
                            "endIndex": col_index + 1
                        },
                        "properties": {
                            "pixelSize": width
                        },
                        "fields": "pixelSize"
                    }
                })
            
            if requests:
                self.spreadsheet.batch_update({"requests": requests})
                
        except Exception as e:
            logging.error(f"❌ Ошибка настройки ширины колонок: {e}")
    
    def filters_to_sheets_data(self, filters: List[Dict], user_info: Dict = None) -> List[List]:
        """Конвертация фильтров в данные для таблицы"""
        today = datetime.now().date()
        sheet_data = []
        
        for f in filters:
            try:
                expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
                last_change = datetime.strptime(str(f['last_change']), '%Y-%m-%d').date()
                days_until_expiry = (expiry_date - today).days
                
                icon, status = self.get_status_icon_and_text(days_until_expiry)
                
                row = [
                    f['id'],
                    f['filter_type'],
                    f['location'],
                    last_change.strftime('%d.%m.%Y'),
                    expiry_date.strftime('%d.%m.%Y'),
                    days_until_expiry,
                    status,
                    icon,
                    f['lifetime_days'],
                    f.get('created_at', '')[:10] if f.get('created_at') else '',
                    f.get('updated_at', '')[:10] if f.get('updated_at') else '',
                    f['user_id'],
                    user_info.get('username', '') if user_info else '',
                    user_info.get('phone', '') if user_info else '',
                    user_info.get('email', '') if user_info else ''
                ]
                sheet_data.append(row)
            except Exception as e:
                logging.error(f"❌ Ошибка конвертации фильтра {f.get('id', 'N/A')}: {e}")
                continue
        
        return sheet_data
    
    def get_status_icon_and_text(self, days_until_expiry: int) -> Tuple[str, str]:
        """Получение иконки и текста статуса"""
        if days_until_expiry <= 0:
            return "🔴", "ПРОСРОЧЕН"
        elif days_until_expiry <= 7:
            return "🟡", "СКОРО ИСТЕЧЕТ"
        elif days_until_expiry <= 30:
            return "🟠", "ВНИМАНИЕ"
        else:
            return "🟢", "НОРМА"
    
    async def sync_filters_to_sheets(self, filters: List[Dict], user_info: Dict = None):
        """Синхронизация фильтров с Google Sheets"""
        try:
            if not self.worksheet:
                await self.setup_worksheet()
            
            # Конвертируем данные
            sheet_data = self.filters_to_sheets_data(filters, user_info)
            
            if not sheet_data:
                logging.info("ℹ️ Нет данных для синхронизации")
                return
            
            # Очищаем старые данные (кроме заголовков)
            if self.worksheet.row_count > 1:
                self.worksheet.delete_rows(2, self.worksheet.row_count)
            
            # Добавляем новые данные
            if sheet_data:
                self.worksheet.update(f'A2:O{len(sheet_data) + 1}', sheet_data)
            
            # Применяем условное форматирование
            self._apply_conditional_formatting(len(sheet_data))
            
            # Добавляем фильтры
            self._add_filters()
            
            logging.info(f"✅ Синхронизировано {len(sheet_data)} фильтров с Google Sheets")
            
            return len(sheet_data)
            
        except Exception as e:
            logging.error(f"❌ Ошибка синхронизации с Google Sheets: {e}")
            raise
    
    def _apply_conditional_formatting(self, data_rows_count: int):
        """Применение условного форматирования"""
        try:
            if data_rows_count == 0:
                return
            
            range_end = data_rows_count + 1
            
            requests = [
                # Красный для просроченных (колонка F - "Осталось дней")
                {
                    'addConditionalFormatRule': {
                        'rule': {
                            'ranges': [{
                                'sheetId': self.worksheet.id, 
                                'startRowIndex': 1, 
                                'endRowIndex': range_end, 
                                'startColumnIndex': 5, 
                                'endColumnIndex': 6
                            }],
                            'booleanRule': {
                                'condition': {
                                    'type': 'LESS_THAN',
                                    'values': [{'userEnteredValue': '0'}]
                                },
                                'format': {
                                    'backgroundColor': {'red': 1.0, 'green': 0.8, 'blue': 0.8},
                                    'textFormat': {'bold': True}
                                }
                            }
                        }
                    }
                },
                # Желтый для скоро истекающих (0-7 дней)
                {
                    'addConditionalFormatRule': {
                        'rule': {
                            'ranges': [{
                                'sheetId': self.worksheet.id, 
                                'startRowIndex': 1, 
                                'endRowIndex': range_end, 
                                'startColumnIndex': 5, 
                                'endColumnIndex': 6
                            }],
                            'booleanRule': {
                                'condition': {
                                    'type': 'BETWEEN',
                                    'values': [
                                        {'userEnteredValue': '0'},
                                        {'userEnteredValue': '7'}
                                    ]
                                },
                                'format': {
                                    'backgroundColor': {'red': 1.0, 'green': 0.95, 'blue': 0.8},
                                    'textFormat': {'bold': True}
                                }
                            }
                        }
                    }
                },
                # Оранжевый для предупреждения (8-30 дней)
                {
                    'addConditionalFormatRule': {
                        'rule': {
                            'ranges': [{
                                'sheetId': self.worksheet.id, 
                                'startRowIndex': 1, 
                                'endRowIndex': range_end, 
                                'startColumnIndex': 5, 
                                'endColumnIndex': 6
                            }],
                            'booleanRule': {
                                'condition': {
                                    'type': 'BETWEEN', 
                                    'values': [
                                        {'userEnteredValue': '8'},
                                        {'userEnteredValue': '30'}
                                    ]
                                },
                                'format': {
                                    'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.7},
                                    'textFormat': {'bold': True}
                                }
                            }
                        }
                    }
                }
            ]
            
            self.spreadsheet.batch_update({'requests': requests})
            
        except Exception as e:
            logging.error(f"❌ Ошибка применения условного форматирования: {e}")
    
    def _add_filters(self):
        """Добавление фильтров к данным"""
        try:
            requests = [
                {
                    "setBasicFilter": {
                        "filter": {
                            "range": {
                                "sheetId": self.worksheet.id,
                                "startRowIndex": 0,
                                "endRowIndex": self.worksheet.row_count,
                                "startColumnIndex": 0,
                                "endColumnIndex": 15
                            }
                        }
                    }
                }
            ]
            
            self.spreadsheet.batch_update({"requests": requests})
            
        except Exception as e:
            logging.error(f"❌ Ошибка добавления фильтров: {e}")
    
    def get_spreadsheet_url(self) -> str:
        """Получение URL таблицы"""
        return self.spreadsheet.url if self.spreadsheet else ""
    
    def get_spreadsheet_id(self) -> str:
        """Получение ID таблицы"""
        return self.spreadsheet.id if self.spreadsheet else ""
    
    async def create_summary_sheet(self):
        """Создание листа с суммарной статистикой"""
        try:
            # Пытаемся создать или получить лист "Статистика"
            try:
                summary_sheet = self.spreadsheet.worksheet("Статистика")
            except gspread.WorksheetNotFound:
                summary_sheet = self.spreadsheet.add_worksheet(
                    title="Статистика", rows=50, cols=10
                )
            
            # Получаем данные с основного листа
            main_data = self.worksheet.get_all_records()
            
            # Рассчитываем статистику
            today = datetime.now().date()
            total_filters = len(main_data)
            expired = 0
            expiring_soon = 0
            warning = 0
            normal = 0
            
            for row in main_data:
                days_left = row.get('Осталось дней', 0)
                if days_left <= 0:
                    expired += 1
                elif days_left <= 7:
                    expiring_soon += 1
                elif days_left <= 30:
                    warning += 1
                else:
                    normal += 1
            
            # Подготавливаем данные для статистики
            stats_data = [
                ["📊 СТАТИСТИКА ФИЛЬТРОВ", ""],
                ["Обновлено", datetime.now().strftime('%d.%m.%Y %H:%M')],
                [""],
                ["Показатель", "Количество"],
                ["Всего фильтров", total_filters],
                ["🟢 Норма", normal],
                ["🟠 Внимание", warning],
                ["🟡 Скоро истечет", expiring_soon],
                ["🔴 Просрочено", expired],
                [""],
                ["Процент просроченных", f"{(expired/total_filters*100):.1f}%" if total_filters > 0 else "0%"],
                ["Процент скоро истекающих", f"{(expiring_soon/total_filters*100):.1f}%" if total_filters > 0 else "0%"]
            ]
            
            # Очищаем и обновляем лист статистики
            summary_sheet.clear()
            summary_sheet.update('A1:B13', stats_data)
            
            # Форматируем статистику
            summary_sheet.format('A1:B1', {
                "backgroundColor": {"red": 0.1, "green": 0.3, "blue": 0.5},
                "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True, "fontSize": 14},
                "horizontalAlignment": "CENTER"
            })
            
            logging.info("✅ Лист статистики создан/обновлен")
            
        except Exception as e:
            logging.error(f"❌ Ошибка создания листа статистики: {e}")

# Глобальный экземпляр менеджера
google_sheets_manager = None

async def init_google_sheets(credentials_file: str = 'credentials.json', 
                           spreadsheet_id: str = None,
                           create_new: bool = False) -> GoogleSheetsManager:
    """Инициализация Google Sheets"""
    global google_sheets_manager
    
    try:
        google_sheets_manager = GoogleSheetsManager(credentials_file)
        
        if create_new:
            title = f"Фильтр-Трекер {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            url = google_sheets_manager.create_spreadsheet(title)
            logging.info(f"📊 Создана новая таблица: {url}")
        elif spreadsheet_id:
            google_sheets_manager.open_spreadsheet(spreadsheet_id)
        
        google_sheets_manager.setup_worksheet()
        
        # Создаем лист статистики
        await google_sheets_manager.create_summary_sheet()
        
        return google_sheets_manager
        
    except Exception as e:
        logging.error(f"❌ Ошибка инициализации Google Sheets: {e}")
        return None
