import json
import os
from datetime import datetime

class TransactionManager:
    def __init__(self, data_file='data.json'):
        self.data_file = data_file
        self.transactions = []
        
    def load_data(self):
        """Загрузка данных из файла"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.transactions = json.load(f)
            else:
                self.transactions = []
        except Exception as e:
            print(f"Ошибка загрузки данных: {e}")
            self.transactions = []
    
    def save_data(self):
        """Сохранение данных в файл"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.transactions, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"Ошибка сохранения данных: {e}")
            return False
    
    def add_transaction(self, transaction):
        """Добавление транзакции"""
        try:
            # Валидация данных
            if not self._validate_transaction(transaction):
                return False
            
            # Добавляем ID если его нет
            if 'id' not in transaction or not transaction['id']:
                transaction['id'] = self._generate_id()
            
            self.transactions.append(transaction)
            self.save_data()
            return True
        except Exception as e:
            print(f"Ошибка добавления транзакции: {e}")
            return False
    
    def delete_transaction(self, transaction_id):
        """Удаление транзакции по ID"""
        self.transactions = [t for t in self.transactions if t.get('id') != transaction_id]
        self.save_data()
    
    def get_all_transactions(self):
        """Получение всех транзакций"""
        return self.transactions.copy()
    
    def get_statistics(self):
        """Получение статистики"""
        total_income = sum(t['amount'] for t in self.transactions if t['type'] == 'доход')
        total_expense = sum(t['amount'] for t in self.transactions if t['type'] == 'расход')
        balance = total_income - total_expense
        
        return {
            'total_income': total_income,
            'total_expense': total_expense,
            'balance': balance
        }
    
    def _validate_transaction(self, transaction):
        """Валидация данных транзакции"""
        required_fields = ['date', 'category', 'amount', 'type']
        for field in required_fields:
            if field not in transaction or not transaction[field]:
                return False
        
        try:
            float(transaction['amount'])
        except ValueError:
            return False
        
        if transaction['type'] not in ['доход', 'расход']:
            return False
            
        return True
    
    def _generate_id(self):
        """Генерация уникального ID"""
        import uuid
        return str(uuid.uuid4())
