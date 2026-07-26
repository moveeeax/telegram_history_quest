"""Тесты логики бота: разбор заданий, защита от выхода за каталог, вычистка токена."""

import logging

import pytest

import bot as bot_module


TASK_SAMPLE = """Текст: Первая строка.
Вторая строка.

Image: https://example.com/pic.jpg

Buttons:
Правильный ответ: next_stage
Неправильный: wrong_next
"""


def test_parse_task_reads_text_image_and_buttons():
    text, image, buttons = bot_module.parse_task(TASK_SAMPLE.splitlines())
    assert text.startswith('Первая строка.')
    assert 'Вторая строка.' in text
    assert image == 'https://example.com/pic.jpg'
    assert buttons == [
        ('Правильный ответ', 'next_stage'),
        ('Неправильный', 'wrong_next'),
    ]


def test_parse_task_collapses_blank_lines_between_paragraphs():
    """Пустая строка внутри "Текст:" — разделитель абзацев, а не текст.

    Раньше она склеивалась как ' ' + '', и между абзацами накапливались
    лишние пробелы — это било по каждому многоабзацному заданию в tasks/.
    """
    lines = ['Текст: Первый абзац.', '', 'Второй абзац.', '', '', 'Третий.']
    text, _image, _buttons = bot_module.parse_task(lines)
    assert text == 'Первый абзац. Второй абзац. Третий.'
    assert '  ' not in text


def test_parse_task_skips_button_line_without_colon():
    """Строка кнопки без двоеточия раньше роняла бота с ValueError."""
    lines = ['Текст: Вопрос', 'Buttons:', 'кнопка без двоеточия', 'Ок: final']
    text, _image, buttons = bot_module.parse_task(lines)
    assert text == 'Вопрос'
    assert buttons == [('Ок', 'final')]


@pytest.fixture
def tasks_dir(tmp_path, monkeypatch):
    directory = tmp_path / 'tasks'
    directory.mkdir()
    (directory / 'intro.txt').write_text(TASK_SAMPLE, encoding='utf-8')
    monkeypatch.setattr(bot_module, 'TASKS_DIR', str(directory))
    return directory


def test_load_task_reads_existing_stage(tasks_dir):
    text, image, buttons = bot_module.load_task('intro')
    assert text.startswith('Первая строка.')
    assert image == 'https://example.com/pic.jpg'
    assert len(buttons) == 2


def test_load_task_returns_none_for_missing_stage(tasks_dir):
    assert bot_module.load_task('does_not_exist') == (None, None, None)


@pytest.mark.parametrize('evil', [
    '../secret',
    '../../secret',
    'sub/../../secret',
    '/etc/hosts',
    'tasks/../../secret',
    '..%2Fsecret',
    '',
    None,
])
def test_load_task_rejects_paths_outside_tasks_dir(tasks_dir, evil):
    """callback_data не доверенный — имя этапа не должно уводить из TASKS_DIR."""
    secret = tasks_dir.parent / 'secret.txt'
    secret.write_text('Текст: СЕКРЕТ\n', encoding='utf-8')
    assert bot_module.load_task(evil) == (None, None, None)


def test_load_task_rejects_oversized_file(tasks_dir, monkeypatch):
    monkeypatch.setattr(bot_module, 'MAX_TASK_BYTES', 32)
    (tasks_dir / 'huge.txt').write_text('Текст: ' + 'x' * 5000, encoding='utf-8')
    assert bot_module.load_task('huge') == (None, None, None)


def test_redact_removes_configured_token():
    token = '1234567890:FAKE-TOKEN-NOT-REAL-000000000000000'
    leaked = f'Max retries exceeded with url: /bot{token}/getUpdates'
    cleaned = bot_module.redact(leaked, token)
    assert token not in cleaned
    assert '<TELEGRAM_BOT_TOKEN>' in cleaned


def test_redact_removes_token_shaped_string_without_knowing_it():
    other = '9876543210:FAKE-TOKEN-NOT-REAL-111111111111111'
    cleaned = bot_module.redact(f'url=/bot{other}/sendMessage')
    assert other not in cleaned


def test_formatter_redacts_token_inside_traceback():
    """Реальная утечка: requests кладёт URL с токеном в текст ConnectionError."""
    token = '1234567890:FAKE-TOKEN-NOT-REAL-000000000000000'
    formatter = bot_module.RedactingFormatter('%(message)s', token=token)
    try:
        raise RuntimeError(f'HTTPSConnectionPool ... url: /bot{token}/getUpdates')
    except RuntimeError:
        record = logging.LogRecord(
            'TeleBot', logging.ERROR, __file__, 1, 'polling failed', None,
            __import__('sys').exc_info(),
        )
    output = formatter.format(record)
    assert token not in output


