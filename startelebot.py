# Телеграмм-бот реализующий применения техники тайм-менеджмента для продуктивной деятельности.
#
# Ключевая особенность:
# Два режима эксплуатации: 1 - режим администрирования. 2 - режим пользователя.
# Добавлена кнопка pause.
# Добавлена обработка сетевых ошибок (модифицирован блок запуска).
# Налажена корректная отправка мотивационных сообщений.
# Налажен запуск каждой сессии в отдельном потоке:
# (теперь используются потокобезопасные структуры данных для управления состояниями).
# Добавлены проверки на существование потоков: теперь бот реализует возможность
# обрабатывать запросы от множества пользователей одновременно без блокировок.
# Реализовано безопасное хранение BOT_TOKEN через (config.json)



import telebot
from telebot import types
import logging
import os
import json
import random
import datetime
import time
import threading

# ====== Настройки ======

# ====== Загрузка конфига ======
def load_config():
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return config
    except Exception as e:
        logging.error(f"Ошибка загрузки config.json: {e}")
        raise

config = load_config()

BOT_TOKEN = config['BOT_TOKEN']
ADMIN_CHAT_ID = config['ADMIN_CHAT_ID']
DATA_FILE = config.get('DATA_FILE', 'user_states.json')  # На случай, если нет ключа

# Инициализация бота
bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Глобальные переменные
user_admin_mapping = {}
stop_flags = {}
pause_flags = {}
pause_times = {}
user_states = {}
active_threads = {}


# ====== Функции клавиатуры ======
def create_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = ["Beginner", "Intermediate", "Advanced", "Stop", "Pause", "Help"]
    keyboard.add(*buttons)
    return keyboard


def send_keyboard(chat_id, text):
    bot.send_message(chat_id, text, reply_markup=create_keyboard())


# ====== Функции данных ======

def save_data():
    data = {
        'admin_chat_id': ADMIN_CHAT_ID,  # Теперь это дублируется из config.json
        'user_admin_mapping': user_admin_mapping,
        'user_states': user_states
    }
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)


def load_data():
    global ADMIN_CHAT_ID, user_admin_mapping, user_states
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            ADMIN_CHAT_ID = data.get('admin_chat_id')
            user_admin_mapping = data.get('user_admin_mapping', {})
            user_states = data.get('user_states', {})
        logging.info("Данные загружены")


# ====== Основные обработчики ======
@bot.message_handler(commands=['start'])
def handle_start(message):
    welcome_text = """
    Привет! Я TimeCraft - твой помощник по тайм-менеджменту.
    Доступные команды:
    - Beginner: Сессия 25/5/15
    - Intermediate: Сессия 35/7/20
    - Advanced: Сессия 50/10/30
    - Stop: Остановить сессию
    - Pause: Приостановить/возобновить сессию
    - Help: Помощь
    - /setadmin - стать администратором
    - /unsetadmin - отменить режим администратора
    """
    send_keyboard(message.chat.id, welcome_text)


@bot.message_handler(commands=['setadmin'])
def handle_setadmin(message):
    global ADMIN_CHAT_ID
    ADMIN_CHAT_ID = message.chat.id
    send_keyboard(ADMIN_CHAT_ID, "⚡ Вы назначены администратором!\nИспользуйте /unsetadmin чтобы отменить этот режим.")
    save_data()


@bot.message_handler(commands=['unsetadmin'])
def handle_unsetadmin(message):
    global ADMIN_CHAT_ID
    if message.chat.id == ADMIN_CHAT_ID:
        ADMIN_CHAT_ID = None
        save_data()
        send_keyboard(message.chat.id,
                      "🔓 Режим администратора отключен. Теперь вы можете использовать бот как обычный пользователь.")
    else:
        bot.reply_to(message, "⚠️ Вы не являетесь администратором.")


