# ========== ИСПРАВЛЕННАЯ GOOGLE SHEETS ИНТЕГРАЦИЯ ==========
class GoogleSheetsSync:
    def __init__(self):
        self.credentials = None
        self.sheet_id = None
        self.auto_sync = False
        self.load_settings()
    
    def load_settings(self):
        """Загрузка настроек из файла"""
        try:
            if os.path.exists('sheets_settings.json'):
                with open('sheets_settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    self.sheet_id = settings.get('sheet_id')
                    self.auto_sync = settings.get('auto_sync', False)
        except Exception as e:
            logging.error(f"Ошибка загрузки настроек Google Sheets: {e}")
    
    def save_settings(self):
        """Сохранение настроек в файл"""
        try:
            settings = {
                'sheet_id': self.sheet_id,
                'auto_sync': self.auto_sync
            }
            with open('sheets_settings.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Ошибка сохранения настроек Google Sheets: {e}")
    
    def is_configured(self) -> bool:
        """Проверка настройки синхронизации"""
        return bool(self.sheet_id and config.GOOGLE_SHEETS_CREDENTIALS)
    
    async def initialize_credentials(self):
        """Инициализация учетных данных Google"""
        try:
            if not config.GOOGLE_SHEETS_CREDENTIALS:
                return False
            
            # Парсим JSON credentials из переменной окружения
            credentials_info = json.loads(config.GOOGLE_SHEETS_CREDENTIALS)
            
            # Импортируем здесь, чтобы не требовать установку если не используется
            try:
                import gspread
                from google.oauth2.service_account import Credentials
            except ImportError:
                logging.error("Библиотеки gspread или google-auth не установлены")
                return False
            
            # Создаем credentials с правильными scope
            scope = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive.file'
            ]
            self.credentials = Credentials.from_service_account_info(credentials_info, scopes=scope)
            return True
            
        except Exception as e:
            logging.error(f"Ошибка инициализации Google Sheets: {e}")
            return False
    
    async def create_new_spreadsheet(self, gc, title: str):
        """Создание новой таблицы"""
        try:
            spreadsheet = gc.create(title)
            
            # Даем доступ для чтения/записи всем, у кого есть ссылка
            spreadsheet.share(None, perm_type='anyone', role='writer')
            
            return spreadsheet
        except Exception as e:
            logging.error(f"Ошибка создания таблицы: {e}")
            return None
    
    async def sync_to_sheets(self, user_id: int, user_filters: List[Dict]) -> tuple[bool, str]:
        """Синхронизация данных с Google Sheets"""
        try:
            if not self.is_configured():
                return False, "Синхронизация не настроена. Укажите ID таблицы и настройте учетные данные."
            
            if not self.credentials:
                if not await self.initialize_credentials():
                    return False, "Ошибка инициализации Google API. Проверьте учетные данные."
            
            import gspread
            
            # Создаем клиент
            gc = gspread.authorize(self.credentials)
            
            try:
                # Пытаемся открыть таблицу
                sheet = gc.open_by_key(self.sheet_id)
            except gspread.exceptions.SpreadsheetNotFound:
                return False, "Таблица не найдена. Проверьте ID таблицы."
            except gspread.exceptions.APIError as e:
                error_msg = str(e)
                if "PERMISSION_DENIED" in error_msg:
                    return False, "Нет доступа к таблице. Убедитесь, что сервисный аккаунт имеет доступ к таблице."
                elif "NOT_FOUND" in error_msg:
                    return False, "Таблица не найдена. Проверьте ID таблицы."
                else:
                    return False, f"Ошибка доступа к таблице: {error_msg}"
            
            # Получаем или создаем лист для пользователя
            worksheet_name = f"User_{user_id}"
            try:
                worksheet = sheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                try:
                    worksheet = sheet.add_worksheet(title=worksheet_name, rows=100, cols=10)
                except Exception as e:
                    return False, f"Ошибка создания листа: {str(e)}"
                
                # Заголовки
                headers = ['ID', 'Тип фильтра', 'Местоположение', 'Дата замены', 
                          'Срок службы (дни)', 'Годен до', 'Статус', 'Осталось дней']
                try:
                    worksheet.append_row(headers)
                except Exception as e:
                    return False, f"Ошибка добавления заголовков: {str(e)}"
            
            # Очищаем старые данные (кроме заголовка)
            try:
                if worksheet.row_count > 1:
                    worksheet.delete_rows(2, worksheet.row_count)
            except Exception as e:
                logging.warning(f"Ошибка очистки данных: {e}")
            
            # Подготавливаем данные
            today = datetime.now().date()
            rows = []
            
            for f in user_filters:
                try:
                    expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
                    last_change = datetime.strptime(str(f['last_change']), '%Y-%m-%d').date()
                    days_until = (expiry_date - today).days
                    
                    icon, status = get_status_icon_and_text(days_until)
                    
                    row = [
                        f['id'],
                        f['filter_type'],
                        f['location'],
                        format_date_nice(last_change),
                        f['lifetime_days'],
                        format_date_nice(expiry_date),
                        status,
                        days_until
                    ]
                    rows.append(row)
                except Exception as e:
                    logging.error(f"Ошибка подготовки данных фильтра {f['id']}: {e}")
                    continue
            
            # Добавляем данные
            if rows:
                try:
                    worksheet.append_rows(rows)
                except Exception as e:
                    return False, f"Ошибка добавления данных: {str(e)}"
            
            # Форматируем таблицу
            try:
                # Заголовки жирным
                worksheet.format('A1:H1', {'textFormat': {'bold': True}})
                
                # Авто-ширина колонок
                worksheet.columns_auto_resize(0, 7)
            except Exception as format_error:
                logging.warning(f"Ошибка форматирования таблицы: {format_error}")
                # Не прерываем выполнение из-за ошибки форматирования
            
            return True, f"Успешно синхронизировано {len(rows)} фильтров"
            
        except Exception as e:
            logging.error(f"Ошибка синхронизации с Google Sheets: {e}")
            return False, f"Ошибка синхронизации: {str(e)}"
    
    async def sync_from_sheets(self, user_id: int) -> tuple[bool, str, int]:
        """Синхронизация данных из Google Sheets"""
        try:
            if not self.is_configured():
                return False, "Синхронизация не настроена", 0
            
            if not self.credentials:
                if not await self.initialize_credentials():
                    return False, "Ошибка инициализации Google API", 0
            
            import gspread
            
            # Создаем клиент
            gc = gspread.authorize(self.credentials)
            
            # Открываем таблицу
            try:
                sheet = gc.open_by_key(self.sheet_id)
            except gspread.exceptions.SpreadsheetNotFound:
                return False, "Таблица не найдена", 0
            except gspread.exceptions.APIError as e:
                return False, f"Ошибка доступа к таблице: {str(e)}", 0
            
            # Получаем лист пользователя
            worksheet_name = f"User_{user_id}"
            try:
                worksheet = sheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                return False, "Таблица для пользователя не найдена", 0
            
            # Читаем данные
            try:
                data = worksheet.get_all_records()
            except Exception as e:
                return False, f"Ошибка чтения данных: {str(e)}", 0
            
            if not data:
                return False, "Нет данных для импорта", 0
            
            # Обрабатываем данные
            imported_count = 0
            errors = []
            
            for index, row in enumerate(data, start=2):
                try:
                    # Пропускаем строки без основных данных
                    if not row.get('Тип фильтра') or not row.get('Местоположение'):
                        continue
                    
                    # Валидация типа фильтра
                    filter_type = str(row['Тип фильтра']).strip()
                    is_valid_type, error_msg = validate_filter_type(filter_type)
                    if not is_valid_type:
                        errors.append(f"Строка {index}: {error_msg}")
                        continue
                    
                    # Валидация местоположения
                    location = str(row['Местоположение']).strip()
                    is_valid_loc, error_msg = validate_location(location)
                    if not is_valid_loc:
                        errors.append(f"Строка {index}: {error_msg}")
                        continue
                    
                    # Валидация даты
                    date_str = str(row.get('Дата замены', ''))
                    if not date_str:
                        errors.append(f"Строка {index}: Отсутствует дата замены")
                        continue
                    
                    try:
                        change_date = validate_date(date_str)
                    except ValueError as e:
                        errors.append(f"Строка {index}: {str(e)}")
                        continue
                    
                    # Валидация срока службы
                    lifetime = row.get('Срок службы (дни)', 0)
                    is_valid_lt, error_msg, lifetime_days = validate_lifetime(str(lifetime))
                    if not is_valid_lt:
                        errors.append(f"Строка {index}: {error_msg}")
                        continue
                    
                    # Расчет даты истечения
                    expiry_date = change_date + timedelta(days=lifetime_days)
                    
                    # Добавление в БД
                    success = await add_filter_to_db(
                        user_id=user_id,
                        filter_type=filter_type,
                        location=location,
                        last_change=change_date.strftime('%Y-%m-%d'),
                        expiry_date=expiry_date.strftime('%Y-%m-%d'),
                        lifetime_days=lifetime_days
                    )
                    
                    if success:
                        imported_count += 1
                    else:
                        errors.append(f"Строка {index}: Ошибка базы данных")
                        
                except Exception as e:
                    errors.append(f"Строка {index}: Неизвестная ошибка")
                    logging.error(f"Ошибка импорта строки {index}: {e}")
            
            message = f"Импортировано {imported_count} фильтров"
            if errors:
                message += f"\nОшибки: {len(errors)}"
            
            return True, message, imported_count
            
        except Exception as e:
            logging.error(f"Ошибка синхронизации из Google Sheets: {e}")
            return False, f"Ошибка синхронизации: {str(e)}", 0

    async def test_connection(self) -> tuple[bool, str]:
        """Тестирование подключения к Google Sheets"""
        try:
            if not self.is_configured():
                return False, "Синхронизация не настроена"
            
            if not self.credentials:
                if not await self.initialize_credentials():
                    return False, "Ошибка инициализации Google API"
            
            import gspread
            
            # Создаем клиент
            gc = gspread.authorize(self.credentials)
            
            # Пытаемся открыть таблицу
            try:
                sheet = gc.open_by_key(self.sheet_id)
                # Пытаемся получить список листов
                worksheets = sheet.worksheets()
                return True, f"Подключение успешно. Найдено листов: {len(worksheets)}"
            except gspread.exceptions.SpreadsheetNotFound:
                return False, "Таблица не найдена. Проверьте ID таблицы."
            except gspread.exceptions.APIError as e:
                error_msg = str(e)
                if "PERMISSION_DENIED" in error_msg:
                    return False, "Нет доступа к таблице. Убедитесь, что сервисный аккаунт имеет доступ к таблице."
                else:
                    return False, f"Ошибка доступа: {error_msg}"
                    
        except Exception as e:
            return False, f"Ошибка подключения: {str(e)}"

# Создаем экземпляр синхронизации
google_sync = GoogleSheetsSync()
