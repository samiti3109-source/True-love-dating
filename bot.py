import os
import json
import sqlite3
import threading
from flask import Flask, send_from_directory
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8959392899:AAG1hIoDIuktlazViTtd-EZ3qbw-CiuLSAk")
WEB_APP_URL = "https://samiti3109-source.github.io/True-love-dating/"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__, static_folder='.')

# 1. FLASK ROUTES (/ እና /app ሁለቱንም እንዲቀበል)
@app.route('/')
@app.route('/app')
def serve_app():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)

# 2. DATABASE
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            age TEXT,
            gender TEXT,
            phone TEXT,
            location TEXT,
            looking_for TEXT,
            pref_age TEXT,
            religion TEXT,
            zodiac TEXT,
            bio TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# 3. BOT /start COMMAND
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    web_app_btn = InlineKeyboardButton(
        text="❤️ Open True Love App", 
        web_app=WebAppInfo(url=WEB_APP_URL)
    )
    markup.add(web_app_btn)
    
    bot.reply_to(
        message, 
        f"ሰላም {message.from_user.first_name}! እንኳን ወደ True Love በደህና መጡ።\n\nታች ያለውን ቁልፍ በመጫን አፑን ይክፈቱ፡", 
        reply_markup=markup
    )

# 4. DATA HANDLER
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        
        if data.get('action') == 'save_profile':
            user_id = message.from_user.id
            
            conn = sqlite3.connect('database.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO users 
                (user_id, name, age, gender, phone, location, looking_for, pref_age, religion, zodiac, bio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id,
                data.get('name'),
                data.get('age'),
                data.get('gender'),
                data.get('phone'),
                data.get('location'),
                data.get('lookingFor'),
                data.get('prefAge'),
                data.get('religion'),
                data.get('zodiac'),
                data.get('bio')
            ))
            
            conn.commit()
            conn.close()
            
            bot.send_message(
                message.chat.id, 
                f"🎉 **ፕሮፋይልዎ በስኬት ተቀምጧል!**\n\n"
                f"👤 **ስም:** {data.get('name')}\n"
                f"🎂 **እድሜ:** {data.get('age')}\n"
                f"📱 **ስልክ:** {data.get('phone')}\n"
                f"📍 **ቦታ:** {data.get('location')}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        print(f"Error: {e}")
        bot.send_message(message.chat.id, "❌ መረጃውን ማስቀመጥ አልተቻለም።")

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    print("True Love Bot is running...")
    bot.infinity_polling(skip_pending=True)