@bot.message_handler(func=lambda m: True)
def handle_all_messages(message):
    if message.text.startswith('/'):
        return

    text = message.text.lower()

    if message.chat.id == ADMIN_CHAT_ID:
        if message.reply_to_message:
            handle_admin_reply(message)
        return

    if text == "help":
        send_help(message.chat.id)
    elif text in ["beginner", "intermediate", "advanced"]:
        start_timecraft_session(message)
    elif text == "stop":
        stop_timecraft_session(message.chat.id)
    elif text == "pause":
        toggle_pause_session(message.chat.id)
    elif ADMIN_CHAT_ID and message.chat.id != ADMIN_CHAT_ID:
        forward_to_admin(message)


def send_help(chat_id):
    help_text = """
    Помощь по использованию:
    - Beginner: 25 мин работа / 5 мин перерыв
    - Intermediate: 35 мин работа / 10 мин перерыв
    - Advanced: 50 мин работа / 15 мин перерыв
    - Stop: Остановить текущую сессию
    - Pause: Приостановить/возобновить сессию
    """
    send_keyboard(chat_id, help_text)


# ====== Логика TimeCraft ======
TIMECRAFT_SETTINGS = {
    "beginner": {"work": 1 * 60, "short": 1 * 60, "long": 15 * 60, "sessions": 4},
    "intermediate": {"work": 35 * 60, "short": 10 * 60, "long": 20 * 60, "sessions": 4},
    "advanced": {"work": 50 * 60, "short": 15 * 60, "long": 30 * 60, "sessions": 4}
}


def toggle_pause_session(chat_id):
    """Приостанавливает или возобновляет текущую сессию"""
    if chat_id not in pause_flags:
        pause_flags[chat_id] = False

    if pause_flags[chat_id]:
        # Возобновляем сессию
        pause_flags[chat_id] = False
        paused_time = time.time() - pause_times.get(chat_id, time.time())
        bot.send_message(chat_id,
                         f"▶️ Сессия возобновлена. Пауза длилась {int(paused_time // 60)} мин {int(paused_time % 60)} сек")
    else:
        # Приостанавливаем сессию
        pause_flags[chat_id] = True
        pause_times[chat_id] = time.time()
        bot.send_message(chat_id, "⏸️ Сессия приостановлена. Нажмите Pause для продолжения.")


def start_timecraft_session(message):
    """Запускает сессию TimeCraft"""
    chat_id = message.chat.id
    level = message.text.lower()

    # Проверяем, есть ли уже активный поток для этого пользователя
    if chat_id in active_threads and active_threads[chat_id].is_alive():
        send_keyboard(chat_id, "У вас уже есть активная сессия!")
        return

    if level not in TIMECRAFT_SETTINGS:
        send_keyboard(chat_id, "Неверный уровень!")
        return

    stop_flags[chat_id] = False
    pause_flags[chat_id] = False
    user_states[chat_id] = {
        "level": level,
        "sessions_done": 0
    }
    save_data()

    send_keyboard(chat_id, f"Старт {level} уровня!")

    # Создаем и запускаем поток для сессии
    session_thread = threading.Thread(
        target=run_session_cycle,
        args=(chat_id, level),
        daemon=True  # Поток завершится при завершении основной программы
    )
    active_threads[chat_id] = session_thread
    session_thread.start()


def stop_timecraft_session(chat_id):
    """Останавливает текущую сессию"""
    if chat_id in stop_flags and not stop_flags[chat_id]:
        stop_flags[chat_id] = True
        if chat_id in pause_flags:
            pause_flags[chat_id] = False

        # Ожидаем завершения потока (но не более 2 секунд)
        if chat_id in active_threads:
            active_threads[chat_id].join(timeout=2)
            del active_threads[chat_id]

        send_keyboard(chat_id, "Сессия остановлена! Выберите уровень!")
    else:
        send_keyboard(chat_id, "Нет активной сессии")


