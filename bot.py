import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import gspread
from google.oauth2.service_account import Credentials

print("1. Начало импорта...")

TOKEN = os.environ.get('ADMIN_TOKEN')
if not TOKEN:
    raise ValueError("ADMIN_TOKEN не установлен!")

print("2. Токен получен")

SPREADSHEET_NAME = "Indev"
PRIMARY_POOL_SHEET = "Первичный пул заявок"
GENERAL_POOL_SHEET = "Общий пул заявок"

EKATERINBURG_TZ = timezone(timedelta(hours=5))

ADMINS = [6067555377, 5518656277]

SOURCE_OPTIONS = [
    "ПРОФИ", "Сайт форма", "Звонок", "Telegram", "WhatsApp",
    "MAX", "Рекомендация", "Повторное", "От работника", "Другое", "н/у"
]

print("3. Константы загружены")

flask_app = Flask(__name__)
telegram_app = None
main_loop = None

print("4. Flask приложение создано")

# ========== GOOGLE SHEETS ==========
def get_worksheet(sheet_name):
    print(f"DEBUG: get_worksheet вызван для {sheet_name}")
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise Exception("GOOGLE_CREDENTIALS не установлена!")
    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=['https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive']
    )
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).worksheet(sheet_name)

def get_next_empty_row(sheet):
    print(f"DEBUG: get_next_empty_row вызван")
    all_values = sheet.get_all_values()
    for idx, row in enumerate(all_values, start=1):
        if all(cell == '' for cell in row):
            print(f"DEBUG: найдена пустая строка {idx}")
            return idx
    new_row = len(all_values) + 1
    print(f"DEBUG: все строки заняты, новая строка {new_row}")
    return new_row

def generate_next_order_id():
    print("DEBUG: generate_next_order_id вызван")
    sheet = get_worksheet(GENERAL_POOL_SHEET)
    all_ids = sheet.col_values(1)  # столбец A
    
    max_num = -1
    for id_str in all_ids:
        if id_str and '-B2B' in id_str:
            try:
                num_part = id_str.split('-')[0]
                if num_part.isdigit():
                    num = int(num_part)
                    if num > max_num:
                        max_num = num
            except:
                continue
    
    next_num = max_num + 1
    next_id = f"{next_num:06d}-B2B"
    print(f"DEBUG: сгенерирован ID {next_id}")
    return next_id

def save_order_to_general_pool(data, order_id):
    print("DEBUG: save_order_to_general_pool вызван")
    sheet = get_worksheet(GENERAL_POOL_SHEET)
    row = get_next_empty_row(sheet)
    print(f"DEBUG: сохраняем в общий пул, строка {row}")
    sheet.update(range_name=f'A{row}', values=[[order_id]])
    sheet.update(range_name=f'B{row}', values=[[data['source']]])
    sheet.update(range_name=f'C{row}', values=[[data['receipt_date']]])
    sheet.update(range_name=f'E{row}', values=[[data['client']]])
    sheet.update(range_name=f'F{row}', values=[[data['address']]])
    sheet.update(range_name=f'G{row}', values=[["Создана, не распределена"]])
    print("DEBUG: данные сохранены в общий пул")

def save_order_to_sheet(data):
    print("DEBUG: save_order_to_sheet вызван")
    
    # Генерируем ID
    order_id = generate_next_order_id()
    data['order_id'] = order_id
    
    # Сохраняем в первичный пул
    sheet = get_worksheet(PRIMARY_POOL_SHEET)
    row = get_next_empty_row(sheet)
    print(f"DEBUG: сохраняем в первичный пул, строка {row}")
    sheet.update(range_name=f'A{row}', values=[[order_id]])
    sheet.update(range_name=f'B{row}', values=[[data['source']]])
    sheet.update(range_name=f'C{row}', values=[[data['receipt_date']]])
    sheet.update(range_name=f'E{row}', values=[[data['client']]])
    sheet.update(range_name=f'F{row}', values=[[data['address']]])
    sheet.update(range_name=f'G{row}', values=[[data['comment']]])
    print("DEBUG: данные сохранены в первичный пул")
    
    # Сохраняем в общий пул
    save_order_to_general_pool(data, order_id)
    
    # Отправка уведомления в беседу
    try:
        chat_id = -5454540811
        notification_text = (
            f"#заявка {data['source']}\n\n"
            f"<i>ID:</i> \"{order_id}\"\n"
            f"<i>Адрес:</i> \"{data['address']}\"\n"
            f"<i>Клиент:</i> \"{data['client']}\"\n"
            f"<i>Комментарий:</i> \"{data['comment']}\""
        )
        asyncio.run_coroutine_threadsafe(
            telegram_app.bot.send_message(chat_id=chat_id, text=notification_text, parse_mode='HTML'),
            main_loop
        )
        print("DEBUG: уведомление отправлено в чат")
    except Exception as e:
        print(f"DEBUG: не удалось отправить уведомление: {e}")

