@dp.message(F.text == "📊 Онлайн Excel")
async def cmd_online_excel(message: types.Message):
    """Онлайн просмотр в Excel-подобном формате"""
    filters = await get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет данных для отображения</b>",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Создаем табличный вид
    table_header = "┌─────┬────────────────────┬──────────────────┬──────────────┬──────────────┐\n"
    table_header += "│ ID  │ Тип фильтра        │ Местоположение   │ Дата замены  │ Годен до     │\n"
    table_header += "├─────┼────────────────────┼──────────────────┼──────────────┼──────────────┤\n"
    
    table_rows = []
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        last_change = datetime.strptime(str(f['last_change']), '%Y-%m-%d').date()
        days_until = (expiry_date - datetime.now().date()).days
        
        icon, _ = get_status_icon_and_text(days_until)
        
        row = f"│ {f['id']:3} │ {f['filter_type'][:18]:18} │ {f['location'][:16]:16} │ {format_date_nice(last_change):12} │ {format_date_nice(expiry_date):12} │ {icon}"
        table_rows.append(row)
    
    table_footer = "└─────┴────────────────────┴──────────────────┴──────────────┴──────────────┘"
    
    table_content = table_header + "\n".join(table_rows) + "\n" + table_footer
    
    # Разбиваем на части если слишком длинное
    if len(table_content) > 4000:
        parts = [table_content[i:i+4000] for i in range(0, len(table_content), 4000)]
        for part in parts:
            await message.answer(f"<pre>{part}</pre>", parse_mode='HTML')
    else:
        await message.answer(f"<pre>{table_content}</pre>", parse_mode='HTML')
    
    await message.answer(
        "📊 <b>ТАБЛИЧНЫЙ ПРОСМОТР</b>\n\n"
        "💡 Легенда статусов:\n"
        "🟢 Норма | 🟡 Скоро истечет | 🟠 Внимание | 🔴 Просрочен",
        reply_markup=get_management_keyboard(),
        parse_mode='HTML'
    )
