"""Telegram-бот исторического квеста «Иван Грозный в эпоху Великих реформ».

Этапы квеста лежат в текстовых файлах каталога TASKS_DIR (по умолчанию
``tasks/``). Имя файла без расширения — идентификатор этапа, который
приходит обратно от Telegram в поле ``callback_data``.
"""

import logging
import os
import re
import sys
import traceback

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger('history_quest')

# Каталог с заданиями. Раньше значение было захардкожено, хотя переменная
# TASKS_DIR уже объявлялась в .env.example и docker-compose.yml.
TASKS_DIR = os.getenv('TASKS_DIR', 'tasks')

# Файл задания читается в память целиком, поэтому ограничиваем его размер.
MAX_TASK_BYTES = 64 * 1024

# Telegram отклоняет подпись к фотографии длиннее 1024 символов.
MAX_CAPTION_LEN = 1024

# Допустимое имя этапа.
#
# ВАЖНО: callback_data приходит от клиента и НЕ является доверенным — Telegram
# не подписывает его, и кастомный клиент может прислать произвольную строку.
# Без этой проверки ``os.path.join(TASKS_DIR, data + '.txt')`` позволял бы
# прочитать любой .txt-файл на диске (например ``../../../../etc/hosts``)
# и отправить его содержимое в чат.
STAGE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

# Токен бота попадает в текст сетевых ошибок requests: URL запроса выглядит
# как https://api.telegram.org/bot<TOKEN>/getUpdates, и библиотека requests
# вставляет его целиком в сообщение ConnectionError. Без вычистки первый же
# обрыв связи печатал полный токен в stdout контейнера (docker logs).
TOKEN_RE = re.compile(r'\d{5,}:[A-Za-z0-9_-]{20,}')

# Состояния пользователей (chat_id -> текущий этап).
user_states = {}


def redact(text, token=None):
    """Убирает из строки токен бота и любые строки, похожие на токен."""
    if not text:
        return text
    if token:
        text = text.replace(token, '<TELEGRAM_BOT_TOKEN>')
    return TOKEN_RE.sub('<TELEGRAM_BOT_TOKEN>', text)


class RedactingFormatter(logging.Formatter):
    """Форматтер, который вычищает токен и из сообщения, и из traceback'а."""

    def __init__(self, fmt=None, datefmt=None, token=None):
        super().__init__(fmt, datefmt)
        self._token = token

    def format(self, record):
        return redact(super().format(record), self._token)


def _make_excepthook(token, previous_hook):
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc, tb)
            return
        text = ''.join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(redact(text, token))
    return hook


def configure_logging(token=None):
    """Настраивает логирование так, чтобы токен не мог попасть в логи."""
    level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    fmt = '%(asctime)s %(levelname)s %(name)s: %(message)s'
    logging.basicConfig(level=level, stream=sys.stdout, format=fmt)

    # У telebot собственный обработчик со своим форматтером, поэтому
    # подменяем форматтер на всех обработчиках, а не только на корневом.
    telebot_logger = logging.getLogger('TeleBot')
    for log in (logging.getLogger(), telebot_logger):
        for handler in log.handlers:
            handler.setFormatter(RedactingFormatter(fmt, token=token))

    # telebot на уровне DEBUG печатает URL каждого запроса; держим его тихим,
    # если пользователь явно не попросил отладочный вывод.
    telebot_logger.setLevel(logging.DEBUG if level <= logging.DEBUG else logging.WARNING)

    sys.excepthook = _make_excepthook(token, sys.excepthook)


def parse_task(lines):
    """Разбирает строки файла задания в кортеж ``(text, image, buttons)``."""
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
            if ':' not in line:
                # Раньше такая строка роняла бота с ValueError.
                logger.warning('Пропущена строка кнопки без двоеточия: %r', line)
                continue
            btn_text, callback = line.split(':', 1)
            buttons.append((btn_text.strip(), callback.strip()))

    return text.strip(), image, buttons


