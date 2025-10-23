def format_date_nice(date_obj) -> str:
    """Форматирование даты в читаемый вид"""
    if isinstance(date_obj, str):
        try:
            date_obj = datetime.strptime(date_obj, '%Y-%m-%d')
        except:
            return date_obj
    
    if isinstance(date_obj, datetime):
        return date_obj.strftime('%d.%m.%Y')
    
    return str(date_obj)

def get_status_icon_and_text(days_until_expiry: int) -> tuple:
    """Получение иконки и текста статуса"""
    if days_until_expiry <= 0:
        return "🔴", "ПРОСРОЧЕН"
    elif days_until_expiry <= 7:
        return "🟡", "СКОРО ИСТЕЧЕТ"
    elif days_until_expiry <= 30:
        return "🟠", "ВНИМАНИЕ"
    else:
        return "🟢", "НОРМА"
        
