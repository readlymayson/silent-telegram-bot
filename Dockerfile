# Используем официальный образ Python
FROM python:3.9-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Создаем пользователя для безопасности
RUN useradd -m -u 1000 botuser

# Создаем директории для данных, логов и сессий с правильными правами
RUN mkdir -p /app/data /app/logs /app/sessions && \
    chown -R botuser:botuser /app && \
    chmod -R 755 /app && \
    chmod -R 777 /app/logs && \
    chmod -R 777 /app/data && \
    chmod -R 777 /app/sessions

# Создаем пустые файлы данных если их нет
RUN touch /app/data/users_data.json /app/data/applications_data.json /app/data/applications.json && \
    chown botuser:botuser /app/data/users_data.json /app/data/applications_data.json /app/data/applications.json && \
    chmod 666 /app/data/users_data.json /app/data/applications_data.json /app/data/applications.json

# Запускаем инициализацию
RUN python init_docker.py

USER botuser

# Открываем порт (если понадобится в будущем)
EXPOSE 8080

# Команда по умолчанию
CMD ["python", "user_bot.py"] 