def test_tasks_dir_is_configurable_via_env(monkeypatch):
    """TASKS_DIR объявлен в .env.example и docker-compose.yml — он должен читаться."""
    monkeypatch.setenv('TASKS_DIR', '/somewhere/else')
    import importlib
    reloaded = importlib.reload(bot_module)
    try:
        assert reloaded.TASKS_DIR == '/somewhere/else'
    finally:
        monkeypatch.delenv('TASKS_DIR', raising=False)
        importlib.reload(bot_module)


def test_module_imports_without_token(monkeypatch):
    """Модуль должен импортироваться без токена — токен нужен только main()."""
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    import importlib
    importlib.reload(bot_module)
    with pytest.raises(ValueError):
        bot_module.main()


class FakeBot:
    def __init__(self):
        self.photos = []
        self.messages = []
        self.answered = []

    def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.photos.append((chat_id, photo, caption))

    def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text))

    def answer_callback_query(self, call_id):
        self.answered.append(call_id)


def test_send_card_falls_back_to_message_when_image_missing():
    """Раньше send_photo(photo='') означал, что пользователь не получал ничего."""
    fake = FakeBot()
    bot_module.send_card(fake, 1, 'текст этапа', '', [('Дальше', 'final')])
    assert fake.photos == []
    assert fake.messages == [(1, 'текст этапа')]


def test_send_card_splits_when_caption_exceeds_telegram_limit():
    fake = FakeBot()
    long_text = 'я' * (bot_module.MAX_CAPTION_LEN + 1)
    bot_module.send_card(fake, 1, long_text, 'https://example.com/p.jpg', [])
    assert fake.photos == [(1, 'https://example.com/p.jpg', None)]
    assert fake.messages == [(1, long_text)]


def test_send_stage_clears_state_at_end_of_quest(tasks_dir):
    fake = FakeBot()
    bot_module.user_states[42] = 'nowhere'
    bot_module.send_stage(fake, 42, 'nowhere')
    assert 42 not in bot_module.user_states
    assert 'Конец квеста' in fake.messages[0][1]


# ---------------------------------------------------------------------------
# Тесты обработчиков build_bot(): раньше вся маршрутизация ('end', 'wrong_',
# обычный переход между этапами) жила только внутри вложенных функций и не
# тестировалась вовсе — а именно там были оба бага из round 1 (KeyError на
# повторном 'end' и IndexError на разборе 'wrong_'). Тесты ниже вызывают
# реально зарегистрированные обработчики напрямую, подменяя только сетевые
# методы TeleBot, поэтому ловят регрессию в самой логике маршрутизации, а не
# только в вынесенных из неё вспомогательных функциях.


class RecordingBot:
    def __init__(self, answer_should_raise=False):
        self.photos = []
        self.messages = []
        self.answered = []
        self._answer_should_raise = answer_should_raise

    def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.photos.append((chat_id, photo, caption, reply_markup))

    def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))

    def answer_callback_query(self, call_id):
        if self._answer_should_raise:
            raise RuntimeError('Telegram недоступен')
        self.answered.append(call_id)


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeMessage:
    def __init__(self, chat_id):
        self.chat = FakeChat(chat_id)


class FakeCall:
    def __init__(self, chat_id, data, call_id='cb-1'):
        self.message = FakeMessage(chat_id)
        self.data = data
        self.id = call_id


def button_targets(markup):
    """callback_data всех кнопок клавиатуры, вне зависимости от раскладки по рядам."""
    if markup is None:
        return []
    return [button.callback_data for row in markup.keyboard for button in row]


