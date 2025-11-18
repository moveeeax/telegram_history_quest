import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

# Получаем токен из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Переменная окружения TELEGRAM_BOT_TOKEN не установлена")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Словарь состояний пользователей (user_id: current_stage)
user_states = {}

# Папка с заданиями
TASKS_DIR = 'tasks/'

def load_task(stage):
    file_path = os.path.join(TASKS_DIR, f'{stage}.txt')
    if not os.path.exists(file_path):
        return None, None, None
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    text = ''
    image = ''
    buttons = []
    section = ''
    
    for line in lines:
        line = line.strip()
        if line.startswith('Текст:'):
            section = 'text'
            text = line.split(':', 1)[1].strip()
        elif line.startswith('Image:'):
            section = 'image'
            image = line.split(':', 1)[1].strip()
        elif line.startswith('Buttons:'):
            section = 'buttons'
        elif section == 'text':
            text += ' ' + line
        elif section == 'buttons' and line:
            btn_text, callback = line.split(':', 1)
            buttons.append((btn_text.strip(), callback.strip()))
    
    return text.strip(), image, buttons

def load_task_from_file(filename):
    """Загружает задание из указанного файла"""
    file_path = os.path.join(TASKS_DIR, filename)
    if not os.path.exists(file_path):
        return None, None, None
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    text = ''
    image = ''
    buttons = []
    section = ''
    
    for line in lines:
        line = line.strip()
        if line.startswith('Текст:'):
            section = 'text'
            text = line.split(':', 1)[1].strip()
        elif line.startswith('Image:'):
            section = 'image'
            image = line.split(':', 1)[1].strip()
        elif line.startswith('Buttons:'):
            section = 'buttons'
        elif section == 'text':
            text += ' ' + line
        elif section == 'buttons' and line:
            btn_text, callback = line.split(':', 1)
            buttons.append((btn_text.strip(), callback.strip()))
    
    return text.strip(), image, buttons

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id
    user_states[user_id] = 'intro'  # Начальный этап
    send_stage(user_id, 'intro')

def send_stage(user_id, stage):
    text, image, buttons = load_task(stage)
    if not text:
        bot.send_message(user_id, "Конец квеста! Сделано в НЭТИ, 2025.")
        return
    
    markup = InlineKeyboardMarkup()
    for btn_text, callback in buttons:
        markup.add(InlineKeyboardButton(btn_text, callback_data=callback))
    
    bot.send_photo(user_id, photo=image, caption=text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.message.chat.id
    data = call.data
    
    if data.startswith('wrong_'):
        # Загружаем объяснение для неправильного ответа из файла
        stage = data.split('_')[1]
        wrong_file = f'wrong_{stage}.txt'
        text, image, buttons = load_task_from_file(wrong_file)
        
        if text:
            markup = InlineKeyboardMarkup()
            for btn_text, callback in buttons:
                markup.add(InlineKeyboardButton(btn_text, callback_data=callback))
            
            if image:
                bot.send_photo(user_id, photo=image, caption=text, reply_markup=markup)
            else:
                bot.send_message(user_id, text, reply_markup=markup)
        else:
            # Fallback если файл не найден
            feedback = f"Неправильно! Изучите материал внимательнее."
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Продолжить", callback_data=user_states[user_id]))
            bot.send_message(user_id, feedback, reply_markup=markup)
    elif data == 'end':
        bot.send_message(user_id, "Квест завершён! Узнай больше на sibistorik.ru.")
        del user_states[user_id]
    else:
        # Следующий этап
        user_states[user_id] = data
        send_stage(user_id, data)

bot.polling()