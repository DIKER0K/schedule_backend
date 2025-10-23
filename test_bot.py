import os
import requests
from datetime import datetime, timedelta
import telebot
from telebot import types
from requests.adapters import HTTPAdapter, Retry

# ============================ НАСТРОЙКИ ============================
BOT_TOKEN = "8468865986:AAGy5vwdtetdb4_mw0r27CU1nJuF7ai9-28"
API_URL = "http://147.78.65.214:3000"

bot = telebot.TeleBot(BOT_TOKEN)

# ============================ HTTP СЕССИЯ ============================
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
session.mount("http://", HTTPAdapter(max_retries=retries))
session.mount("https://", HTTPAdapter(max_retries=retries))

def _get(url, **kwargs):
    return session.get(url, timeout=10, **kwargs)

def _post(url, **kwargs):
    return session.post(url, timeout=10, **kwargs)

def _put(url, **kwargs):
    return session.put(url, timeout=10, **kwargs)

# ============================ ВСПОМОГАТЕЛЬНЫЕ ============================
days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]

def get_current_day():
    i = datetime.now().weekday()
    if i == 6:  # воскресенье
        return None
    return days_ru[i]

def get_tomorrow_day():
    i = (datetime.now() + timedelta(days=1)).weekday()
    if i == 6:
        return None
    return days_ru[i]

# ============================ API ============================
def api_get_user(user_id):
    try:
        r = _get(f"{API_URL}/users/{user_id}")
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None

def api_create_user(user_id, role="student"):
    payload = {"user_id": user_id, "role": role}
    try:
        r = _post(f"{API_URL}/users/", json=payload)
        return r.json() if r.status_code == 200 else None
    except:
        return None

def api_update_user(user_id, data: dict):
    try:
        r = _put(f"{API_URL}/users/{user_id}", json=data)
        return r.json() if r.status_code == 200 else None
    except:
        return None

def api_get_all_groups():
    try:
        r = _get(f"{API_URL}/schedule/")
        if r.status_code == 200:
            return [s["group_name"] for s in r.json()]
        return []
    except:
        return []

def api_get_schedule(group_name):
    try:
        r = _get(f"{API_URL}/schedule/{group_name}")
        return r.json() if r.status_code == 200 else None
    except:
        return None

def api_get_teacher_schedule(fio):
    try:
        r = _get(f"{API_URL}/schedule/teacher/{fio}")
        return r.json() if r.status_code == 200 else None
    except:
        return None

# ============================ ФОРМАТИРОВАНИЕ ============================
def format_schedule(schedule_doc: dict, day: str):
    if not schedule_doc:
        return "❌ Расписание не найдено"
    schedule = schedule_doc.get("schedule", {})
    days = schedule.get("days", {})
    zero = schedule.get("zero_lesson", {}).get(day, {})
    lessons = days.get(day, {})

    if not zero and not lessons:
        return f"📅 В {day} пар нет"

    result = [f"📚 Расписание на {day} ({schedule_doc.get('group_name')})", ""]
    if zero:
        s = f"0. {zero.get('subject', '')}"
        if zero.get("classroom"):
            s += f" {zero['classroom']} каб."
        if zero.get("teacher"):
            s += f" ({zero['teacher']})"
        result.append(s)

    for num, info in sorted(lessons.items(), key=lambda x: int(x[0])):
        s = f"{num}. {info.get('subject', '')}"
        if info.get("classroom"):
            s += f" {info['classroom']} каб."
        if info.get("teacher"):
            s += f" ({info['teacher']})"
        result.append(s)
    return "\n".join(result)

def format_teacher_schedule(schedule_doc: dict, day: str):
    if not schedule_doc or "schedule" not in schedule_doc:
        return "❌ Расписание не найдено"
    fio = schedule_doc.get("teacher_fio", "")
    sch = schedule_doc["schedule"]
    lines = [f"👨‍🏫 Расписание {fio} на {day}", ""]

    for shift_name, shift_data in sch.items():
        if day not in shift_data:
            continue
        for num, info in shift_data[day].items():
            s = f"{num}. {info.get('subject', '')} — {info.get('group', '')}"
            if info.get("classroom"):
                s += f" ({info['classroom']})"
            s += f" [{ '1' if shift_name == 'first_shift' else '2' } смена]"
            lines.append(s)

    if len(lines) == 2:
        return f"📅 В {day} занятий нет"
    return "\n".join(lines)

