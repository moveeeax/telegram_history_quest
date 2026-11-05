# Используем официальный образ Python 3.15
FROM python:3.15-slim

# Логи должны сразу уходить в docker logs, а не залипать в буфере —
# stdout это единственный канал наблюдения за ботом.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Создаём непривилегированного пользователя до копирования кода
RUN useradd --create-home --shell /bin/bash bot

# Копируем исходный код проекта (что не попадёт в образ — см. .dockerignore)
COPY --chown=bot:bot . .

USER bot

# Запускаем бота
CMD ["python", "bot.py"]