def load_task(stage):
    """Читает этап ``stage``. Возвращает ``(None, None, None)``, если этапа нет.

    Имя этапа проверяется по белому списку символов, поэтому выйти за пределы
    TASKS_DIR через ``..`` или абсолютный путь невозможно.
    """
    if not stage or not STAGE_NAME_RE.match(stage):
        logger.warning('Отклонено недопустимое имя этапа: %r', stage)
        return None, None, None

    file_path = os.path.join(TASKS_DIR, stage + '.txt')
    if not os.path.isfile(file_path):
        return None, None, None

    if os.path.getsize(file_path) > MAX_TASK_BYTES:
        logger.error('Файл задания %s больше %d байт, пропущен', file_path, MAX_TASK_BYTES)
        return None, None, None

    with open(file_path, 'r', encoding='utf-8') as f:
        return parse_task(f)


def build_markup(buttons):
    markup = InlineKeyboardMarkup()
    for btn_text, callback in buttons:
        markup.add(InlineKeyboardButton(btn_text, callback_data=callback))
    return markup


def send_card(bot, chat_id, text, image, buttons):
    """Отправляет карточку этапа: фото с подписью либо просто текст."""
    markup = build_markup(buttons)

    if not image:
        # Раньше здесь вызывался send_photo(photo=''), и пользователь
        # не получал вообще ничего.
        bot.send_message(chat_id, text, reply_markup=markup)
        return

    if len(text) <= MAX_CAPTION_LEN:
        bot.send_photo(chat_id, photo=image, caption=text, reply_markup=markup)
        return

    # Слишком длинная подпись — Telegram отверг бы всё сообщение целиком,
    # поэтому отправляем картинку и текст по отдельности.
    logger.warning('Текст этапа длиннее %d символов, отправляю отдельным сообщением',
                   MAX_CAPTION_LEN)
    bot.send_photo(chat_id, photo=image)
    bot.send_message(chat_id, text, reply_markup=markup)


def send_stage(bot, chat_id, stage):
    text, image, buttons = load_task(stage)
    if not text:
        user_states.pop(chat_id, None)
        bot.send_message(chat_id, 'Конец квеста! Сделано в НЭТИ, 2025.')
        return
    send_card(bot, chat_id, text, image, buttons)


def build_bot(token):
    """Создаёт бота и регистрирует обработчики."""
    bot = telebot.TeleBot(token)

    @bot.message_handler(commands=['start'])
    def start(message):
        chat_id = message.chat.id
        user_states[chat_id] = 'intro'
        send_stage(bot, chat_id, 'intro')

    @bot.callback_query_handler(func=lambda call: True)
    def callback_handler(call):
        chat_id = call.message.chat.id
        data = call.data or ''

        # Пока бот не ответит на callback, клиент крутит «часики» на кнопке.
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            logger.debug('Не удалось ответить на callback_query', exc_info=True)

        if data == 'end':
            # Раньше здесь был del user_states[chat_id]: повторное нажатие
            # или нажатие без /start роняло обработчик с KeyError.
            user_states.pop(chat_id, None)
            bot.send_message(chat_id, 'Квест завершён! Узнай больше на sibistorik.ru.')
            return

        if data.startswith('wrong_'):
            # Раньше имя файла собиралось как wrong_{data.split('_')[1]}, что
            # падало с IndexError на 'wrong_' и ломалось на этапах, в имени
            # которых есть подчёркивание.
            text, image, buttons = load_task(data)
            if text:
                send_card(bot, chat_id, text, image, buttons)
                return

            markup = InlineKeyboardMarkup()
            current = user_states.get(chat_id)
            if current:
                markup.add(InlineKeyboardButton('Продолжить', callback_data=current))
            bot.send_message(chat_id, 'Неправильно! Изучите материал внимательнее.',
                             reply_markup=markup)
            return

        user_states[chat_id] = data
        send_stage(bot, chat_id, data)

    return bot


def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError('Переменная окружения TELEGRAM_BOT_TOKEN не установлена')

    configure_logging(token)
    bot = build_bot(token)
    logger.info('Бот запущен, каталог заданий: %s', TASKS_DIR)
    # infinity_polling переживает разрывы связи; обычный polling() умирал
    # навсегда при первой же сетевой ошибке.
    bot.infinity_polling()


if __name__ == '__main__':
    main()
