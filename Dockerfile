# Используем официальный образ Python 3.11
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код проекта
COPY . .

# Создаем пользователя для безопасности
RUN useradd --create-home --shell /bin/bash bot
RUN chown -R bot:bot /app
USER bot

# Запускаем бота
CMD ["python", "bot.py"]