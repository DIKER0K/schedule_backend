# Используем официальный Python образ
FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements.txt и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Создаем пользователя для безопасности
RUN useradd --create-home --shell /bin/bash app

# Создаем пустой файл group_shifts.json с правильными правами ПОСЛЕ создания пользователя
RUN rm -rf group_shifts.json && \
    touch group_shifts.json && \
    chmod 644 group_shifts.json && \
    chown app:app group_shifts.json

# Меняем владельца всех файлов на пользователя app
RUN chown -R app:app /app

USER app

# Открываем порт
EXPOSE 3020

# Команда запуска
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3020"]