# ========== КОМАНДЫ ==========
async def start(update, context):
    print("DEBUG: start функция вызвана")
    user_id = update.effective_user.id
    print(f"DEBUG: user_id = {user_id}")
    
    if user_id not in ADMINS:
        print(f"DEBUG: доступ запрещён для {user_id}")
        await update.message.reply_text("Доступ запрещён.")
        return
    
    print("DEBUG: доступ разрешён")
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("СОЗДАТЬ ЗАЯВКУ", callback_data="create_order")],
        [InlineKeyboardButton("РАСПРЕДЕЛИТЬ СУЩЕСТВУЮЩУЮ ЗАЯВКУ", callback_data="distribute_order")]
    ]
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    print("DEBUG: главное меню отправлено")

async def button_handler(update, context):
    print("DEBUG: button_handler вызван")
    query = update.callback_query
    print(f"DEBUG: query.data = {query.data}")
    await query.answer()
    
    if query.data == "create_order":
        print("DEBUG: выбрано create_order")
        context.user_data.clear()
        context.user_data['step'] = 'source'
        keyboard = [[InlineKeyboardButton(opt, callback_data=f"src_{opt}")] for opt in SOURCE_OPTIONS]
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        await query.edit_message_text(
            "Выберите источник заявки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        print("DEBUG: меню выбора источника отправлено")
    elif query.data == "distribute_order":
        print("DEBUG: выбрано distribute_order")
        context.user_data.clear()
        context.user_data['step'] = 'distribute'
        
        # Получаем заявки из первичного пула
        sheet = get_worksheet(PRIMARY_POOL_SHEET)
        all_values = sheet.get_all_values()
        
        # Пропускаем заголовок (первая строка)
        orders = []
        for idx, row in enumerate(all_values[1:], start=2):
            if any(row):  # если строка не пустая
                orders.append({
                    'row': idx,
                    'id': row[0] if len(row) > 0 else '',  # A
                    'source': row[1] if len(row) > 1 else '',  # B
                    'receipt_date': row[2] if len(row) > 2 else '',  # C
                    'client': row[4] if len(row) > 4 else '',  # E
                    'address': row[5] if len(row) > 5 else '',  # F
                    'comment': row[6] if len(row) > 6 else ''  # G
                })
        
        if not orders:
            await query.edit_message_text("Нет нераспределённых заявок.")
            return
        
        context.user_data['orders'] = orders
        
        # Формируем текст списка
        text = "Список нераспределённых (новых) заявок:\n\n"
        for i, order in enumerate(orders, start=1):
            text += f"{i}. ID: {order['id']} / Источник заявки: {order['source']} / Дата создания: {order['receipt_date']} / Клиент: {order['client']} / Адрес: {order['address']} / Комментарий: {order['comment']}\n"
        
        # Создаём кнопки с номерами
        keyboard = []
        for i, order in enumerate(orders, start=1):
            keyboard.append([InlineKeyboardButton(f"{i} / ID: {order['id']}", callback_data=f"distribute_{i-1}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        print("DEBUG: список распределения отправлен")

async def distribute_order_callback(update, context):
    print("DEBUG: distribute_order_callback вызван")
    query = update.callback_query
    print(f"DEBUG: query.data = {query.data}")
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ Отменено.")
        context.user_data.clear()
        return
    
    # Пока просто подтверждаем выбор
    await query.edit_message_text("Функция выбора мастера в разработке.")

async def source_callback(update, context):
    print("DEBUG: source_callback вызван")
    query = update.callback_query
    print(f"DEBUG: query.data = {query.data}")
    await query.answer()
    
    if query.data == "cancel":
        print("DEBUG: нажата отмена")
        await query.edit_message_text("❌ Отменено.")
        context.user_data.clear()
        return
    
    if query.data.startswith("src_"):
        source = query.data.split('_', 1)[1]
        print(f"DEBUG: выбран источник: {source}")
        context.user_data['source'] = source
        context.user_data['step'] = 'address'
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
        await query.edit_message_text(
            "Введите адрес:\n\n<i>Например, ул. Опалихинская, д. 20, подъезд 3, этаж 5, кв. 228</i>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        print("DEBUG: запрос адреса отправлен")

async def handle_text(update, context):
    print("DEBUG: handle_text вызван")
    step = context.user_data.get('step')
    print(f"DEBUG: текущий step = {step}")
    user_text = update.message.text
    print(f"DEBUG: текст пользователя = {user_text}")
    
    if step == 'address':
        print("DEBUG: обработка адреса")
        context.user_data['address'] = user_text
        context.user_data['step'] = 'client'
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
        await update.message.reply_text(
            "Введите клиента:\n\n"
            "<i>Следует перечислить реквизиты клиента в одну строку, например: Елена, 89990004422.</i>\n\n"
            "<i>Jika perlu untuk mencantumkan beberapa requisits dan/atau penjelasan untuk requisits, hal ini juga harus dilakukan dalam satu baris dengan pemisahan visual yang jelas, misalnya: \"Елена (pemilik, untuk pembayaran), 89990004422. Anastasia (penyewa, untuk perencanaan keberangkatan), 89997776655\"</i>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        print("DEBUG: запрос клиента отправлен")
    elif step == 'client':
        print("DEBUG: обработка клиента")
        context.user_data['client'] = user_text
        context.user_data['step'] = 'comment'
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
        await update.message.reply_text(
            "Введите комментарий:\n\n"
            "<i>Следует указать комментарий касательно заявки в свободной форме и необходимом объёме, например: <b>Хочет 5 сеток, пенсионерка, просит скидку, бла-бла-бла, свободна только в день летнего солнцестояния с 14:31 до 14:50, представиться напарником Виктора, ориентировал 2600 за сетку</b></i>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        print("DEBUG: запрос комментария отправлен")
    elif step == 'comment':
        print("DEBUG: обработка комментария")
        context.user_data['comment'] = user_text
        await show_confirmation(update, context)
    else:
        print(f"DEBUG: неизвестный step = {step}")
        await update.message.reply_text("Начните с /start")

async def show_confirmation(update, context):
    print("DEBUG: show_confirmation вызван")
    data = context.user_data
    context.user_data['step'] = 'confirm'
    keyboard = [
        [InlineKeyboardButton("✅ Сформировать заявку", callback_data="submit")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    await update.message.reply_text(
        f"Проверьте данные:\n\n"
        f"<b>Источник заявки</b>\n<i>{data.get('source', '')}</i>\n\n"
        f"<b>Адрес</b>\n<i>{data.get('address', '')}</i>\n\n"
        f"<b>Клиент</b>\n<i>{data.get('client', '')}</i>\n\n"
        f"<b>Комментарий</b>\n<i>{data.get('comment', '')}</i>\n\n"
        f"Следует проверить правильность введённых данных и отправить заявку, если всё в порядке.",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    print("DEBUG: форма подтверждения отправлена")

async def confirm_callback(update, context):
    print("DEBUG: confirm_callback вызван")
    query = update.callback_query
    print(f"DEBUG: query.data = {query.data}")
    await query.answer()
    
    if query.data == "cancel":
        print("DEBUG: нажата отмена")
        await query.edit_message_text("❌ Отменено.")
        context.user_data.clear()
        await show_main_menu(query.message, context)
    elif query.data == "back":
        print("DEBUG: нажато назад")
        await go_back(update, context)
    elif query.data == "submit":
        print("DEBUG: нажато подтверждение")
        data = context.user_data
        data['receipt_date'] = datetime.now(EKATERINBURG_TZ).strftime("%d.%m.%Y")
        print(f"DEBUG: дата = {data['receipt_date']}")
        save_order_to_sheet(data)
        await query.edit_message_text("✅ Заявка успешно сохранена в Первичный пул.")
        context.user_data.clear()
        await show_main_menu(query.message, context)

async def go_back(update, context):
    print("DEBUG: go_back вызван")
    query = update.callback_query
    await query.answer()
    
    step = context.user_data.get('step')
    print(f"DEBUG: текущий step = {step}")
    
    if step == 'address':
        print("DEBUG: возврат к выбору источника")
        context.user_data['step'] = 'source'
        keyboard = [[InlineKeyboardButton(opt, callback_data=f"src_{opt}")] for opt in SOURCE_OPTIONS]
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        await query.edit_message_text(
            "Выберите источник заявки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif step == 'client':
        print("DEBUG: возврат к вводу адреса")
        context.user_data['step'] = 'address'
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
        await query.edit_message_text(
            "Введите адрес:\n\n<i>Например, ул. Опалихинская, д. 20, подъезд 3, этаж 5, кв. 228</i>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif step == 'comment':
        print("DEBUG: возврат к вводу клиента")
        context.user_data['step'] = 'client'
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
        await query.edit_message_text(
            "Введите клиента:\n\n"
            "<i>Следует перечислить реквизиты клиента в одну строку, например: Елена, 89990004422.</i>\n\n"
            "<i>Jika perlu untuk mencantumkan beberapa requisits dan/atau penjelasan untuk requisits, hal ini juga harus dilakukan dalam satu baris dengan pemisahan visual yang jelas, misalnya: \"Елена (pemilik, untuk pembayaran), 89990004422. Anastasia (penyewa, untuk perencanaan keberangkatan), 89997776655\"</i>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif step == 'confirm':
        print("DEBUG: возврат к вводу комментария")
        context.user_data['step'] = 'comment'
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
        await query.edit_message_text(
            "Введите комментарий:\n\n"
            "<i>Следует указать комментарий касательно заявки в свободной форме и необходимом объёме, например: <b>Хочет 5 сеток, пенсионерка, просит скидку, бла-бла-бла, свободна только в день летнего солнцестояния с 14:31 до 14:50, представиться напарником Виктора, ориентировал 2600 за сетку</b></i>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def cancel_handler(update, context):
    print("DEBUG: cancel_handler вызван")
    context.user_data.clear()
    await update.message.reply_text("❌ Отменено.")
    await show_main_menu(update.message, context)

async def show_main_menu(message, context):
    print("DEBUG: show_main_menu вызван")
    keyboard = [
        [InlineKeyboardButton("СОЗДАТЬ ЗАЯВКУ", callback_data="create_order")],
        [InlineKeyboardButton("РАСПРЕДЕЛИТЬ СУЩЕСТВУЮЩУЮ ЗАЯВКУ", callback_data="distribute_order")]
    ]
    await message.reply_text(
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    print("DEBUG: главное меню отправлено")

# ========== ВЕБХУК ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    global telegram_app, main_loop
    try:
        data = request.get_json()
        update = Update.de_json(data, telegram_app.bot)
        asyncio.run_coroutine_threadsafe(
            telegram_app.process_update(update),
            main_loop
        )
        return "OK", 200
    except Exception as e:
        print(f"Ошибка: {e}")
        return "Internal Server Error", 500

@flask_app.route('/')
def home():
    return "Admin bot works"

# ========== ЗАПУСК ==========
def run_webhook():
    global telegram_app, main_loop
    
    telegram_app = Application.builder().token(TOKEN).build()
    
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("cancel", cancel_handler))
    
    # Обработчики в правильном порядке
    telegram_app.add_handler(CallbackQueryHandler(button_handler, pattern="^(create_order|distribute_order)$"))
    telegram_app.add_handler(CallbackQueryHandler(source_callback, pattern="^src_"))
    telegram_app.add_handler(CallbackQueryHandler(distribute_order_callback, pattern="^distribute_"))
    telegram_app.add_handler(CallbackQueryHandler(confirm_callback, pattern="^(submit|back|cancel)$"))
    telegram_app.add_handler(CallbackQueryHandler(go_back, pattern="^back$"))
    
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    main_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(main_loop)
    main_loop.run_until_complete(telegram_app.initialize())
    main_loop.run_until_complete(telegram_app.start())
    
    port = int(os.environ.get("PORT", 8080))
    print(f"Admin bot started on port {port}")
    
    def run_flask():
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    main_loop.run_forever()

print("5. Всё загружено, запускаем...")

if __name__ == '__main__':
    run_webhook()
