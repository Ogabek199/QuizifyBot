import os
import asyncio
import logging
import random
import string
import sqlite3
import time
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, BotCommand
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN muhit o'zgaruvchisi kerak. .env faylini yarating.")

# Admin configuration: comma-separated usernames (without @) and optional comma-separated IDs
ADMIN_USERNAMES = [u.strip().lstrip('@') for u in os.environ.get('ADMIN_USERNAMES', 'otaxonov_o17').split(',') if u.strip()]
ADMIN_IDS = [int(x) for x in os.environ.get('ADMIN_IDS', '').split(',') if x.strip().isdigit()]

def is_admin(telegram_id: Optional[int], username: Optional[str] = None) -> bool:
    try:
        if telegram_id and telegram_id in ADMIN_IDS:
            return True
    except Exception:
        pass
    if username:
        uname = username.lstrip('@').lower()
        return any(uname == a.lower() for a in ADMIN_USERNAMES)
    return False

DB_PATH = os.path.join(os.path.dirname(__file__), 'quizify.db')
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Upload & parsing limits
MAX_DOCX_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_PDF_SIZE = 15 * 1024 * 1024   # 15 MB
MAX_QUESTIONS = 1500              # maximum allowed questions per test
PARSE_TIMEOUT = 60                # seconds to allow parsing

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# ─────────────────────────── Internationalization ────────────────────────────
SUPPORTED_LANGS = ['uz', 'ru', 'en']

TRANSLATIONS = {
    'welcome': {
        'uz': "👋 <b>Quizify botga xush kelibsiz!</b>\n\nBu bot orqali DOCX/PDF testlarni yuklash, ulashish va yechish mumkin.",
        'ru': "👋 <b>Добро пожаловать в Quizify!</b>\n\nЭтот бот позволяет загружать, делиться и проходить тесты в формате DOCX/PDF.",
        'en': "👋 <b>Welcome to Quizify!</b>\n\nThis bot lets you upload, share and take tests (DOCX/PDF).",
    },
    'menu_tests': {'uz': '📚 Testlar', 'ru': '📚 Тесты', 'en': '📚 Tests'},
    'menu_upload': {'uz': '➕ Test yuklash', 'ru': '➕ Загрузить тест', 'en': '➕ Upload test'},
    'menu_results': {'uz': '📊 Mening natijalarim', 'ru': '📊 Мои результаты', 'en': '📊 My results'},
    'menu_profile': {'uz': '👤 Profil', 'ru': '👤 Профиль', 'en': '👤 Profile'},
    'menu_help': {'uz': 'ℹ️ Yordam', 'ru': 'ℹ️ Помощь', 'en': 'ℹ️ Help'},
    'upload_prompt': {
        'uz': "📎 PDF yoki DOCX fayl yoki test matnini yuboring.\n\n<i>Format: 1. Savol matni\nA) variant\nJavob: A</i>",
        'ru': "📎 Отправьте PDF/DOCX файл или текст теста.\n\n<i>Формат: 1. Вопрос\nA) вариант\nОтвет: A</i>",
        'en': "📎 Send a PDF/DOCX file or test text.\n\n<i>Format: 1. Question\nA) option\nAnswer: A</i>",
    },
    'help_text': {
        'uz': "ℹ️ <b>Yordam</b>\n\n• PDF yoki DOCX faylni yuboring — bot savollarni ajratib oladi\n• Natijali testda balllar hisoblanadi\n• Mashq testida faqat savollar ko'rinadi\n• Test kodi orqali do'stlaringizga ulashing\n\n📞 Admin: @otaxonov_o17",
        'ru': "ℹ️ <b>Помощь</b>\n\n• Отправьте PDF/DOCX — бот извлечет вопросы\n• В тестах с результатом считаются баллы\n• В тренировочных тестах баллы не считаются\n• Делитесь тестом по коду\n\n📞 Админ: @otaxonov_o17",
        'en': "ℹ️ <b>Help</b>\n\n• Send a PDF/DOCX — the bot will extract questions\n• Result tests are scored\n• Practice tests are not scored\n• Share tests via code\n\n📞 Admin: @otaxonov_o17",
    },
    'only_pdf_docx': {
        'uz': "❌ Faqat PDF yoki DOCX formatlar qabul qilinadi.",
        'ru': "❌ Поддерживаются только PDF или DOCX.",
        'en': "❌ Only PDF or DOCX files are accepted.",
    },
    'docx_too_large': {
        'uz': "❌ DOCX fayl maksimal {max_mb} MB bo'lishi kerak. Siz yuborgan: {size_mb} MB",
        'ru': "❌ DOCX должен быть не больше {max_mb} МБ. Ваш файл: {size_mb} МБ",
        'en': "❌ DOCX must be <= {max_mb} MB. Your file: {size_mb} MB",
    },
    'pdf_too_large': {
        'uz': "❌ PDF fayl maksimal {max_mb} MB bo'lishi kerak. Siz yuborgan: {size_mb} MB",
        'ru': "❌ PDF должен быть не больше {max_mb} МБ. Ваш файл: {size_mb} МБ",
        'en': "❌ PDF must be <= {max_mb} MB. Your file: {size_mb} MB",
    },
    'parsing_timeout': {
        'uz': "⏳ Tahlil {timeout} soniyadan oshib ketdi. Iltimos faylni kichikroq bo'laklarga bo'lib yuboring.",
        'ru': "⏳ Анализ превысил {timeout} секунд. Пожалуйста, разбейте файл на части.",
        'en': "⏳ Parsing exceeded {timeout} seconds. Please split the file into smaller parts.",
    },
    'found_questions': {
        'uz': "✅ <b>{count} ta savol topildi!</b>\n📋 Test kodi: <code>{code}</code>\n\nTest turini tanlang:",
        'ru': "✅ <b>Найдено {count} вопросов!</b>\n📋 Код теста: <code>{code}</code>\n\nВыберите тип теста:",
        'en': "✅ <b>{count} questions found!</b>\n📋 Test code: <code>{code}</code>\n\nChoose test type:",
    },
    'no_questions_parse_error': {
        'uz': "⚠️ Fayldan savollar ajratib olishda muammo.\nIltimos fayl formatini tekshiring yoki savollarni oddiy matn sifatida yuboring.",
        'ru': "⚠️ Проблема при извлечении вопросов.\nПроверьте формат файла или отправьте текст.",
        'en': "⚠️ Problem extracting questions from file.\nCheck file format or send the test as plain text.",
    },
    'choose_language': {
        'uz': "🌐 Tilni tanlang:",
        'ru': "🌐 Выберите язык:",
        'en': "🌐 Choose language:",
    },
    'lang_set': {
        'uz': "✅ Til o'zgartirildi: O'zbekcha",
        'ru': "✅ Язык изменён: Русский",
        'en': "✅ Language changed: English",
    }
}


def db_get_user_lang(telegram_id: int) -> str:
    conn = get_conn()
    try:
        row = conn.execute('SELECT lang FROM users WHERE telegram_id=?', (telegram_id,)).fetchone()
        if row and row['lang'] in SUPPORTED_LANGS:
            return row['lang']
    finally:
        conn.close()
    return 'uz'


def db_set_user_lang(telegram_id: int, lang: str):
    if lang not in SUPPORTED_LANGS:
        return
    conn = get_conn()
    try:
        conn.execute('UPDATE users SET lang=? WHERE telegram_id=?', (lang, telegram_id))
        conn.commit()
    finally:
        conn.close()


