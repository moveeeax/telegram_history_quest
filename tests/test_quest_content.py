"""Проверки целостности самого квеста: все ссылки ведут куда-то, все этапы достижимы.

Именно этот набор ловит реальную регрессию: восемь из шестнадцати файлов
в tasks/ были недостижимы из intro, потому что kill.txt вёл сразу в final.
"""

import os

import pytest

import bot as bot_module


TASKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tasks')


def load_all_stages():
    stages = {}
    for filename in sorted(os.listdir(TASKS_DIR)):
        if not filename.endswith('.txt'):
            continue
        with open(os.path.join(TASKS_DIR, filename), 'r', encoding='utf-8') as handle:
            stages[filename[:-4]] = bot_module.parse_task(handle)
    return stages


STAGES = load_all_stages()


def targets(buttons):
    """Возвращает имена этапов, в которые ведут кнопки (кроме 'end')."""
    return [callback for _text, callback in buttons if callback != 'end']


def test_tasks_directory_is_not_empty():
    assert STAGES, 'в tasks/ не найдено ни одного .txt файла'


def test_intro_stage_exists():
    assert 'intro' in STAGES, '/start отправляет пользователя на этап intro'


@pytest.mark.parametrize('name', sorted(STAGES))
def test_stage_has_text_and_image(name):
    text, image, _buttons = STAGES[name]
    assert text, f'{name}.txt: отсутствует секция "Текст:"'
    assert image.startswith('http'), f'{name}.txt: секция "Image:" должна быть ссылкой'


@pytest.mark.parametrize('name', sorted(STAGES))
def test_stage_caption_fits_telegram_limit(name):
    text = STAGES[name][0]
    assert len(text) <= bot_module.MAX_CAPTION_LEN, (
        f'{name}.txt: текст {len(text)} символов, Telegram обрежет подпись '
        f'длиннее {bot_module.MAX_CAPTION_LEN}'
    )


@pytest.mark.parametrize('name', sorted(STAGES))
def test_stage_name_is_accepted_by_the_bot(name):
    assert bot_module.STAGE_NAME_RE.match(name), (
        f'{name}.txt: имя этапа не проходит валидацию callback_data'
    )


@pytest.mark.parametrize('name', sorted(STAGES))
def test_every_button_points_at_an_existing_stage(name):
    _text, _image, buttons = STAGES[name]
    assert buttons, f'{name}.txt: нет ни одной кнопки, квест упрётся в тупик'
    for target in targets(buttons):
        assert target in STAGES, f'{name}.txt: кнопка ведёт в несуществующий этап {target!r}'


def test_every_stage_is_reachable_from_intro():
    seen = set()
    stack = ['intro']
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(targets(STAGES[name][2]))

    unreachable = sorted(set(STAGES) - seen)
    assert not unreachable, (
        'этапы недостижимы из intro и никогда не покажутся игроку: '
        + ', '.join(unreachable)
    )


def test_quest_can_be_finished():
    """Из intro должен существовать путь до кнопки с callback 'end'."""
    seen = set()
    stack = ['intro']
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        if any(callback == 'end' for _t, callback in STAGES[name][2]):
            return
        stack.extend(targets(STAGES[name][2]))
    pytest.fail("из intro нельзя дойти до кнопки 'end' — квест не завершается")
