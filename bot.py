import os
import json
import sqlite3
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# 1. BOT TOKEN እና URL ማዘጋጀት
# Render ላይ ከሆንክ የ ENVIRONMENT VARIABLE መጠቀም ትችላለህ ወይም ቀጥታ Token አስገባ
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
WEB_APP_URL = "https://true-love-dating.onrender.com"  # Render የሚሰጥህ የራሱ URL

bot = telebot.TeleBot(BOT_TOKEN)

# 2. SQLite3 DATABASE ማዘጋጀት (ከሌለ በራሱ ይፈጥራል)
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

# ቦቱ ሲነሳ ዳታቤዙ እንዲዘጋጅ ማድረግ
init_db()

# 3. /start COMMAND HANDLER
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    # Mini App የሚከፍተው Button
    web_app_btn = InlineKeyboardButton(
        text="❤️ Open True Love App", 
        web_app=WebAppInfo(url=WEB_APP_URL)
    )
    markup.add(web_app_btn)
    
    bot.reply_to(
        message, 
        f"ሰላም {message.from_user.first_name}! እንኳን ወደ **True Love** በደህና መጡ።\n\nታች ያለውን ቁልፍ በመጫን ፕሮፋይልዎን ያስተካክሉ፡", 
        reply_markup=markup,
        parse_mode="Markdown"
    )

# 4. WEB APP DATA HANDLER (ከ Mini App የሚመጣውን Profile የመቀበያ ቦታ)
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        # ከ WebApp የመጣውን JSON ማወቅ
        data = json.loads(message.web_app_data.data)
        
        if data.get('action') == 'save_profile':
            user_id = message.from_user.id
            
            # መረጃውን ዳታቤዝ ውስጥ ማስገባት
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
            
            # ለተጠቃሚው በቴሌግራም የማረጋገጫ መልእክት መላክ
            bot.send_message(
                message.chat.id, 
                f"🎉 **ፕሮፋይልዎ በስኬት ተቀምጧል!**\n\n"
                f"👤 **ስም:** {data.get('name')}\n"
                f"🎂 **እድሜ:** {data.get('age')}\n"
                f"📱 **ስልክ:** {data.get('phone')}\n"
                f"📍 **ቦታ:** {data.get('location')}\n"
                f"📝 **Bio:** {data.get('bio')}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        print(f"Error handling web_app_data: {e}")
        bot.send_message(message.chat.id, "❌ ፕሮፋይል ሲቀመጥ ስህተት ተፈጥሯል፣ እባክዎ እንደገና ይሞክሩ።")

# 5. BOT RUNNING
if __name__ == '__main__':
    print("True Love Bot is running...")
    bot.infinity_polling(skip_pending=True)