# ============================ КНОПКИ ============================
def main_keyboard(role="student"):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add(types.KeyboardButton("📅 Сегодня"), types.KeyboardButton("📅 Завтра"))
    kb.add(types.KeyboardButton("⚙️ Настройки"))
    if role in ("teacher", "admin"):
        kb.add(types.KeyboardButton("👨‍🏫 Панель преподавателя"))
    if role == "admin":
        kb.add(types.KeyboardButton("👑 Админ панель"))
    return kb

# ============================ ХЭНДЛЕРЫ ============================
@bot.message_handler(commands=["start"])
def on_start(message):
    user_id = message.from_user.id
    user = api_get_user(user_id)
    if not user:
        user = api_create_user(user_id)
    if not user:
        bot.send_message(user_id, "❌ Сервер недоступен. Попробуйте позже.")
        return

    role = user.get("role", "student")
    fio = user.get("teacher_fio")

    # преподаватель без ФИО → запросим
    if role == "teacher" and not fio:
        msg = bot.send_message(user_id, "👨‍🏫 Введите ваше ФИО полностью (например: Иванов Иван Иванович):")
        bot.register_next_step_handler(msg, process_teacher_fio)
        return

    if role == "teacher" and fio:
        bot.send_message(user_id, f"👨‍🏫 Добро пожаловать, {fio}!", reply_markup=main_keyboard("teacher"))
        return

    if role == "admin":
        bot.send_message(user_id, "👑 Добро пожаловать, админ!", reply_markup=main_keyboard("admin"))
        return

    # студент
    groups = api_get_all_groups()
    if not groups:
        bot.send_message(user_id, "❌ Расписание недоступно.")
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for i in range(0, len(groups), 2):
        kb.add(*[types.KeyboardButton(g) for g in groups[i:i+2]])
    bot.send_message(user_id, "👋 Выберите вашу группу:", reply_markup=kb)

def short_fio(fio: str):
    parts = fio.strip().split()
    if len(parts) < 2:
        return fio
    fam = parts[0].capitalize()
    initials = "".join(p[0].upper() + "." for p in parts[1:3])
    return f"{fam} {initials}"

def process_teacher_fio(message):
    user_id = message.from_user.id
    fio = message.text.strip()
    if len(fio.split()) < 2:
        msg = bot.send_message(user_id, "❌ Введите корректное ФИО полностью:")
        bot.register_next_step_handler(msg, process_teacher_fio)
        return

    short = short_fio(fio)
    api_update_user(user_id, {"teacher_fio": short})
    bot.send_message(user_id, f"✅ {short}, вас зарегистрировали!", reply_markup=main_keyboard("teacher"))

# ============================ КНОПКИ РАСПИСАНИЯ ============================
@bot.message_handler(func=lambda m: m.text in ["📅 Сегодня", "📅 Завтра"])
def on_schedule(message):
    user_id = message.from_user.id
    user = api_get_user(user_id)
    if not user:
        bot.send_message(user_id, "❌ Сервер недоступен.")
        return

    role = user.get("role", "student")
    day = get_current_day() if message.text == "📅 Сегодня" else get_tomorrow_day()
    if not day:
        bot.send_message(user_id, "🎉 Воскресенье — выходной!")
        return

    if role == "teacher":
        fio = user.get("teacher_fio")
        if not fio:
            bot.send_message(user_id, "❌ Укажите ваше ФИО через /start")
            return
        sch = api_get_teacher_schedule(fio)
        text = format_teacher_schedule(sch, day)
        bot.send_message(user_id, text)
        return

    # студент
    group = user.get("group_name")
    if not group:
        bot.send_message(user_id, "❌ Сначала выберите вашу группу через /start")
        return
    sch = api_get_schedule(group)
    text = format_schedule(sch, day)
    bot.send_message(user_id, text)

print("🤖 Бот запущен. Подключен к API:", API_URL)
bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
