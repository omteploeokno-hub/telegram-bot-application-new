import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
import gspread
from google.oauth2.service_account import Credentials

TOKEN = os.environ.get('ADMIN_TOKEN')
if not TOKEN:
    raise ValueError("ADMIN_TOKEN не установлен!")

SPREADSHEET_NAME = "Indev"
PRIMARY_POOL_SHEET = "Первичный пул заявок"

EKATERINBURG_TZ = timezone(timedelta(hours=5))

ADMINS = [6067555377,5518656277]

# Состояния разговора
SOURCE, ADDRESS, CLIENT, COMMENT, CONFIRM = range(5)

SOURCE_OPTIONS = [
    "ПРОФИ", "Сайт форма", "Звонок", "Telegram", "WhatsApp",
    "MAX", "Рекомендация", "Повторное", "От работника", "Другое", "н/у"
]

flask_app = Flask(__name__)
telegram_app = None
main_loop = None

def get_worksheet(sheet_name):
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
    col_a = sheet.col_values(1)
    for idx, val in enumerate(col_a, start=1):
        if not val:
            return idx
    return len(col_a) + 1

def save_order_to_sheet(data):
    sheet = get_worksheet(PRIMARY_POOL_SHEET)
    row = get_next_empty_row(sheet)
    sheet.update(f'B{row}', [[data['source']]])
    sheet.update(f'C{row}', [[data['receipt_date']]])
    sheet.update(f'E{row}', [[data['client']]])
    sheet.update(f'F{row}', [[data['address']]])
    sheet.update(f'G{row}', [[data['comment']]])

async def start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("Доступ запрещён.")
        return
    
    keyboard = [
        [InlineKeyboardButton("СОЗДАТЬ ЗАЯВКУ", callback_data="create_order")],
        [InlineKeyboardButton("РАСПРЕДЕЛИТЬ СУЩЕСТВУЮЩУЮ ЗАЯВКУ", callback_data="distribute_order")]
    ]
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "create_order":
        context.user_data.clear()
        await query.edit_message_text(
            "Выберите источник заявки:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(opt, callback_data=f"src_{opt}")] for opt in SOURCE_OPTIONS] +
                [[InlineKeyboardButton("Отмена", callback_data="cancel")]]
            )
        )
        return SOURCE
    elif query.data == "distribute_order":
        await query.edit_message_text("Функция распределения заявок в разработке.")
        return ConversationHandler.END

async def source_selected(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("Отменено.")
        return ConversationHandler.END
    
    source = query.data.split('_', 1)[1]
    context.user_data['source'] = source
    
    await query.edit_message_text(
        "Введите адрес:\n\n<i>Например, ул. Опалихинская, д. 20, подъезд 3, этаж 5, кв. 228</i>",
        parse_mode='HTML'
    )
    return ADDRESS

async def address_received(update, context):
    context.user_data['address'] = update.message.text
    await update.message.reply_text(
        "Введите клиента:\n\n"
        "<i>Следует перечислить реквизиты клиента в одну строку, например: Елена, 89990004422.</i>\n\n"
        "<i>Если необходимо перечислить несколько реквизитов и/или какие-либо пояснения к реквизитам, следует делать это также в одной строке с явным визуальным разделением, например: \"Елена (собственник, по оплате), 89990004422. Анастасия (арендатор, для планирования выезда), 89997776655\"</i>",
        parse_mode='HTML'
    )
    return CLIENT

async def client_received(update, context):
    context.user_data['client'] = update.message.text
    await update.message.reply_text(
        "Введите комментарий:\n\n"
        "<i>Следует указать комментарий касательно заявки в свободной форме и необходимом объёме, например: <b>Хочет 5 сеток, пенсионерка, просит скидку, бла-бла-бла, свободна только в день летнего солнцестояния с 14:31 до 14:50, представиться напарником Виктора, ориентировал 2600 за сетку</b></i>",
        parse_mode='HTML'
    )
    return COMMENT

async def comment_received(update, context):
    context.user_data['comment'] = update.message.text
    await show_confirmation(update, context)
    return CONFIRM

async def show_confirmation(update, context):
    data = context.user_data
    await update.message.reply_text(
        f"Проверьте данные:\n\n"
        f"<b>Источник заявки</b>\n<i>{data.get('source', '')}</i>\n\n"
        f"<b>Адрес</b>\n<i>{data.get('address', '')}</i>\n\n"
        f"<b>Клиент</b>\n<i>{data.get('client', '')}</i>\n\n"
        f"<b>Комментарий</b>\n<i>{data.get('comment', '')}</i>\n\n"
        f"Следует проверить правильность введённых данных и отправить заявку, если всё в порядке.",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Сформировать заявку", callback_data="submit")],
            [InlineKeyboardButton("Заново", callback_data="restart")],
            [InlineKeyboardButton("Отмена", callback_data="cancel")]
        ])
    )

async def confirm_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "submit":
        data = context.user_data
        data['receipt_date'] = datetime.now(EKATERINBURG_TZ).strftime("%d.%m.%Y")
        save_order_to_sheet(data)
        await query.edit_message_text("Заявка успешно сохранена в Первичный пул.")
        context.user_data.clear()
        return ConversationHandler.END
    elif query.data == "restart":
        await query.edit_message_text(
            "Выберите источник заявки:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(opt, callback_data=f"src_{opt}")] for opt in SOURCE_OPTIONS] +
                [[InlineKeyboardButton("Отмена", callback_data="cancel")]]
            )
        )
        return SOURCE
    else:
        await query.edit_message_text("Отменено.")
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

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

def run_webhook():
    global telegram_app, main_loop
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^create_order$")],
        states={
            SOURCE: [CallbackQueryHandler(source_selected, pattern="^src_")],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, address_received)],
            CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, client_received)],
            COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, comment_received)],
            CONFIRM: [CallbackQueryHandler(confirm_callback, pattern="^(submit|restart|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(conv_handler)
    
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

if __name__ == '__main__':
    run_webhook()
