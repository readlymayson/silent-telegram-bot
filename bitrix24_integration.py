import aiohttp
import json
import logging
import os
from typing import Dict, List, Optional
from datetime import datetime

# Настройка логирования с датой и временем
from logger_config import bitrix24_logger as logger

class Bitrix24Integration:
    def __init__(self, webhook_url: str):
        """
        Инициализация интеграции с Bitrix24
        
        Args:
            webhook_url: URL webhook'а Bitrix24 (например: https://your-domain.bitrix24.ru/rest/1/webhook_key/)
        """
        self.webhook_url = webhook_url.rstrip('/')
        self.session = None
    
    async def _get_session(self):
        """Получение HTTP сессии"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def create_lead(self, user_data: Dict) -> Optional[Dict]:
        """
        Создание лида в Bitrix24
        
        Args:
            user_data: Данные пользователя
                {
                    'user_id': int,
                    'username': str,
                    'first_name': str,
                    'last_name': str,
                    'phone_number': str,
                    'consultation_time': str,
                    'answers': Dict,
                    'status': str
                }
        
        Returns:
            Dict с результатом создания лида или None при ошибке
        """
        try:
            session = await self._get_session()
            
            # Формируем данные для лида
            lead_data = {
                'fields': {
                    'TITLE': f'Заявка {user_data.get("first_name", "")} {user_data.get("last_name", "")} от @ivan_spisanie_dolga',
                    'NAME': user_data.get("first_name", ""),
                    'LAST_NAME': user_data.get("last_name", ""),
                    'PHONE': [{'VALUE': user_data.get("phone_number", ""), 'VALUE_TYPE': 'WORK'}],
                    'COMMENTS': self._format_lead_comments(user_data),
                    'SOURCE_ID': '46',
                    'STATUS_ID': 'NEW',
                    'CURRENCY_ID': 'RUB',
                    'OPPORTUNITY': 0,
                    'ASSIGNED_BY_ID': 1,  # ID ответственного менеджера
                }
            }
            
            # Создаем лид
            url = f"{self.webhook_url}/crm.lead.add.json"
            async with session.post(url, json=lead_data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('result'):
                        logger.info(f"✅ Лид создан в Bitrix24: {result['result']}")
                        return result
                    else:
                        logger.error(f"❌ Ошибка создания лида: {result}")
                        return None
                else:
                    logger.error(f"❌ HTTP ошибка при создании лида: {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Ошибка при создании лида: {e}")
            return None
    
    def _format_lead_comments(self, user_data: Dict) -> str:
        """Форматирование комментария к лиду"""
        comments = []
        
        # Основная информация
        comments.append(f"ID пользователя: {user_data.get('user_id')}")
        if user_data.get('username'):
            comments.append(f"Username: @{user_data.get('username')}")
        
        # Ответы на вопросы
        answers = user_data.get('answers', {})
        if answers:
            comments.append("\n📋 Ответы на вопросы:")
            questions = [
                "Общая сумма долгов",
                "Имущество в залоге", 
                "Зарегистрированное имущество",
                "Сделки с имуществом за 3 года",
                "Официальный доход"
            ]
            
            for i, (question, answer) in enumerate(zip(questions, answers.values())):
                comments.append(f"{i+1}. {question}: {answer}")
        
        # Контактная информация
        if user_data.get('phone_number'):
            comments.append(f"\n📞 Телефон: {user_data.get('phone_number')}")
        if user_data.get('consultation_time'):
            comments.append(f"🕐 Время консультации: {user_data.get('consultation_time')}")
        
        comments.append(f"\n📅 Дата создания: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        
        return "\n".join(comments)
    
    async def get_leads(self, limit: int = 50) -> List[Dict]:
        """
        Получение списка лидов
        
        Args:
            limit: Количество лидов для получения
        
        Returns:
            Список лидов
        """
        try:
            session = await self._get_session()
            
            url = f"{self.webhook_url}/crm.lead.list.json"
            params = {
                'select': ['ID', 'TITLE', 'NAME', 'LAST_NAME', 'PHONE', 'COMMENTS', 'DATE_CREATE', 'STATUS_ID'],
                'filter': {'SOURCE_ID': 'TELEGRAM_BOT'},
                'order': {'DATE_CREATE': 'DESC'},
                'start': 0
            }
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('result'):
                        return result['result']
                    else:
                        logger.error(f"❌ Ошибка получения лидов: {result}")
                        return []
                else:
                    logger.error(f"❌ HTTP ошибка при получении лидов: {response.status}")
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Ошибка при получении лидов: {e}")
            return []
    
    async def get_new_leads(self) -> List[Dict]:
        """
        Получение новых лидов
        
        Returns:
            Список новых лидов
        """
        try:
            session = await self._get_session()
            
            url = f"{self.webhook_url}/crm.lead.list.json"
            params = {
                'select': ['ID', 'TITLE', 'NAME', 'LAST_NAME', 'PHONE', 'COMMENTS', 'DATE_CREATE'],
                'filter': {
                    'SOURCE_ID': 'TELEGRAM_BOT',
                    'STATUS_ID': 'NEW'
                },
                'order': {'DATE_CREATE': 'DESC'},
                'start': 0
            }
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('result'):
                        return result['result']
                    else:
                        logger.error(f"❌ Ошибка получения новых лидов: {result}")
                        return []
                else:
                    logger.error(f"❌ HTTP ошибка при получении новых лидов: {response.status}")
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Ошибка при получении новых лидов: {e}")
            return []
    
    async def update_lead_status(self, lead_id: int, status_id: str) -> bool:
        """
        Обновление статуса лида
        
        Args:
            lead_id: ID лида
            status_id: Новый статус
        
        Returns:
            True при успехе, False при ошибке
        """
        try:
            session = await self._get_session()
            
            url = f"{self.webhook_url}/crm.lead.update.json"
            data = {
                'id': lead_id,
                'fields': {
                    'STATUS_ID': status_id
                }
            }
            
            async with session.post(url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('result'):
                        logger.info(f"✅ Статус лида {lead_id} обновлен на {status_id}")
                        return True
                    else:
                        logger.error(f"❌ Ошибка обновления статуса лида: {result}")
                        return False
                else:
                    logger.error(f"❌ HTTP ошибка при обновлении статуса лида: {response.status}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении статуса лида: {e}")
            return False
    
    async def get_lead_statistics(self) -> Dict:
        """
        Получение статистики по лидам
        
        Returns:
            Словарь со статистикой
        """
        try:
            session = await self._get_session()
            
            # Получаем все лиды от бота
            url = f"{self.webhook_url}/crm.lead.list.json"
            params = {
                'select': ['STATUS_ID', 'DATE_CREATE'],
                'filter': {'SOURCE_ID': 'TELEGRAM_BOT'},
                'start': 0
            }
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('result'):
                        leads = result['result']
                        
                        # Подсчитываем статистику
                        stats = {
                            'total': len(leads),
                            'new': 0,
                            'processed': 0,
                            'converted': 0,
                            'lost': 0
                        }
                        
                        for lead in leads:
                            status = lead.get('STATUS_ID', 'NEW')
                            if status == 'NEW':
                                stats['new'] += 1
                            elif status in ['PROCESSED', 'IN_PROCESS']:
                                stats['processed'] += 1
                            elif status == 'CONVERTED':
                                stats['converted'] += 1
                            elif status == 'JUNK':
                                stats['lost'] += 1
                        
                        return stats
                    else:
                        logger.error(f"❌ Ошибка получения статистики: {result}")
                        return {}
                else:
                    logger.error(f"❌ HTTP ошибка при получении статистики: {response.status}")
                    return {}
                    
        except Exception as e:
            logger.error(f"❌ Ошибка при получении статистики: {e}")
            return {}
    
    async def close(self):
        """Закрытие HTTP сессии"""
        if self.session:
            await self.session.close()
            self.session = None
