import asyncio
import logging
import os
import json
import signal
import sys
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.types import User
from typing import Dict, Optional
import re

from config import API_ID, API_HASH, PHONE_NUMBER, SESSION_NAME, BOT_NAME
from config import QUESTIONS, FINAL_MESSAGE, GREETING_MESSAGE, GREETING_VIDEO_PATH, PHONE_QUESTION_VIDEO_PATH, BITRIX24_WEBHOOK_URL
from bitrix24_integration import Bitrix24Integration

# Настройка логирования с датой и временем
from logger_config import user_bot_logger as logger

class SilentUserBot:
    def __init__(self):
        # Используем отдельный файл сессии для user_bot
        self.client = TelegramClient("user_bot_session", API_ID, API_HASH)
        
        # Интеграция с Bitrix24
        if BITRIX24_WEBHOOK_URL:
            self.bitrix = Bitrix24Integration(BITRIX24_WEBHOOK_URL)
            logger.info("✅ Интеграция с Bitrix24 включена")
        else:
            self.bitrix = None
            logger.warning("⚠️ Интеграция с Bitrix24 не настроена (BITRIX24_WEBHOOK_URL не указан)")
        
        # Состояния пользователей
        self.user_states = {}  # Состояния пользователей
        self.user_answers = {}  # Ответы пользователей
        
        # Админ панель
        # Фиксированный список администраторов (username без @)
        # Для изменения списка администраторов отредактируйте этот блок:
        self.admin_usernames = {
            'readlymayson',  # Основной администратор
            'inkiselev',         # Дополнительный администратор
            # Добавьте сюда других администраторов по необходимости 
            # Пример: 'username1', 'username2'
        }
        self.admin_users = set()  # Кэш ID администраторов
        self.admin_mode = False  # Режим админ панели
        self.active_admin_user = None  # ID пользователя, который активировал админ панель
        
        # Создаем директорию для заявок если её нет
        os.makedirs('data', exist_ok=True)

        # Напоминания
        self.reminder_tasks = {} # Словарь для хранения активных задач напоминаний
        self.last_message_times = {} # Словарь для хранения времени последнего сообщения от пользователя
        self.survey_reminder_sent = {} # Словарь для отслеживания отправленных напоминаний в опроснике
        self.scheduled_reminders = {} # Словарь для хранения запланированных напоминаний (для восстановления)
        
        # Система активации бота по ключевым словам
        self.user_message_counts = {}  # Счетчик сообщений для каждого пользователя
        self.activated_users = set()  # Пользователи, для которых бот активирован
        self.expired_users = set()  # Пользователи, которые превысили лимит в 5 сообщений
        self.deactivated_users = set()  # Пользователи, которые деактивированы после заполнения заявки
        self.trigger_keywords = {'хочу', 'консультацию', 'консультация'}  # Ключевые слова для активации
        
        # Файл для сохранения данных пользователей
        self.users_data_file = 'data/users_data.json'
        self.applications_data_file = 'data/applications_data.json'
        
        # Загружаем сохраненные данные при инициализации
        self.load_users_data()
        self.load_applications_data()
    
    def validate_phone_number(self, phone_text: str) -> tuple[bool, str, str]:
        """
        Валидирует номер телефона
        
        Args:
            phone_text: Текст с номером телефона
            
        Returns:
            tuple: (is_valid, clean_phone, error_message)
        """
        try:
            # Удаляем все пробелы, скобки, дефисы и другие символы
            clean_phone = re.sub(r'[^\d+]', '', phone_text)
            
            # Проверяем различные форматы российских номеров
            patterns = [
                r'^\+7\d{10}$',  # +7XXXXXXXXXX
                r'^8\d{10}$',    # 8XXXXXXXXXX
                r'^7\d{10}$',    # 7XXXXXXXXXX
                r'^\d{10}$',     # XXXXXXXXXX (без кода страны)
                r'^\d{11}$',     # XXXXXXXXXXX (с кодом страны)
            ]
            
            # Проверяем каждый паттерн
            for pattern in patterns:
                if re.match(pattern, clean_phone):
                    # Нормализуем номер к формату +7XXXXXXXXXX
                    if clean_phone.startswith('8'):
                        normalized_phone = '+7' + clean_phone[1:]
                    elif clean_phone.startswith('7') and len(clean_phone) == 11:
                        normalized_phone = '+' + clean_phone
                    elif len(clean_phone) == 10:
                        normalized_phone = '+7' + clean_phone
                    elif clean_phone.startswith('+7'):
                        normalized_phone = clean_phone
                    else:
                        normalized_phone = '+7' + clean_phone[-10:]
                    
                    # Проверяем, что номер действительно российский
                    if normalized_phone.startswith('+7') and len(normalized_phone) == 12:
                        return True, normalized_phone, ""
            
            return False, "", "Неверный формат номера телефона. Используйте формат: +7XXXXXXXXXX или 8XXXXXXXXXX"
            
        except Exception as e:
            logger.error(f"Ошибка при валидации номера телефона: {e}")
            return False, "", "Ошибка при проверке номера телефона"
    
    def extract_phone_from_text(self, text: str) -> tuple[bool, str, str]:
        """
        Извлекает номер телефона из текста
        
        Args:
            text: Текст сообщения
            
        Returns:
            tuple: (found, phone_number, error_message)
        """
        try:
            # Паттерны для поиска номера телефона в тексте
            patterns = [
                r'(\+7|8|7)?[\s\-\(]?(\d{3})[\s\-\)]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})',  # Основной паттерн
                r'(\+7|8|7)?[\s\-\(]?(\d{4})[\s\-\)]?(\d{2})[\s\-]?(\d{2})[\s\-]?(\d{2})',  # Альтернативный формат
                r'(\d{3})[\s\-]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})',  # Простой формат
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, text)
                for match in matches:
                    phone_part = match.group(0)
                    # Валидируем найденный номер
                    is_valid, clean_phone, error_msg = self.validate_phone_number(phone_part)
                    if is_valid:
                        return True, clean_phone, ""
            
            return False, "", "Номер телефона не найден в сообщении"
            
        except Exception as e:
            logger.error(f"Ошибка при извлечении номера телефона: {e}")
            return False, "", "Ошибка при поиске номера телефона"
    
    def signal_handler(self, signum, frame):
        """Обработчик сигналов для корректного завершения"""
        logger.info("Получен сигнал завершения, очищаем ресурсы...")
        sys.exit(0)
        
    def is_admin_panel_running(self):
        """Проверяет, запущена ли админ панель"""
        return self.admin_mode
    
    def get_active_admin_user(self):
        """Получает ID пользователя, который активировал админ панель"""
        return self.active_admin_user
    
    def is_user_admin(self, user_id, username=None):
        """Проверяет, является ли пользователь администратором"""
        # Сначала проверяем кэш
        if user_id in self.admin_users:
            return True
        
        # Если username передан, проверяем его
        if username and username.lower() in self.admin_usernames:
            self.admin_users.add(user_id)  # Добавляем в кэш
            return True
        
        return False
    
    def is_user_blocked(self, user_id):
        """Проверяет, заблокирован ли конкретный пользователь"""
        logger.info(f"🔍 Проверка блокировки для пользователя {user_id}")
        
        # Проверяем, запущена ли админ панель
        admin_running = self.is_admin_panel_running()
        logger.info(f"🔍 Админ панель запущена: {admin_running}")
        
        if not admin_running:
            logger.info(f"🔓 Админ панель не запущена, пользователь {user_id} не заблокирован")
            return False
        
        # Получаем активного администратора
        active_admin_user = self.get_active_admin_user()
        logger.info(f"🔍 Активный администратор: {active_admin_user}")
        
        if active_admin_user and active_admin_user == user_id:
            logger.info(f"🔒 Пользователь {user_id} является активным администратором, заблокирован")
            return True
        
        logger.info(f"🔓 Пользователь {user_id} не заблокирован")
        return False
    
    def clear_user_states(self):
        """Очищает состояния всех пользователей"""
        try:
            # Отменяем все активные напоминания
            for user_id in list(self.reminder_tasks.keys()):
                self.cancel_reminder(user_id)
            
            self.user_states.clear()
            self.user_answers.clear()
            self.last_message_times.clear()
            self.survey_reminder_sent.clear()
            self.user_message_counts.clear()
            self.activated_users.clear()
            self.expired_users.clear()
            self.deactivated_users.clear()
            self.scheduled_reminders.clear()
            logger.info("🧹 Состояния пользователей, напоминания, запланированные напоминания, деактивированные пользователи и данные опросника очищены")
            
            # Сохраняем очищенные данные
            self.save_users_data()
        except Exception as e:
            logger.error(f"Ошибка при очистке состояний пользователей: {e}")
    
    def clear_specific_user_state(self, user_id):
        """Очищает состояние конкретного пользователя"""
        try:
            # Отменяем напоминания для этого пользователя
            self.cancel_reminder(user_id)
            
            if user_id in self.user_states:
                del self.user_states[user_id]
                logger.info(f"🧹 Состояние пользователя {user_id} очищено")
            if user_id in self.user_answers:
                del self.user_answers[user_id]
                logger.info(f"🧹 Ответы пользователя {user_id} очищены")
            if user_id in self.last_message_times:
                del self.last_message_times[user_id]
                logger.info(f"🧹 Время последнего сообщения пользователя {user_id} очищено")
            if user_id in self.survey_reminder_sent:
                del self.survey_reminder_sent[user_id]
                logger.info(f"🧹 Данные о напоминании в опроснике пользователя {user_id} очищены")
            if user_id in self.user_message_counts:
                del self.user_message_counts[user_id]
                logger.info(f"🧹 Счетчик сообщений пользователя {user_id} очищен")
            if user_id in self.activated_users:
                self.activated_users.remove(user_id)
                logger.info(f"🧹 Статус активации пользователя {user_id} сброшен")
            if user_id in self.expired_users:
                self.expired_users.remove(user_id)
                logger.info(f"🧹 Статус истечения пользователя {user_id} сброшен")
            if user_id in self.deactivated_users:
                self.deactivated_users.remove(user_id)
                logger.info(f"🧹 Статус деактивации пользователя {user_id} сброшен")
            # Сохраняем обновленные данные
            self.save_users_data()
        except Exception as e:
            logger.error(f"Ошибка при очистке состояния пользователя {user_id}: {e}")
    
    async def send_greeting_video(self, chat_id, user_id):
        """Отправляет приветственное видео"""
        try:
            if not os.path.exists(GREETING_VIDEO_PATH):
                logger.warning(f"⚠️ Видеофайл {GREETING_VIDEO_PATH} не найден")
                return False
            
            # Проверяем размер файла
            file_size = os.path.getsize(GREETING_VIDEO_PATH)
            logger.info(f"📏 Размер видеофайла: {file_size} байт")
            
            if file_size > 50 * 1024 * 1024:  # 50 МБ
                logger.warning(f"⚠️ Видеофайл слишком большой: {file_size} байт")
                return False
            
            # Проверяем расширение файла
            if not GREETING_VIDEO_PATH.lower().endswith('.mp4'):
                logger.warning(f"⚠️ Неподдерживаемый формат файла: {GREETING_VIDEO_PATH}")
                return False
            
            # Отправляем видео как кружок (видеосообщение)
            logger.info(f"📤 Отправка видеофайла как кружок: {GREETING_VIDEO_PATH}")
            await self.client.send_file(
                entity=chat_id,
                file=GREETING_VIDEO_PATH,
                video_note=True,  # Это делает видео кружком
                supports_streaming=True
            )
            
            logger.info(f"✅ Приветственное видео-кружок отправлен пользователю {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке видео: {e}")
            
            # Попробуем альтернативный способ
            try:
                logger.info("🔄 Попытка альтернативной отправки видео как кружок...")
                await self.client.send_file(
                    entity=chat_id,
                    file=GREETING_VIDEO_PATH,
                    video_note=True
                )
                logger.info("✅ Видео отправлено альтернативным способом как кружок")
                return True
            except Exception as e2:
                logger.error(f"❌ Альтернативная отправка тоже не удалась: {e2}")
                return False
    
    async def send_phone_question_video(self, chat_id, user_id):
        """Отправляет видео с запросом номера телефона"""
        try:
            if not os.path.exists(PHONE_QUESTION_VIDEO_PATH):
                logger.warning(f"⚠️ Видеофайл {PHONE_QUESTION_VIDEO_PATH} не найден")
                return False
            
            # Проверяем размер файла
            file_size = os.path.getsize(PHONE_QUESTION_VIDEO_PATH)
            logger.info(f"📏 Размер видеофайла запроса телефона: {file_size} байт")
            
            if file_size > 50 * 1024 * 1024:  # 50 МБ
                logger.warning(f"⚠️ Видеофайл запроса телефона слишком большой: {file_size} байт")
                return False
            
            # Проверяем расширение файла
            if not PHONE_QUESTION_VIDEO_PATH.lower().endswith('.mp4'):
                logger.warning(f"⚠️ Неподдерживаемый формат файла: {PHONE_QUESTION_VIDEO_PATH}")
                return False
            
            # Отправляем видео как кружок (видеосообщение)
            logger.info(f"📤 Отправка видеофайла запроса телефона как кружок: {PHONE_QUESTION_VIDEO_PATH}")
            await self.client.send_file(
                entity=chat_id,
                file=PHONE_QUESTION_VIDEO_PATH,
                video_note=True,  # Это делает видео кружком
                supports_streaming=True
            )
            
            logger.info(f"✅ Видео-кружок запроса телефона отправлен пользователю {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке видео запроса телефона: {e}")
            
            # Попробуем альтернативный способ
            try:
                logger.info("🔄 Попытка альтернативной отправки видео запроса телефона как кружок...")
                await self.client.send_file(
                    entity=chat_id,
                    file=PHONE_QUESTION_VIDEO_PATH,
                    video_note=True
                )
                logger.info("✅ Видео запроса телефона отправлено альтернативным способом как кружок")
                return True
            except Exception as e2:
                logger.error(f"❌ Альтернативная отправка видео запроса телефона тоже не удалась: {e2}")
                return False
    
    async def check_clear_signals(self):
        """Проверяет сигналы для очистки состояний"""
        while True:
            try:
                clear_file = "clear_user_states.flag"
                if os.path.exists(clear_file):
                    logger.info("🧹 Обнаружен сигнал очистки состояний")
                    self.clear_user_states()
                    os.remove(clear_file)
                    logger.info("✅ Сигнал очистки обработан")
            except Exception as e:
                logger.error(f"Ошибка при проверке сигналов очистки: {e}")
            
            await asyncio.sleep(5)  # Проверяем каждые 5 секунд
    
    # Методы админ панели
    async def activate_admin_mode(self, event):
        """Активация режима админ панели"""
        try:
            sender = await event.get_sender()
            if not isinstance(sender, User):
                await event.respond("❌ Ошибка: не удалось определить пользователя")
                return
            
            user_id = sender.id
            self.admin_mode = True
            self.active_admin_user = user_id
            await event.respond("🔓 Админ панель активирована! Основной бот заблокирован только для вас.")
            logger.info(f"Админ панель активирована пользователем {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при активации админ панели: {e}")
            await event.respond("❌ Ошибка при активации админ панели.")
    
    async def deactivate_admin_mode(self, event):
        """Деактивация режима админ панели"""
        try:
            sender = await event.get_sender()
            if not isinstance(sender, User):
                await event.respond("❌ Ошибка: не удалось определить пользователя")
                return
            
            user_id = sender.id
            
            # Проверяем, что деактивирует тот же пользователь, который активировал
            if self.active_admin_user and self.active_admin_user != user_id:
                await event.respond("❌ Только пользователь, который активировал админ панель, может её деактивировать.")
                return
            
            self.admin_mode = False
            self.active_admin_user = None
            
            # Очищаем состояния пользователей
            self.clear_user_states()
            
            await event.respond("🔒 Админ панель деактивирована! Основной бот разблокирован и состояния очищены.")
            logger.info(f"Админ панель деактивирована пользователем {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при деактивации админ панели: {e}")
            await event.respond("❌ Ошибка при деактивации админ панели.")
    
    async def show_help(self, event):
        """Показать справку по командам"""
        help_text = """
🤖 Панель администратора Silent UserBot

Основные команды:
/admin - Активировать админ панель (блокирует основной бот только для вас)
/stop - Деактивировать админ панель (разблокирует основной бот)
/status - Показать статус админ панели
/clear - Принудительно очистить состояния пользователей
/admins - Показать список администраторов

Команды в режиме админ панели:
/help - Показать эту справку
/applications - Показать данные заявок (ID, телефон, дата)

Команды для работы с лидами (если настроена интеграция с Bitrix24):
/leads - Показать все лиды
/new - Показать новые лиды
/stats - Показать статистику лидов
/export - Экспорт лидов в JSON

📝 Примечание: 
- При активации админ панели основной бот блокируется только для вас
- Другие пользователи могут продолжать общаться с ботом
- При деактивации админ панели состояния пользователей автоматически очищаются
- Доступ к админ панели имеют только авторизованные администраторы
"""
        await event.respond(help_text)
    
    async def show_admins(self, event):
        """Показать список администраторов"""
        try:
            admin_list = "👥 Список администраторов:\n"
            admin_list += "=" * 30 + "\n\n"
            
            for username in sorted(self.admin_usernames):
                admin_list += f"• @{username}\n"
            
            admin_list += f"\n📊 Всего администраторов: {len(self.admin_usernames)}"
            admin_list += "\n\n💡 Только эти пользователи могут использовать админ панель"
            
            await event.respond(admin_list)
            
        except Exception as e:
            logger.error(f"Ошибка при показе списка администраторов: {e}")
            await event.respond(f"❌ Ошибка при показе списка администраторов: {e}")
    
    async def show_activation_status(self, event):
        """Показать статус активации пользователей"""
        try:
            status_text = "🔑 Статус активации пользователей:\n"
            status_text += "=" * 40 + "\n\n"
            
            if not self.user_message_counts:
                status_text += "📭 Нет активных пользователей\n"
            else:
                # Группируем пользователей по статусу
                activated_users = []
                pending_users = []
                expired_users = []
                deactivated_users = []
                
                for user_id, message_count in self.user_message_counts.items():
                    is_activated = user_id in self.activated_users
                    is_expired = user_id in self.expired_users
                    is_deactivated = user_id in self.deactivated_users
                    
                    if is_deactivated:
                        deactivated_users.append((user_id, message_count))
                    elif is_activated:
                        activated_users.append((user_id, message_count))
                    elif is_expired:
                        expired_users.append((user_id, message_count))
                    else:
                        pending_users.append((user_id, message_count))
                
                # Показываем активированных пользователей
                if activated_users:
                    status_text += "✅ Активированные пользователи:\n"
                    for user_id, count in activated_users:
                        status_text += f"• ID: {user_id} (сообщений: {count})\n"
                    status_text += "\n"
                
                # Показываем ожидающих активации
                if pending_users:
                    status_text += "⏳ Ожидающие активации:\n"
                    for user_id, count in pending_users:
                        status_text += f"• ID: {user_id} (сообщений: {count}/5)\n"
                    status_text += "\n"
                
                # Показываем истекших
                if expired_users:
                    status_text += "❌ Истекшие (превысили лимит):\n"
                    for user_id, count in expired_users:
                        status_text += f"• ID: {user_id} (сообщений: {count})\n"
                    status_text += "\n"
                
                # Показываем деактивированных
                if deactivated_users:
                    status_text += "🔇 Деактивированные (заполнили заявку):\n"
                    for user_id, count in deactivated_users:
                        status_text += f"• ID: {user_id} (сообщений: {count})\n"
                    status_text += "\n"
                
                # Общая статистика
                status_text += f"📊 Общая статистика:\n"
                status_text += f"• Всего пользователей: {len(self.user_message_counts)}\n"
                status_text += f"• Активировано: {len(activated_users)}\n"
                status_text += f"• Ожидают активации: {len(pending_users)}\n"
                status_text += f"• Истекли: {len(expired_users)}\n"
                status_text += f"• Деактивированы: {len(deactivated_users)}\n"
            
            # Информация о ключевых словах
            status_text += f"\n🔑 Ключевые слова для активации:\n"
            status_text += f"• Обязательно: 'хочу'\n"
            status_text += f"• И одно из: 'консультацию' или 'консультация'\n"
            
            await event.respond(status_text)
            
        except Exception as e:
            logger.error(f"Ошибка при показе статуса активации: {e}")
            await event.respond(f"❌ Ошибка при показе статуса активации: {e}")
    
    async def show_applications_data(self, event):
        """Показать данные заявок (только ID, телефон и дата)"""
        try:
            if not self.applications_data:
                await event.respond("📭 Нет данных о заявках")
                return
            
            # Сортируем заявки по дате (новые сначала)
            sorted_applications = sorted(
                self.applications_data, 
                key=lambda x: x.get('application_date', ''), 
                reverse=True
            )
            
            status_text = "📋 Данные заявок:\n"
            status_text += "=" * 40 + "\n\n"
            
            for i, app in enumerate(sorted_applications[:20], 1):  # Показываем последние 20
                user_id = app.get('user_id', 'N/A')
                phone = app.get('phone_number', 'N/A')
                date = app.get('application_date', 'N/A')
                
                # Форматируем дату
                try:
                    if date != 'N/A':
                        dt = datetime.fromisoformat(date)
                        formatted_date = dt.strftime("%d.%m.%Y %H:%M")
                    else:
                        formatted_date = 'N/A'
                except:
                    formatted_date = date
                
                status_text += f"{i}. ID: {user_id} | 📱 {phone} | 📅 {formatted_date}\n"
            
            if len(sorted_applications) > 20:
                status_text += f"\n... и еще {len(sorted_applications) - 20} заявок"
            
            status_text += f"\n\n📊 Всего заявок: {len(sorted_applications)}"
            
            await event.respond(status_text)
            
        except Exception as e:
            logger.error(f"Ошибка при показе данных заявок: {e}")
            await event.respond(f"❌ Ошибка при показе данных заявок: {e}")
    
    async def show_applications(self, event):
        """Показать заявки (отключено)"""
        try:
            await event.respond("📝 Функция просмотра заявок отключена.\n\n💡 Для работы с заявками используйте:\n- /status - проверка статуса\n- /debug - диагностика системы")
        except Exception as e:
            logger.error(f"Ошибка при показе заявок: {e}")
            await event.respond(f"❌ Ошибка при показе заявок: {e}")
    
    async def show_leads(self, event):
        """Показать все лиды из Bitrix24"""
        try:
            if not self.bitrix:
                await event.respond("❌ Интеграция с Bitrix24 не настроена")
                return
            
            await event.respond("📊 Загружаю лиды из Bitrix24...")
            
            leads = await self.bitrix.get_leads()
            
            if not leads:
                await event.respond("📭 Лиды не найдены")
                return
            
            # Показываем последние 10 лидов
            recent_leads = leads[:10]
            
            response = "📋 Последние лиды:\n"
            response += "=" * 50 + "\n\n"
            
            for i, lead in enumerate(recent_leads, 1):
                lead_id = lead.get('ID', 'N/A')
                title = lead.get('TITLE', 'N/A')
                name = lead.get('NAME', 'N/A')
                last_name = lead.get('LAST_NAME', 'N/A')
                status = lead.get('STATUS_ID', 'N/A')
                date_create = lead.get('DATE_CREATE', 'N/A')
                
                # Форматируем дату
                try:
                    if date_create != 'N/A':
                        dt = datetime.fromisoformat(date_create.replace('Z', '+00:00'))
                        formatted_date = dt.strftime("%d.%m.%Y %H:%M")
                    else:
                        formatted_date = 'N/A'
                except:
                    formatted_date = date_create
                
                response += f"{i}. ID: {lead_id} | {name} {last_name}\n"
                response += f"   📝 {title}\n"
                response += f"   📊 Статус: {status} | 📅 {formatted_date}\n\n"
            
            if len(leads) > 10:
                response += f"... и еще {len(leads) - 10} лидов"
            
            response += f"\n📊 Всего лидов: {len(leads)}"
            
            await event.respond(response)
            
        except Exception as e:
            logger.error(f"Ошибка при показе лидов: {e}")
            await event.respond(f"❌ Ошибка при загрузке лидов: {e}")
    
    async def show_new_leads(self, event):
        """Показать новые лиды из Bitrix24"""
        try:
            if not self.bitrix:
                await event.respond("❌ Интеграция с Bitrix24 не настроена")
                return
            
            await event.respond("📊 Загружаю новые лиды из Bitrix24...")
            
            new_leads = await self.bitrix.get_new_leads()
            
            if not new_leads:
                await event.respond("📭 Новых лидов не найдено")
                return
            
            response = "🆕 Новые лиды:\n"
            response += "=" * 50 + "\n\n"
            
            for i, lead in enumerate(new_leads, 1):
                lead_id = lead.get('ID', 'N/A')
                title = lead.get('TITLE', 'N/A')
                name = lead.get('NAME', 'N/A')
                last_name = lead.get('LAST_NAME', 'N/A')
                date_create = lead.get('DATE_CREATE', 'N/A')
                
                # Форматируем дату
                try:
                    if date_create != 'N/A':
                        dt = datetime.fromisoformat(date_create.replace('Z', '+00:00'))
                        formatted_date = dt.strftime("%d.%m.%Y %H:%M")
                    else:
                        formatted_date = 'N/A'
                except:
                    formatted_date = date_create
                
                response += f"{i}. ID: {lead_id} | {name} {last_name}\n"
                response += f"   📝 {title}\n"
                response += f"   📅 {formatted_date}\n\n"
            
            response += f"📊 Всего новых лидов: {len(new_leads)}"
            
            await event.respond(response)
            
        except Exception as e:
            logger.error(f"Ошибка при показе новых лидов: {e}")
            await event.respond(f"❌ Ошибка при загрузке новых лидов: {e}")
    
    async def show_lead_statistics(self, event):
        """Показать статистику лидов из Bitrix24"""
        try:
            if not self.bitrix:
                await event.respond("❌ Интеграция с Bitrix24 не настроена")
                return
            
            await event.respond("📊 Загружаю статистику из Bitrix24...")
            
            stats = await self.bitrix.get_lead_statistics()
            
            if not stats:
                await event.respond("❌ Не удалось загрузить статистику")
                return
            
            response = "📈 Статистика лидов:\n"
            response += "=" * 30 + "\n\n"
            
            response += f"📊 Всего лидов: {stats.get('total', 0)}\n"
            response += f"🆕 Новых: {stats.get('new', 0)}\n"
            response += f"⚙️ В обработке: {stats.get('processed', 0)}\n"
            response += f"✅ Конвертированных: {stats.get('converted', 0)}\n"
            response += f"❌ Потерянных: {stats.get('lost', 0)}\n"
            
            # Вычисляем процент конверсии
            total = stats.get('total', 0)
            converted = stats.get('converted', 0)
            if total > 0:
                conversion_rate = (converted / total) * 100
                response += f"\n📈 Процент конверсии: {conversion_rate:.1f}%"
            
            await event.respond(response)
            
        except Exception as e:
            logger.error(f"Ошибка при показе статистики: {e}")
            await event.respond(f"❌ Ошибка при загрузке статистики: {e}")
    
    async def export_leads(self, event):
        """Экспорт лидов в JSON"""
        try:
            if not self.bitrix:
                await event.respond("❌ Интеграция с Bitrix24 не настроена")
                return
            
            await event.respond("📊 Загружаю лиды для экспорта...")
            
            leads = await self.bitrix.get_leads()
            
            if not leads:
                await event.respond("📭 Лиды не найдены")
                return
            
            # Сохраняем в файл
            export_file = 'data/leads_export.json'
            os.makedirs('data', exist_ok=True)
            
            with open(export_file, 'w', encoding='utf-8') as f:
                json.dump(leads, f, ensure_ascii=False, indent=2)
            
            response = f"📤 Экспорт завершен!\n\n"
            response += f"📁 Файл: {export_file}\n"
            response += f"📊 Количество лидов: {len(leads)}\n"
            response += f"📅 Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            
            await event.respond(response)
            
        except Exception as e:
            logger.error(f"Ошибка при экспорте лидов: {e}")
            await event.respond(f"❌ Ошибка при экспорте лидов: {e}")
    
    async def show_status(self, event):
        """Показать статус админ панели"""
        try:
            sender = await event.get_sender()
            if not isinstance(sender, User):
                await event.respond("❌ Ошибка: не удалось определить пользователя")
                return
            
            user_id = sender.id
            status_text = "📊 Статус админ панели\n"
            status_text += "=" * 30 + "\n\n"
            
            if self.admin_mode:
                status_text += "🟢 Админ панель: АКТИВНА\n"
                if self.active_admin_user:
                    status_text += f"👤 Активный администратор: {self.active_admin_user}\n"
                    if self.active_admin_user == user_id:
                        status_text += "✅ Вы являетесь активным администратором\n"
                        status_text += "🔒 Основной бот заблокирован для вас\n"
                    else:
                        status_text += "❌ Вы не являетесь активным администратором\n"
                        status_text += "🔓 Основной бот работает для вас\n"
                else:
                    status_text += "⚠️ Активный администратор не определен\n"
            else:
                status_text += "🔴 Админ панель: НЕАКТИВНА\n"
                status_text += "🔓 Основной бот работает для всех\n"
            
            status_text += f"\n👤 Ваш ID: {user_id}\n"
            status_text += f"📱 Username: @{username or 'Не указан'}\n"
            status_text += f"🔑 Администратор: {'Да' if self.is_user_admin(user_id, username) else 'Нет'}\n"
            
            await event.respond(status_text)
            
        except Exception as e:
            logger.error(f"Ошибка при показе статуса: {e}")
            await event.respond(f"❌ Ошибка при показе статуса: {e}")
    
    async def force_clear_states(self, event):
        """Принудительная очистка состояний пользователей"""
        try:
            self.clear_user_states()
            await event.respond("🧹 Состояния пользователей принудительно очищены!")
            logger.info("Принудительная очистка состояний пользователей")
        except Exception as e:
            logger.error(f"Ошибка при принудительной очистке: {e}")
            await event.respond(f"❌ Ошибка при очистке состояний: {e}")
    
    async def handle_admin_command(self, event):
        """Обработка команд администратора"""
        try:
            sender = await event.get_sender()
            if not isinstance(sender, User):
                return
            
            user_id = sender.id
            username = sender.username
            message_text = event.message.text.strip()
            
            # Отмечаем сообщение как прочитанное
            await self.mark_message_as_read(event)
            
            # Проверяем, является ли пользователь администратором
            if not self.is_user_admin(user_id, username):
                await event.respond("❌ Доступ запрещен. Вы не являетесь администратором системы.")
                logger.warning(f"Попытка доступа к админ панели от неавторизованного пользователя: {user_id} (@{username})")
                return
            
            # Обрабатываем команды
            if message_text == '/admin':
                await self.activate_admin_mode(event)
            elif message_text == '/stop':
                await self.deactivate_admin_mode(event)
            elif message_text == '/status':
                await self.show_status(event)
            elif message_text == '/clear':
                await self.force_clear_states(event)
            elif message_text == '/admins':
                await self.show_admins(event)
            elif self.admin_mode:
                # Команды доступные только в режиме админ панели
                if message_text.startswith('/help'):
                    await self.show_help(event)
                elif message_text.startswith('/applications'):
                    await self.show_applications_data(event)
                elif message_text.startswith('/leads'):
                    await self.show_leads(event)
                elif message_text.startswith('/new'):
                    await self.show_new_leads(event)
                elif message_text.startswith('/stats'):
                    await self.show_lead_statistics(event)
                elif message_text.startswith('/export'):
                    await self.export_leads(event)
                else:
                    await event.respond("❌ Неизвестная команда. Используйте /help для списка команд.")
            else:
                await event.respond("🔒 Админ панель неактивна. Используйте /admin для активации.")
                
        except Exception as e:
            logger.error(f"Ошибка при обработке команды: {e}")
            await event.respond("❌ Произошла ошибка при обработке команды.")
        
    async def start(self):
        """Запуск бота"""
        await self.client.start(phone=PHONE_NUMBER)
        logger.info(f"UserBot {BOT_NAME} запущен")
        
        # Проверяем авторизацию
        me = await self.client.get_me()
        logger.info(f"👤 Авторизован как: {me.first_name} {me.last_name or ''} (@{me.username or 'без username'})")
        
        # Логируем список администраторов
        admin_list = ', '.join([f"@{username}" for username in self.admin_usernames])
        logger.info(f"👥 Настроенные администраторы: {admin_list}")
        
        # Регистрация обработчиков событий
        self.client.add_event_handler(
            self.handle_new_message, 
            events.NewMessage(incoming=True)
        )
        logger.info("✅ Обработчик сообщений зарегистрирован")
        
        # Добавляем простой обработчик для тестирования
        self.client.add_event_handler(
            self.test_handler,
            events.NewMessage(pattern=r'^/test$')
        )
        logger.info("✅ Тестовый обработчик зарегистрирован")
        
        # Регистрируем обработчик админ команд
        self.client.add_event_handler(
            self.handle_admin_command,
            events.NewMessage(pattern=r'^/')
        )
        logger.info("✅ Обработчик админ команд зарегистрирован")
        
        # Запускаем фоновую задачу для проверки сигналов очистки
        asyncio.create_task(self.check_clear_signals())
        logger.info("✅ Фоновая задача проверки сигналов запущена")
        
        # Проверяем статус блокировки
        if self.is_admin_panel_running():
            logger.info("⚠️ Админ панель активна, основной бот может быть заблокирован")
        else:
            logger.info("🔓 Админ панель неактивна, основной бот готов к работе")
        
        # Восстанавливаем запланированные напоминания
        await self.restore_scheduled_reminders()
        
        # Запуск в фоновом режиме
        logger.info("🚀 Основной бот запущен и ожидает сообщения...")
        
        try:
            await self.client.run_until_disconnected()
        finally:
            # Закрываем HTTP сессию Bitrix24
            if self.bitrix:
                await self.bitrix.close()
                logger.info("🔒 HTTP сессия Bitrix24 закрыта")
    
    async def handle_new_message(self, event):
        """Обработка новых сообщений"""
        try:
            logger.info(f"📨 Получено событие: {type(event).__name__}")
            
            # Получаем информацию о пользователе
            sender = await event.get_sender()
            if not isinstance(sender, User):
                logger.info(f"❌ Отправитель не является пользователем: {type(sender).__name__}")
                return
            
            user_id = sender.id
            username = sender.username
            first_name = sender.first_name
            last_name = sender.last_name
            message_text = event.message.text.strip()
            
            logger.info(f"👤 Сообщение от пользователя {user_id} ({first_name} {last_name or ''}): {message_text}")
            
            # Получаем время отправки сообщения
            message_date = event.message.date
            current_time = asyncio.get_event_loop().time()
            
            # Проверяем, заблокирован ли этот пользователь
            is_blocked = self.is_user_blocked(user_id)
            logger.info(f"🔍 Пользователь {user_id} заблокирован: {is_blocked}")
            
            if is_blocked:
                logger.info(f"🔒 Пользователь {user_id} заблокирован админ панелью, игнорируем сообщение")
                return
            
            # Проверяем, не слишком ли старое сообщение (старше 30 секунд)
            if hasattr(message_date, 'timestamp'):
                message_timestamp = message_date.timestamp()
                time_diff = current_time - message_timestamp
                logger.info(f"⏰ Время сообщения: {time_diff:.1f} секунд назад")
                if time_diff > 30:
                    logger.info(f"⏰ Игнорируем старое сообщение от {user_id} (старше 30 секунд)")
                    return
            
            logger.info(f"🔓 Обрабатываем сообщение от {user_id}: {message_text}")
            
            # Проверяем, не деактивирован ли пользователь (кроме администраторов)
            if user_id in self.deactivated_users and not self.is_user_admin(user_id, username):
                logger.info(f"🔇 Пользователь {user_id} деактивирован, игнорируем сообщение (кроме админ команд)")
                # Позволяем администраторам использовать админ команды даже если они деактивированы
                if message_text.startswith('/'):
                    await self.handle_admin_command(event)
                return
            
            # Обрабатываем сообщение для проверки активации бота
            is_activated = self.process_user_message_for_activation(user_id, message_text)
            
            # Если бот не активирован для пользователя, игнорируем сообщение
            if not is_activated:
                logger.info(f"🔇 Бот не активирован для пользователя {user_id}, игнорируем сообщение")
                return
            
            # Обновляем время последнего сообщения и отменяем напоминания
            self.update_last_message_time(user_id)
            self.cancel_reminder(user_id)
            
            # Отмечаем сообщение как прочитанное
            await self.mark_message_as_read(event)
            
            # Проверяем, является ли это первым сообщением от пользователя
            if user_id not in self.user_states:
                logger.info(f"🆕 Новый пользователь {user_id}, начинаем беседу")
                await self.start_conversation(event, user_id, username, first_name, last_name)
            else:
                logger.info(f"📝 Продолжаем беседу с пользователем {user_id}")
                await self.process_user_response(event, user_id, message_text)
                
        except Exception as e:
            logger.error(f"Ошибка при обработке сообщения: {e}")
            import traceback
            logger.error(f"Полный стек ошибки: {traceback.format_exc()}")
    
    async def test_handler(self, event):
        """Тестовый обработчик для проверки работы бота"""
        try:
            sender = await event.get_sender()
            if not isinstance(sender, User):
                return
            
            user_id = sender.id
            logger.info(f"🧪 Тестовое сообщение от пользователя {user_id}")
            
            # Отмечаем сообщение как прочитанное
            await self.mark_message_as_read(event)
            
            await event.respond("🧪 Тест! Основной бот работает!")
            logger.info(f"✅ Тестовый ответ отправлен пользователю {user_id}")
            
        except Exception as e:
            logger.error(f"Ошибка в тестовом обработчике: {e}")
    
    async def start_conversation(self, event, user_id: int, username: str, 
                               first_name: str, last_name: str):
        """Начало разговора с новым пользователем"""
        try:
            # Проверяем, есть ли сохраненный прогресс
            if user_id in self.user_states and user_id in self.user_answers:
                current_question = self.user_states[user_id].get('current_question', 0)
                saved_answers = self.user_answers[user_id]
                
                if current_question > 0 or saved_answers:
                    logger.info(f"🔄 Восстановлен прогресс пользователя {user_id} (вопрос {current_question})")
                    
                    # Отправляем следующий вопрос
                    if current_question < len(QUESTIONS):
                        await event.respond(f"🔄 Продолжаем заполнение заявки с вопроса {current_question + 1}")
                        await asyncio.sleep(1)
                        await event.respond(QUESTIONS[current_question])
                    else:
                        # Если все вопросы заполнены, запрашиваем контакт
                        self.user_states[user_id]['waiting_for_contact'] = True
                        await event.respond("🔄 Продолжаем заполнение заявки. Осталось указать контактную информацию.")
                        await asyncio.sleep(1)
                        await event.respond(FINAL_MESSAGE)
                    
                    return
            
            # Инициализируем новое состояние пользователя
            self.user_states[user_id] = {
                'current_question': 0,
                'waiting_for_contact': False,
                'username': username,
                'first_name': first_name,
                'last_name': last_name
            }
            self.user_answers[user_id] = {}
            
            # Отправляем приветственное видео как кружок (если есть)
            logger.info(f"🎥 Попытка отправки приветственного видео-кружка пользователю {user_id}")
            video_sent = await self.send_greeting_video(event.chat_id, user_id)
            
            # Небольшая пауза между сообщениями
            await asyncio.sleep(1)
            
            # Отправляем текстовое приветствие
            await event.respond(GREETING_MESSAGE)
            logger.info(f"📝 Отправлено текстовое приветствие пользователю {user_id}")
            
            # Если видео не было отправлено, логируем это
            if not video_sent:
                logger.warning(f"⚠️ Видео-кружок не был отправлен пользователю {user_id}")
            
            # Отправляем первый вопрос
            await asyncio.sleep(1)
            await event.respond(QUESTIONS[0])
            
            logger.info(f"Начата беседа с пользователем {user_id}")
            
        except Exception as e:
            logger.error(f"Ошибка при начале беседы: {e}")
    
    async def process_user_response(self, event, user_id: int, message_text: str):
        """Обработка ответа пользователя"""
        try:
            if user_id not in self.user_states:
                # Если состояние потеряно, начинаем заново
                await self.start_conversation(event, user_id, None, None, None)
                return
            
            state = self.user_states[user_id]
            
            if state['waiting_for_contact']:
                await self.process_contact_info(event, user_id, message_text)
            else:
                await self.process_question_answer(event, user_id, message_text)
                
        except Exception as e:
            logger.error(f"Ошибка при обработке ответа: {e}")
    
    async def process_question_answer(self, event, user_id: int, message_text: str):
        """Обработка ответа на вопрос"""
        try:
            state = self.user_states[user_id]
            current_question = state['current_question']
            
            # Сохраняем ответ
            self.user_answers[user_id][f"question_{current_question + 1}"] = message_text
            
            # Переходим к следующему вопросу
            state['current_question'] += 1
            
            # Сохраняем прогресс
            self.save_users_data()
            
            if state['current_question'] < len(QUESTIONS):
                # Отправляем следующий вопрос
                await asyncio.sleep(1)
                await event.respond(QUESTIONS[state['current_question']])
                
                # Планируем напоминание в опроснике (только одно на весь опросник)
                await self.schedule_survey_reminder(user_id, event.chat_id)
            else:
                # Все вопросы заданы, запрашиваем контактную информацию
                state['waiting_for_contact'] = True
                
                # Отправляем видео с запросом номера телефона (если есть)
                logger.info(f"📱 Попытка отправки видео-кружка запроса телефона пользователю {user_id}")
                video_sent = await self.send_phone_question_video(event.chat_id, user_id)
                
                # Небольшая пауза между сообщениями
                await asyncio.sleep(1)
                
                # Отправляем текстовый запрос контактной информации
                await event.respond(FINAL_MESSAGE)
                logger.info(f"📝 Отправлен текстовый запрос контактной информации пользователю {user_id}")
                
                # Если видео не было отправлено, логируем это
                if not video_sent:
                    logger.warning(f"⚠️ Видео-кружок запроса телефона не был отправлен пользователю {user_id}")
                
                # Планируем первое напоминание через 5 минут
                await self.schedule_reminder(user_id, event.chat_id, 5, 'first')
                
        except Exception as e:
            logger.error(f"Ошибка при обработке ответа на вопрос: {e}")
    
    async def process_contact_info(self, event, user_id: int, message_text: str):
        """Обработка контактной информации"""
        try:
            logger.info(f"📱 Обработка контактной информации от пользователя {user_id}: {message_text}")
            
            # Извлекаем и валидируем номер телефона
            phone_found, phone_number, phone_error = self.extract_phone_from_text(message_text)
            
            if not phone_found:
                # Если номер не найден, просим пользователя ввести корректный номер
                error_message = (
                    "❌ Не удалось найти корректный номер телефона в вашем сообщении.\n\n"
                    "📱 Пожалуйста, укажите номер телефона в одном из форматов:\n"
                    "• +7XXXXXXXXXX\n"
                    "• 8XXXXXXXXXX\n"
                    "• XXXXXXXXXX\n\n"
                    "💡 Примеры: +79001234567, 89001234567, 9001234567"
                )
                await event.respond(error_message)
                logger.warning(f"❌ Некорректный номер телефона от пользователя {user_id}: {phone_error}")
                return
            
            logger.info(f"✅ Найден корректный номер телефона: {phone_number}")
            
            # Извлекаем время консультации
            consultation_time = self.extract_consultation_time(message_text)
            
            # Сохраняем данные заявки локально (без отправки в CRM)
            user_data = {
                'user_id': user_id,
                'username': self.user_states[user_id].get('username'),
                'first_name': self.user_states[user_id].get('first_name'),
                'last_name': self.user_states[user_id].get('last_name'),
                'phone_number': phone_number,
                'consultation_time': consultation_time,
                'answers': self.user_answers[user_id],
                'status': 'new',
                'validation_status': 'validated'
            }
            
            # Сохраняем заявку локально
            self.save_application(user_data)
            
            # Добавляем запись о заявке в файл данных
            self.add_application_record(user_id, phone_number)
            
            # Отправляем заявку в Bitrix24
            if self.bitrix:
                try:
                    logger.info(f"📤 Отправка заявки в Bitrix24 для пользователя {user_id}")
                    bitrix_result = await self.bitrix.create_lead(user_data)
                    if bitrix_result:
                        logger.info(f"✅ Заявка успешно отправлена в Bitrix24: {bitrix_result}")
                    else:
                        logger.error(f"❌ Ошибка отправки заявки в Bitrix24 для пользователя {user_id}")
                except Exception as e:
                    logger.error(f"❌ Исключение при отправке в Bitrix24: {e}")
            else:
                logger.warning("⚠️ Интеграция с Bitrix24 не настроена, заявка не отправлена")
            
            # Очищаем прогресс заявки
            if user_id in self.user_states:
                del self.user_states[user_id]
            if user_id in self.user_answers:
                del self.user_answers[user_id]
            self.save_users_data()
            
            # Отправляем подтверждение
            confirmation_message = (
                f"✅ Спасибо! Ваша заявка принята.\n\n"
                f"📱 Номер телефона: {phone_number}\n"
                f"⏰ Время консультации: {consultation_time}\n\n"
                f"Я свяжусь с вами в указанное время для бесплатной консультации! 🙏🏻"
            )
            await event.respond(confirmation_message)
            
            # Отменяем все напоминания для этого пользователя
            self.cancel_reminder(user_id)
            
            # Удаляем запланированные напоминания из файлов при успешной подаче заявки
            if user_id in self.scheduled_reminders:
                del self.scheduled_reminders[user_id]
                self.save_users_data()
                logger.info(f"🗑️ Удалено запланированное напоминание для пользователя {user_id} из файлов (успешная подача заявки)")
            
            # Деактивируем пользователя после успешной подачи заявки
            self.deactivated_users.add(user_id)
            if user_id in self.activated_users:
                self.activated_users.remove(user_id)
            if user_id in self.expired_users:
                self.expired_users.remove(user_id)
            if user_id in self.user_message_counts:
                del self.user_message_counts[user_id]
            
            logger.info(f"🔇 Пользователь {user_id} деактивирован после успешной подачи заявки")
            
            # Очищаем состояние пользователя
            if user_id in self.user_states:
                del self.user_states[user_id]
            if user_id in self.user_answers:
                del self.user_answers[user_id]
            if user_id in self.last_message_times:
                del self.last_message_times[user_id]
                
            logger.info(f"✅ Заявка от пользователя {user_id} успешно завершена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке контактной информации: {e}")
            await event.respond("❌ Произошла ошибка при обработке вашей заявки. Пожалуйста, попробуйте еще раз.")
    
    def extract_consultation_time(self, text: str) -> str:
        """
        Извлекает время консультации из текста
        
        Args:
            text: Текст сообщения
            
        Returns:
            str: Время консультации или "Не указано"
        """
        try:
            # Ключевые слова для поиска времени
            time_keywords = [
                'утром', 'днем', 'вечером', 'ночью',
                'завтра', 'сегодня', 'послезавтра',
                'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье',
            ]
            
            # Ищем ключевые слова в тексте
            text_lower = text.lower()
            found_keywords = []
            
            for keyword in time_keywords:
                if keyword in text_lower:
                    found_keywords.append(keyword)
            
            if found_keywords:
                # Если найдены ключевые слова, возвращаем весь текст как время
                return text.strip()
            
            # Если ключевых слов нет, но есть цифры времени (например, 14:00, 15:30)
            time_pattern = r'\b\d{1,2}:\d{2}\b'
            time_match = re.search(time_pattern, text)
            if time_match:
                return text.strip()
            
            return "Не указано"
            
        except Exception as e:
            logger.error(f"Ошибка при извлечении времени консультации: {e}")
            return "Не указано"
    
    async def schedule_reminder(self, user_id: int, chat_id: int, delay_minutes: int, reminder_type: str):
        """
        Планирует напоминание для пользователя
        
        Args:
            user_id: ID пользователя
            chat_id: ID чата
            delay_minutes: Задержка в минутах
            reminder_type: Тип напоминания ('first' или 'final')
        """
        try:
            # Отменяем предыдущее напоминание для этого пользователя, если оно есть
            if user_id in self.reminder_tasks:
                self.reminder_tasks[user_id].cancel()
            
            # Сохраняем информацию о запланированном напоминании
            scheduled_time = datetime.now() + timedelta(minutes=delay_minutes)
            self.scheduled_reminders[user_id] = {
                'chat_id': chat_id,
                'delay_minutes': delay_minutes,
                'reminder_type': reminder_type,
                'scheduled_time': scheduled_time.isoformat(),
                'created_time': datetime.now().isoformat()
            }
            
            # Сохраняем данные
            self.save_users_data()
            
            # Создаем новую задачу напоминания
            task = asyncio.create_task(self.send_reminder(user_id, chat_id, delay_minutes, reminder_type))
            self.reminder_tasks[user_id] = task
            
            logger.info(f"⏰ Запланировано напоминание для пользователя {user_id} через {delay_minutes} минут (тип: {reminder_type})")
            
        except Exception as e:
            logger.error(f"Ошибка при планировании напоминания: {e}")
    
    async def schedule_survey_reminder(self, user_id: int, chat_id: int):
        """
        Планирует напоминание в опроснике (только одно на весь опросник)
        
        Args:
            user_id: ID пользователя
            chat_id: ID чата
        """
        try:
            # Проверяем, не было ли уже отправлено напоминание для этого пользователя
            if user_id in self.survey_reminder_sent and self.survey_reminder_sent[user_id]:
                logger.info(f"⏰ Напоминание в опроснике уже было отправлено пользователю {user_id}")
                return
            
            # Создаем задачу напоминания через 20 минут
            task = asyncio.create_task(self.send_survey_reminder(user_id, chat_id))
            
            # Сохраняем задачу в reminder_tasks с уникальным ключом
            reminder_key = f"survey_{user_id}"
            if reminder_key in self.reminder_tasks:
                self.reminder_tasks[reminder_key].cancel()
            
            self.reminder_tasks[reminder_key] = task
            
            logger.info(f"⏰ Запланировано напоминание в опроснике для пользователя {user_id} через 20 минут")
            
        except Exception as e:
            logger.error(f"Ошибка при планировании напоминания в опроснике: {e}")
    
    async def send_survey_reminder(self, user_id: int, chat_id: int):
        """
        Отправляет напоминание в опроснике
        
        Args:
            user_id: ID пользователя
            chat_id: ID чата
        """
        try:
            # Ждем 20 минут
            await asyncio.sleep(20 * 60)
            
            # Проверяем, что пользователь все еще в опроснике
            if user_id not in self.user_states:
                logger.info(f"⏰ Пользователь {user_id} больше не в опроснике, отменяем напоминание")
                return
            
            # Проверяем, не отправил ли пользователь сообщение за это время
            if user_id in self.last_message_times:
                last_message_time = self.last_message_times[user_id]
                current_time = datetime.now()
                time_diff = current_time - last_message_time
                
                if time_diff.total_seconds() < 20 * 60:
                    logger.info(f"⏰ Пользователь {user_id} отправил сообщение после планирования напоминания в опроснике, отменяем")
                    return
            
            # Проверяем, не было ли уже отправлено напоминание
            if user_id in self.survey_reminder_sent and self.survey_reminder_sent[user_id]:
                logger.info(f"⏰ Напоминание в опроснике уже было отправлено пользователю {user_id}")
                return
            
            # Отправляем напоминание
            reminder_text = (
                "⏰ Напоминание: вы находитесь в процессе заполнения опросника для получения бесплатной консультации.\n\n"
                "📝 Пожалуйста, ответьте на следующий вопрос, чтобы мы могли лучше понять вашу ситуацию и подготовить для вас индивидуальное решение.\n\n"
                "💡 Чем подробнее вы ответите, тем точнее мы сможем подобрать оптимальную стратегию списания ваших долгов."
            )
            
            await self.client.send_message(chat_id, reminder_text)
            logger.info(f"📤 Отправлено напоминание в опроснике пользователю {user_id}")
            
            # Отмечаем, что напоминание было отправлено
            self.survey_reminder_sent[user_id] = True
            
            # Удаляем задачу из reminder_tasks
            reminder_key = f"survey_{user_id}"
            if reminder_key in self.reminder_tasks:
                del self.reminder_tasks[reminder_key]
            
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания в опроснике: {e}")
    
    def load_users_data(self):
        """Загружает данные пользователей из файла"""
        try:
            # Пробуем загрузить из основного файла
            if os.path.exists(self.users_data_file):
                try:
                    with open(self.users_data_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    logger.info(f"📂 Загружены данные из {self.users_data_file}")
                except PermissionError:
                    logger.warning(f"⚠️ Нет прав на чтение {self.users_data_file}")
                    return
            else:
                # Пробуем загрузить из резервного файла
                fallback_file = 'users_data.json'
                if os.path.exists(fallback_file):
                    try:
                        with open(fallback_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        logger.info(f"📂 Загружены данные из резервного файла {fallback_file}")
                    except PermissionError:
                        logger.warning(f"⚠️ Нет прав на чтение резервного файла {fallback_file}")
                        return
                else:
                    logger.info("📂 Файлы данных пользователей не найдены, создаем новые")
                    return
            
            # Восстанавливаем данные
            try:
                # Восстанавливаем счетчики сообщений (только превысивших лимит)
                expired_counts = data.get('user_message_counts', {})
                self.user_message_counts = {}
                for user_id_str, is_expired in expired_counts.items():
                    if is_expired:
                        user_id = int(user_id_str)
                        self.user_message_counts[user_id] = 6  # Устанавливаем значение больше 5
                
                # Восстанавливаем активированных пользователей
                self.activated_users = set(int(user_id) for user_id in data.get('activated_users', []))
                
                # Восстанавливаем истекших пользователей
                self.expired_users = set(int(user_id) for user_id in data.get('expired_users', []))
                
                # Восстанавливаем деактивированных пользователей
                self.deactivated_users = set(int(user_id) for user_id in data.get('deactivated_users', []))
                
                # Восстанавливаем данные о напоминаниях в опроснике
                self.survey_reminder_sent = {int(k): v for k, v in data.get('survey_reminder_sent', {}).items()}
                
                # Восстанавливаем время последних сообщений
                last_message_times = data.get('last_message_times', {})
                self.last_message_times = {}
                for user_id_str, time_str in last_message_times.items():
                    try:
                        user_id = int(user_id_str)
                        self.last_message_times[user_id] = datetime.fromisoformat(time_str)
                    except (ValueError, TypeError):
                        logger.warning(f"Некорректные данные времени для пользователя {user_id_str}")
                
                # Восстанавливаем прогресс заявок
                self.user_states = {int(k): v for k, v in data.get('user_states', {}).items()}
                self.user_answers = {int(k): v for k, v in data.get('user_answers', {}).items()}
                
                # Восстанавливаем запланированные напоминания
                self.scheduled_reminders = {int(k): v for k, v in data.get('scheduled_reminders', {}).items()}
                
                logger.info(f"📂 Восстановлены данные {len(self.user_message_counts)} пользователей (превысивших лимит), прогресс {len(self.user_states)} заявок, {len(self.scheduled_reminders)} запланированных напоминаний и {len(self.deactivated_users)} деактивированных пользователей")
                
            except Exception as e:
                logger.error(f"Ошибка при восстановлении данных пользователей: {e}")
                
        except Exception as e:
            logger.error(f"Ошибка при загрузке данных пользователей: {e}")
            # Не прерываем работу бота из-за ошибок загрузки
    
    def save_users_data(self):
        """Сохраняет данные пользователей в файл"""
        try:
            # Подготавливаем данные для сохранения
            # Сохраняем только пользователей, которые превысили лимит в 5 сообщений
            expired_message_counts = {}
            for user_id, count in self.user_message_counts.items():
                if count > 5:
                    expired_message_counts[str(user_id)] = True
            
            data = {
                'user_message_counts': expired_message_counts,
                'activated_users': list(self.activated_users),
                'expired_users': list(self.expired_users),
                'deactivated_users': list(self.deactivated_users),
                'survey_reminder_sent': self.survey_reminder_sent,
                'last_message_times': {
                    str(user_id): time.isoformat() 
                    for user_id, time in self.last_message_times.items()
                },
                'user_states': self.user_states,
                'user_answers': self.user_answers,
                'scheduled_reminders': self.scheduled_reminders,
                'last_save': datetime.now().isoformat()
            }
            
            # Пробуем сохранить в основной файл
            try:
                # Создаем директорию если её нет
                os.makedirs(os.path.dirname(self.users_data_file), exist_ok=True)
                
                # Сохраняем в файл
                with open(self.users_data_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                logger.info(f"💾 Сохранены данные {len(expired_message_counts)} пользователей (превысивших лимит)")
                
            except PermissionError:
                # Если нет прав на запись в data/, сохраняем в корневой директории
                fallback_file = 'users_data.json'
                logger.warning(f"⚠️ Нет прав на запись в {self.users_data_file}, сохраняем в {fallback_file}")
                
                with open(fallback_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                logger.info(f"💾 Сохранены данные {len(expired_message_counts)} пользователей (превысивших лимит) в {fallback_file}")
                
        except Exception as e:
            logger.error(f"Ошибка при сохранении данных пользователей: {e}")
    
    def load_applications_data(self):
        """Загружает данные заявок из файла"""
        try:
            # Пробуем загрузить из основного файла
            if os.path.exists(self.applications_data_file):
                try:
                    with open(self.applications_data_file, 'r', encoding='utf-8') as f:
                        self.applications_data = json.load(f)
                    logger.info(f"📂 Загружены данные {len(self.applications_data)} заявок из {self.applications_data_file}")
                except PermissionError:
                    logger.warning(f"⚠️ Нет прав на чтение {self.applications_data_file}")
                    self.applications_data = []
                    return
            else:
                # Пробуем загрузить из резервного файла
                fallback_file = 'applications_data.json'
                if os.path.exists(fallback_file):
                    try:
                        with open(fallback_file, 'r', encoding='utf-8') as f:
                            self.applications_data = json.load(f)
                        logger.info(f"📂 Загружены данные {len(self.applications_data)} заявок из резервного файла {fallback_file}")
                    except PermissionError:
                        logger.warning(f"⚠️ Нет прав на чтение резервного файла {fallback_file}")
                        self.applications_data = []
                        return
                else:
                    self.applications_data = []
                    logger.info("📂 Файлы данных заявок не найдены, создаем новые")
                    return
                    
        except Exception as e:
            logger.error(f"Ошибка при загрузке данных заявок: {e}")
            self.applications_data = []
            # Не прерываем работу бота из-за ошибок загрузки
    
    def save_applications_data(self):
        """Сохраняет данные заявок в файл"""
        try:
            # Пробуем сохранить в основной файл
            try:
                # Создаем директорию если её нет
                os.makedirs(os.path.dirname(self.applications_data_file), exist_ok=True)
                
                # Сохраняем в файл
                with open(self.applications_data_file, 'w', encoding='utf-8') as f:
                    json.dump(self.applications_data, f, ensure_ascii=False, indent=2)
                
                logger.info(f"💾 Сохранены данные {len(self.applications_data)} заявок")
                
            except PermissionError:
                # Если нет прав на запись в data/, сохраняем в корневой директории
                fallback_file = 'applications_data.json'
                logger.warning(f"⚠️ Нет прав на запись в {self.applications_data_file}, сохраняем в {fallback_file}")
                
                with open(fallback_file, 'w', encoding='utf-8') as f:
                    json.dump(self.applications_data, f, ensure_ascii=False, indent=2)
                
                logger.info(f"💾 Сохранены данные {len(self.applications_data)} заявок в {fallback_file}")
                
        except Exception as e:
            logger.error(f"Ошибка при сохранении данных заявок: {e}")
    

    
    def add_application_record(self, user_id: int, phone_number: str):
        """Добавляет запись о заявке"""
        try:
            application_record = {
                'user_id': user_id,
                'phone_number': phone_number,
                'application_date': datetime.now().isoformat()
            }
            
            self.applications_data.append(application_record)
            self.save_applications_data()
            
            logger.info(f"📝 Добавлена запись о заявке пользователя {user_id}")
            
        except Exception as e:
            logger.error(f"Ошибка при добавлении записи о заявке: {e}")
    
    def update_user_activation_status(self, user_id: int):
        """Обновляет статус активации пользователя и сохраняет данные"""
        try:
            # Данные уже обновлены в соответствующих методах
            # Просто сохраняем в файл
            self.save_users_data()
            
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса активации пользователя {user_id}: {e}")
    
    def update_last_message_time(self, user_id: int):
        """Обновляет время последнего сообщения и сохраняет данные"""
        try:
            self.last_message_times[user_id] = datetime.now()
            self.save_users_data()
            
        except Exception as e:
            logger.error(f"Ошибка при обновлении времени последнего сообщения: {e}")
    
    def check_activation_keywords(self, text: str) -> bool:
        """
        Проверяет, содержит ли текст ключевые слова для активации бота
        
        Args:
            text: Текст сообщения
            
        Returns:
            bool: True если найдены ключевые слова "хочу" И ("консультацию" или "консультация")
        """
        try:
            text_lower = text.lower().strip()
            
            # Проверяем наличие обязательного слова "хочу"
            has_want = 'хочу' in text_lower
            
            # Проверяем наличие одного из слов "консультацию" или "консультация"
            has_consultation = 'консультацию' in text_lower or 'консультация' in text_lower
            
            # Активация происходит только при наличии ОБОИХ условий
            if has_want and has_consultation:
                logger.info(f"🔑 Найдены ключевые слова для активации: 'хочу' и ('консультацию' или 'консультация') в тексте: '{text}'")
                return True
            else:
                if not has_want:
                    logger.info(f"❌ Не найдено обязательное слово 'хочу' в тексте: '{text}'")
                if not has_consultation:
                    logger.info(f"❌ Не найдено слово 'консультацию' или 'консультация' в тексте: '{text}'")
                return False
            
        except Exception as e:
            logger.error(f"Ошибка при проверке ключевых слов: {e}")
            return False
    
    def process_user_message_for_activation(self, user_id: int, message_text: str) -> bool:
        """
        Обрабатывает сообщение пользователя для проверки активации бота
        
        Args:
            user_id: ID пользователя
            message_text: Текст сообщения
            
        Returns:
            bool: True если бот должен быть активирован
        """
        try:
            # Проверяем, не деактивирован ли пользователь
            if user_id in self.deactivated_users:
                logger.info(f"🔇 Пользователь {user_id} деактивирован, бот не будет активирован")
                return False
            
            # Инициализируем счетчик для нового пользователя
            if user_id not in self.user_message_counts:
                self.user_message_counts[user_id] = 0
            
            # Увеличиваем счетчик сообщений
            self.user_message_counts[user_id] += 1
            
            logger.info(f"📝 Сообщение {self.user_message_counts[user_id]} от пользователя {user_id}: '{message_text}'")
            
            # Проверяем активацию только в первых 5 сообщениях
            if self.user_message_counts[user_id] <= 5:
                if self.check_activation_keywords(message_text):
                    self.activated_users.add(user_id)
                    logger.info(f"✅ Бот активирован для пользователя {user_id} после {self.user_message_counts[user_id]} сообщения")
                    # Сохраняем данные при активации
                    self.save_users_data()
                    return True
                else:
                    logger.info(f"❌ Ключевые слова не найдены в сообщении {self.user_message_counts[user_id]} от пользователя {user_id}")
            
            # Если ровно 5 сообщений и бот не активирован, помечаем как истекшего
            if self.user_message_counts[user_id] == 5 and user_id not in self.activated_users:
                self.expired_users.add(user_id)
                logger.info(f"⏰ Пользователь {user_id} достиг лимита в 5 сообщений без активации, помечен как истекший")
                # Сохраняем данные при истечении
                self.save_users_data()
            
            # Если уже больше 5 сообщений и бот не активирован, не активируем
            if self.user_message_counts[user_id] > 5 and user_id not in self.activated_users:
                if user_id not in self.expired_users:
                    self.expired_users.add(user_id)
                    logger.info(f"⏰ Пользователь {user_id} превысил лимит в 5 сообщений, помечен как истекший")
                    # Сохраняем данные при истечении
                    self.save_users_data()
                logger.info(f"⏰ Пользователь {user_id} превысил лимит в 5 сообщений, бот не будет активирован")
            
            return user_id in self.activated_users
            
        except Exception as e:
            logger.error(f"Ошибка при обработке сообщения для активации: {e}")
            return False
    
    def is_bot_activated_for_user(self, user_id: int) -> bool:
        """
        Проверяет, активирован ли бот для пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            bool: True если бот активирован
        """
        # Проверяем, не деактивирован ли пользователь
        if user_id in self.deactivated_users:
            return False
        
        return user_id in self.activated_users
    
    def get_user_activation_status(self, user_id: int) -> dict:
        """
        Возвращает статус активации пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            dict: Информация о статусе активации
        """
        try:
            message_count = self.user_message_counts.get(user_id, 0)
            is_activated = user_id in self.activated_users
            is_expired = user_id in self.expired_users
            is_deactivated = user_id in self.deactivated_users
            
            # Определяем статус пользователя
            if is_deactivated:
                status = "deactivated"
            elif is_activated:
                status = "activated"
            elif is_expired:
                status = "expired"
            elif message_count > 0:
                status = "pending"
            else:
                status = "new"
            
            return {
                'user_id': user_id,
                'message_count': message_count,
                'is_activated': is_activated,
                'is_expired': is_expired,
                'is_deactivated': is_deactivated,
                'status': status,
                'can_activate': message_count <= 5 and not is_activated and not is_expired and not is_deactivated
            }
        except Exception as e:
            logger.error(f"Ошибка при получении статуса активации: {e}")
            return {}
    
    async def send_reminder(self, user_id: int, chat_id: int, delay_minutes: int, reminder_type: str):
        """
        Отправляет напоминание пользователю
        
        Args:
            user_id: ID пользователя
            chat_id: ID чата
            delay_minutes: Задержка в минутах
            reminder_type: Тип напоминания ('first' или 'final')
        """
        try:
            # Ждем указанное время
            await asyncio.sleep(delay_minutes * 60)
            
            # Проверяем, что пользователь все еще в состоянии ожидания телефона
            if user_id not in self.user_states or not self.user_states[user_id].get('waiting_for_contact', False):
                logger.info(f"⏰ Пользователь {user_id} уже не ожидает ввода телефона, отменяем напоминание")
                return
            
            # Проверяем, не отправил ли пользователь сообщение за это время
            if user_id in self.last_message_times:
                last_message_time = self.last_message_times[user_id]
                current_time = datetime.now()
                time_diff = current_time - last_message_time
                
                if time_diff.total_seconds() < delay_minutes * 60:
                    logger.info(f"⏰ Пользователь {user_id} отправил сообщение после планирования напоминания, отменяем")
                    return
            
            # Отправляем соответствующее напоминание
            if reminder_type == 'first':
                reminder_text = (
                    "🔍 Вижу, вы ещё не оставили свой номер телефона для бесплатной консультации по списанию долгов.\n\n"
                    "Не переживайте, если сейчас не самое удобное время — мы подберём для вас оптимальный вариант!\n\n"
                    "📝 Просто напишите ваш контактный номер, и мы:\n"
                    " • Согласуем удобное время звонка\n"
                    " • Ответим на все вопросы\n"
                    " • Разработаем план действий\n\n"
                    "📞 Готовы связаться с вами в любой день недели, в том числе в выходные.\n"
                    "Оставьте свой номер прямо сейчас 👇\n\n"
                    "P.S. Помните: чем раньше начнём работу над вашей ситуацией, тем быстрее найдём решение!"
                )
            elif reminder_type == 'final':
                reminder_text = (
                    "😔 К сожалению, мы так и не получили ваш номер телефона…\n"
                    "Понимаем, что решение финансовых вопросов может вызывать тревогу. Но помните: бездействие только усугубляет ситуацию.\n\n"
                    "🔔 Есть альтернативный вариант:\n"
                    "Подписывайтесь на наш канал, где мы ежедневно публикуем:\n"
                    " • Юридические лайфхаки\n"
                    " • Истории успешных списаний долгов\n"
                    " • Пошаговые инструкции по банкротству\n"
                    " • Ответы на частые вопросы\n\n"
                    "👉 Присоединяйтесь к нам прямо сейчас:\n"
                    "@ivan_kiselev_spisanie\n\n"
                    "Возможно, именно там вы найдёте ответы, которые помогут вам принять правильное решение."
                )
            else:
                logger.error(f"Неизвестный тип напоминания: {reminder_type}")
                return
            
            # Отправляем напоминание
            await self.client.send_message(chat_id, reminder_text)
            logger.info(f"📤 Отправлено напоминание пользователю {user_id} (тип: {reminder_type})")
            
            # Удаляем запланированное напоминание из файлов после срабатывания
            if user_id in self.scheduled_reminders:
                del self.scheduled_reminders[user_id]
                self.save_users_data()
                logger.info(f"🗑️ Удалено запланированное напоминание для пользователя {user_id} из файлов после срабатывания")
            
            # Если это первое напоминание, планируем финальное через 23 часа 54 минуты (1439 - 5)
            if reminder_type == 'first':
                await self.schedule_reminder(user_id, chat_id, 1434, 'final')
                logger.info(f"⏰ Запланировано финальное напоминание для пользователя {user_id} через 23 часа 54 минуты")
            # Если это финальное напоминание, очищаем состояние пользователя
            elif reminder_type == 'final':
                if user_id in self.user_states:
                    del self.user_states[user_id]
                if user_id in self.user_answers:
                    del self.user_answers[user_id]
                if user_id in self.reminder_tasks:
                    del self.reminder_tasks[user_id]
                if user_id in self.last_message_times:
                    del self.last_message_times[user_id]
                if user_id in self.scheduled_reminders:
                    del self.scheduled_reminders[user_id]
                    self.save_users_data()
                logger.info(f"🧹 Состояние пользователя {user_id} очищено после финального напоминания")
            
        except asyncio.CancelledError:
            logger.info(f"⏰ Напоминание для пользователя {user_id} отменено")
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания пользователю {user_id}: {e}")
    
    def update_last_message_time(self, user_id: int):
        """Обновляет время последнего сообщения от пользователя"""
        self.last_message_times[user_id] = datetime.now()
        
        # Удаляем запланированные напоминания при получении сообщения от пользователя
        if user_id in self.scheduled_reminders:
            del self.scheduled_reminders[user_id]
            logger.info(f"🗑️ Удалено запланированное напоминание для пользователя {user_id} из файлов (получено сообщение)")
        
        # Сохраняем данные в файл
        self.save_users_data()
    
    def cancel_reminder(self, user_id: int):
        """Отменяет напоминание для пользователя"""
        if user_id in self.reminder_tasks:
            self.reminder_tasks[user_id].cancel()
            del self.reminder_tasks[user_id]
            logger.info(f"⏰ Напоминание для пользователя {user_id} отменено")
        
        # Отменяем напоминание в опроснике
        reminder_key = f"survey_{user_id}"
        if reminder_key in self.reminder_tasks:
            self.reminder_tasks[reminder_key].cancel()
            del self.reminder_tasks[reminder_key]
            logger.info(f"⏰ Напоминание в опроснике для пользователя {user_id} отменено")
        
        # Очищаем запланированное напоминание
        if user_id in self.scheduled_reminders:
            del self.scheduled_reminders[user_id]
            self.save_users_data()
            logger.info(f"⏰ Запланированное напоминание для пользователя {user_id} очищено")
    
    async def restore_scheduled_reminders(self):
        """Восстанавливает запланированные напоминания при перезапуске"""
        try:
            if not self.scheduled_reminders:
                logger.info("📂 Нет запланированных напоминаний для восстановления")
                return
            
            logger.info(f"🔄 Восстанавливаем {len(self.scheduled_reminders)} запланированных напоминаний...")
            
            current_time = datetime.now()
            restored_count = 0
            expired_count = 0
            
            for user_id, reminder_data in self.scheduled_reminders.items():
                try:
                    # Проверяем, что пользователь все еще в состоянии ожидания телефона
                    if user_id not in self.user_states or not self.user_states[user_id].get('waiting_for_contact', False):
                        logger.info(f"⏰ Пользователь {user_id} больше не ожидает ввода телефона, пропускаем восстановление напоминания")
                        continue
                    
                    # Проверяем, не истекло ли время напоминания
                    scheduled_time = datetime.fromisoformat(reminder_data['scheduled_time'])
                    if scheduled_time <= current_time:
                        logger.info(f"⏰ Время напоминания для пользователя {user_id} истекло, пропускаем")
                        expired_count += 1
                        continue
                    
                    # Вычисляем оставшееся время
                    time_diff = scheduled_time - current_time
                    remaining_minutes = int(time_diff.total_seconds() / 60)
                    
                    if remaining_minutes <= 0:
                        logger.info(f"⏰ Напоминание для пользователя {user_id} должно было сработать, пропускаем")
                        expired_count += 1
                        continue
                    
                    # Восстанавливаем напоминание
                    chat_id = reminder_data['chat_id']
                    reminder_type = reminder_data['reminder_type']
                    
                    # Создаем новую задачу напоминания
                    task = asyncio.create_task(self.send_reminder(user_id, chat_id, remaining_minutes, reminder_type))
                    self.reminder_tasks[user_id] = task
                    
                    logger.info(f"✅ Восстановлено напоминание для пользователя {user_id} через {remaining_minutes} минут (тип: {reminder_type})")
                    restored_count += 1
                    
                except Exception as e:
                    logger.error(f"Ошибка при восстановлении напоминания для пользователя {user_id}: {e}")
                    continue
            
            logger.info(f"🔄 Восстановление завершено: {restored_count} восстановлено, {expired_count} истекло")
            
        except Exception as e:
            logger.error(f"Ошибка при восстановлении запланированных напоминаний: {e}")
    
    async def mark_message_as_read(self, event):
        """Отмечает сообщение пользователя как прочитанное ботом"""
        try:
            await event.message.mark_read()
            logger.info(f"✅ Сообщение отмечено как прочитанное")
        except Exception as e:
            logger.error(f"❌ Ошибка при отметке сообщения как прочитанного: {e}")

    async def send_notification_to_admin(self, user_id: int, application_data: Dict):
        """Отправка уведомления администратору о новой заявке"""
        try:
            # Здесь можно добавить логику отправки уведомления
            # на другой аккаунт или в канал
            logger.info(f"Новая заявка от пользователя {user_id}: {application_data}")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления: {e}")

    def save_application(self, user_data: Dict):
        """Сохраняет заявку в локальный файл (только за последние 7 дней)"""
        try:
            # Добавляем timestamp
            user_data['timestamp'] = datetime.now().isoformat()
            
            # Читаем существующие заявки
            applications_file = 'data/applications.json'
            applications = []
            
            # Пробуем загрузить из основного файла
            if os.path.exists(applications_file):
                try:
                    with open(applications_file, 'r', encoding='utf-8') as f:
                        applications = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    applications = []
                except PermissionError:
                    logger.warning(f"⚠️ Нет прав на чтение {applications_file}")
                    applications = []
            
            # Фильтруем заявки за последние 7 дней
            seven_days_ago = datetime.now() - timedelta(days=7)
            filtered_applications = []
            
            for app in applications:
                try:
                    app_timestamp = datetime.fromisoformat(app.get('timestamp', ''))
                    if app_timestamp >= seven_days_ago:
                        filtered_applications.append(app)
                except (ValueError, TypeError):
                    # Если не удалось распарсить дату, оставляем заявку
                    filtered_applications.append(app)
            
            # Добавляем новую заявку
            filtered_applications.append(user_data)
            
            logger.info(f"📊 Сохранено {len(filtered_applications)} заявок за последние 7 дней (было {len(applications)})")
            
            # Пробуем сохранить в основной файл
            try:
                # Создаем директорию если её нет
                os.makedirs(os.path.dirname(applications_file), exist_ok=True)
                
                # Сохраняем обратно в файл
                with open(applications_file, 'w', encoding='utf-8') as f:
                    json.dump(filtered_applications, f, ensure_ascii=False, indent=2)
                
                logger.info(f"📝 Заявка сохранена в {applications_file}")
                
            except PermissionError:
                # Если нет прав на запись в data/, сохраняем в рабочей директории
                fallback_file = os.path.join(self.working_dir, 'applications.json')
                logger.warning(f"⚠️ Нет прав на запись в {applications_file}, сохраняем в {fallback_file}")
                
                with open(fallback_file, 'w', encoding='utf-8') as f:
                    json.dump(filtered_applications, f, ensure_ascii=False, indent=2)
                
                logger.info(f"📝 Заявка сохранена в {fallback_file}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении заявки: {e}")

async def main():
    """Главная функция"""
    bot = SilentUserBot()
    # Устанавливаем обработчик сигналов для корректного завершения
    signal.signal(signal.SIGINT, bot.signal_handler)
    signal.signal(signal.SIGTERM, bot.signal_handler)

    try:
        await bot.start()
    finally:
        # Очистка ресурсов
        pass

if __name__ == "__main__":
    asyncio.run(main())
