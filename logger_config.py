import logging
import os
import datetime
from logging.handlers import RotatingFileHandler

def setup_logger(name: str, log_file: str = None, level: int = logging.INFO):
    """
    Настройка логгера с датой и временем
    
    Args:
        name: Имя логгера
        log_file: Путь к файлу лога (если None, создается автоматически)
        level: Уровень логирования
    
    Returns:
        Настроенный логгер
    """
    # Создаем форматтер с датой и временем
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Создаем обработчик для консоли
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # Настраиваем логгер
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Очищаем существующие обработчики
    logger.handlers.clear()
    
    # Добавляем обработчик консоли
    logger.addHandler(console_handler)
    
    # Пытаемся создать обработчик для файла
    try:
        # Создаем директорию для логов если её нет
        os.makedirs('logs', exist_ok=True)
        
        # Если файл лога не указан, создаем автоматически
        if log_file is None:
            today = datetime.datetime.now().strftime("%Y%m%d")
            log_file = f'logs/{name}_{today}.log'
        
        # Создаем обработчик для файла с ротацией
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        
        # Добавляем обработчик файла
        logger.addHandler(file_handler)
        
    except (PermissionError, OSError) as e:
        # Если не удалось создать файл лога, используем только консоль
        print(f"⚠️ Не удалось создать файл лога для {name}: {e}")
        print("📝 Логирование будет происходить только в консоль")
    
    return logger

def get_logger(name: str):
    """
    Получение логгера с автоматической настройкой
    
    Args:
        name: Имя логгера
    
    Returns:
        Настроенный логгер
    """
    return setup_logger(name)

# Создаем стандартные логгеры
user_bot_logger = setup_logger('user_bot')
admin_panel_logger = setup_logger('admin_panel')
bitrix24_logger = setup_logger('bitrix24')
system_logger = setup_logger('system')
