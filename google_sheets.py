import gspread
from google.oauth2.service_account import Credentials
from google.auth.exceptions import GoogleAuthError
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound
import time
import os

class GoogleSheetsSync:
    def __init__(self, credentials_file='credentials.json', spreadsheet_name='FinanceTracker'):
        self.credentials_file = credentials_file
        self.spreadsheet_name = spreadsheet_name
        self.client = None
        self.spreadsheet = None
        self.worksheet = None
        
    def authenticate(self):
        """Аутентификация в Google Sheets API с обработкой ошибок"""
        try:
            # Проверяем существование файла credentials
            if not os.path.exists(self.credentials_file):
                error_msg = f"Файл {self.credentials_file} не найден. Следуйте инструкции по настройке."
                print(error_msg)
                return False, error_msg
            
            # Определяем область доступа
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive.file",  # Только файлы, созданные приложением
                "https://www.googleapis.com/auth/spreadsheets"
            ]
            
            try:
                # Создаем учетные данные
                creds = Credentials.from_service_account_file(
                    self.credentials_file, scopes=scope
                )
                
                # Авторизуем клиент
                self.client = gspread.authorize(creds)
                
            except GoogleAuthError as e:
                error_msg = f"Ошибка аутентификации: {e}. Проверьте корректность файла credentials.json"
                print(error_msg)
                return False, error_msg
            
            # Пытаемся открыть существующую таблицу или создать новую
            try:
                self.spreadsheet = self.client.open(self.spreadsheet_name)
                print(f"Открыта существующая таблица: {self.spreadsheet_name}")
                
            except SpreadsheetNotFound:
                try:
                    self.spreadsheet = self.client.create(self.spreadsheet_name)
                    print(f"Создана новая таблица: {self.spreadsheet_name}")
                    
                    # Даем доступ на редактирование владельцу (более безопасно)
                    # Вместо общего доступа всем
                    
                except APIError as e:
                    error_msg = f"Ошибка создания таблицы: {e}. Проверьте права доступа сервисного аккаунта"
                    print(error_msg)
                    return False, error_msg
            
            # Получаем или создаем лист для транзакций
            try:
                self.worksheet = self.spreadsheet.worksheet("Транзакции")
                print("Лист 'Транзакции' найден")
                
            except WorksheetNotFound:
                try:
                    self.worksheet = self.spreadsheet.add_worksheet(
                        title="Транзакции", rows="1000", cols="10"
                    )
                    # Добавляем заголовки
                    headers = ["Дата", "Категория", "Сумма", "Тип", "Описание", "ID"]
                    self.worksheet.append_row(headers)
                    print("Создан новый лист 'Транзакции'")
                    
                except APIError as e:
                    error_msg = f"Ошибка создания листа: {e}"
                    print(error_msg)
                    return False, error_msg
            
            print("Успешная синхронизация с Google Sheets!")
            return True, "Успешная аутентификация"
            
        except Exception as e:
            error_msg = f"Неизвестная ошибка аутентификации: {e}"
            print(error_msg)
            return False, error_msg
    
    def upload_data(self, transactions):
        """Загрузка данных в Google Sheets с обработкой ограничений API"""
        if not self.worksheet:
            auth_success, auth_message = self.authenticate()
            if not auth_success:
                return False, auth_message
        
        try:
            # Очищаем старые данные (кроме заголовков)
            if self.worksheet.row_count > 1:
                try:
                    self.worksheet.delete_rows(2, self.worksheet.row_count)
                    time.sleep(1)  # Задержка для избежания лимитов API
                except APIError as e:
                    if "Quota exceeded" in str(e):
                        time.sleep(10)  # Большая задержка при превышении квоты
                        return self.upload_data(transactions)  # Рекурсивный вызов
                    else:
                        raise e
            
            # Добавляем новые данные порциями для избежания лимитов
            batch_size = 50
            for i in range(0, len(transactions), batch_size):
                batch = transactions[i:i + batch_size]
                rows = []
                
                for transaction in batch:
                    row = [
                        transaction.get('date', ''),
                        transaction.get('category', ''),
                        transaction.get('amount', 0),
                        transaction.get('type', ''),
                        transaction.get('description', ''),
                        transaction.get('id', '')
                    ]
                    rows.append(row)
                
                try:
                    self.worksheet.append_rows(rows)
                    if i + batch_size < len(transactions):
                        time.sleep(1)  # Задержка между батчами
                        
                except APIError as e:
                    if "Quota exceeded" in str(e):
                        print("Превышена квота API, ждем 60 секунд...")
                        time.sleep(60)
                        self.worksheet.append_rows(rows)  # Повторяем попытку
                    else:
                        raise e
            
            print(f"Данные успешно загружены в Google Sheets! Записей: {len(transactions)}")
            return True, f"Успешно загружено {len(transactions)} записей"
            
        except APIError as e:
            error_msg = f"Ошибка API при загрузке данных: {e}"
            print(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Неизвестная ошибка при загрузке данных: {e}"
            print(error_msg)
            return False, error_msg
    
    def download_data(self):
        """Загрузка данных из Google Sheets с обработкой ограничений"""
        if not self.worksheet:
            auth_success, auth_message = self.authenticate()
            if not auth_success:
                return []
        
        try:
            # Получаем все данные (кроме заголовков)
            data = self.worksheet.get_all_records()
            
            transactions = []
            for row in data:
                try:
                    amount = float(row.get('Сумма', 0))
                    transaction = {
                        'date': row.get('Дата', ''),
                        'category': row.get('Категория', ''),
                        'amount': amount,
                        'type': row.get('Тип', ''),
                        'description': row.get('Описание', ''),
                        'id': row.get('ID', '')
                    }
                    transactions.append(transaction)
                except ValueError:
                    print(f"Пропущена строка с неверной суммой: {row.get('Сумма', '')}")
                    continue
            
            print(f"Данные успешно загружены из Google Sheets! Записей: {len(transactions)}")
            return transactions
            
        except APIError as e:
            if "Quota exceeded" in str(e):
                print("Превышена квота API, пробуем снова через 60 секунд...")
                time.sleep(60)
                return self.download_data()  # Рекурсивный вызов
            else:
                print(f"Ошибка API при загрузке данных: {e}")
                return []
        except Exception as e:
            print(f"Неизвестная ошибка при загрузке данных: {e}")
            return []
    
    def get_spreadsheet_url(self):
        """Получить ссылку на таблицу"""
        if self.spreadsheet:
            return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet.id}"
        return None
    
    def set_credentials_file(self, file_path):
        """Установить новый путь к файлу credentials"""
        self.credentials_file = file_path
        # Сбрасываем соединение
        self.client = None
        self.spreadsheet = None
        self.worksheet = None
