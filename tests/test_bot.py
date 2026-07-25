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
