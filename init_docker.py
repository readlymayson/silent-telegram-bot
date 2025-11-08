#!/usr/bin/env python3
"""
Скрипт инициализации для Docker контейнера
"""

import os
import sys
import subprocess
from pathlib import Path

def init_docker():
    """Инициализация Docker контейнера"""
    print("🔧 Инициализация Docker контейнера...")
    
    # Создаем необходимые директории
    directories = [
        '/app/data',
        '/app/logs', 
        '/app/sessions',
        '/app/config'
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
        print(f"✅ Создана директория: {directory}")
    
    # Устанавливаем правильные права доступа
    try:
        # Устанавливаем права на директории
        subprocess.run(['chmod', '-R', '755', '/app'], check=True)
        
        # Устанавливаем владельца на botuser (если пользователь существует)
        try:
            subprocess.run(['chown', '-R', 'botuser:botuser', '/app'], check=True)
            print("✅ Права доступа установлены для botuser")
        except subprocess.CalledProcessError:
            # Если botuser не существует, устанавливаем права для всех
            subprocess.run(['chmod', '-R', '777', '/app/logs'], check=True)
            subprocess.run(['chmod', '-R', '777', '/app/data'], check=True)
            print("✅ Права доступа установлены для всех пользователей")
            
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Не удалось установить права доступа: {e}")
        # Пытаемся установить права только на критичные директории
        try:
            subprocess.run(['chmod', '777', '/app/logs'], check=True)
            subprocess.run(['chmod', '777', '/app/data'], check=True)
            print("✅ Критичные права доступа установлены")
        except subprocess.CalledProcessError:
            print("❌ Не удалось установить права доступа")
    
    # Проверяем наличие необходимых файлов
    required_files = [
        'user_bot.py',
        'config.py',
        'requirements.txt'
    ]
    
    missing_files = []
    for file in required_files:
        if not os.path.exists(file):
            missing_files.append(file)
    
    if missing_files:
        print(f"❌ Отсутствуют файлы: {', '.join(missing_files)}")
        return False
    
    print("✅ Все необходимые файлы найдены")
    
    # Проверяем Python зависимости
    try:
        import telethon
        import dotenv
        print("✅ Python зависимости установлены")
    except ImportError as e:
        print(f"❌ Отсутствует зависимость: {e}")
        return False
    
    print("🎉 Инициализация Docker контейнера завершена успешно!")
    return True

if __name__ == "__main__":
    success = init_docker()
    sys.exit(0 if success else 1)