def t(key: str, telegram_id: int = None) -> str:
    """Get translated string for user's language (fallback to uz)."""
    lang = 'uz'
    try:
        if telegram_id:
            lang = db_get_user_lang(telegram_id)
    except Exception:
        pass
    return TRANSLATIONS.get(key, {}).get(lang, TRANSLATIONS.get(key, {}).get('uz', ''))


# Har bir foydalanuvchi uchun aktiv timer tasklari
# key: telegram_id -> asyncio.Task
_timer_tasks: dict[int, asyncio.Task] = {}


# ─────────────────────────────── FSM States ──────────────────────────────────

class UploadStates(StatesGroup):
    waiting_file = State()
    waiting_title = State()


class TakeTestStates(StatesGroup):
    choosing_mode = State()  # vaqtli/vaqtsiz tanlash
    choosing_time = State()  # har savol uchun soniya tanlash
    answering = State()  # savollar yechilmoqda


class EditStates(StatesGroup):
    waiting_new_title = State()


# ───────────────────────────────── Database ───────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript('''
                       CREATE TABLE IF NOT EXISTS users
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           telegram_id
                           INTEGER
                           UNIQUE
                           NOT
                           NULL,
                           fullname
                           TEXT,
                           username
                           TEXT,
                           lang
                           TEXT DEFAULT 'uz',
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       );

                       CREATE TABLE IF NOT EXISTS tests
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           owner_telegram_id
                           INTEGER
                           NOT
                           NULL,
                           title
                           TEXT
                           NOT
                           NULL,
                           file_id
                           TEXT,
                           mime_type
                           TEXT,
                           code
                           TEXT
                           UNIQUE
                           NOT
                           NULL,
                           type
                           TEXT
                           DEFAULT
                           'practice',
                           has_answers
                           INTEGER
                           DEFAULT
                           0,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       );

                       CREATE TABLE IF NOT EXISTS questions
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           test_code
                           TEXT
                           NOT
                           NULL,
                           question
                           TEXT
                           NOT
                           NULL,
                           option_a
                           TEXT,
                           option_b
                           TEXT,
                           option_c
                           TEXT,
                           option_d
                           TEXT,
                           correct_answer
                           TEXT,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       );

                       CREATE TABLE IF NOT EXISTS results
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           user_telegram_id
                           INTEGER
                           NOT
                           NULL,
                           test_code
                           TEXT
                           NOT
                           NULL,
                           correct_count
                           INTEGER
                           DEFAULT
                           0,
                           wrong_count
                           INTEGER
                           DEFAULT
                           0,
                           skipped_count
                           INTEGER
                           DEFAULT
                           0,
                           percentage
                           REAL
                           DEFAULT
                           0.0,
                           time_per_q
                           INTEGER
                           DEFAULT
                           0,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       );

                       CREATE INDEX IF NOT EXISTS idx_q_code ON questions(test_code);
                       CREATE INDEX IF NOT EXISTS idx_r_user ON results(user_telegram_id);
                       CREATE INDEX IF NOT EXISTS idx_r_test ON results(test_code);
                       CREATE INDEX IF NOT EXISTS idx_t_owner ON tests(owner_telegram_id);
                       ''')
    conn.commit()

    # Migration: ensure skipped_count and time_per_q exist for older DBs
    try:
        cols = [r['name'] for r in conn.execute("PRAGMA table_info(results)").fetchall()]
        to_add = []
        if 'skipped_count' not in cols:
            conn.execute("ALTER TABLE results ADD COLUMN skipped_count INTEGER DEFAULT 0")
            to_add.append('skipped_count')
        if 'time_per_q' not in cols:
            conn.execute("ALTER TABLE results ADD COLUMN time_per_q INTEGER DEFAULT 0")
            to_add.append('time_per_q')
        if to_add:
            conn.commit()
            logger.info(f"DB migration: added {', '.join(to_add)} to results table")

        # Migration: ensure users.lang exists
        user_cols = [r['name'] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if 'lang' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'uz'")
            conn.commit()
            logger.info("DB migration: added lang to users table")
    except Exception as e:
        logger.exception(f"DB migration check failed: {e}")
    finally:
        conn.close()


def db_ensure_user(telegram_id: int, fullname: str = None, username: str = None):
    conn = get_conn()
    try:
        conn.execute(
            'INSERT OR IGNORE INTO users (telegram_id, fullname, username) VALUES (?,?,?)',
            (telegram_id, fullname, username)
        )
        conn.execute(
            'UPDATE users SET fullname=COALESCE(?,fullname), username=COALESCE(?,username) WHERE telegram_id=?',
            (fullname, username, telegram_id)
        )
        conn.commit()
    finally:
        conn.close()


def db_generate_unique_code(prefix: str = 'TEST') -> str:
    prefix = ''.join(c for c in prefix.upper() if c.isalpha())[:5] or 'TEST'
    conn = get_conn()
    for _ in range(20):
        code = f"{prefix}-{''.join(random.choices(string.digits, k=4))}"
        if not conn.execute('SELECT 1 FROM tests WHERE code=?', (code,)).fetchone():
            conn.close()
            return code
    conn.close()
    return f"TEST-{''.join(random.choices(string.digits, k=6))}"


def db_insert_test(owner_id: int, title: str, file_id: str, mime: str, code: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            'INSERT INTO tests (owner_telegram_id,title,file_id,mime_type,code) VALUES (?,?,?,?,?)',
            (owner_id, title, file_id, mime, code)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def db_update_test(code: str, type_: str = None, has_answers: int = None, title: str = None):
    conn = get_conn()
    try:
        parts, vals = [], []
        if type_ is not None: parts.append('type=?');        vals.append(type_)
        if has_answers is not None: parts.append('has_answers=?'); vals.append(has_answers)
        if title is not None: parts.append('title=?');       vals.append(title)
        if not parts: return
        vals.append(code)
        conn.execute(f'UPDATE tests SET {",".join(parts)} WHERE code=?', vals)
        conn.commit()
    finally:
        conn.close()


def db_insert_question(test_code: str, question: str,
                       a=None, b=None, c=None, d=None, correct=None):
    conn = get_conn()
    try:
        conn.execute(
            'INSERT INTO questions (test_code,question,option_a,option_b,option_c,option_d,correct_answer)'
            ' VALUES (?,?,?,?,?,?,?)',
            (test_code, question, a, b, c, d, correct)
        )
        conn.commit()
    finally:
        conn.close()


def db_update_question_answer(q_id: int, correct_answer: str):
    conn = get_conn()
    try:
        conn.execute('UPDATE questions SET correct_answer=? WHERE id=?', (correct_answer, q_id))
        conn.commit()
    finally:
        conn.close()


def db_get_test(code: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute('SELECT * FROM tests WHERE code=?', (code,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def db_get_test_questions(test_code: str) -> list:
    """Savollarni id bo'yicha TARTIBDA qaytaradi"""
    conn = get_conn()
    try:
        rows = conn.execute(
            'SELECT * FROM questions WHERE test_code=? ORDER BY id ASC', (test_code,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def db_has_correct_answers(test_code: str) -> bool:
    """Return True if at least one question has a non-empty correct_answer"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM questions WHERE test_code=? AND TRIM(IFNULL(correct_answer,''))<>''",
            (test_code,)
        ).fetchone()
        return (row['c'] or 0) > 0
    finally:
        conn.close()


def db_get_all_tests(limit: int = 50, offset: int = 0) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            'SELECT * FROM tests ORDER BY created_at DESC LIMIT ? OFFSET ?', (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def db_save_result(user_id: int, test_code: str,
                   correct: int, wrong: int, skipped: int,
                   pct: float, time_per_q: int = 0):
    conn = get_conn()
    try:
        conn.execute(
            'INSERT INTO results (user_telegram_id,test_code,correct_count,wrong_count,'
            'skipped_count,percentage,time_per_q) VALUES (?,?,?,?,?,?,?)',
            (user_id, test_code, correct, wrong, skipped, pct, time_per_q)
        )
        conn.commit()
    finally:
        conn.close()


def db_get_my_results(user_id: int) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            '''SELECT r.*, t.title
               FROM results r
                        JOIN tests t ON r.test_code = t.code
               WHERE r.user_telegram_id = ?
               ORDER BY r.created_at DESC LIMIT 20''',
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def db_get_profile(telegram_id: int) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            'SELECT fullname,username FROM users WHERE telegram_id=?', (telegram_id,)
        ).fetchone()
        created = conn.execute(
            'SELECT COUNT(*) as c FROM tests WHERE owner_telegram_id=?', (telegram_id,)
        ).fetchone()['c']
        res = conn.execute(
            'SELECT COUNT(*) as cnt, ROUND(AVG(percentage),1) as avg FROM results WHERE user_telegram_id=?',
            (telegram_id,)
        ).fetchone()
        return {
            'fullname': row['fullname'] if row else None,
            'username': row['username'] if row else None,
            'created_tests': created,
            'done_count': res['cnt'] or 0,
            'avg_percent': res['avg'] or 0.0,
        }
    finally:
        conn.close()


def db_get_test_stats(test_code: str) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            'SELECT COUNT(*) as cnt, ROUND(AVG(percentage),1) as avg,'
            ' ROUND(MAX(percentage),1) as best, ROUND(MIN(percentage),1) as worst'
            ' FROM results WHERE test_code=?', (test_code,)
        ).fetchone()
        qcount = conn.execute(
            'SELECT COUNT(*) as c FROM questions WHERE test_code=?', (test_code,)
        ).fetchone()['c']
        return {
            'attempts': row['cnt'] or 0,
            'avg': row['avg'] or 0.0,
            'best': row['best'] or 0.0,
            'worst': row['worst'] or 0.0,
            'questions': qcount,
        }
    finally:
        conn.close()


def db_get_global_stats() -> dict:
    conn = get_conn()
    try:
        users = conn.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
        tests = conn.execute('SELECT COUNT(*) as c FROM tests').fetchone()['c']
        questions = conn.execute('SELECT COUNT(*) as c FROM questions').fetchone()['c']
        row = conn.execute('SELECT COUNT(*) as c, ROUND(AVG(percentage),2) as avg FROM results').fetchone()
        results = row['c'] if row else 0
        avg = row['avg'] if row and row['avg'] is not None else 0.0
        return {
            'users': users,
            'tests': tests,
            'questions': questions,
            'results': results,
            'avg_percentage': avg,
        }
    finally:
        conn.close()


def db_get_top_users(limit: int = 10) -> list:
    """Return top users ordered by number of attempts (results). Each item: telegram_id, fullname, username, attempts, avg_pct"""
    conn = get_conn()
    try:
        rows = conn.execute(
            '''SELECT u.telegram_id, u.fullname, u.username,
                      COUNT(r.id) as attempts,
                      ROUND(AVG(r.percentage),1) as avg_pct
               FROM users u
               LEFT JOIN results r ON u.telegram_id = r.user_telegram_id
               GROUP BY u.telegram_id
               ORDER BY attempts DESC
               LIMIT ?''', (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def db_delete_test(code: str, owner_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute(
            'DELETE FROM tests WHERE code=? AND owner_telegram_id=?', (code, owner_id)
        )
        conn.execute('DELETE FROM questions WHERE test_code=?', (code,))
        conn.execute('DELETE FROM results   WHERE test_code=?', (code,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ──────────────────────────────── Keyboards ──────────────────────────────────

def main_menu_kb(telegram_id: int = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t('menu_tests', telegram_id), callback_data='menu:tests')],
        [InlineKeyboardButton(text=t('menu_upload', telegram_id), callback_data='menu:upload')],
        [InlineKeyboardButton(text=t('menu_results', telegram_id), callback_data='menu:results')],
        [InlineKeyboardButton(text=t('menu_profile', telegram_id), callback_data='menu:profile')],
        [InlineKeyboardButton(text=t('menu_help', telegram_id), callback_data='menu:help')],
    ])


def back_kb(target: str = 'menu:main') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔙 Orqaga', callback_data=target)]
    ])


def test_type_kb(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Natijali test', callback_data=f'type:{code}:result')],
        [InlineKeyboardButton(text='📚 Mashq testi', callback_data=f'type:{code}:practice')],
    ])


def has_answers_kb(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Ha, faylda bor', callback_data=f'ans:{code}:yes')],
        [InlineKeyboardButton(text="❌ Yo'q", callback_data=f'ans:{code}:no')],
    ])


def manual_or_practice_kb(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, qo'lda kiritaman", callback_data=f'manual:{code}:yes')],
        [InlineKeyboardButton(text='📚 Mashq testi sifatida saqlash', callback_data=f'manual:{code}:no')],
    ])


def set_answer_kb(code: str, q_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='A', callback_data=f'setans:{code}:{q_idx}:A'),
        InlineKeyboardButton(text='B', callback_data=f'setans:{code}:{q_idx}:B'),
        InlineKeyboardButton(text='C', callback_data=f'setans:{code}:{q_idx}:C'),
        InlineKeyboardButton(text='D', callback_data=f'setans:{code}:{q_idx}:D'),
    ]])


def quiz_mode_kb(code: str) -> InlineKeyboardMarkup:
    """Vaqtli yoki vaqtsiz tanlash"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⏱ Vaqtli test', callback_data=f'qmode:{code}:timed')],
        [InlineKeyboardButton(text='♾ Vaqtsiz test', callback_data=f'qmode:{code}:notimed')],
    ])


def time_select_kb(code: str) -> InlineKeyboardMarkup:
    """Har bir savol uchun vaqt tanlash (soniyada)"""
    options = [
        ('15 soniya', 15), ('20 soniya', 20), ('30 soniya', 30),
        ('45 soniya', 45), ('60 soniya', 60), ('90 soniya', 90),
    ]
    rows = []
    for label, sec in options:
        rows.append([InlineKeyboardButton(text=label, callback_data=f'qtime:{code}:{sec}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def answer_kb(code: str, q_id: int, q_idx: int, total: int) -> InlineKeyboardMarkup:
    """Test yechish klaviaturasi: A-D + to'xtatish va bosh sahifa tugmalari"""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='A', callback_data=f'qa:{code}:{q_id}:{q_idx}:{total}:A'),
        InlineKeyboardButton(text='B', callback_data=f'qa:{code}:{q_id}:{q_idx}:{total}:B'),
        InlineKeyboardButton(text='C', callback_data=f'qa:{code}:{q_id}:{q_idx}:{total}:C'),
        InlineKeyboardButton(text='D', callback_data=f'qa:{code}:{q_id}:{q_idx}:{total}:D'),
    ], [
        InlineKeyboardButton(text="⏸ To'xtatish", callback_data=f'pause:{code}:{q_id}:{q_idx}:{total}'),
        InlineKeyboardButton(text='🏠 Bosh sahifa', callback_data='menu:main'),
    ]])


def test_manage_kb(code: str, is_owner: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text='▶️ Testni boshlash', callback_data=f'start_test:{code}')]]
    if is_owner:
        rows.append([
            InlineKeyboardButton(text='✏️ Tahrirlash', callback_data=f'edit_test:{code}'),
            InlineKeyboardButton(text="🗑️ O'chirish", callback_data=f'del_test:{code}'),
        ])
        rows.append([InlineKeyboardButton(text='📈 Statistika', callback_data=f'stat:{code}')])
    rows.append([InlineKeyboardButton(text='🔙 Orqaga', callback_data='menu:tests')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def retry_kb(code: str) -> InlineKeyboardMarkup:
    """Keyboard shown after finishing a test: retry or go back to tests"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔁 Qayta ishlash', callback_data=f'restart_test:{code}')],
        [InlineKeyboardButton(text='🔙 Orqaga', callback_data='menu:tests')],
    ])


def tests_list_kb(tests: list, page: int = 0, page_size: int = 8) -> InlineKeyboardMarkup:
    start = page * page_size
    chunk = tests[start:start + page_size]
    rows = []
    for t in chunk:
        icon = '📊' if t['type'] == 'result' else '📚'
        rows.append([InlineKeyboardButton(
            text=f"{icon} {t['title']} [{t['code']}]",
            callback_data=f'view_test:{t["code"]}'
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'tests_page:{page - 1}'))
    if start + page_size < len(tests):
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'tests_page:{page + 1}'))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text='🔙 Orqaga', callback_data='menu:main')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────────────────────────── Helpers ────────────────────────────────────

def fmt_question(q: dict, idx: int, total: int, time_per_q: int = 0) -> str:
    opts = []
    for key, letter in [('option_a', 'A'), ('option_b', 'B'), ('option_c', 'C'), ('option_d', 'D')]:
        if q.get(key):
            opts.append(f"  {letter}) {q[key]}")
    # Show a red alert emoji when 5 seconds or less remain (Telegram doesn't support colored text)
    if time_per_q > 0:
        alert = "🔴 " if time_per_q <= 5 else ""
        timer_line = f"\n⏱ {alert}Vaqt: <b>{time_per_q} soniya</b>"
    else:
        timer_line = ""
    return (
            f"📝 <b>Savol {idx + 1} / {total}</b>{timer_line}\n\n"
            f"{q['question']}\n\n"
            + ('\n'.join(opts) if opts else "(variantlar yo'q)")
    )


def cancel_timer(uid: int):
    """Foydalanuvchining mavjud timer taskini bekor qiladi"""
    task = _timer_tasks.pop(uid, None)
    if task and not task.done():
        task.cancel()


async def safe_query_answer(query: CallbackQuery):
    """Answer callback query safely; ignore "query is too old" errors."""
    try:
        await query.answer()
    except TelegramBadRequest as e:
        # Common when the callback is old — ignore
        logger.debug(f"query.answer skipped: {e}")
    except Exception as e:
        logger.exception(f"query.answer error: {e}")


async def auto_skip_task(
        bot: Bot,
        state: FSMContext,
        uid: int,
        chat_id: int,
        msg_id: int,
        code: str,
        q_id: int,
        q_idx: int,
        total: int,
        time_per_q: int
):
    """Vaqtni real-time ko'rsatib, tugagach keyingi savolga o'tkazadi"""
    remaining = int(time_per_q)

    # Loop countdown, update message every second
    try:
        while remaining > 0:
            await asyncio.sleep(1)
            remaining -= 1

            # If task cancelled externally, stop
            t = _timer_tasks.get(uid)
            if t is None or t.cancelled():
                return

            s = await state.get_data()
            # If user moved on or paused, stop updating
            if s.get('q_index') != q_idx or s.get('paused'):
                return

            # Update message text to show remaining seconds
            try:
                questions = db_get_test_questions(code)
                if q_idx < len(questions):
                    q = questions[q_idx]
                    await bot.edit_message_text(
                        fmt_question(q, q_idx, total, remaining),
                        chat_id=chat_id,
                        message_id=msg_id,
                        parse_mode='HTML',
                        reply_markup=answer_kb(code, q['id'], q_idx, total)
                    )
            except Exception:
                # ignore edit errors (rate limits, message changed, etc.)
                pass

        # Time expired -> treat as skipped
        s = await state.get_data()
        if s.get('q_index') != q_idx:
            return
        skipped = s.get('skipped', 0) + 1
        await state.update_data(skipped=skipped, q_index=q_idx + 1)

        questions = db_get_test_questions(code)
        next_idx = q_idx + 1

        if next_idx < total:
            next_q = questions[next_idx]
            _timer_tasks.pop(uid, None)
            # send next question
            sent = await bot.send_message(
                chat_id,
                fmt_question(next_q, next_idx, total, time_per_q),
                parse_mode='HTML',
                reply_markup=answer_kb(code, next_q['id'], next_idx, total)
            )
            # start new timer for next question
            task = asyncio.create_task(
                auto_skip_task(
                    bot, state, uid, chat_id,
                    sent.message_id, code,
                    next_q['id'], next_idx, total, time_per_q
                )
            )
            _timer_tasks[uid] = task
            # remove reply_markup from old message
            try:
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=None)
            except Exception:
                pass
        else:
            # Test finished due to timeout
            await finish_quiz(bot, state, uid, chat_id, code, total, timed_out=True)
    except asyncio.CancelledError:
        # task cancelled normally
        return
    except Exception as e:
        logger.exception(f"auto_skip_task error: {e}")


async def finish_quiz(
        bot: Bot,
        state: FSMContext,
        uid: int,
        chat_id: int,
        code: str,
        total: int,
        timed_out: bool = False
):
    """Test yakunlash — natijalarni hisoblash va saqlash"""
    cancel_timer(uid)
    s = await state.get_data()
    test_type = s.get('test_type', 'practice')
    correct = s.get('correct', 0)
    wrong = s.get('wrong', 0)
    skipped = s.get('skipped', 0)
    time_per_q = s.get('time_per_q', 0)
    await state.clear()

    if test_type == 'result':
        answered = correct + wrong
        pct = round(correct / total * 100, 1) if total > 0 else 0.0
        db_save_result(uid, code, correct, wrong, skipped, pct, time_per_q)
        emoji = '🏆' if pct >= 90 else '👍' if pct >= 60 else '📚'
        timeout_note = "\n⏰ Ba'zi savollar vaqt tugab o'tkazib yuborildi." if timed_out else ""
        text = (
            f"{emoji} <b>Test yakunlandi!</b>{timeout_note}\n\n"
            f"✅ To'g'ri:    {correct}\n"
            f"❌ Noto'g'ri:  {wrong}\n"
            f"⏭ O'tkazildi: {skipped}\n"
            f"📊 Natija:     <b>{pct}%</b>"
        )
    else:
        timeout_note = "\n⏰ Ba'zi savollar vaqt tugab o'tkazib yuborildi." if timed_out else ""
        text = f"📚 <b>Mashq testi yakunlandi!</b>{timeout_note}\n\nBarcha savollar ko'rib chiqildi."

    try:
        await bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=retry_kb(code))
    except Exception as e:
        logger.exception(f"finish_quiz send error: {e}")


# ────────────────────────── Quiz start flow ──────────────────────────────────

async def ask_quiz_mode(message: Message, state: FSMContext, code: str):
    """Testni boshlashdan oldin vaqtli/vaqtsiz so'raymiz"""
    questions = db_get_test_questions(code)
    if not questions:
        await message.answer("❌ Bu testda savollar yo'q.")
        return
    test = db_get_test(code)
    if not test:
        await message.answer("❌ Test topilmadi.")
        return

    # If test is marked as 'result' but no correct answers exist, prevent starting
    if test.get('type') == 'result' and not db_has_correct_answers(code):
        owner_id = test.get('owner_telegram_id')
        if message.from_user.id == owner_id:
            await message.answer(
                "⚠️ Ushbu test 'Natijali' deb belgilangan, lekin savollarda to'g'ri javoblar topilmadi.\n"
                "Qo'lda javoblarni kiritishni xohlaysizmi yoki testni mashq sifatida saqlaysizmi?",
                reply_markup=manual_or_practice_kb(code)
            )
            return
        else:
            await message.answer(
                "⚠️ Ushbu test natijali deb belgilangan, ammo to'g'ri javoblar mavjud emas.\n"
                "Test egasi javoblarni kiritmaguncha bu testni yecha olmaysiz.\n"
                "Testlar sahifasiga o'tib egasi bilan bog'laning.",
                reply_markup=back_kb()
            )
            return

    await state.set_state(TakeTestStates.choosing_mode)
    await state.update_data(
        test_code=code,
        test_type=test['type'],
        q_ids=[q['id'] for q in questions],
        q_index=0,
        correct=0,
        wrong=0,
        skipped=0,
        time_per_q=0,
    )
    await message.answer(
        f"⚙️ <b>{test['title']}</b>\n"
        f"❓ Savollar soni: {len(questions)} ta\n\n"
        f"Test rejimini tanlang:",
        parse_mode='HTML',
        reply_markup=quiz_mode_kb(code)
    )


async def send_question(
        target,  # Message yoki chat_id (int)
        bot: Bot,
        state: FSMContext,
        uid: int,
        code: str,
        q_idx: int
):
    """Berilgan indeksdagi savolni yuboradi va timer ishga tushiradi"""
    questions = db_get_test_questions(code)
    total = len(questions)
    s = await state.get_data()
    time_per_q = s.get('time_per_q', 0)

    if q_idx >= total:
        # Barcha savollar tugadi
        chat_id = target if isinstance(target, int) else target.chat.id
        await finish_quiz(bot, state, uid, chat_id, code, total)
        return

    q = questions[q_idx]
    await state.update_data(q_index=q_idx)

    text = fmt_question(q, q_idx, total, time_per_q)
    kb = answer_kb(code, q['id'], q_idx, total)

    if isinstance(target, int):
        chat_id = target
        sent = await bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=kb)
    else:
        chat_id = target.chat.id
        sent = await target.answer(text, parse_mode='HTML', reply_markup=kb)

    # Timer
    cancel_timer(uid)
    if time_per_q > 0:
        task = asyncio.create_task(
            auto_skip_task(
                bot, state, uid, chat_id,
                sent.message_id, code,
                q['id'], q_idx, total, time_per_q
            )
        )
        _timer_tasks[uid] = task


# ─────────────────────────── Command handlers ────────────────────────────────

async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    cancel_timer(message.from_user.id)
    await state.clear()
    uid = message.from_user.id
    db_ensure_user(uid,
                   getattr(message.from_user, 'full_name', None),
                   getattr(message.from_user, 'username', None))

    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        code = args[1].strip()
        test = db_get_test(code)
        if test:
            await show_test_detail(message, test, uid, bot)
            return
        await message.answer(f"❌ Test topilmadi: <code>{code}</code>", parse_mode='HTML')

    await message.answer(
        t('welcome', uid),
        parse_mode='HTML',
        reply_markup=main_menu_kb(uid)
    )


async def cmd_help(message: Message):
    uid = message.from_user.id
    await message.answer(
        t('help_text', uid),
        parse_mode='HTML',
        reply_markup=back_kb()
    )


async def cmd_setlang(message: Message):
    """Command /setlang - ask user to choose language."""
    uid = message.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data='lang:uz'),
            InlineKeyboardButton(text="Русский", callback_data='lang:ru'),
            InlineKeyboardButton(text="English", callback_data='lang:en'),
        ]
    ])
    await message.answer(t('choose_language', uid), reply_markup=kb)


# ───────────────────────── File upload handler ────────────────────────────────

async def handle_document(message: Message, state: FSMContext, bot: Bot):
    doc = message.document
    if not doc:
        await message.answer("❌ Fayl topilmadi.")
        return

    mime = doc.mime_type or ''
    fname = doc.file_name or 'file'
    size = getattr(doc, 'file_size', 0) or 0
    ext = os.path.splitext(fname)[1].lower()

    if not (fname.lower().endswith('.pdf') or fname.lower().endswith('.docx')
            or 'pdf' in mime or 'word' in mime or 'officedocument' in mime):
        await message.answer("❌ Faqat PDF yoki DOCX formatlar qabul qilinadi.")
        return

    # Enforce size limits
    if ext == '.docx' or 'word' in mime or 'officedocument' in mime:
        if size > MAX_DOCX_SIZE:
            await message.answer(f"❌ DOCX fayl maksimal {MAX_DOCX_SIZE//(1024*1024)} MB bo'lishi kerak. Siz yuborgan: {round(size/(1024*1024),2)} MB")
            return
    if ext == '.pdf' or 'pdf' in mime:
        if size > MAX_PDF_SIZE:
            await message.answer(f"❌ PDF fayl maksimal {MAX_PDF_SIZE//(1024*1024)} MB bo'lishi kerak. Siz yuborgan: {round(size/(1024*1024),2)} MB")
            return

    await message.answer("⏳ Fayl yuklanmoqda va tahlil qilinmoqda...")

    code = db_generate_unique_code(os.path.splitext(fname)[0])
    title = os.path.splitext(fname)[0]

    try:
        db_insert_test(message.from_user.id, title, doc.file_id, mime, code)
    except Exception:
        logger.exception("DB insert test failed")
        await message.answer("❌ Bazaga saqlashda xato.")
        return

    questions = []
    parse_error = None
    local_path = None
    try:
        file_obj = await bot.get_file(doc.file_id)
        local_path = os.path.join(UPLOADS_DIR, f"{code}_{fname}")
        await bot.download_file(file_obj.file_path, destination=local_path)
        from parser import parse_file
        # parse in background thread with timeout
        try:
            questions = await asyncio.wait_for(asyncio.to_thread(parse_file, local_path), timeout=PARSE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.exception("Parse timeout")
            # Cleanup: remove DB record and local file
            try:
                db_delete_test(code, message.from_user.id)
            except Exception:
                logger.exception("Failed to delete test record after timeout")
            try:
                if local_path and os.path.exists(local_path):
                    os.remove(local_path)
            except Exception:
                logger.exception("Failed to remove local file after timeout")
            await message.answer(f"⏳ Tahlil {PARSE_TIMEOUT} soniyadan oshib ketdi. Iltimos faylni kichikroq bo'laklarga bo'lib yuboring.")
            return
        except asyncio.CancelledError:
            logger.exception("Parse task cancelled")
            try:
                db_delete_test(code, message.from_user.id)
            except Exception:
                logger.exception("Failed to delete test record after cancel")
            try:
                if local_path and os.path.exists(local_path):
                    os.remove(local_path)
            except Exception:
                logger.exception("Failed to remove local file after cancel")
            await message.answer("⏳ Tahlil bekor qilindi. Iltimos qayta urinib ko'ring.")
            return
        except Exception as e:
            logger.exception("Parse failed")
            parse_error = str(e)
    except Exception as e:
        logger.exception("File download failed or parse error: %s", e)
        parse_error = str(e)
        # attempt to cleanup local file if created
        try:
            if local_path and os.path.exists(local_path):
                os.remove(local_path)
        except Exception:
            logger.exception("Failed to remove local file after download/parse error")

    if questions:
        if len(questions) > MAX_QUESTIONS:
            try:
                db_delete_test(code, message.from_user.id)
            except Exception:
                pass
            await message.answer(f"❌ Faylda {len(questions)} ta savol topildi — maksimal ruxsat etilgan {MAX_QUESTIONS} ta. Iltimos faylni bo'ling yoki savollar sonini kamaytiring.")
            return

        for q in questions:
            db_insert_question(code,
                               q.get('question', ''),
                               q.get('a'), q.get('b'),
                               q.get('c'), q.get('d'),
                               q.get('correct'))
        has_auto = any(q.get('correct') for q in questions)
        await state.update_data(code=code, q_count=len(questions), has_auto_answers=has_auto)
        await message.answer(
            f"✅ <b>{len(questions)} ta savol topildi!</b>\n"
            f"📋 Test kodi: <code>{code}</code>\n\n"
            f"Test turini tanlang:",
            parse_mode='HTML',
            reply_markup=test_type_kb(code)
        )
    else:
        err = f"\n⚠️ Sabab: {parse_error}" if parse_error else ""
        try:
            db_delete_test(code, message.from_user.id)
        except Exception:
            pass
        await state.update_data(code=code, q_count=0, has_auto_answers=False)
        await message.answer(
            f"⚠️ Fayldan savollar ajratib olishda muammo.{err}\n"
            f"Iltimos fayl formatini tekshiring yoki savollarni oddiy matn sifatida yuboring.\n\n"
            f"Test kodi: <code>{code}</code>",
            parse_mode='HTML', reply_markup=back_kb()
        )


# ─────────────────────────── Test detail view ────────────────────────────────

async def show_test_detail(target, test: dict, viewer_id: int, bot: Bot):
    is_owner = test['owner_telegram_id'] == viewer_id
    stats = db_get_test_stats(test['code'])
    type_lbl = "📊 Natijali" if test['type'] == 'result' else "📚 Mashq"
    try:
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start={test['code']}"
    except Exception:
        link = f"(bot username topilmadi)"

    text = (
        f"<b>{test['title']}</b>\n\n"
        f"🔑 Kod: <code>{test['code']}</code>\n"
        f"📂 Tur: {type_lbl}\n"
        f"❓ Savollar: {stats['questions']} ta\n"
        f"👥 Ishlangan: {stats['attempts']} marta\n\n"
        f"🔗 {link}"
    )
    msg = target.message if isinstance(target, CallbackQuery) else target
    await msg.answer(text, parse_mode='HTML',
                     reply_markup=test_manage_kb(test['code'], is_owner))


# ────────────────────────────── Callback router ──────────────────────────────

async def callback_router(query: CallbackQuery, state: FSMContext, bot: Bot):
    data = query.data or ''
    uid = query.from_user.id

    # ── Language change ──
    if data.startswith('lang:'):
        lang = data.split(':', 1)[1]
        if lang in SUPPORTED_LANGS:
            db_set_user_lang(uid, lang)
        await safe_query_answer(query)
        # Confirmation in the newly set language
        await query.message.answer(t('lang_set', uid))
        try:
            await query.message.edit_text(t('welcome', uid), reply_markup=main_menu_kb(uid))
        except Exception:
            pass
        return

    # ── Main menu ──
    if data.startswith('menu:'):
        key = data.split(':', 1)[1]

        if key == 'main':
            cancel_timer(uid)
            await state.clear()
            await safe_query_answer(query)
            try:
                await query.message.edit_text("📋 Asosiy menyu:", reply_markup=main_menu_kb())
            except Exception:
                await query.message.answer("📋 Asosiy menyu:", reply_markup=main_menu_kb())

        elif key == 'upload':
            await safe_query_answer(query)
            await state.set_state(UploadStates.waiting_file)
            await query.message.answer(
                "📎 PDF yoki DOCX fayl yoki test matnini yuboring.\n\n"
                "<i>Format: 1. Savol matni\nA) variant\nJavob: A</i>",
                parse_mode='HTML'
            )

        elif key == 'tests':
            await safe_query_answer(query)
            tests = db_get_all_tests()
            if not tests:
                await query.message.answer("📭 Hali testlar yo'q.", reply_markup=back_kb())
                return
            await query.message.answer(
                f"📚 <b>Barcha testlar</b> ({len(tests)} ta):",
                parse_mode='HTML',
                reply_markup=tests_list_kb(tests, 0)
            )

        elif key == 'results':
            await safe_query_answer(query)
            results = db_get_my_results(uid)
            if not results:
                await query.message.answer("📭 Hali natijalar yo'q.", reply_markup=back_kb())
            else:
                lines = ["📊 <b>Mening natijalarim:</b>\n"]
                for r in results:
                    pct = r.get('percentage', 0)
                    emoji = "🏆" if pct >= 90 else "✅" if pct >= 60 else "❌"
                    lines.append(f"{emoji} {r.get('title', '?')} — {pct}%")
                await query.message.answer('\n'.join(lines), parse_mode='HTML', reply_markup=back_kb())

        elif key == 'profile':
            await safe_query_answer(query)
            p = db_get_profile(uid)
            name = p['fullname'] or "Noma'lum"
            uname = f"@{p['username']}" if p['username'] else "—"
            await query.message.answer(
                f"👤 <b>Profil</b>\n\n"
                f"👨 Ism: {name}\n"
                f"🔖 Username: {uname}\n\n"
                f"✅ Ishlangan testlar: {p['done_count']}\n"
                f"📈 O'rtacha natija: {p['avg_percent']}%\n"
                f"📁 Yaratilgan testlar: {p['created_tests']}",
                parse_mode='HTML', reply_markup=back_kb()
            )

        elif key == 'help':
            await safe_query_answer(query)
            await query.message.answer(
                "ℹ️ <b>Yordam</b>\n\n"
                "• PDF yoki DOCX faylni yuklang — bot savollarni ajratadi\n"
                "• Natijali testda balllar hisoblanadi\n"
                "• Mashq testida faqat savollar ko'rinadi\n"
                "• Test kodi orqali do'stlaringizga ulashing\n\n"
                "📞 Admin: @otaxonov_o17",
                parse_mode='HTML', reply_markup=back_kb()
            )
        return

    await safe_query_answer(query)

    # ── Test turi tanlash ──
    if data.startswith('type:'):
        parts = data.split(':')
        if len(parts) < 3: return
        code, chosen = parts[1], parts[2]
        if chosen == 'practice':
            db_update_test(code, type_='practice', has_answers=0)
            await query.message.answer(
                f"✅ <b>Mashq testi saqlandi!</b>\n🔑 Kod: <code>{code}</code>\n\n"
                f"Foydalanuvchilar endi bu testni yecha oladi.",
            parse_mode='HTML', reply_markup=test_manage_kb(code, True)
            )
        else:
            sd = await state.get_data()
            if sd.get('has_auto_answers'):
                db_update_test(code, type_='result', has_answers=1)
                await query.message.answer(
                    f"✅ <b>Natijali test saqlandi!</b>\n🔑 Kod: <code>{code}</code>\n\n"
                    f"To'g'ri javoblar fayldan avtomatik topildi.",
                parse_mode='HTML', reply_markup=test_manage_kb(code, True)
                )
            else:
                await query.message.answer(
                    "❓ Faylda to'g'ri javoblar mavjudmi?",
                    reply_markup=has_answers_kb(code)
                )
        return

    # ── Faylda javob bormi? ──
    if data.startswith('ans:'):
        parts = data.split(':')
        code, choice = parts[1], parts[2]
        if choice == 'yes':
            # Validate that questions actually have correct answers
            if db_has_correct_answers(code):
                db_update_test(code, type_='result', has_answers=1)
                await query.message.answer(
                    f"✅ <b>Natijali test saqlandi!</b>\n🔑 Kod: <code>{code}</code>",
                    parse_mode='HTML', reply_markup=test_manage_kb(code, True)
                )
            else:
                # Parser did not find answers; ask owner to enter or mark as practice
                await query.message.answer(
                    "⚠️ Faylda avtomatik tarzda to'g'ri javoblar topilmadi.\n"
                    "Qo'lda javoblarni kiritishni xohlaysizmi yoki bu testni mashq sifatida saqlaysizmi?",
                    reply_markup=manual_or_practice_kb(code)
                )
        else:
            await query.message.answer(
                "📝 To'g'ri javoblarni qo'lda kiritishni xohlaysizmi?",
                reply_markup=manual_or_practice_kb(code)
            )
        return

    # ── Qo'lda javob kiritish? ──
    if data.startswith('manual:'):
        parts = data.split(':')
        code, choice = parts[1], parts[2]
        if choice == 'no':
            db_update_test(code, type_='practice', has_answers=0)
            await query.message.answer(
                f"✅ Mashq testi sifatida saqlandi.\n🔑 Kod: <code>{code}</code>",
                parse_mode='HTML', reply_markup=test_manage_kb(code, True)
            )
        else:
            questions = db_get_test_questions(code)
            if not questions:
                await query.message.answer("❌ Savollar topilmadi.")
                return
            await state.update_data(manual_code=code, manual_idx=0)
            q = questions[0]
            await query.message.answer(
                f"✏️ <b>1/{len(questions)} — To'g'ri javobni tanlang:</b>\n\n{q['question']}",
                parse_mode='HTML',
                reply_markup=set_answer_kb(code, 0)
            )
        return

    # ── Qo'lda javob belgilash ──
    if data.startswith('setans:'):
        parts = data.split(':')
        code, q_idx, answer = parts[1], int(parts[2]), parts[3]
        questions = db_get_test_questions(code)
        if q_idx < len(questions):
            db_update_question_answer(questions[q_idx]['id'], answer)
        next_idx = q_idx + 1
        if next_idx < len(questions):
            q = questions[next_idx]
            await query.message.edit_text(
                f"✏️ <b>{next_idx + 1}/{len(questions)} — To'g'ri javobni tanlang:</b>\n\n{q['question']}",
                parse_mode='HTML',
                reply_markup=set_answer_kb(code, next_idx)
            )
        else:
            db_update_test(code, type_='result', has_answers=1)
            await query.message.edit_text(
                f"✅ <b>Barcha javoblar saqlandi!</b>\n🔑 Kod: <code>{code}</code>\n\nNatijali test tayyor.",
                parse_mode='HTML', reply_markup=test_manage_kb(code, True)
            )
        return

    # ── Tests ro'yxati sahifalash ──
    if data.startswith('tests_page:'):
        page = int(data.split(':')[1])
        tests = db_get_all_tests()
        try:
            await query.message.edit_reply_markup(reply_markup=tests_list_kb(tests, page))
        except Exception:
            pass
        return

    # ── Test ko'rish ──
    if data.startswith('view_test:'):
        code = data.split(':', 1)[1]
        test = db_get_test(code)
        if not test:
            await query.message.answer("❌ Test topilmadi.")
            return
        await show_test_detail(query, test, uid, bot)
        return

    # ── Testni boshlash → rejim tanlash ──
    if data.startswith('start_test:'):
        code = data.split(':', 1)[1]
        cancel_timer(uid)
        await state.clear()
        await ask_quiz_mode(query.message, state, code)
        return

    # ── Qayta ishga tushirish (retry) ──
    if data.startswith('restart_test:'):
        code = data.split(':', 1)[1]
        cancel_timer(uid)
        await state.clear()
        await ask_quiz_mode(query.message, state, code)
        return

    # ── Rejim tanlandi (vaqtli/vaqtsiz) ──
    if data.startswith('qmode:'):
        parts = data.split(':')
        code, mode = parts[1], parts[2]
        if mode == 'notimed':
            await state.update_data(time_per_q=0)
            await state.set_state(TakeTestStates.answering)
            await send_question(query.message, bot, state, uid, code, 0)
        else:
            await state.set_state(TakeTestStates.choosing_time)
            await query.message.answer(
                "⏱ Har bir savol uchun vaqt tanlang:",
                reply_markup=time_select_kb(code)
            )
        return

    # ── Vaqt tanlandi ──
    if data.startswith('qtime:'):
        parts = data.split(':')
        code = parts[1]
        time_per_q = int(parts[2])
        await state.update_data(time_per_q=time_per_q)
        await state.set_state(TakeTestStates.answering)
        await query.message.answer(
            f"⏱ Har bir savolga <b>{time_per_q} soniya</b> vaqt beriladi.\n\nTest boshlanmoqda...",
            parse_mode='HTML'
        )
        await send_question(query.message, bot, state, uid, code, 0)
        return

    # ── Savol javobi ──
    if data.startswith('qa:'):
        # qa:CODE:Q_ID:Q_IDX:TOTAL:ANSWER
        parts = data.split(':')
        code = parts[1]
        q_id = int(parts[2])
        q_idx = int(parts[3])
        total = int(parts[4])
        answer = parts[5]

        s = await state.get_data()
        # Eski savolga bosilganmi? (timer o'tkazib yuborganidan keyin)
        if s.get('q_index', 0) != q_idx:
            return

        cancel_timer(uid)

        test_type = s.get('test_type', 'practice')
        correct_cnt = s.get('correct', 0)
        wrong_cnt = s.get('wrong', 0)
        time_per_q = s.get('time_per_q', 0)

        if test_type == 'result':
            questions = db_get_test_questions(code)
            q = next((x for x in questions if x['id'] == q_id), None)
            if q:
                correct_ans = q.get('correct_answer')
                if correct_ans:
                    if answer == correct_ans:
                        correct_cnt += 1
                    else:
                        wrong_cnt += 1

        await state.update_data(correct=correct_cnt, wrong=wrong_cnt, q_index=q_idx + 1)

        next_idx = q_idx + 1
        if next_idx < total:
            # Tugmalarni o'chirib keyingi savolni yuboramiz
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await send_question(query.message, bot, state, uid, code, next_idx)
        else:
            await finish_quiz(bot, state, uid, query.message.chat.id, code, total)
        return

    # ── Testni to'xtatish / davom ettirish ──
    if data.startswith('pause:'):
        # pause:CODE:Q_ID:Q_IDX:TOTAL
        parts = data.split(':')
        if len(parts) < 5:
            return
        code = parts[1]
        q_idx = int(parts[3])
        # cancel running timer
        cancel_timer(uid)
        await state.update_data(paused=1)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='▶️ Davom ettirish', callback_data=f'resume:{code}:{q_idx}')],
            [InlineKeyboardButton(text='🏠 Bosh sahifa', callback_data='menu:main')],
        ])
        try:
            await query.message.edit_text("⏸ <b>Test to'xtatildi</b>\nTestni davom ettirish yoki bosh sahifaga qaytishingiz mumkin.", parse_mode='HTML', reply_markup=kb)
        except Exception:
            await query.message.answer("⏸ <b>Test to'xtatildi</b>\nTestni davom ettirish yoki bosh sahifaga qaytishingiz mumkin.", parse_mode='HTML', reply_markup=kb)
        return

    if data.startswith('resume:'):
        # resume:CODE:Q_IDX
        parts = data.split(':')
        if len(parts) < 3:
            return
        code = parts[1]
        q_idx = int(parts[2])
        await state.update_data(paused=0)
        await state.set_state(TakeTestStates.answering)
        await safe_query_answer(query)
        await send_question(query.message, bot, state, uid, code, q_idx)
        return

    # ── Statistika ──
    if data.startswith('stat:'):
        code = data.split(':', 1)[1]
        test = db_get_test(code)
        if not test or test['owner_telegram_id'] != uid:
            await query.message.answer("❌ Ruxsat yo'q.")
            return
        stats = db_get_test_stats(code)
        await query.message.answer(
            f"📈 <b>Statistika: {test['title']}</b>\n\n"
            f"❓ Savollar: {stats['questions']} ta\n"
            f"👥 Ishlanganlar: {stats['attempts']} marta\n"
            f"📊 O'rtacha: {stats['avg']}%\n"
            f"🏆 Eng yuqori: {stats['best']}%\n"
            f"📉 Eng past: {stats['worst']}%",
            parse_mode='HTML',
            reply_markup=back_kb(f'view_test:{code}')
        )
        return

    # ── Test tahrirlash ──
    if data.startswith('edit_test:'):
        code = data.split(':', 1)[1]
        test = db_get_test(code)
        if not test or test['owner_telegram_id'] != uid:
            await query.message.answer("❌ Ruxsat yo'q.")
            return
        await state.set_state(EditStates.waiting_new_title)
        await state.update_data(edit_code=code)
        await query.message.answer(
            f"✏️ Yangi nom kiriting (hozirgi: <b>{test['title']}</b>):",
            parse_mode='HTML'
        )
        return

    # ── Test o'chirish ──
    if data.startswith('del_test:'):
        code = data.split(':', 1)[1]
        if db_delete_test(code, uid):
            await query.message.answer(
                f"🗑️ Test <code>{code}</code> o'chirildi.",
                parse_mode='HTML', reply_markup=back_kb('menu:tests')
            )
        else:
            await query.message.answer("❌ Test topilmadi yoki ruxsat yo'q.")
        return

    await query.message.answer("❓ Noma'lum buyruq.")


# ──────────────────────── Text message handler ───────────────────────────────

async def handle_text(message: Message, state: FSMContext):
    current = await state.get_state()

    # Allow direct text uploads as tests when waiting for a file
    if current == UploadStates.waiting_file:
        text = (message.text or '').strip()
        if not text:
            await message.answer("❌ Matn topilmadi.")
            return
        await message.answer("⏳ Matn tahlil qilinmoqda...")

        prefix = text.splitlines()[0].strip()[:20] if text.splitlines() else 'TEXT'
        code = db_generate_unique_code(prefix)
        title = prefix or 'Text Test'

        try:
            # store with no file_id, mark mime as text
            db_insert_test(message.from_user.id, title, None, 'text/plain', code)
        except Exception:
            logger.exception("DB insert test failed (text)")
            await message.answer("❌ Bazaga saqlashda xato.")
            return

        from parser import parse_text_to_questions
        questions = []
        parse_error = None
        try:
            # run parsing in thread with timeout
            try:
                questions = await asyncio.wait_for(asyncio.to_thread(parse_text_to_questions, text), timeout=PARSE_TIMEOUT)
            except asyncio.TimeoutError:
                logger.exception("Parse text timeout")
                try:
                    db_delete_test(code, message.from_user.id)
                except Exception:
                    pass
                await message.answer(f"⏳ Tahlil {PARSE_TIMEOUT} soniyadan oshib ketdi. Iltimos matnni kichikroq bo'laklarga bo'lib yuboring.")
                return
        except Exception as e:
            logger.exception("Parse text failed")
            parse_error = str(e)

        if questions:
            if len(questions) > MAX_QUESTIONS:
                try:
                    db_delete_test(code, message.from_user.id)
                except Exception:
                    pass
                await message.answer(f"❌ Matnda {len(questions)} ta savol topildi — maksimal ruxsat etilgan {MAX_QUESTIONS} ta. Iltimos matnni bo'ling yoki savollar sonini kamaytiring.")
                return

            for q in questions:
                db_insert_question(code,
                                   q.get('question', ''),
                                   q.get('a'), q.get('b'),
                                   q.get('c'), q.get('d'),
                                   q.get('correct'))
            has_auto = any(q.get('correct') for q in questions)
            await state.update_data(code=code, q_count=len(questions), has_auto_answers=has_auto)
            await message.answer(
                f"✅ <b>{len(questions)} ta savol topildi!</b>\n"
                f"📋 Test kodi: <code>{code}</code>\n\n"
                f"Test turini tanlang:",
                parse_mode='HTML',
                reply_markup=test_type_kb(code)
            )
        else:
            err = f"\n⚠️ Sabab: {parse_error}" if parse_error else ""
            try:
                db_delete_test(code, message.from_user.id)
            except Exception:
                pass
            await state.update_data(code=code, q_count=0, has_auto_answers=False)
            await message.answer(
                f"⚠️ Matndan savollar ajratib olishda muammo.{err}\n"
                f"Iltimos matn formatini tekshiring yoki fayl sifatida yuboring.\n\n"
                f"Test kodi: <code>{code}</code>",
                parse_mode='HTML', reply_markup=back_kb()
            )
        return

    if current == EditStates.waiting_new_title:
        new_title = message.text.strip()
        if len(new_title) < 2:
            await message.answer("❌ Nom kamida 2 ta belgidan iborat bo'lishi kerak.")
            return
        data = await state.get_data()
        db_update_test(data.get('edit_code'), title=new_title)
        await state.clear()
        await message.answer(
            f"✅ Test nomi yangilandi: <b>{new_title}</b>",
            parse_mode='HTML', reply_markup=back_kb('menu:tests')
        )
        return

    await message.answer("📋 Asosiy menyu:", reply_markup=main_menu_kb())


async def cmd_admin(message: Message):
    uid = getattr(message.from_user, 'id', None)
    uname = getattr(message.from_user, 'username', None)
    if not is_admin(uid, uname):
        await message.answer('❌ Siz admin emassiz.')
        return
    stats = db_get_global_stats()

    lines = [
        "🔒 <b>Admin statistika</b>\n",
        f"👥 Foydalanuvchilar: {stats.get('users',0)}",
        f"📚 Testlar: {stats.get('tests',0)}",
        f"❓ Savollar jami: {stats.get('questions',0)}",
        f"📊 Natija yozilganlar: {stats.get('results',0)}",
        f"📈 O'rtacha natija: {stats.get('avg_percentage',0.0)}%\n",
    ]

    # Top active users
    top = db_get_top_users(10)
    if top:
        lines.append("<b>Eng faol foydalanuvchilar:</b>")
        i = 1
        for r in top:
            name = (r.get('fullname') or '')
            uname = r.get('username')
            display = name if name else (f"@{uname}" if uname else str(r.get('telegram_id')))
            attempts = r.get('attempts') or 0
            avg_pct = r.get('avg_pct') or 0.0
            lines.append(f"{i}. {display} — {attempts} ta — {avg_pct}%")
            i += 1
    else:
        lines.append("Eng faol foydalanuvchilar topilmadi.")

    await message.answer('\n'.join(lines), parse_mode='HTML', reply_markup=back_kb())


# ──────────────────────────────── Main ───────────────────────────────────────

async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command('help'))
    dp.message.register(cmd_admin, Command('admin'))
    dp.message.register(cmd_setlang, Command('setlang'))
    dp.message.register(handle_document, F.document)
    dp.message.register(handle_text, F.text)
    dp.callback_query.register(callback_router)

    await bot.set_my_commands([
        BotCommand(command='start', description='Asosiy menyu'),
        BotCommand(command='help', description='Yordam'),
        BotCommand(command='admin', description='Admin statistika'),
        BotCommand(command='setlang', description='Tilni o''zgartirish'),
    ])

    logger.info("✅ Bot ishga tushdi")
    try:
        await dp.start_polling(bot, allowed_updates=['message', 'callback_query'])
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
