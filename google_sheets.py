import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime

class GoogleSheetsSync:
    def __init__(self, credentials_file='credentials.json', spreadsheet_name='FinanceTracker'):
        self.credentials_file = credentials_file
        self.spreadsheet_name = spreadsheet_name
        self.client = None
        self.spreadsheet = None
        self.worksheet = None
        
    def authenticate(self):
        """Аутентификация в Google Sheets API"""
        try:
            # Определяем область доступа
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
            
            # Создаем учетные данные
            creds = Credentials.from_service_account_file(
                self.credentials_file, scopes=scope
            )
            
            # Авторизуем клиент
            self.client = gspread.authorize(creds)
            
            # Пытаемся открыть существующую таблицу или создать новую
            try:
                self.spreadsheet = self.client.open(self.spreadsheet_name)
            except gspread.SpreadsheetNotFound:
                self.spreadsheet = self.client.create(self.spreadsheet_name)
                # Даем доступ на редактирование всем (можно изменить)
                self.spreadsheet.share(None, perm_type='anyone', role='writer')
            
            # Получаем или создаем лист для транзакций
            try:
                self.worksheet = self.spreadsheet.worksheet("Транзакции")
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(
                    title="Транзакции", rows="1000", cols="10"
                )
                # Добавляем заголовки
                headers = ["Дата", "Категория", "Сумма", "Тип", "Описание", "ID"]
                self.worksheet.append_row(headers)
            
            print("Успешная синхронизация с Google Sheets!")
            return True
            
        except Exception as e:
            print(f"Ошибка аутентификации: {e}")
            return False
    
    def upload_data(self, transactions):
        """Загрузка данных в Google Sheets"""
        if not self.worksheet:
            if not self.authenticate():
                return False
        
        try:
            # Очищаем старые данные (кроме заголовков)
            if self.worksheet.row_count > 1:
                self.worksheet.delete_rows(2, self.worksheet.row_count)
            
            # Добавляем новые данные
            for transaction in transactions:
                row = [
                    transaction.get('date', ''),
                    transaction.get('category', ''),
                    transaction.get('amount', 0),
                    transaction.get('type', ''),
                    transaction.get('description', ''),
                    transaction.get('id', '')
                ]
                self.worksheet.append_row(row)
            
            print(f"Данные успешно загружены в Google Sheets! Записей: {len(transactions)}")
            return True
            
        except Exception as e:
            print(f"Ошибка загрузки данных: {e}")
            return False
    
    def download_data(self):
        """Загрузка данных из Google Sheets"""
        if not self.worksheet:
            if not self.authenticate():
                return []
        
        try:
            # Получаем все данные (кроме заголовков)
            data = self.worksheet.get_all_records()
            
            transactions = []
            for row in data:
                transaction = {
                    'date': row.get('Дата', ''),
                    'category': row.get('Категория', ''),
                    'amount': float(row.get('Сумма', 0)),
                    'type': row.get('Тип', ''),
                    'description': row.get('Описание', ''),
                    'id': row.get('ID', '')
                }
                transactions.append(transaction)
            
            print(f"Данные успешно загружены из Google Sheets! Записей: {len(transactions)}")
            return transactions
            
        except Exception as e:
            print(f"Ошибка загрузки данных: {e}")
            return []
    
    def get_spreadsheet_url(self):
        """Получить ссылку на таблицу"""
        if self.spreadsheet:
            return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet.id}"
        return None
