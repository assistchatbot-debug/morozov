#!/bin/bash
set -e

echo "🚀 Начало развёртывания 1C-Bitrix24 Integration..."

cd /root/morozov

# Проверка .env
if [ ! -f ".env" ]; then
    echo "❌ Файл .env не найден!"
    exit 1
fi

echo "✅ Файл .env найден"

# Остановка старых контейнеров
echo "🛑 Остановка старых контейнеров..."
docker compose down 2>/dev/null || true

# Сборка образа
echo "🔨 Сборка Docker образа..."
docker compose build

# Создание директории для логов
mkdir -p logs

# Запуск контейнера
echo "▶️ Запуск middleware..."
docker compose up -d

# Проверка
sleep 5
if docker compose ps | grep -q "Up"; then
    echo "✅ Middleware успешно запущен!"
    echo "📊 Статус:"
    docker compose ps
    echo ""
    echo "🌐 API: http://178.128.20.100:8000"
else
    echo "❌ Ошибка запуска"
    docker compose logs
    exit 1
fi

echo ""
echo "🎉 Развёртывание завершено!"