def run_session_cycle(chat_id, level):
    """Выполняет цикл сессий"""
    settings = TIMECRAFT_SETTINGS[level]

    while not stop_flags.get(chat_id, True):
        if user_states[chat_id]["sessions_done"] < settings["sessions"]:
            # Рабочая сессия
            run_phase(chat_id, "Рабочий период", settings["work"])
            if stop_flags.get(chat_id, True): break

            # Короткий перерыв (только если это не последняя сессия)
            if user_states[chat_id]["sessions_done"] < settings["sessions"] - 1:
                run_phase(chat_id, "Короткий перерыв", settings["short"])
                if stop_flags.get(chat_id, True): break

            user_states[chat_id]["sessions_done"] += 1
            save_data()

            # Если это была последняя сессия - переходим к длинному перерыву
            if user_states[chat_id]["sessions_done"] == settings["sessions"]:
                run_phase(chat_id, "Длинный перерыв", settings["long"])
                if stop_flags.get(chat_id, True): break
                user_states[chat_id]["sessions_done"] = 0
                save_data()
        else:
            # На всякий случай оставим этот блок (хотя с новым алгоритмом сюда не должны попадать)
            run_phase(chat_id, "Длинный перерыв", settings["long"])
            if stop_flags.get(chat_id, True): break
            user_states[chat_id]["sessions_done"] = 0
            save_data()

    send_keyboard(chat_id, "Сессия завершена!")


def run_phase(chat_id, phase_name, duration):
    """Выполняет одну фазу (работу или перерыв)"""
    if phase_name == "Длинный перерыв" and user_states[chat_id]["sessions_done"] == \
            TIMECRAFT_SETTINGS[user_states[chat_id]["level"]]["sessions"]:
        start_msg = f"🎉 Вы завершили все сессии уровня {user_states[chat_id]['level']}!\n"
        start_msg += f"⏳ Длинный перерыв ({duration // 60} мин) начат в {datetime.datetime.now().strftime('%H:%M')}"
    else:
        start_msg = f"⏳ {phase_name.capitalize()} начат в {datetime.datetime.now().strftime('%H:%M')}"

    bot.send_message(chat_id, start_msg)

    remaining_time = duration
    while remaining_time > 0:
        if stop_flags.get(chat_id, True):
            return

        if pause_flags.get(chat_id, False):
            while pause_flags.get(chat_id, False):
                time.sleep(1)
                if stop_flags.get(chat_id, True):
                    return
            # После возобновления обновляем время начала
            resume_msg = f"⏳ {phase_name.capitalize()} продолжен в {datetime.datetime.now().strftime('%H:%M')}"
            bot.send_message(chat_id, resume_msg)

        time.sleep(1)
        remaining_time -= 1

    if not stop_flags.get(chat_id, True):
        end_time = datetime.datetime.now().strftime("%H:%M")
        bot.send_message(chat_id, f"✅ {phase_name.capitalize()} завершен в {end_time}")

        if phase_name == "Рабочий период":
            send_motivation(chat_id)


sent_messages = set()


def send_motivation(chat_id):
    """Отправляет одно случайное мотивационное сообщение"""
    messages = [
        "Отличная работа! 🎉",
        "Ты молодец! Продолжай в том же духе! 💪",
        "Еще один шаг к цели! 🚀",
        "Отличная работа! Ты справился с этой сессией! 🎉",
        "Ты молодец! Продолжай в том же духе! 💪",
        "Сессия завершена! Теперь ты ближе к своей цели! 🌟",
        "Круто! Ты сделал ещё один шаг к успеху! 🚀",
        "Эта сессия позади! Отдохни и продолжай движение вперёд! ⏳",
        "Держись! Ты на правильном пути! 💪",
        "Ты справляешься отлично! Продолжай в том же духе! 🚀",
        "Каждая минута приближает тебя к цели! ⏳",
        "Ты можешь всё, что задумаешь! ✨",
        "Не сдавайся — ты уже близок к успеху! 🌟",
        "Маленькие шаги приводят к большим результатам! 🌱",
        "Ты делаешь потрясающую работу! 👏",
        "Сосредоточься и двигайся вперёд! 🔥",
        "Ты настоящий мастер продуктивности! 💡",
        "Успех — это просто последовательность маленьких побед! 🎉",
        "Ты как звезда: чем больше усилий прикладываешь, тем ярче сияешь! ✨🌟",
        "Каждый шаг — это новая страница в твоей истории успеха. Продолжай писать её! 📖🔥",
        "Даже маленький ручей может стать рекой. Продолжай двигаться вперёд! 💧➡️🌊",
        "Ты уже победил, просто начав этот путь. А теперь иди дальше и покоряй новые вершины! 🏔️🏆",
        "Сегодня ты лучше, чем вчера. А завтра будешь ещё лучше. Так работает прогресс! ⬆️💪",
        "Ты создаёшь своё будущее прямо сейчас. Каждое усилие — это кирпичик в твой фундамент успеха! 🏗️✨",
        "Ты не просто идёшь к цели — ты становишься тем человеком, который достоин её достичь! 🚀🎯",
        "Успех любит тех, кто не боится работать. Ты уже на пути к нему! 💼💥",
        "Помни: даже самый длинный путь начинается с одного шага. А ты уже сделал его! 👣🌍"
    ]

    # Очищаем историю после использования 80% сообщений
    if len(sent_messages) >= len(messages) * 0.8:
        sent_messages.clear()

    # Выбираем случайное сообщение, которого еще не было
    while True:
        msg = random.choice(messages)
        if msg not in sent_messages:
            sent_messages.add(msg)
            break

    time.sleep(1)  # Задержка 1 секунда перед отправкой
    logging.info(f"Отправка мотивационного сообщения: {msg}")
    bot.send_message(chat_id, msg)



