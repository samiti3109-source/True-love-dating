import os
import threading
from flask import Flask, send_from_directory
import telebot

# 1. Telegram Bot Token (ከ @BotFather ያገኘኸውን ማስገባት ትችላለህ)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8959392899:AAG1hIoDIuktlazViTtd-EZ3qbw-CiuLSAk")
bot = telebot.TeleBot(BOT_TOKEN)

# 2. Web App (Flask Server) - HTML ፋይሉን ለማስተናገድ
app = Flask(__name__, static_folder='.')

@app.route('/')
def home():
    # Render ቦቱ እንዳይዘጋ ድረ-ገጹ ሲከፈት 200 OK ይመልሳል
    return "True Love Bot is Running 24/7!"

@app.route('/app')
def serve_webapp():
    # HTML ፋይሉን ለቴሌግራም Mini App ያቀርባል
    return send_from_directory('.', 'index.html')

# 3. የቴሌግራም /start ትእዛዝ ማስተናገጃ
@bot.message_handler(commands=['start'])
def send_welcome(message):
    keyboard = telebot.types.InlineKeyboardMarkup()
    # የምትጠቀመውን የ WebApp URL እዚህ ይተካል ( Render URL )
    web_app_url = "https://" + os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost") + "/app"
    
    web_app_info = telebot.types.WebAppInfo(url=web_app_url)
    button = telebot.types.InlineKeyboardButton(text="❤️ Open True Love App", web_app=web_app_info)
    keyboard.add(button)
    
    bot.reply_to(message, "እንኳን ወደ True Love በሰላም መጡ! አፑን ለመክፈት ከታች ያለውን ይጫኑ፡", reply_markup=keyboard)

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    # Flask ሰርቨሩን በጀርባ (Background Thread) ማስነሳት
    threading.Thread(target=run_flask).start()
    
    print("🤖 True Love Bot is running...")
    # የቴሌግራም ቦቱን ማስነሳት
    bot.infinity_polling(skip_pending=True)
