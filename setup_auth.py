#!/usr/bin/env python3
"""
Скрипт для настройки авторизации Telegram UserBot
Создает отдельные авторизации для user_bot и admin_panel
"""

import asyncio
import os
import shutil
from telethon import TelegramClient
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

async def setup_user_bot_auth():
    """Настройка авторизации для user_bot"""
    print("🔐 Настройка авторизации для UserBot")
    print("=" * 50)
    
    # Получаем данные из .env
    api_id = os.getenv('API_ID')
    api_hash = os.getenv('API_HASH')
    phone_number = os.getenv('PHONE_NUMBER')
    
    if not all([api_id, api_hash, phone_number]):
        print("❌ Ошибка: Не все переменные окружения заполнены!")
        return False
    
    print(f"📱 Номер телефона: {phone_number}")
    print(f"🔑 API ID: {api_id}")
    print(f"🔐 API Hash: {api_hash[:10]}...")
    
    # Создаем клиент для user_bot
    client = TelegramClient('user_bot_session', api_id, api_hash)
    
    try:
        print("\n🔗 Подключение к Telegram для UserBot...")
        await client.connect()
        
        if not await client.is_user_authorized():
            print("\n📲 Отправка кода подтверждения для UserBot...")
            await client.send_code_request(phone_number)
            
            # Попытки ввода кода
            max_attempts = 3
            for attempt in range(max_attempts):
                if attempt > 0:
                    print(f"\n🔄 Попытка {attempt + 1} из {max_attempts}")
                    print("📲 Повторная отправка кода подтверждения...")
                    await client.send_code_request(phone_number)
                    print("⏳ Ожидание 5 секунд...")
                    await asyncio.sleep(5)
                
                print(f"\n📝 Введите код для UserBot (попытка {attempt + 1}):")
                code = input("Код: ").strip()
                
                try:
                    await client.sign_in(phone_number, code)
                    print("✅ Авторизация UserBot успешна!")
                    break
                except Exception as e:
                    if "password" in str(e).lower():
                        print("\n🔒 Требуется пароль двухфакторной аутентификации:")
                        password = input("Пароль: ")
                        await client.sign_in(password=password)
                        print("✅ Авторизация UserBot с 2FA успешна!")
                        break
                    elif "phone code" in str(e).lower() and attempt < max_attempts - 1:
                        print(f"❌ Неверный код: {e}")
                        print("Попробуйте еще раз...")
                        continue
                    else:
                        print(f"❌ Ошибка авторизации UserBot: {e}")
                        return False
            else:
                print("❌ Превышено количество попыток ввода кода")
                return False
        else:
            print("✅ UserBot уже авторизован!")
        
        # Получаем информацию о пользователе
        me = await client.get_me()
        print(f"\n👤 UserBot авторизован как: {me.first_name} {me.last_name or ''}")
        print(f"📱 Username: @{me.username or 'Не указан'}")
        print(f"🆔 ID: {me.id}")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка подключения UserBot: {e}")
        return False
    finally:
        await client.disconnect()

async def setup_admin_panel_auth():
    """Настройка авторизации для admin_panel (отключено - теперь интегрировано в user_bot)"""
    print("\n🔐 Настройка авторизации для AdminPanel")
    print("=" * 50)
    print("ℹ️ AdminPanel теперь интегрирован в UserBot")
    print("✅ Отдельная авторизация не требуется")
    return True

async def verify_separate_sessions():
    """Проверка сессии user_bot"""
    print("\n🔍 Проверка сессии user_bot...")
    print("=" * 50)
    
    # Получаем данные из .env
    api_id = os.getenv('API_ID')
    api_hash = os.getenv('API_HASH')
    
    # Проверяем user_bot сессию
    print("🔍 Проверка user_bot_session...")
    user_bot_client = TelegramClient('user_bot_session', api_id, api_hash)
    
    try:
        await user_bot_client.connect()
        if await user_bot_client.is_user_authorized():
            me = await user_bot_client.get_me()
            print(f"✅ user_bot_session: {me.first_name} {me.last_name or ''}")
            return True
        else:
            print("❌ user_bot_session не авторизован")
            return False
    except Exception as e:
        print(f"❌ Ошибка проверки user_bot_session: {e}")
        return False
    finally:
        await user_bot_client.disconnect()

def main():
    """Главная функция"""
    print("🚀 Настройка авторизации Telegram UserBot")
    print("=" * 50)
    print("💡 Создается авторизация для user_bot (admin_panel интегрирован)")
    print("")
    
    # Проверяем наличие .env файла
    if not os.path.exists('.env'):
        print("❌ Файл .env не найден!")
        print("Создайте файл .env с вашими данными:")
        print("API_ID=your_api_id")
        print("API_HASH=your_api_hash")
        print("PHONE_NUMBER=+79001234567")
        return
    
    # Удаляем старые файлы сессий
    print("🗑️ Удаление старых файлов сессий...")
    old_files = [
        'silent_bot_session.session',
        'user_bot_session.session'
    ]
    
    for file in old_files:
        if os.path.exists(file):
            os.remove(file)
            print(f"✅ Удален: {file}")
    
    # Настраиваем авторизацию для user_bot
    if asyncio.run(setup_user_bot_auth()):
        print("\n✅ Авторизация UserBot настроена!")
        
        # Проверяем сессию
        if asyncio.run(verify_separate_sessions()):
            print("\n✅ Сессия проверена и работает!")
            print("\n🎉 Настройка завершена успешно!")
            print("\n📄 Созданные файлы сессий:")
            print("   - user_bot_session.session (для user_bot с интегрированной админ панелью)")
            print("\n💡 Следующие шаги:")
            print("   1. Запустите: ./manage.sh")
            print("   2. Выберите пункт 5 - Запустить контейнеры")
            print("   3. Проверьте логи: пункт 10")
            print("   4. Напишите боту в Telegram")
            print("   5. Используйте /admin для активации админ панели")
        else:
            print("\n❌ Ошибка проверки сессии")
    else:
        print("\n❌ Ошибка настройки авторизации UserBot")

if __name__ == "__main__":
    main()