# ====== Логика чата ======
def forward_to_admin(message):
    """Пересылка сообщения администратору"""
    try:
        # Пересылаем оригинальное сообщение
        forwarded = bot.forward_message(ADMIN_CHAT_ID, message.chat.id, message.message_id)
        # Создаем кнопку для ответа
        markup = types.InlineKeyboardMarkup()
        reply_button = types.InlineKeyboardButton(
            text="Ответить",
            callback_data=f"reply_{message.chat.id}_{forwarded.message_id}"
        )
        markup.add(reply_button)
        # Отправляем уведомление администратору
        bot.send_message(
            ADMIN_CHAT_ID,
            f"Сообщение от пользователя {message.from_user.first_name} (ID: {message.chat.id}):",
            reply_markup=markup
        )
    except Exception as e:
        logging.error(f"Ошибка пересылки сообщения: {e}")
        bot.reply_to(message, "⚠️ Не удалось отправить сообщение администратору.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("reply_"))
def handle_reply_callback(call):
    """Обработка нажатия кнопки ответа"""
    try:
        _, user_id, message_id = call.data.split('_')
        user_id = int(user_id)
        # Сохраняем связь администратора с пользователем
        user_admin_mapping[call.message.chat.id] = user_id
        save_data()
        # Просим администратора ввести ответ
        bot.send_message(
            call.message.chat.id,
            f"Введите ответ для пользователя {user_id}:",
            reply_markup=types.ForceReply(selective=False)
        )
    except Exception as e:
        logging.error(f"Ошибка обработки callback: {e}")
        bot.answer_callback_query(call.id, "⚠️ Ошибка обработки запроса")


@bot.message_handler(func=lambda message: message.reply_to_message and message.chat.id == ADMIN_CHAT_ID)
def handle_admin_reply(message):
    """Обработка ответа администратора"""
    try:
        # Получаем ID пользователя из user_admin_mapping
        user_id = user_admin_mapping.get(message.chat.id)
        if not user_id:
            bot.reply_to(message, "Ошибка: не найден пользователь для ответа.")
            return
        # Отправляем ответ пользователю
        bot.send_message(user_id, f"📨 Ответ от администратора:\n{message.text}")
        bot.reply_to(message, "✅ Ответ отправлен пользователю.")
        # Удаляем связь
        if message.chat.id in user_admin_mapping:
            del user_admin_mapping[message.chat.id]
            save_data()
    except Exception as e:
        logging.error(f"Ошибка отправки ответа: {e}")
        bot.reply_to(message, "⚠️ Не удалось отправить ответ пользователю.")


# ====== Запуск бота ======

if __name__ == '__main__':
    load_data()
    while True:
        try:
            logging.info("Бот запущен")
            bot.polling(none_stop=True)
        except Exception as e:
            logging.critical(f"Ошибка: {e}")
            time.sleep(10)