def _build_dispatch_bot(tmp_path, monkeypatch, answer_should_raise=False):
    directory = tmp_path / 'tasks'
    directory.mkdir()
    (directory / 'intro.txt').write_text(
        'Текст: Привет.\n\nImage: https://example.com/i.jpg\n\n'
        'Buttons:\nДальше: next_stage\nОшибка: wrong_next_stage\n',
        encoding='utf-8',
    )
    (directory / 'next_stage.txt').write_text(
        'Текст: Продолжение.\n\nImage: https://example.com/i2.jpg\n\n'
        'Buttons:\nЗавершить: end\n',
        encoding='utf-8',
    )
    (directory / 'wrong_next_stage.txt').write_text(
        'Текст: Разбор ошибки.\n\nImage: https://example.com/i3.jpg\n\n'
        'Buttons:\nПродолжить: next_stage\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(bot_module, 'TASKS_DIR', str(directory))

    built = bot_module.build_bot('fake-token')
    fake = RecordingBot(answer_should_raise=answer_should_raise)
    built.send_message = fake.send_message
    built.send_photo = fake.send_photo
    built.answer_callback_query = fake.answer_callback_query

    start = built.message_handlers[0]['function']
    callback = built.callback_query_handlers[0]['function']
    return start, callback, fake


@pytest.fixture
def dispatch_bot(tmp_path, monkeypatch):
    start, callback, fake = _build_dispatch_bot(tmp_path, monkeypatch)
    yield start, callback, fake
    bot_module.user_states.clear()


def test_start_handler_sets_intro_state_and_sends_card(dispatch_bot):
    start, _callback, fake = dispatch_bot
    start(FakeMessage(1))
    assert bot_module.user_states[1] == 'intro'
    assert len(fake.photos) == 1
    chat_id, photo, caption, markup = fake.photos[0]
    assert (chat_id, photo, caption) == (1, 'https://example.com/i.jpg', 'Привет.')
    assert sorted(button_targets(markup)) == ['next_stage', 'wrong_next_stage']


def test_callback_handler_navigates_and_updates_state(dispatch_bot):
    _start, callback, fake = dispatch_bot
    callback(FakeCall(1, 'next_stage', call_id='cb-42'))
    assert bot_module.user_states[1] == 'next_stage'
    assert fake.answered == ['cb-42']
    assert fake.photos[-1][:3] == (1, 'https://example.com/i2.jpg', 'Продолжение.')


def test_callback_handler_end_pops_state_and_survives_repeated_clicks(dispatch_bot):
    """Регрессия на баг round 1: было ``del user_states[chat_id]``, второй клик
    на 'end' (или клик без предшествующего /start) ронял обработчик с KeyError."""
    _start, callback, fake = dispatch_bot
    callback(FakeCall(99, 'end'))
    callback(FakeCall(99, 'end'))
    assert 99 not in bot_module.user_states
    assert len(fake.messages) == 2
    assert all('Квест завершён' in text for _cid, text, _markup in fake.messages)


def test_callback_handler_wrong_prefix_shows_explanation_without_touching_state(dispatch_bot):
    _start, callback, fake = dispatch_bot
    bot_module.user_states[5] = 'intro'
    callback(FakeCall(5, 'wrong_next_stage'))
    assert fake.photos[-1][:3] == (5, 'https://example.com/i3.jpg', 'Разбор ошибки.')
    # 'wrong_' — это разбор ответа, а не новый этап: текущее состояние не меняется.
    assert bot_module.user_states[5] == 'intro'


def test_callback_handler_wrong_prefix_falls_back_to_current_stage_when_missing(dispatch_bot):
    _start, callback, fake = dispatch_bot
    bot_module.user_states[7] = 'intro'
    callback(FakeCall(7, 'wrong_does_not_exist'))
    chat_id, text, markup = fake.messages[-1]
    assert chat_id == 7
    assert 'Неправильно' in text
    assert button_targets(markup) == ['intro']


def test_callback_handler_wrong_prefix_falls_back_without_crashing_when_no_state(dispatch_bot):
    """Спуфленный callback_data 'wrong_...' без предшествующего /start — не должен падать."""
    _start, callback, fake = dispatch_bot
    callback(FakeCall(8, 'wrong_does_not_exist'))
    chat_id, text, markup = fake.messages[-1]
    assert chat_id == 8
    assert 'Неправильно' in text
    assert button_targets(markup) == []


def test_callback_handler_delivers_card_even_if_answering_callback_fails(tmp_path, monkeypatch):
    """answer_callback_query обёрнут в try/except — сбой ответа на клик не должен
    мешать отправке самой карточки этапа."""
    _start, callback, fake = _build_dispatch_bot(tmp_path, monkeypatch, answer_should_raise=True)
    try:
        callback(FakeCall(3, 'next_stage'))
        assert fake.answered == []
        assert fake.photos[-1][:3] == (3, 'https://example.com/i2.jpg', 'Продолжение.')
    finally:
        bot_module.user_states.clear()
