import os
import asyncio
import json
from datetime import datetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
import gspread
from google.oauth2.service_account import Credentials

TOKEN = os.environ.get('ADMIN_TOKEN')
if not TOKEN:
    raise ValueError("ADMIN_TOKEN не установлен!")

SPREADSHEET_NAME = "Indev"
PRIMARY_POOL_SHEET = "Первичный пул заявок"

flask_app = Flask(__name__)
telegram_app = None
main_loop = None

ADMINS = [
    6067555377,5518656277
]

# ========== GOOGLE SHEETS ==========
def get_worksheet(sheet_name):
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise Exception("GOOGLE_CREDENTIALS не установлена!")
    
    creds_info = json.loads(creds_json)
    # Правильные scopes (доступ и к таблицам, и к диску)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
    )
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).worksheet(sheet_name)

# ========== КОМАНДЫ ==========
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
        await query.edit_message_text("Функция создания заявки в разработке.")
    elif query.data == "distribute_order":
        await query.edit_message_text("Функция распределения заявок в разработке.")

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

def run_webhook():
    global telegram_app, main_loop
    
    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    
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
