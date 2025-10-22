import os

def check_credentials():
    """Проверка наличия файла credentials.json"""
    if not os.path.exists('credentials.json'):
        print("""
        ⚠️ Файл credentials.json не найден!
        
        Для работы с Google Sheets необходимо:
        
        1. Перейти на https://console.cloud.google.com/
        2. Создать новый проект
        3. Включить Google Sheets API
        4. Создать сервисный аккаунт
        5. Скачать JSON-ключ и переименовать в credentials.json
        6. Положить файл в папку с приложением
        
        Подробная инструкция: https://docs.gspread.org/en/latest/oauth2.html
        """)
        return False
    else:
        print("✅ Файл credentials.json найден!")
        return True

if __name__ == "__main__":
    check_credentials()
