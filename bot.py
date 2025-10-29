async def enhanced_main():
    """Улучшенная функция запуска"""
    try:
        # ... существующий код ...
        
        # Расширенная диагностика
        logging.info("=== ЗАПУСК РАСШИРЕННОЙ ДИАГНОСТИКИ ===")
        
        # 1. Проверка прав
        if not check_database_permissions():
            logging.error("❌ Проблемы с правами доступа к БД")
        
        # 2. Диагностика подключения
        if not debug_database_connection():
            logging.error("❌ Проблемы с подключением к БД")
        
        # 3. Инициализация БД
        init_db()
        
        # 4. Финальная проверка
        debug_database_connection()
        
        logging.info("=== ДИАГНОСТИКА ЗАВЕРШЕНА ===")
        
        # ... остальной код ...
