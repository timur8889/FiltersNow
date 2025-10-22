import tkinter as tk
from tkinter import ttk, messagebox
from transaction_manager import TransactionManager
from google_sheets import GoogleSheetsSync
import json
import os
import webbrowser

class FinanceTracker:
    def __init__(self, root):
        self.root = root
        self.root.title("Финансовый трекер с синхронизацией")
        self.root.geometry("1000x700")
        
        self.transaction_manager = TransactionManager('data.json')
        self.google_sheets = GoogleSheetsSync('credentials.json')
        
        # Загружаем данные
        self.transaction_manager.load_data()
        
        self.setup_ui()
        self.refresh_transactions()
        
    def setup_ui(self):
        # Создаем вкладки
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Вкладка управления финансами
        finance_frame = ttk.Frame(notebook)
        notebook.add(finance_frame, text="Управление финансами")
        
        # Вкладка синхронизации
        sync_frame = ttk.Frame(notebook)
        notebook.add(sync_frame, text="Синхронизация")
        
        self.setup_finance_tab(finance_frame)
        self.setup_sync_tab(sync_frame)
    
    def setup_finance_tab(self, parent):
        # Форма добавления транзакции
        form_frame = ttk.LabelFrame(parent, text="Добавить транзакцию", padding=10)
        form_frame.grid(row=0, column=0, columnspan=2, sticky='ew', padx=5, pady=5)
        
        # Поля формы
        ttk.Label(form_frame, text="Дата (ГГГГ-ММ-ДД):").grid(row=0, column=0, sticky='w')
        self.date_entry = ttk.Entry(form_frame)
        self.date_entry.grid(row=0, column=1, sticky='ew', padx=5, pady=2)
        
        ttk.Label(form_frame, text="Категория:").grid(row=1, column=0, sticky='w')
        self.category_combo = ttk.Combobox(form_frame, values=["Еда", "Транспорт", "Развлечения", "Жилье", "Здоровье", "Другое"])
        self.category_combo.grid(row=1, column=1, sticky='ew', padx=5, pady=2)
        
        ttk.Label(form_frame, text="Сумма:").grid(row=2, column=0, sticky='w')
        self.amount_entry = ttk.Entry(form_frame)
        self.amount_entry.grid(row=2, column=1, sticky='ew', padx=5, pady=2)
        
        ttk.Label(form_frame, text="Тип:").grid(row=3, column=0, sticky='w')
        self.type_combo = ttk.Combobox(form_frame, values=["доход", "расход"])
        self.type_combo.grid(row=3, column=1, sticky='ew', padx=5, pady=2)
        
        ttk.Label(form_frame, text="Описание:").grid(row=4, column=0, sticky='w')
        self.description_entry = ttk.Entry(form_frame)
        self.description_entry.grid(row=4, column=1, sticky='ew', padx=5, pady=2)
        
        # Кнопки
        button_frame = ttk.Frame(form_frame)
        button_frame.grid(row=5, column=0, columnspan=2, pady=10)
        
        ttk.Button(button_frame, text="Добавить", command=self.add_transaction).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Очистить", command=self.clear_form).pack(side='left', padx=5)
        
        # Таблица транзакций
        table_frame = ttk.LabelFrame(parent, text="Транзакции", padding=10)
        table_frame.grid(row=1, column=0, columnspan=2, sticky='nsew', padx=5, pady=5)
        
        # Настройка таблицы
        columns = ("id", "date", "category", "amount", "type", "description")
        self.tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=15)
        
        # Заголовки
        self.tree.heading("id", text="ID")
        self.tree.heading("date", text="Дата")
        self.tree.heading("category", text="Категория")
        self.tree.heading("amount", text="Сумма")
        self.tree.heading("type", text="Тип")
        self.tree.heading("description", text="Описание")
        
        # Колонки
        self.tree.column("id", width=0, stretch=False)  # Скрываем ID колонку
        self.tree.column("date", width=100)
        self.tree.column("category", width=120)
        self.tree.column("amount", width=100)
        self.tree.column("type", width=80)
        self.tree.column("description", width=200)
        
        # Скроллбар
        scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Кнопки управления
        control_frame = ttk.Frame(parent)
        control_frame.grid(row=2, column=0, columnspan=2, pady=10)
        
        ttk.Button(control_frame, text="Удалить выделенное", 
                  command=self.delete_selected).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Обновить", 
                  command=self.refresh_transactions).pack(side='left', padx=5)
        
        # Статистика
        stats_frame = ttk.LabelFrame(parent, text="Статистика", padding=10)
        stats_frame.grid(row=3, column=0, columnspan=2, sticky='ew', padx=5, pady=5)
        
        self.stats_label = ttk.Label(stats_frame, text="")
        self.stats_label.pack()
        
        # Настройка весов строк и колонок
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(1, weight=1)
    
    def setup_sync_tab(self, parent):
        """Настройка вкладки синхронизации"""
        sync_frame = ttk.LabelFrame(parent, text="Синхронизация с Google Sheets", padding=20)
        sync_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Информация о синхронизации
        info_text = """Для работы синхронизации с Google Sheets необходимо:

1. Создать проект в Google Cloud Console
2. Включить Google Sheets API
3. Создать сервисный аккаунт и скачать credentials.json
4. Положить файл credentials.json в папку с приложением
5. Предоставить доступ к таблице для сервисного аккаунта"""
        
        info_label = ttk.Label(sync_frame, text=info_text, justify='left')
        info_label.pack(pady=10)
        
        # Кнопки синхронизации
        button_frame = ttk.Frame(sync_frame)
        button_frame.pack(pady=20)
        
        ttk.Button(button_frame, text="Проверить подключение", 
                  command=self.test_connection).pack(pady=5)
        
        ttk.Button(button_frame, text="Загрузить в Google Sheets", 
                  command=self.upload_to_sheets).pack(pady=5)
        
        ttk.Button(button_frame, text="Загрузить из Google Sheets", 
                  command=self.download_from_sheets).pack(pady=5)
        
        ttk.Button(button_frame, text="Открыть таблицу в браузере", 
                  command=self.open_sheets).pack(pady=5)
        
        # Статус синхронизации
        self.sync_status = ttk.Label(sync_frame, text="Статус: Не подключено", foreground='red')
        self.sync_status.pack(pady=10)
    
    def test_connection(self):
        """Тестирование подключения к Google Sheets"""
        if self.google_sheets.authenticate():
            self.sync_status.config(text="Статус: Подключено", foreground='green')
            messagebox.showinfo("Успех", "Успешное подключение к Google Sheets!")
        else:
            self.sync_status.config(text="Статус: Ошибка подключения", foreground='red')
            messagebox.showerror("Ошибка", "Не удалось подключиться к Google Sheets")
    
    def upload_to_sheets(self):
        """Загрузка данных в Google Sheets"""
        transactions = self.transaction_manager.get_all_transactions()
        if self.google_sheets.upload_data(transactions):
            messagebox.showinfo("Успех", "Данные успешно загружены в Google Sheets!")
        else:
            messagebox.showerror("Ошибка", "Не удалось загрузить данные в Google Sheets")
    
    def download_from_sheets(self):
        """Загрузка данных из Google Sheets"""
        result = messagebox.askyesno(
            "Подтверждение", 
            "Вы уверены? Текущие локальные данные будут заменены данными из Google Sheets."
        )
        if not result:
            return
            
        transactions = self.google_sheets.download_data()
        if transactions:
            # Очищаем текущие транзакции и заменяем новыми
            self.transaction_manager.transactions = transactions
            self.transaction_manager.save_data()
            self.refresh_transactions()
            messagebox.showinfo("Успех", "Данные успешно загружены из Google Sheets!")
        else:
            messagebox.showerror("Ошибка", "Не удалось загрузить данные из Google Sheets")
    
    def open_sheets(self):
        """Открытие таблицы в браузере"""
        url = self.google_sheets.get_spreadsheet_url()
        if url:
            webbrowser.open(url)
        else:
            messagebox.showwarning("Предупреждение", "Сначала подключитесь к Google Sheets")
    
    def add_transaction(self):
        try:
            transaction = {
                'date': self.date_entry.get(),
                'category': self.category_combo.get(),
                'amount': float(self.amount_entry.get()),
                'type': self.type_combo.get(),
                'description': self.description_entry.get()
            }
            
            if self.transaction_manager.add_transaction(transaction):
                self.refresh_transactions()
                self.clear_form()
                messagebox.showinfo("Успех", "Транзакция добавлена!")
            else:
                messagebox.showerror("Ошибка", "Неверные данные транзакции")
                
        except ValueError:
            messagebox.showerror("Ошибка", "Неверный формат суммы")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка при добавлении: {e}")
    
    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Предупреждение", "Выберите транзакцию для удаления")
            return
        
        for item in selected:
            item_id = self.tree.item(item)['values'][0]  # Получаем ID из первой колонки
            self.transaction_manager.delete_transaction(item_id)
        
        self.refresh_transactions()
        messagebox.showinfo("Успех", "Транзакции удалены!")
    
    def refresh_transactions(self):
        # Очищаем таблицу
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Добавляем транзакции
        for transaction in self.transaction_manager.get_all_transactions():
            self.tree.insert('', 'end', values=(
                transaction['id'],
                transaction['date'],
                transaction['category'],
                f"{transaction['amount']:.2f}",
                transaction['type'],
                transaction['description']
            ))
        
        # Обновляем статистику
        self.update_statistics()
    
    def update_statistics(self):
        stats = self.transaction_manager.get_statistics()
        stats_text = f"""Общий доход: {stats['total_income']:.2f} ₽
Общий расход: {stats['total_expense']:.2f} ₽
Баланс: {stats['balance']:.2f} ₽"""
        self.stats_label.config(text=stats_text)
    
    def clear_form(self):
        self.date_entry.delete(0, tk.END)
        self.category_combo.set('')
        self.amount_entry.delete(0, tk.END)
        self.type_combo.set('')
        self.description_entry.delete(0, tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    app = FinanceTracker(root)
    root.mainloop()
