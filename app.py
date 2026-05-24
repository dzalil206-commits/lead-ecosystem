import os, uuid, sqlite3, random, string, io, asyncio, threading, concurrent.futures
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from datetime import datetime, timedelta
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, send_file, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, PasswordHashInvalidError,
    FloodWaitError, PhoneNumberInvalidError,
)
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# ---------- НАСТРОЙКИ ----------
SECRET_KEY       = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
ADMIN_SECRET_KEY = os.environ.get('ADMIN_SECRET_KEY', '')
USDT_WALLET      = os.environ.get('USDT_WALLET', '')
ADMIN_ID            = int(os.environ.get('ADMIN_ID', '5062414502'))
DATABASE            = os.environ.get('DATABASE', 'lead_ecosystem.db')
SUPPORT_USERNAME    = '@TGLeadSupportBot'
YOOKASSA_SHOP_ID    = os.environ.get('YOOKASSA_SHOP_ID', '')
YOOKASSA_SECRET_KEY = os.environ.get('YOOKASSA_SECRET_KEY', '')
LAVA_API_KEY        = os.environ.get('LAVA_API_KEY', '')
REVIEW_BOT_TOKEN    = os.environ.get('REVIEW_BOT_TOKEN', '')
NOTIFY_BOT_TOKEN    = os.environ.get('NOTIFY_BOT_TOKEN', '')
BASE_URL            = os.environ.get('BASE_URL', 'http://localhost:5000')
BOT_MAIN_SECRET     = os.environ.get('BOT_MAIN_SECRET', '')
SMTP_HOST           = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT           = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER           = os.environ.get('SMTP_USER', '')
SMTP_PASS           = os.environ.get('SMTP_PASS', '')
SMTP_FROM           = os.environ.get('SMTP_FROM', 'noreply@tgleadwareon.ru')

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ---------- RATE LIMITER (in-memory, sliding window) ----------
_rl_lock = threading.Lock()
_rl_store: dict = defaultdict(list)  # key -> list of timestamps

def _rate_limit(key: str, max_calls: int, window_seconds: int) -> bool:
    """Returns True if request is allowed, False if rate limit exceeded."""
    now = datetime.now()
    cutoff = now - timedelta(seconds=window_seconds)
    with _rl_lock:
        _rl_store[key] = [t for t in _rl_store[key] if t > cutoff]
        if len(_rl_store[key]) >= max_calls:
            return False
        _rl_store[key].append(now)
        return True

def rate_limit_ip(action: str, max_calls: int, window_seconds: int) -> bool:
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    return _rate_limit(f'{action}:{ip}', max_calls, window_seconds)

def rate_limit_user(user_id: int, action: str, max_calls: int, window_seconds: int) -> bool:
    return _rate_limit(f'{action}:u{user_id}', max_calls, window_seconds)

def parse_dt(value):
    """Парсит дату из SQLite — пробует форматы с микросекундами и без."""
    if isinstance(value, datetime):
        return value
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f'Неизвестный формат даты: {value!r}')

def send_email(to_email: str, subject: str, html_body: str):
    """Отправляет HTML-письмо через SMTP. Если SMTP не настроен — логирует и пропускает."""
    if not SMTP_USER or not SMTP_PASS:
        logging.warning(f'EMAIL: SMTP не настроен, пропускаем письмо для {to_email}')
        return
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f'TG Lead Wareon <{SMTP_FROM}>'
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(SMTP_FROM, [to_email], msg.as_string())
        logging.info(f'EMAIL: отправлено на {to_email}')
    except Exception as e:
        logging.error(f'EMAIL: ошибка при отправке на {to_email} — {e}')


def send_purchase_email(to_email: str, product: str, price: int, expires_at):
    """Читает email_purchase.html и отправляет письмо с деталями покупки."""
    try:
        tpl_path = os.path.join(os.path.dirname(__file__), 'templates', 'email_purchase.html')
        with open(tpl_path, 'r', encoding='utf-8') as f:
            html = f.read()
        html = html.replace('{PRODUCT_NAME}', product)
        html = html.replace('{AMOUNT}', str(price))
        expires_str = expires_at.strftime('%d.%m.%Y') if hasattr(expires_at, 'strftime') else str(expires_at)[:10]
        html = html.replace('{EXPIRES_DATE}', expires_str)
        send_email(to_email, f'✅ Лицензия TG Lead Wareon {product} активирована', html)
    except Exception as e:
        logging.error(f'EMAIL: ошибка при подготовке purchase-письма — {e}')


def create_lava_payment(amount_rub, user_id, product, days, user_email=''):
    """Создаёт счёт в Lava.top. Возвращает (payment_url, invoice_id) или (None, None)."""
    if not LAVA_API_KEY:
        logging.warning('LAVA: LAVA_API_KEY не задан')
        return None, None
    offer_id = os.environ.get(f'LAVA_OFFER_{product.upper()}', '')
    if not offer_id:
        logging.warning(f'LAVA: LAVA_OFFER_{product.upper()} не задан в .env')
        return None, None
    try:
        order_id = f'tglw-{user_id}-{product}-{uuid.uuid4().hex[:8]}'
        payload = {
            'email':         user_email or f'user{user_id}@tgleadwareon.ru',
            'offerId':       offer_id,
            'currency':      'RUB',
            'periodicity':   'MONTHLY',
            'buyerLanguage': 'RU',
            'orderId':       order_id,
            'successUrl':    f'{BASE_URL}/payment/success?product={product}&provider=lava',
            'failUrl':       f'{BASE_URL}/pricing',
            'hookUrl':       f'{BASE_URL}/payment/lava/webhook',
        }
        logging.info(f'LAVA: создаём счёт order_id={order_id} offer={offer_id} amount={amount_rub}')
        resp = requests.post(
            'https://gate.lava.top/api/v2/invoice',
            json=payload,
            headers={
                'X-Api-Key':    LAVA_API_KEY,
                'Content-Type': 'application/json',
                'Accept':       'application/json',
            },
            timeout=10,
        )
        logging.info(f'LAVA: ответ {resp.status_code} — {resp.text[:300]}')
        data = resp.json()
        pay_url = data.get('paymentUrl') or data.get('url') or data.get('URL')
        inv_id  = data.get('id') or data.get('InvoiceId') or order_id
        if not pay_url:
            logging.error(f'LAVA: нет url в ответе — {data}')
            return None, None
        return pay_url, inv_id
    except Exception as e:
        logging.error(f'LAVA: исключение — {e}')
        return None, None


def create_yookassa_payment(amount_rub, user_id, product, days):
    """Создаёт платёж в ЮKassa. Возвращает (confirmation_url, payment_id) или (None, None)."""
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        return None, None
    try:
        resp = requests.post(
            'https://api.yookassa.ru/v3/payments',
            json={
                'amount': {'value': f'{amount_rub}.00', 'currency': 'RUB'},
                'confirmation': {
                    'type': 'redirect',
                    'return_url': f'{BASE_URL}/payment/success?product={product}',
                },
                'capture': True,
                'description': f'TG Lead Wareon — {product} на {days} дней',
                'metadata': {
                    'user_id': str(user_id),
                    'product': product,
                    'days': str(days),
                },
            },
            auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
            headers={'Idempotence-Key': str(uuid.uuid4())},
            timeout=10,
        )
        data = resp.json()
        url = data.get('confirmation', {}).get('confirmation_url')
        pid = data.get('id')
        return url, pid
    except Exception:
        return None, None


def verify_yookassa_payment(payment_id):
    """Проверяет статус платежа в ЮKassa. Возвращает dict или None."""
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        return None
    try:
        resp = requests.get(
            f'https://api.yookassa.ru/v3/payments/{payment_id}',
            auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
            timeout=10,
        )
        return resp.json()
    except Exception:
        return None


def run_async(coro):
    """Run an async coroutine from a synchronous Flask route."""
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)

@app.context_processor
def utility_processor():
    return dict(ADMIN_ID=ADMIN_ID, SUPPORT_USERNAME=SUPPORT_USERNAME, datetime=datetime)

# ---------- БАЗА ДАННЫХ ----------
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT,
            referral_id INTEGER,
            balance INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            trial_used INTEGER DEFAULT 0,
            total_sent INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            license_key TEXT UNIQUE NOT NULL,
            price INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS sender_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            phone TEXT,
            api_id INTEGER,
            api_hash TEXT,
            session_file TEXT,
            proxy_id INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT,
            host TEXT,
            port INTEGER,
            username TEXT,
            password TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS miner_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            source_link TEXT,
            status TEXT DEFAULT 'pending',
            leads_count INTEGER DEFAULT 0,
            error_msg TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            tg_id TEXT,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_username TEXT,
            telegram_id TEXT,
            user_id INTEGER,
            rating INTEGER CHECK(rating BETWEEN 1 AND 5),
            text TEXT,
            is_approved INTEGER DEFAULT 0,
            bonus_days INTEGER DEFAULT 2,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS proxy_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            host TEXT,
            port INTEGER,
            username TEXT,
            password TEXT,
            is_sold INTEGER DEFAULT 0,
            sold_to INTEGER,
            sold_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS user_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            ip TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    db.commit()
    # Миграции для существующих таблиц (безопасны — падают если колонка уже есть)
    for sql in [
        "ALTER TABLE users ADD COLUMN referral_id INTEGER",
        "ALTER TABLE miner_jobs ADD COLUMN error_msg TEXT",
        "ALTER TABLE users ADD COLUMN telegram_id TEXT",
        "ALTER TABLE proxies ADD COLUMN secret TEXT",
    ]:
        try:
            db.execute(sql)
        except Exception:
            pass
    db.commit()


def send_telegram(chat_id, text):
    """Отправляет сообщение через Telegram-бот. Никогда не падает."""
    token = NOTIFY_BOT_TOKEN or REVIEW_BOT_TOKEN
    if not token or not chat_id:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
            timeout=5,
        )
    except Exception:
        pass


def log_action(user_id, action, details=None):
    """Log a user action for audit trail. Never raises."""
    try:
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
        ua = request.headers.get('User-Agent', '')[:200]
        get_db().execute(
            "INSERT INTO user_actions (user_id, action, details, ip, user_agent) VALUES (?,?,?,?,?)",
            (user_id, action, str(details)[:500] if details else None, ip, ua)
        )
        get_db().commit()
    except Exception:
        pass


class User(UserMixin):
    def __init__(self, id, email, full_name):
        self.id = id
        self.email = email
        self.full_name = full_name

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        return User(row['id'], row['email'], row['full_name'])
    return None

def generate_license_key():
    chars = string.ascii_uppercase + string.digits
    return f"TGLS-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}"

# ---------- ГЛАВНАЯ ----------
@app.route('/')
def index():
    return render_template('index.html')

# ---------- СТРАНИЦЫ ----------
@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.route('/cases')
def cases():
    db = get_db()
    db_reviews = db.execute(
        "SELECT * FROM reviews WHERE is_approved=1 ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    return render_template('cases.html', db_reviews=db_reviews)

@app.route('/blog')
def blog():
    return render_template('blog.html')

@app.route('/blog/<slug>')
def blog_post(slug):
    return render_template(f'blog/{slug}.html')

@app.route('/faq')
def faq():
    return render_template('faq.html')

@app.route('/support', methods=['GET', 'POST'])
def support():
    if request.method == 'POST':
        if not rate_limit_ip('support', max_calls=5, window_seconds=3600):
            flash('Слишком много сообщений. Попробуйте через час.', 'error')
            return redirect(url_for('support'))
        flash('Сообщение отправлено!', 'success')
        return redirect(url_for('support'))
    return render_template('support.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/wheel')
def wheel():
    return render_template('wheel.html')

@app.route('/checklist', methods=['GET', 'POST'])
def checklist():
    return render_template('checklist.html')

@app.route('/checklist/download', methods=['POST'])
def checklist_download():
    return send_file('static/checklist.pdf', as_attachment=True)

@app.route('/generator')
def generator():
    return render_template('generator.html')

@app.route('/download')
def download():
    return render_template('download.html')

# ---------- РЕГИСТРАЦИЯ / ВХОД ----------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if not rate_limit_ip('register', max_calls=5, window_seconds=3600):
            flash('Слишком много попыток регистрации. Попробуйте через час.', 'error')
            return render_template('register.html')

        email     = request.form['email'].strip()
        password  = request.form['password'].strip()
        full_name = request.form.get('full_name', '').strip()
        ref_id    = request.args.get('ref')

        if not request.form.get('agree'):
            flash('Необходимо принять Пользовательское соглашение и Политику конфиденциальности.', 'error')
            return render_template('register.html')

        if len(password) < 8:
            flash('Пароль должен содержать не менее 8 символов.', 'error')
            return render_template('register.html')

        db = get_db()
        if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            flash('Этот email уже зарегистрирован.', 'error')
            return render_template('register.html')
        hashed_pw = generate_password_hash(password)
        cursor = db.execute(
            "INSERT INTO users (email, password, full_name, referral_id) VALUES (?, ?, ?, ?)",
            (email, hashed_pw, full_name, int(ref_id) if ref_id and ref_id.isdigit() else None)
        )
        user_id = cursor.lastrowid
        db.commit()
        license_key = generate_license_key()
        expires_at = datetime.now() + timedelta(days=3)
        db.execute("INSERT INTO licenses (user_id, license_key, product, price, expires_at, is_active) VALUES (?, ?, ?, ?, ?, 1)", (user_id, license_key, 'Miner', 0, expires_at))
        db.commit()
        if ref_id and ref_id.isdigit():
            db.execute("UPDATE licenses SET expires_at = datetime(expires_at, '+1 day') WHERE user_id = ? AND is_active = 1", (ref_id,))
            db.commit()
        log_action(user_id, 'register', email)
        flash('Регистрация успешна! Войдите.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if not rate_limit_ip('login', max_calls=10, window_seconds=900):
            flash('Слишком много попыток входа. Подождите 15 минут.', 'error')
            return render_template('login.html')

        email = request.form['email'].strip()
        password = request.form['password'].strip()
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row and check_password_hash(row['password'], password):
            user = User(row['id'], row['email'], row['full_name'])
            login_user(user)
            log_action(row['id'], 'login', email)
            return redirect(url_for('dashboard'))
        log_action(None, 'login_failed', email)
        flash('Неверный email или пароль.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# ---------- ЛИЧНЫЙ КАБИНЕТ ----------
@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    active_licenses_count = db.execute("SELECT COUNT(*) FROM licenses WHERE user_id = ? AND is_active = 1", (current_user.id,)).fetchone()[0]
    total_leads_collected = db.execute("SELECT COALESCE(SUM(leads_count), 0) FROM miner_jobs WHERE user_id = ?", (current_user.id,)).fetchone()[0]
    total_messages_sent = db.execute("SELECT COALESCE(total_sent, 0) FROM users WHERE id = ?", (current_user.id,)).fetchone()[0]
    miner_license = db.execute("SELECT * FROM licenses WHERE user_id = ? AND is_active = 1 AND product = 'Miner' ORDER BY expires_at DESC LIMIT 1", (current_user.id,)).fetchone()
    sender_license = db.execute("SELECT * FROM licenses WHERE user_id = ? AND is_active = 1 AND product = 'Sender' ORDER BY expires_at DESC LIMIT 1", (current_user.id,)).fetchone()
    sender_accounts = db.execute("SELECT * FROM sender_accounts WHERE user_id = ?", (current_user.id,)).fetchall()
    active_accounts_count   = db.execute("SELECT COUNT(*) FROM sender_accounts WHERE user_id=? AND is_active=1", (current_user.id,)).fetchone()[0]
    inactive_accounts_count = db.execute("SELECT COUNT(*) FROM sender_accounts WHERE user_id=? AND is_active=0", (current_user.id,)).fetchone()[0]
    proxies = db.execute("SELECT * FROM proxies WHERE user_id = ?", (current_user.id,)).fetchall()
    licenses = db.execute("SELECT * FROM licenses WHERE user_id = ? AND is_active = 1", (current_user.id,)).fetchall()
    user_licenses = []
    for lic in licenses:
        days_left = (parse_dt(lic['expires_at']) - datetime.now()).days
        user_licenses.append({
            'product': lic['product'],
            'created_at': lic['created_at'],
            'expires_at': lic['expires_at'],
            'days_left': max(0, days_left),
            'is_expired': days_left <= 0
        })
    days_left = None
    if miner_license:
        try:
            days_left = (parse_dt(miner_license['expires_at']) - datetime.now()).days
        except Exception:
            days_left = None

    # Данные для графиков — последние 7 дней
    today = datetime.now().date()
    chart_days  = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    chart_labels = [d.strftime('%d.%m') for d in chart_days]
    from_date    = chart_days[0].strftime('%Y-%m-%d')

    raw = db.execute("""
        SELECT date(created_at) as day, COALESCE(SUM(leads_count), 0) as cnt
        FROM miner_jobs WHERE user_id=? AND status='done' AND date(created_at) >= ?
        GROUP BY day
    """, (current_user.id, from_date)).fetchall()
    leads_map   = {r['day']: r['cnt'] for r in raw}
    chart_leads = [leads_map.get(d.strftime('%Y-%m-%d'), 0) for d in chart_days]

    # Реферальная программа
    referral_count = db.execute(
        "SELECT COUNT(*) FROM users WHERE referral_id=?", (current_user.id,)
    ).fetchone()[0]

    return render_template('dashboard.html',
                           active_licenses_count=active_licenses_count,
                           total_leads_collected=total_leads_collected,
                           total_messages_sent=total_messages_sent,
                           miner_license=miner_license,
                           sender_license=sender_license,
                           sender_accounts=sender_accounts,
                           active_accounts_count=active_accounts_count,
                           inactive_accounts_count=inactive_accounts_count,
                           proxies=proxies,
                           user_licenses=user_licenses,
                           days_left=days_left,
                           chart_labels=chart_labels,
                           chart_leads=chart_leads,
                           referral_count=referral_count)

# ---------- ПОДКЛЮЧЕНИЕ АККАУНТА (реальный Telegram auth) ----------

def _make_session_path(user_id, phone):
    safe_phone = phone.replace('+', '').replace(' ', '').replace('-', '')
    return os.path.join('sessions', f'u{user_id}_{safe_phone}')

def _get_user_proxy(db, user_id):
    """Вернуть первый активный прокси пользователя как внутренний dict, или None.
    Приоритет: mtproto → socks5 → socks4 → http.
    """
    row = db.execute(
        "SELECT * FROM proxies WHERE user_id=? AND is_active=1 "
        "ORDER BY CASE type WHEN 'mtproto' THEN 0 WHEN 'socks5' THEN 1 WHEN 'socks4' THEN 2 ELSE 3 END, id ASC LIMIT 1",
        (user_id,)
    ).fetchone()
    if not row:
        return None
    proxy_type = (row['type'] or 'socks5').lower()

    if proxy_type == 'mtproto':
        raw_secret = (row['secret'] or '').strip()
        try:
            secret_bytes = bytes.fromhex(raw_secret)
        except (ValueError, AttributeError):
            try:
                import base64
                secret_bytes = base64.b64decode(raw_secret)
            except Exception:
                secret_bytes = raw_secret.encode('utf-8')
        return {
            '_type':  'mtproto',
            'host':   row['host'],
            'port':   int(row['port']),
            'secret': secret_bytes,
        }

    # SOCKS / HTTP — dict для python-socks / Telethon
    # python-socks на некоторых версиях делает bytearray += username,
    # что падает с "can't concat str to bytes" если username — str.
    # Кодируем в bytes заранее.
    uname = row['username'] or None
    pword = row['password'] or None
    return {
        '_type':      'socks',
        'proxy_type': proxy_type,
        'addr':       row['host'],
        'port':       int(row['port']),
        'username':   uname.encode('utf-8') if isinstance(uname, str) else uname,
        'password':   pword.encode('utf-8') if isinstance(pword, str) else pword,
        'rdns':       True,
    }


def _make_tg_client(session_path, api_id, api_hash, proxy_info=None):
    """Создать TelegramClient с правильной обработкой SOCKS и MTProto прокси.

    Telethon для MTProto требует:
        connection=ConnectionTcpMTProxyRandomizedIntermediate
        proxy=(host, port, secret_bytes)
    Для SOCKS/HTTP:
        proxy={'proxy_type': ..., 'addr': ..., 'port': ..., ...}
    """
    if proxy_info is None:
        return TelegramClient(session_path, api_id, api_hash)

    if proxy_info.get('_type') == 'mtproto':
        from telethon.network.connection import ConnectionTcpMTProxyRandomizedIntermediate
        return TelegramClient(
            session_path, api_id, api_hash,
            connection=ConnectionTcpMTProxyRandomizedIntermediate,
            proxy=(proxy_info['host'], proxy_info['port'], proxy_info['secret']),
        )

    # SOCKS / HTTP — убираем служебный ключ _type перед передачей
    socks_dict = {k: v for k, v in proxy_info.items() if k != '_type'}
    return TelegramClient(session_path, api_id, api_hash, proxy=socks_dict)

@app.route('/sender_add_account', methods=['POST'])
@login_required
def sender_add_account():
    phone    = request.form.get('phone', '').strip()
    api_id   = request.form.get('api_id', '').strip()
    api_hash = request.form.get('api_hash', '').strip()

    if not phone or not api_id or not api_hash:
        flash('Заполните все поля.', 'error')
        return redirect(url_for('dashboard'))
    try:
        api_id_int = int(api_id)
    except ValueError:
        flash('API ID должен быть числом.', 'error')
        return redirect(url_for('dashboard'))

    session_path = _make_session_path(current_user.id, phone)
    db = get_db()
    proxy = _get_user_proxy(db, current_user.id)

    async def send_code():
        client = _make_tg_client(session_path, api_id_int, api_hash, proxy)
        await client.connect()
        try:
            if await client.is_user_authorized():
                return 'already_authed', None
            result = await client.send_code_request(phone)
            return 'code_sent', result.phone_code_hash
        except PhoneNumberInvalidError:
            return 'invalid_phone', None
        except FloodWaitError as e:
            return 'flood', e.seconds
        finally:
            await client.disconnect()

    try:
        status, payload = run_async(send_code())
    except Exception as e:
        flash(f'Ошибка подключения к Telegram: {e}', 'error')
        return redirect(url_for('dashboard'))

    if status == 'invalid_phone':
        flash('Неверный формат номера телефона.', 'error')
        return redirect(url_for('dashboard'))
    if status == 'flood':
        flash(f'Слишком много попыток. Подождите {payload} секунд.', 'error')
        return redirect(url_for('dashboard'))

    # Сохраняем/обновляем аккаунт в БД (is_active=0 до подтверждения)
    existing = db.execute(
        "SELECT id FROM sender_accounts WHERE user_id=? AND phone=?",
        (current_user.id, phone)
    ).fetchone()
    session_name = os.path.basename(session_path)
    if existing:
        db.execute(
            "UPDATE sender_accounts SET api_id=?, api_hash=?, session_file=?, is_active=0 WHERE id=?",
            (api_id_int, api_hash, session_name, existing['id'])
        )
    else:
        db.execute(
            "INSERT INTO sender_accounts (user_id, phone, api_id, api_hash, session_file, is_active) VALUES (?,?,?,?,?,0)",
            (current_user.id, phone, api_id_int, api_hash, session_name)
        )
    db.commit()

    if status == 'already_authed':
        db.execute(
            "UPDATE sender_accounts SET is_active=1 WHERE user_id=? AND phone=?",
            (current_user.id, phone)
        )
        db.commit()
        flash(f'Аккаунт {phone} уже авторизован и подключён!', 'success')
        return redirect(url_for('dashboard'))

    # Сохраняем состояние авторизации в cookie-сессии
    session['tg_auth'] = {
        'phone': phone,
        'api_id': api_id_int,
        'api_hash': api_hash,
        'phone_code_hash': payload,
        'session_path': session_path,
    }
    return redirect(url_for('verify_code'))


@app.route('/verify_code', methods=['GET', 'POST'])
@login_required
def verify_code():
    auth = session.get('tg_auth')
    if not auth:
        flash('Сессия истекла. Добавьте аккаунт заново.', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'GET':
        return render_template('verify_code.html', phone=auth['phone'])

    code = request.form.get('code', '').strip()

    async def do_sign_in():
        db_inner = get_db()
        client = _make_tg_client(auth['session_path'], auth['api_id'], auth['api_hash'],
                                  _get_user_proxy(db_inner, current_user.id))
        await client.connect()
        try:
            await client.sign_in(
                phone=auth['phone'],
                code=code,
                phone_code_hash=auth['phone_code_hash'],
            )
            return 'success', None
        except SessionPasswordNeededError:
            return 'need_2fa', None
        except PhoneCodeInvalidError:
            return 'invalid_code', None
        except PhoneCodeExpiredError:
            return 'expired_code', None
        except FloodWaitError as e:
            return 'flood', e.seconds
        finally:
            await client.disconnect()

    try:
        status, payload = run_async(do_sign_in())
    except Exception as e:
        flash(f'Ошибка: {e}', 'error')
        return render_template('verify_code.html', phone=auth['phone'])

    if status == 'success':
        db = get_db()
        db.execute(
            "UPDATE sender_accounts SET is_active=1 WHERE user_id=? AND phone=?",
            (current_user.id, auth['phone'])
        )
        db.commit()
        session.pop('tg_auth', None)
        flash(f'Аккаунт {auth["phone"]} успешно подключён!', 'success')
        return redirect(url_for('dashboard'))

    if status == 'need_2fa':
        return redirect(url_for('verify_2fa'))

    if status == 'flood':
        flash(f'Слишком много попыток. Подождите {payload} секунд.', 'error')
        session.pop('tg_auth', None)
        return redirect(url_for('dashboard'))

    msg = 'Неверный код.' if status == 'invalid_code' else 'Код устарел. Добавьте аккаунт заново.'
    flash(msg, 'error')
    if status == 'expired_code':
        session.pop('tg_auth', None)
        return redirect(url_for('dashboard'))
    return render_template('verify_code.html', phone=auth['phone'])


@app.route('/verify_2fa', methods=['GET', 'POST'])
@login_required
def verify_2fa():
    auth = session.get('tg_auth')
    if not auth:
        flash('Сессия истекла. Добавьте аккаунт заново.', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'GET':
        return render_template('verify_2fa.html', phone=auth['phone'])

    password = request.form.get('password', '').strip()

    async def do_sign_in_2fa():
        db_inner = get_db()
        client = _make_tg_client(auth['session_path'], auth['api_id'], auth['api_hash'],
                                  _get_user_proxy(db_inner, current_user.id))
        await client.connect()
        try:
            await client.sign_in(password=password)
            return 'success'
        except PasswordHashInvalidError:
            return 'invalid_password'
        except FloodWaitError as e:
            return f'flood:{e.seconds}'
        finally:
            await client.disconnect()

    try:
        status = run_async(do_sign_in_2fa())
    except Exception as e:
        flash(f'Ошибка: {e}', 'error')
        return render_template('verify_2fa.html', phone=auth['phone'])

    if status == 'success':
        db = get_db()
        db.execute(
            "UPDATE sender_accounts SET is_active=1 WHERE user_id=? AND phone=?",
            (current_user.id, auth['phone'])
        )
        db.commit()
        session.pop('tg_auth', None)
        flash(f'Аккаунт {auth["phone"]} успешно подключён!', 'success')
        return redirect(url_for('dashboard'))

    if status.startswith('flood:'):
        secs = status.split(':')[1]
        flash(f'Слишком много попыток. Подождите {secs} секунд.', 'error')
        session.pop('tg_auth', None)
        return redirect(url_for('dashboard'))

    flash('Неверный пароль. Попробуйте ещё раз.', 'error')
    return render_template('verify_2fa.html', phone=auth['phone'])

@app.route('/sender_add_proxy', methods=['POST'])
@login_required
def sender_add_proxy():
    db = get_db()
    db.execute("INSERT INTO proxies (user_id, type, host, port, username, password) VALUES (?, ?, ?, ?, ?, ?)", (current_user.id, request.form['type'], request.form['host'], int(request.form['port']), request.form.get('username', ''), request.form.get('password', '')))
    db.commit()
    flash('Прокси добавлен.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/sender/add_proxy', methods=['POST'])
@login_required
def sender_add_proxy_api():
    """JSON API: добавить прокси (socks5 / socks4 / http / mtproto)."""
    data = request.get_json(force=True) or {}
    proxy_type = (data.get('type') or 'socks5').strip().lower()
    host       = (data.get('host') or '').strip()
    port       = data.get('port')
    username   = (data.get('username') or '').strip()
    password   = (data.get('password') or '').strip()
    secret     = (data.get('secret') or '').strip()

    if not host or not port:
        return jsonify({'error': 'Укажите хост и порт'})
    try:
        port_int = int(port)
        if not (1 <= port_int <= 65535):
            raise ValueError
    except ValueError:
        return jsonify({'error': 'Порт должен быть числом от 1 до 65535'})
    if proxy_type not in ('socks5', 'http', 'socks4', 'mtproto'):
        return jsonify({'error': 'Тип прокси: socks5, socks4, http или mtproto'})

    if proxy_type == 'mtproto':
        secret_clean = secret.lstrip('ee').lstrip('dd') if False else secret  # сохраняем как есть
        if not secret_clean:
            return jsonify({'error': 'Для MTProto прокси необходимо указать Secret'})
        # Проверяем что это валидный hex
        try:
            bytes.fromhex(secret_clean)
        except ValueError:
            # Может быть base64 или другой формат — принимаем
            pass

    db = get_db()
    db.execute(
        "INSERT INTO proxies (user_id, type, host, port, username, password, secret, is_active) VALUES (?,?,?,?,?,?,?,1)",
        (current_user.id, proxy_type, host, port_int, username, password, secret if proxy_type == 'mtproto' else None)
    )
    db.commit()
    row = db.execute("SELECT last_insert_rowid() as id").fetchone()
    return jsonify({'success': True, 'id': row['id'], 'type': proxy_type, 'host': host, 'port': port_int, 'secret': secret})


@app.route('/sender/test_proxy', methods=['POST'])
@login_required
def sender_test_proxy_api():
    """JSON API: проверить прокси — TCP-доступность и соединение с Telegram."""
    import socket
    data     = request.get_json(force=True) or {}
    proxy_id = data.get('proxy_id')
    db       = get_db()
    row      = db.execute(
        "SELECT * FROM proxies WHERE id=? AND user_id=?",
        (proxy_id, current_user.id)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Прокси не найден'})

    host = row['host']
    port = int(row['port'])

    # Шаг 1: TCP-проверка (доступен ли прокси-сервер)
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
    except socket.timeout:
        return jsonify({'ok': False, 'msg': f'❌ Прокси {host}:{port} не отвечает (таймаут 5с)'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'❌ Прокси недоступен: {e}'})

    proxy_type = (row['type'] or 'socks5').lower()
    username   = row['username'] or None
    password   = row['password'] or None
    secret     = row['secret'] or None

    # ── MTProto proxy: проверяем через Telethon connect ──────────────────────
    if proxy_type == 'mtproto':
        async def _test_mtproto():
            try:
                raw_secret = (secret or '').strip()
                try:
                    secret_bytes = bytes.fromhex(raw_secret)
                except (ValueError, AttributeError):
                    import base64
                    try:
                        secret_bytes = base64.b64decode(raw_secret)
                    except Exception:
                        secret_bytes = raw_secret.encode('utf-8')

                # Временный клиент без аккаунта — пробуем подключиться
                import tempfile, os
                tmp_session = os.path.join(tempfile.gettempdir(), f'tg_proxy_test_{row["id"]}')
                proxy_info = {'_type': 'mtproto', 'host': host, 'port': port, 'secret': secret_bytes}
                client = _make_tg_client(tmp_session, 2040, 'b18441a1ff607e10a989891a5462e627', proxy_info)
                await asyncio.wait_for(client.connect(), timeout=12)
                connected = client.is_connected()
                await client.disconnect()
                # Удалить временный файл сессии
                for ext in ('', '.session', '.session-journal'):
                    try:
                        os.remove(tmp_session + ext)
                    except Exception:
                        pass
                return connected, None
            except asyncio.TimeoutError:
                return False, 'Таймаут подключения (12с) — MTProto прокси не отвечает'
            except Exception as ex:
                return False, str(ex)

        try:
            ok, err = run_async(_test_mtproto())
        except Exception as e:
            ok, err = False, str(e)

        if ok:
            return jsonify({'ok': True, 'msg': f'✅ MTProto прокси работает! Telegram DC доступен через {host}:{port}'})
        else:
            return jsonify({'ok': False, 'msg': f'❌ MTProto прокси не работает: {err or "нет соединения"}'})

    # ── SOCKS / HTTP: TCP → Telethon DC тест ──────────────────────────────────
    # Telegram DCs: 149.154.167.51:443 (DC2), 91.108.4.1:443 (DC4)
    TELEGRAM_TEST_HOSTS = [
        ('149.154.167.51', 443),
        ('149.154.175.50', 443),
        ('91.108.4.1',     443),
    ]

    async def _test_via_socks():
        try:
            from python_socks.async_.asyncio import Proxy
            type_map = {'socks5': 'socks5', 'socks4': 'socks4', 'http': 'http'}
            proxy_url = f"{type_map.get(proxy_type, 'socks5')}://"
            if username:
                proxy_url += f"{username}:{password}@"
            proxy_url += f"{host}:{port}"
            p = Proxy.from_url(proxy_url)
            for tg_host, tg_port in TELEGRAM_TEST_HOSTS:
                try:
                    sock = await asyncio.wait_for(
                        p.connect(dest_host=tg_host, dest_port=tg_port),
                        timeout=10
                    )
                    sock.close()
                    return True, tg_host
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    continue
            return False, 'Все DC Telegram недостижимы через этот прокси'
        except ImportError:
            return None, 'python-socks не установлен'

    try:
        ok, detail = run_async(_test_via_socks())
    except Exception as e:
        ok, detail = False, str(e)

    if ok is None:
        return jsonify({'ok': False, 'msg': f'⚠️ python-socks не установлен на сервере. Выполните: pip3 install "python-socks[asyncio]"'})
    if ok:
        return jsonify({'ok': True, 'msg': f'✅ Прокси работает! Telegram DC {detail} доступен через {host}:{port}'})
    else:
        return jsonify({'ok': False, 'msg': f'❌ Прокси не пропускает Telegram: {detail}. Нужен SOCKS5-прокси без блокировки Telegram (не Россия).'})


@app.route('/sender/delete_proxy', methods=['POST'])
@login_required
def sender_delete_proxy_api():
    """JSON API: удалить прокси."""
    data     = request.get_json(force=True) or {}
    proxy_id = data.get('proxy_id')
    db       = get_db()
    row      = db.execute(
        "SELECT id FROM proxies WHERE id=? AND user_id=?",
        (proxy_id, current_user.id)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Прокси не найден'})
    db.execute("DELETE FROM proxies WHERE id=?", (proxy_id,))
    db.commit()
    return jsonify({'success': True})


# ---------- JSON API: Telegram account activation ----------

@app.route('/sender/send_code', methods=['POST'])
@login_required
def sender_send_code_api():
    """JSON API: отправить код подтверждения на номер Telegram."""
    data     = request.get_json(force=True) or {}
    phone    = (data.get('phone') or '').strip()
    api_id   = (data.get('api_id') or '').strip()
    api_hash = (data.get('api_hash') or '').strip()

    if not phone or not api_id or not api_hash:
        return jsonify({'error': 'Заполните все поля'})
    try:
        api_id_int = int(api_id)
    except ValueError:
        return jsonify({'error': 'API ID должен быть числом'})
    if not rate_limit_user(current_user.id, 'send_code', 5, 300):
        return jsonify({'error': 'Слишком много попыток. Подождите 5 минут.'})

    db           = get_db()
    proxy        = _get_user_proxy(db, current_user.id)
    session_path = _make_session_path(current_user.id, phone)
    os.makedirs('sessions', exist_ok=True)

    async def _send():
        client = _make_tg_client(session_path, api_id_int, api_hash, proxy)
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            if await client.is_user_authorized():
                return 'already_authed', None
            result = await client.send_code_request(phone)
            return 'ok', result.phone_code_hash
        except asyncio.TimeoutError:
            return 'timeout', None
        except PhoneNumberInvalidError:
            return 'invalid_phone', None
        except FloodWaitError as e:
            return 'flood', e.seconds
        except Exception as e:
            return 'error', str(e)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    try:
        status, payload = run_async(_send())
    except Exception as e:
        import traceback as _tb
        return jsonify({'error': f'Ошибка: {e}', 'trace': _tb.format_exc()})

    if status == 'timeout':
        return jsonify({'error': 'Таймаут подключения к Telegram. Добавьте прокси в настройках.'})
    if status == 'invalid_phone':
        return jsonify({'error': 'Неверный формат номера телефона'})
    if status == 'flood':
        return jsonify({'error': f'Слишком много попыток. Подождите {payload} сек.'})
    if status == 'error':
        return jsonify({'error': f'Ошибка: {payload}'})

    # Сохранить/обновить запись в БД (is_active=0 до подтверждения)
    session_name = os.path.basename(session_path)
    existing = db.execute(
        "SELECT id FROM sender_accounts WHERE user_id=? AND phone=?",
        (current_user.id, phone)
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE sender_accounts SET api_id=?, api_hash=?, session_file=?, is_active=0 WHERE id=?",
            (api_id_int, api_hash, session_name, existing['id'])
        )
    else:
        db.execute(
            "INSERT INTO sender_accounts (user_id, phone, api_id, api_hash, session_file, is_active) VALUES (?,?,?,?,?,0)",
            (current_user.id, phone, api_id_int, api_hash, session_name)
        )
    db.commit()

    if status == 'already_authed':
        db.execute(
            "UPDATE sender_accounts SET is_active=1 WHERE user_id=? AND phone=?",
            (current_user.id, phone)
        )
        db.commit()
        return jsonify({'success': True, 'already_authed': True, 'phone': phone})

    # Хранить phone_code_hash во Flask-сессии (не в БД)
    session['tg_auth'] = {
        'phone':           phone,
        'api_id':          api_id_int,
        'api_hash':        api_hash,
        'phone_code_hash': payload,
        'session_path':    session_path,
    }
    return jsonify({'success': True})


@app.route('/sender/verify_code', methods=['POST'])
@login_required
def sender_verify_code_api():
    """JSON API: подтвердить код (и 2FA-пароль, если требуется)."""
    auth = session.get('tg_auth')
    if not auth:
        return jsonify({'error': 'Сессия истекла. Начните процедуру заново.'})

    data     = request.get_json(force=True) or {}
    code     = (data.get('code') or '').strip()
    password = (data.get('password') or '').strip()

    db    = get_db()
    proxy = _get_user_proxy(db, current_user.id)

    async def _verify():
        client = _make_tg_client(auth['session_path'], auth['api_id'], auth['api_hash'], proxy)
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            if password:
                await client.sign_in(password=password)
            else:
                await client.sign_in(
                    phone=auth['phone'],
                    code=code,
                    phone_code_hash=auth['phone_code_hash'],
                )
            return 'success', None
        except asyncio.TimeoutError:
            return 'timeout', None
        except SessionPasswordNeededError:
            return 'need_2fa', None
        except PhoneCodeInvalidError:
            return 'invalid_code', None
        except PhoneCodeExpiredError:
            return 'expired_code', None
        except PasswordHashInvalidError:
            return 'invalid_password', None
        except FloodWaitError as e:
            return 'flood', e.seconds
        except Exception as e:
            return 'error', str(e)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    try:
        status, payload = run_async(_verify())
    except Exception as e:
        return jsonify({'error': f'Ошибка: {e}'})

    if status == 'success':
        db = get_db()
        db.execute(
            "UPDATE sender_accounts SET is_active=1 WHERE user_id=? AND phone=?",
            (current_user.id, auth['phone'])
        )
        db.commit()
        session.pop('tg_auth', None)
        return jsonify({'success': True, 'phone': auth['phone']})

    if status == 'timeout':
        return jsonify({'error': 'Таймаут подключения к Telegram. Проверьте прокси.'})
    if status == 'need_2fa':
        return jsonify({'need_2fa': True})
    if status == 'invalid_code':
        return jsonify({'error': 'Неверный код. Попробуйте ещё раз.'})
    if status == 'expired_code':
        session.pop('tg_auth', None)
        return jsonify({'error': 'Код устарел. Начните процедуру заново.'})
    if status == 'invalid_password':
        return jsonify({'error': 'Неверный пароль 2FA.'})
    if status == 'flood':
        session.pop('tg_auth', None)
        return jsonify({'error': f'Слишком много попыток. Подождите {payload} сек.'})
    return jsonify({'error': f'Ошибка: {payload}'})


@app.route('/sender/delete_account', methods=['POST'])
@login_required
def sender_delete_account_api():
    """JSON API: удалить аккаунт из БД и сессионный файл."""
    data       = request.get_json(force=True) or {}
    account_id = data.get('account_id')
    db         = get_db()
    acc        = db.execute(
        "SELECT * FROM sender_accounts WHERE id=? AND user_id=?",
        (account_id, current_user.id)
    ).fetchone()
    if not acc:
        return jsonify({'error': 'Аккаунт не найден'})

    # Удалить файл сессии (с расширением и без)
    sf = acc['session_file'] or ''
    if sf:
        for suffix in ['', '.session']:
            path = os.path.join('sessions', sf + suffix)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    db.execute("DELETE FROM sender_accounts WHERE id=?", (account_id,))
    db.commit()
    return jsonify({'success': True})


# ---------- ПОКУПКА ----------
PRODUCT_PRICES = {
    'miner':           {'rub': 490,   'usdt': 8,   'days': 30, 'label': 'TG Lead Miner'},
    'sender':          {'rub': 990,   'usdt': 15,  'days': 30, 'label': 'TG Lead Sender'},
    'start':           {'rub': 990,   'usdt': 15,  'days': 30, 'label': 'Start'},
    'pro':             {'rub': 2490,  'usdt': 38,  'days': 30, 'label': 'Pro'},
    'scale':           {'rub': 6990,  'usdt': 108, 'days': 30, 'label': 'Scale'},
    'addon_warmup':    {'rub': 390,   'usdt': 6,   'days': 30, 'label': 'Автопрогрев аккаунтов'},
    'addon_aishield':  {'rub': 390,   'usdt': 6,   'days': 30, 'label': 'AI-Защита аккаунта'},
    'addon_analytics': {'rub': 290,   'usdt': 5,   'days': 30, 'label': 'Расширенная аналитика'},
    'addon_neuro':     {'rub': 590,   'usdt': 9,   'days': 30, 'label': 'Нейро-ответчик 2.0'},
    'addon_crm':       {'rub': 490,   'usdt': 8,   'days': 30, 'label': 'Интеграция с CRM'},
}

ADDON_NAMES = {
    'warmup':    'addon_warmup',
    'aishield':  'addon_aishield',
    'analytics': 'addon_analytics',
    'neuro':     'addon_neuro',
    'crm':       'addon_crm',
}

@app.route('/buy/<product>', methods=['GET', 'POST'])
@app.route('/buy', methods=['GET', 'POST'])
@login_required
def buy(product='start'):
    if product not in PRODUCT_PRICES:
        product = 'start'
    info = PRODUCT_PRICES[product]

    # Обрабатываем дополнительные модули из URL: ?addons=warmup,analytics
    addons_param  = request.args.get('addons', '') or request.form.get('addons', '')
    addon_keys    = [k.strip() for k in addons_param.split(',') if k.strip() and k.strip() in ADDON_NAMES]
    addon_details = [{'key': k, **PRODUCT_PRICES[ADDON_NAMES[k]]} for k in addon_keys]
    addon_total   = sum(PRODUCT_PRICES[ADDON_NAMES[k]]['rub'] for k in addon_keys)
    total_rub     = info['rub'] + addon_total

    if request.method == 'POST':
        db = get_db()
        user_row = db.execute("SELECT email FROM users WHERE id=?", (current_user.id,)).fetchone()
        user_email = user_row['email'] if user_row else ''

        # Описание заказа с учётом аддонов
        product_label = info.get('label', product.capitalize())
        if addon_keys:
            product_label += ' + ' + ', '.join(PRODUCT_PRICES[ADDON_NAMES[k]]['label'] for k in addon_keys)

        # 1. Пробуем Lava.top
        confirm_url, payment_id = create_lava_payment(
            total_rub, current_user.id, product, info['days'], user_email
        )
        provider = 'lava'

        # 2. Фолбэк на ЮKassa
        if not confirm_url:
            confirm_url, payment_id = create_yookassa_payment(
                total_rub, current_user.id, product, info['days']
            )
            provider = 'yookassa'

        if confirm_url:
            db.execute(
                "INSERT INTO payments (user_id, product, amount, status) VALUES (?, ?, ?, ?)",
                (current_user.id, product_label, total_rub, payment_id)
            )
            db.commit()
            log_action(current_user.id, 'payment_initiated', f'{product_label}:{total_rub}rub:{provider}')
            return redirect(confirm_url)

        # 3. Ни одна система не настроена — фиксируем вручную
        db.execute(
            "INSERT INTO payments (user_id, product, amount, status) VALUES (?, ?, ?, 'pending')",
            (current_user.id, product_label, total_rub)
        )
        db.commit()
        flash('Платёж зафиксирован. Лицензия будет выдана после подтверждения оплаты.', 'info')
        return redirect(url_for('dashboard'))

    return render_template('buy.html', product=product, amount_rub=total_rub,
                           base_rub=info['rub'], addon_details=addon_details,
                           addon_total=addon_total, addons_param=addons_param,
                           product_label=info.get('label', product.capitalize()))


@app.route('/payment/webhook', methods=['POST'])
def payment_webhook():
    """Вебхук от ЮKassa — вызывается после успешной оплаты."""
    data = request.get_json(silent=True) or {}
    event = data.get('event', '')
    obj   = data.get('object', {})

    if event != 'payment.succeeded':
        return jsonify({'ok': True})

    payment_id = obj.get('id', '')
    if not payment_id:
        return jsonify({'error': 'no payment id'}), 400

    # Верифицируем платёж через API ЮKassa (защита от поддельных запросов)
    payment = verify_yookassa_payment(payment_id)
    if not payment or payment.get('status') != 'succeeded':
        return jsonify({'error': 'payment not confirmed'}), 400

    meta    = payment.get('metadata', {})
    user_id = int(meta.get('user_id', 0))
    product = meta.get('product', 'miner').capitalize()
    days    = int(meta.get('days', 30))

    if not user_id:
        return jsonify({'error': 'no user_id'}), 400

    db = get_db()
    # Защита от двойного начисления
    already = db.execute(
        "SELECT id FROM licenses WHERE user_id=? AND product=? AND price=? AND created_at >= datetime('now', '-1 minute')",
        (user_id, product, PRODUCT_PRICES.get(product.lower(), {}).get('rub', 0))
    ).fetchone()
    if already:
        return jsonify({'ok': True})

    license_key = generate_license_key()
    expires_at  = datetime.now() + timedelta(days=days)
    price       = PRODUCT_PRICES.get(product.lower(), {}).get('rub', 0)
    db.execute(
        "INSERT INTO licenses (user_id, license_key, product, price, expires_at, is_active) VALUES (?,?,?,?,?,1)",
        (user_id, license_key, product, price, expires_at)
    )
    db.execute(
        "UPDATE payments SET status='succeeded' WHERE status=? AND user_id=? AND product=?",
        (payment_id, user_id, product.lower())
    )
    db.commit()
    return jsonify({'ok': True})


@app.route('/payment/lava/webhook', methods=['POST'])
def lava_webhook():
    """Вебхук от Lava.top для подписок."""
    data = request.get_json(silent=True) or {}
    event = data.get('event', '') or data.get('status', '')

    # Достаём orderId из разных возможных полей
    order_id = (
        data.get('orderId') or
        data.get('order_id') or
        (data.get('payload') or {}).get('orderId') or ''
    )
    invoice_id = data.get('id') or data.get('contractId') or data.get('invoiceId') or ''

    # orderId формата: tglw-{user_id}-{product}-{hex}
    if not order_id or not order_id.startswith('tglw-'):
        return jsonify({'ok': True})  # чужой вебхук, игнорируем

    parts = order_id.split('-')
    if len(parts) < 3:
        return jsonify({'error': 'bad order format'}), 400

    try:
        user_id = int(parts[1])
        product = parts[2].capitalize()
    except (ValueError, IndexError):
        return jsonify({'error': 'bad order format'}), 400

    db = get_db()

    # Новая подписка или ежемесячное продление
    if event in ('SUBSCRIPTION_ACTIVE', 'PAYMENT_SUCCESS', 'payment.succeeded',
                 'SUBSCRIPTION_RENEWED', 'subscription.renewed', 'success'):

        days  = PRODUCT_PRICES.get(product.lower(), {}).get('days', 30)
        price = PRODUCT_PRICES.get(product.lower(), {}).get('rub', 0)

        existing = db.execute(
            "SELECT id, expires_at FROM licenses WHERE user_id=? AND product=? AND is_active=1",
            (user_id, product)
        ).fetchone()

        if existing:
            db.execute(
                "UPDATE licenses SET expires_at = datetime(expires_at, '+30 days') WHERE id=?",
                (existing['id'],)
            )
            log_action(user_id, 'subscription_renewed', f'{product}:lava:{invoice_id}')
        else:
            license_key = generate_license_key()
            expires_at  = datetime.now() + timedelta(days=days)
            db.execute(
                "INSERT INTO licenses (user_id, license_key, product, price, expires_at, is_active) VALUES (?,?,?,?,?,1)",
                (user_id, license_key, product, price, expires_at)
            )
            db.execute(
                "UPDATE payments SET status='succeeded' WHERE user_id=? AND product=? AND status='pending'",
                (user_id, product.lower())
            )
            log_action(user_id, 'subscription_activated', f'{product}:{price}rub:lava:{invoice_id}')
            # Уведомление пользователю и админу
            user_row = db.execute("SELECT telegram_id, email FROM users WHERE id=?", (user_id,)).fetchone()
            if user_row and user_row['telegram_id']:
                send_telegram(user_row['telegram_id'],
                    f'✅ <b>TG Lead Wareon</b>\n\nЛицензия <b>{product}</b> активирована на 30 дней.\n'
                    f'Войдите в кабинет: {BASE_URL}/dashboard')
            send_telegram(ADMIN_ID,
                f'💰 Новая оплата!\nПользователь: {user_id} ({(user_row or {}).get("email","")})\n'
                f'Тариф: {product} · {price}₽\nПровайдер: Lava.top')
            # Email-уведомление пользователю
            if user_row and user_row['email']:
                threading.Thread(
                    target=send_purchase_email,
                    args=(user_row['email'], product, price, expires_at),
                    daemon=True
                ).start()

        db.commit()

    # Отмена или истечение подписки — деактивируем лицензию
    elif event in ('SUBSCRIPTION_CANCELLED', 'SUBSCRIPTION_EXPIRED',
                   'subscription.cancelled', 'subscription.expired'):
        db.execute(
            "UPDATE licenses SET is_active=0 WHERE user_id=? AND product=? AND is_active=1",
            (user_id, product)
        )
        db.commit()
        log_action(user_id, f'subscription_{event.lower()}', f'{product}:lava:{invoice_id}')

    return jsonify({'ok': True})


@app.route('/payment/success')
@login_required
def payment_success():
    product = request.args.get('product', 'miner')
    flash(f'Оплата прошла успешно! Лицензия {product.capitalize()} активирована.', 'success')
    return redirect(url_for('dashboard'))

# ---------- MINER ----------
@app.route('/miner')
@login_required
def miner():
    db = get_db()
    miner_jobs = db.execute("SELECT * FROM miner_jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (current_user.id,)).fetchall()
    return render_template('miner.html', miner_jobs=miner_jobs)

# ---------- MINER: ФОНОВЫЙ СБОР ----------

def run_miner_job(job_id, session_path, api_id, api_hash, link, user_id, proxy, limit):
    """Запускается в отдельном потоке. Создаёт собственное соединение с БД."""
    async def _collect():
        conn = sqlite3.connect(DATABASE)
        try:
            conn.execute("UPDATE miner_jobs SET status='running' WHERE id=?", (job_id,))
            conn.commit()
            client = _make_tg_client(session_path, api_id, api_hash, proxy)
            await client.connect()
            try:
                entity = await client.get_entity(link)
                participants = await client.get_participants(entity, limit=limit)
                batch = [
                    (job_id, user_id, str(m.id), m.username, m.first_name or '', m.last_name or '')
                    for m in participants if not m.bot
                ]
                conn.executemany(
                    "INSERT INTO leads (job_id, user_id, tg_id, username, first_name, last_name) VALUES (?,?,?,?,?,?)",
                    batch,
                )
                conn.execute(
                    "UPDATE miner_jobs SET status='done', leads_count=? WHERE id=?",
                    (len(batch), job_id),
                )
            except Exception as e:
                conn.execute(
                    "UPDATE miner_jobs SET status='error', error_msg=? WHERE id=?",
                    (str(e)[:255], job_id),
                )
            finally:
                await client.disconnect()
            conn.commit()
        except Exception as e:
            try:
                conn.execute(
                    "UPDATE miner_jobs SET status='error', error_msg=? WHERE id=?",
                    (str(e)[:255], job_id),
                )
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()

    asyncio.run(_collect())


@app.route('/miner/collect', methods=['POST'])
@login_required
def miner_collect():
    link = request.form.get('link', '').strip()
    if not link:
        flash('Введите ссылку на чат или канал.', 'error')
        return redirect(url_for('miner'))

    if not rate_limit_user(current_user.id, 'miner_collect', max_calls=20, window_seconds=3600):
        flash('Превышен лимит запросов. Подождите немного.', 'error')
        return redirect(url_for('miner'))

    db = get_db()

    # Проверка лицензии
    lic = db.execute(
        "SELECT * FROM licenses WHERE user_id=? AND is_active=1 AND product='Miner'",
        (current_user.id,),
    ).fetchone()
    if not lic:
        flash('Нужна активная лицензия Miner.', 'error')
        return redirect(url_for('miner'))

    is_trial = (lic['price'] == 0)
    collect_limit = 200 if is_trial else 5000

    if is_trial:
        count_today = db.execute(
            "SELECT COUNT(*) FROM miner_jobs WHERE user_id=? AND date(created_at)=date('now')",
            (current_user.id,),
        ).fetchone()[0]
        if count_today >= 2:
            flash('Лимит пробного тарифа — 2 сбора в день. Купите лицензию для снятия ограничений.', 'error')
            return redirect(url_for('miner'))

    # Активный аккаунт
    account = db.execute(
        "SELECT * FROM sender_accounts WHERE user_id=? AND is_active=1 LIMIT 1",
        (current_user.id,),
    ).fetchone()
    if not account:
        flash('Сначала подключите аккаунт Telegram в разделе «Аккаунты».', 'error')
        return redirect(url_for('miner'))

    session_path = os.path.join('sessions', account['session_file'])
    proxy = _get_user_proxy(db, current_user.id)

    cursor = db.execute(
        "INSERT INTO miner_jobs (user_id, source_link, status) VALUES (?,?,'pending')",
        (current_user.id, link),
    )
    job_id = cursor.lastrowid
    db.commit()

    threading.Thread(
        target=run_miner_job,
        args=(job_id, session_path, account['api_id'], account['api_hash'],
              link, current_user.id, proxy, collect_limit),
        daemon=True,
    ).start()

    log_action(current_user.id, 'miner_collect', link)
    flash('Сбор запущен! Статус обновляется автоматически.', 'success')
    return redirect(url_for('miner'))


@app.route('/miner/job/<int:job_id>/export')
@login_required
def miner_export(job_id):
    db = get_db()
    job = db.execute(
        "SELECT * FROM miner_jobs WHERE id=? AND user_id=?",
        (job_id, current_user.id),
    ).fetchone()
    if not job or job['status'] != 'done':
        flash('Экспорт недоступен.', 'error')
        return redirect(url_for('miner'))

    rows = db.execute(
        "SELECT tg_id, username, first_name, last_name FROM leads WHERE job_id=?",
        (job_id,),
    ).fetchall()

    buf = io.StringIO()
    buf.write("tg_id,username,first_name,last_name\n")
    for r in rows:
        buf.write(f"{r['tg_id']},{r['username'] or ''},{r['first_name'] or ''},{r['last_name'] or ''}\n")

    return send_file(
        io.BytesIO(buf.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'leads_job{job_id}.csv',
    )

# ---------- МАГАЗИН ПРОКСИ ----------
@app.route('/buy_proxy')
@login_required
def buy_proxy():
    db = get_db()
    available = db.execute("SELECT COUNT(*) FROM proxy_pool WHERE is_sold = 0").fetchone()[0]
    return render_template('buy_proxy.html', available_count=available, price_per_proxy=150)

@app.route('/buy_proxy/checkout', methods=['POST'])
@login_required
def buy_proxy_checkout():
    db = get_db()
    quantity = int(request.form.get('quantity', 1))
    proxies = db.execute("SELECT id, host, port, type, username, password FROM proxy_pool WHERE is_sold = 0 LIMIT ?", (quantity,)).fetchall()
    if len(proxies) < quantity:
        flash('Недостаточно прокси.', 'error')
        return redirect(url_for('buy_proxy'))
    for proxy in proxies:
        db.execute("UPDATE proxy_pool SET is_sold = 1, sold_to = ?, sold_at = ? WHERE id = ?", (current_user.id, datetime.now(), proxy['id']))
        db.execute("INSERT INTO proxies (user_id, type, host, port, username, password, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)", (current_user.id, proxy['type'], proxy['host'], proxy['port'], proxy['username'], proxy['password']))
    db.commit()
    flash(f'Куплено {len(proxies)} прокси!', 'success')
    return redirect(url_for('dashboard'))

# ---------- API ДЛЯ БОТА ----------
@app.route('/api/check_account')
def api_check_account():
    phone = request.args.get('phone', '').strip().replace(' ', '').replace('+', '')
    db = get_db()
    all_accounts = db.execute("SELECT * FROM sender_accounts WHERE is_active = 0").fetchall()
    account = None
    for acc in all_accounts:
        db_phone = acc['phone'].replace(' ', '').replace('+', '')
        if db_phone == phone:
            account = acc
            break
    if not account:
        return jsonify({'error': 'Аккаунт не найден'}), 404
    return jsonify({'api_id': account['api_id'], 'api_hash': account['api_hash']})

@app.route('/api/activate_account', methods=['POST'])
def api_activate_account():
    data = request.get_json()
    phone = data.get('phone', '').strip().replace(' ', '').replace('+', '')
    db = get_db()
    all_accounts = db.execute("SELECT * FROM sender_accounts WHERE is_active = 0").fetchall()
    account = None
    for acc in all_accounts:
        db_phone = acc['phone'].replace(' ', '').replace('+', '')
        if db_phone == phone:
            account = acc
            break
    if not account:
        return jsonify({'error': 'Аккаунт не найден'}), 404
    db.execute("UPDATE sender_accounts SET is_active = 1 WHERE id = ?", (account['id'],))
    db.commit()
    return jsonify({'success': True})

# ---------- АДМИН-ПАНЕЛЬ ----------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('key', '') == ADMIN_SECRET_KEY:
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        flash('Неверный ключ', 'error')
    return render_template('admin_login.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    db = get_db()
    users            = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    licenses         = db.execute("SELECT * FROM licenses ORDER BY created_at DESC").fetchall()
    pending_payments = db.execute("SELECT * FROM payments WHERE status='pending' ORDER BY created_at DESC LIMIT 20").fetchall()
    pending_reviews  = db.execute("SELECT COUNT(*) FROM reviews WHERE is_approved=0").fetchone()[0]
    recent_actions   = db.execute("SELECT * FROM user_actions ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template('admin.html', users=users, licenses=licenses,
                           pending_payments=pending_payments, pending_reviews=pending_reviews,
                           recent_actions=recent_actions)

@app.route('/admin/give_license', methods=['POST'])
def admin_give_license():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    product = request.form.get('product', 'miner').capitalize()
    days    = int(request.form.get('days', 30))
    db      = get_db()

    # Поддержка как email, так и user_id (для кнопки из таблицы платежей)
    user_id_direct = request.form.get('user_id', '').strip()
    if user_id_direct:
        user_row = db.execute("SELECT id FROM users WHERE id=?", (user_id_direct,)).fetchone()
    else:
        user_email = request.form.get('email', '').strip()
        user_row   = db.execute("SELECT id FROM users WHERE email=?", (user_email,)).fetchone()

    if not user_row:
        flash('Пользователь не найден.', 'error')
        return redirect(url_for('admin_dashboard'))

    license_key = generate_license_key()
    expires_at  = datetime.now() + timedelta(days=days)
    price       = PRODUCT_PRICES.get(product.lower(), {}).get('rub', 0)
    db.execute(
        "INSERT INTO licenses (user_id, license_key, product, price, expires_at, is_active) VALUES (?,?,?,?,?,1)",
        (user_row['id'], license_key, product, price, expires_at)
    )
    db.commit()
    flash(f'Лицензия {product} выдана на {days} дней.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/revoke_license/<int:license_id>')
def admin_revoke_license(license_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    db = get_db()
    db.execute("UPDATE licenses SET is_active = 0 WHERE id = ?", (license_id,))
    db.commit()
    flash('Лицензия отозвана', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/activate_user/<int:user_id>')
def admin_activate_user(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    db = get_db()
    db.execute("UPDATE sender_accounts SET is_active = 1 WHERE user_id = ?", (user_id,))
    db.commit()
    flash('Аккаунты активированы.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))

# ---------- API ДЛЯ ДЕСКТОПНОГО MINER ----------
@app.route('/api/verify_license', methods=['POST'])
def api_verify_license():
    data = request.get_json()
    license_key = data.get('license_key', '').strip()
    
    if not license_key:
        return jsonify({'error': 'Ключ не указан'}), 400
    
    db = get_db()
    license = db.execute(
        "SELECT * FROM licenses WHERE license_key = ? AND is_active = 1",
        (license_key,)
    ).fetchone()
    
    if not license:
        return jsonify({'error': 'Лицензия не найдена или неактивна'}), 404
    
    # Проверяем срок действия
    expires_at = parse_dt(license['expires_at'])
    if datetime.now() > expires_at:
        return jsonify({'error': 'Срок лицензии истёк'}), 410
    
    return jsonify({
        'success': True,
        'product': license['product'],
        'expires_at': license['expires_at'],
        'user_id': license['user_id']
    })

# ---------- РЕФЕРАЛЬНАЯ ПРОГРАММА ----------
@app.route('/referral')
@login_required
def referral():
    db = get_db()
    referred = db.execute(
        "SELECT id, full_name, email, created_at FROM users WHERE referral_id=? ORDER BY created_at DESC",
        (current_user.id,),
    ).fetchall()
    bonus_days = len(referred)
    ref_link = f"{BASE_URL}/?ref={current_user.id}"
    return render_template('referral.html',
                           referred=referred,
                           bonus_days=bonus_days,
                           ref_link=ref_link)


# ---------- API ОТЗЫВОВ ----------
@app.route('/api/review_bonus', methods=['POST'])
def api_review_bonus():
    """Вызывается ботом @TGLeadReviewsBot после получения отзыва."""
    data = request.get_json(silent=True) or {}

    token = data.get('token', '')
    if not REVIEW_BOT_TOKEN or token != REVIEW_BOT_TOKEN:
        return jsonify({'error': 'Неверный токен'}), 403

    telegram_id       = str(data.get('telegram_id', ''))
    telegram_username = data.get('username', '')
    rating            = int(data.get('rating', 5))
    text              = data.get('text', '').strip()
    user_email        = data.get('user_email', '').strip()

    if not telegram_id or not text or rating not in range(1, 6):
        return jsonify({'error': 'Неверные данные'}), 400

    db = get_db()

    # Ищем пользователя сайта по email (если передан)
    site_user = None
    if user_email:
        site_user = db.execute("SELECT * FROM users WHERE email=?", (user_email,)).fetchone()

    bonus_days = 2
    db.execute(
        "INSERT INTO reviews (telegram_username, telegram_id, user_id, rating, text, bonus_days) VALUES (?,?,?,?,?,?)",
        (telegram_username, telegram_id, site_user['id'] if site_user else None, rating, text, bonus_days)
    )

    # Начисляем бонус если нашли пользователя на сайте
    if site_user:
        db.execute(
            "UPDATE licenses SET expires_at = datetime(expires_at, '+{} days') WHERE user_id=? AND is_active=1".format(bonus_days),
            (site_user['id'],)
        )

    db.commit()
    return jsonify({'ok': True, 'bonus_days': bonus_days if site_user else 0})


# ---------- ADMIN: ОТЗЫВЫ ----------
@app.route('/admin/reviews')
def admin_reviews():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    db = get_db()
    pending  = db.execute("SELECT * FROM reviews WHERE is_approved=0 ORDER BY created_at DESC").fetchall()
    approved = db.execute("SELECT * FROM reviews WHERE is_approved=1 ORDER BY created_at DESC LIMIT 30").fetchall()
    return render_template('admin_reviews.html', pending=pending, approved=approved)


@app.route('/admin/reviews/<int:review_id>/approve', methods=['POST'])
def admin_approve_review(review_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    db = get_db()
    db.execute("UPDATE reviews SET is_approved=1 WHERE id=?", (review_id,))
    db.commit()
    flash('Отзыв опубликован.', 'success')
    return redirect(url_for('admin_reviews'))


@app.route('/admin/reviews/<int:review_id>/reject', methods=['POST'])
def admin_reject_review(review_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    db = get_db()
    db.execute("DELETE FROM reviews WHERE id=?", (review_id,))
    db.commit()
    flash('Отзыв удалён.', 'success')
    return redirect(url_for('admin_reviews'))


# ---------- CRON: НАПОМИНАНИЯ ОБ ИСТЕЧЕНИИ ----------
@app.route('/cron/expiry_check')
def cron_expiry_check():
    """Вызывай каждый день через cron или внешний планировщик.
    Защита: секретный ключ в параметре ?key="""
    if request.args.get('key', '') != ADMIN_SECRET_KEY:
        return jsonify({'error': 'forbidden'}), 403

    db = get_db()
    # Лицензии, истекающие через 1–3 дня
    expiring = db.execute("""
        SELECT l.user_id, l.product, l.expires_at,
               u.email, u.telegram_id
        FROM licenses l
        JOIN users u ON u.id = l.user_id
        WHERE l.is_active = 1
          AND date(l.expires_at) BETWEEN date('now', '+1 day') AND date('now', '+3 days')
    """).fetchall()

    notified = 0
    for row in expiring:
        days_left = (parse_dt(row['expires_at']) - datetime.now()).days + 1
        text = (
            f'⏰ <b>TG Lead Wareon</b>\n\n'
            f'Лицензия <b>{row["product"]}</b> истекает через <b>{days_left} дн.</b>\n\n'
            f'Продлите, чтобы не прерывать работу:\n{BASE_URL}/pricing'
        )
        if row['telegram_id']:
            send_telegram(row['telegram_id'], text)
            notified += 1
        # Всегда уведомляем админа
        send_telegram(ADMIN_ID,
            f'⏰ Истекает лицензия\nПользователь: {row["user_id"]} ({row["email"]})\n'
            f'Тариф: {row["product"]} · через {days_left} дн.')

    return jsonify({'checked': len(expiring), 'notified': notified})


# ══════════════════════════════════════════════════════════
# ---------- API ГЛАВНОГО БОТА (@TGLeadWareonBot) ----------
# ══════════════════════════════════════════════════════════

def _bot_auth():
    """Проверяет X-Bot-Secret. Возвращает True если запрос от бота."""
    secret = request.headers.get('X-Bot-Secret', '')
    return bool(BOT_MAIN_SECRET and secret == BOT_MAIN_SECRET)


@app.route('/api/bot/user_info', methods=['GET'])
def bot_user_info():
    """Возвращает данные пользователя по tg_id или email + список лицензий."""
    if not _bot_auth():
        return jsonify({'error': 'Forbidden'}), 403

    tg_id = request.args.get('tg_id', '').strip()
    email = request.args.get('email', '').strip().lower()

    db = get_db()
    if tg_id:
        user = db.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    elif email:
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    else:
        return jsonify({'found': False})

    if not user:
        return jsonify({'found': False})

    licenses = db.execute(
        "SELECT * FROM licenses WHERE user_id=? AND is_active=1 ORDER BY expires_at DESC",
        (user['id'],)
    ).fetchall()

    license_list = []
    for lic in licenses:
        try:
            expires   = parse_dt(lic['expires_at'])
            days_left = (expires - datetime.now()).days
        except Exception:
            days_left = 0
        license_list.append({
            'product':     lic['product'],
            'license_key': lic['license_key'],
            'expires_at':  lic['expires_at'],
            'days_left':   max(0, days_left),
            'is_expired':  days_left < 0,
        })

    referral_count = db.execute(
        "SELECT COUNT(*) FROM users WHERE referral_id=?", (user['id'],)
    ).fetchone()[0]

    return jsonify({
        'found': True,
        'user': {
            'id':          user['id'],
            'name':        user['full_name'] or user['email'].split('@')[0],
            'email':       user['email'],
            'telegram_id': user['telegram_id'],
        },
        'licenses':       license_list,
        'referral_count': referral_count,
        'ref_link':       f"{BASE_URL}/?ref={user['id']}",
    })


@app.route('/api/bot/link', methods=['POST'])
def bot_link_account():
    """Привязывает telegram_id к аккаунту по email."""
    if not _bot_auth():
        return jsonify({'error': 'Forbidden'}), 403

    data        = request.get_json() or {}
    email       = data.get('email', '').strip().lower()
    tg_id       = str(data.get('tg_id', '')).strip()
    tg_username = data.get('tg_username', '').strip()

    if not email or not tg_id:
        return jsonify({'ok': False, 'error': 'Нужны email и tg_id'})

    db   = get_db()
    user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()

    if not user:
        return jsonify({'ok': False, 'error': 'Пользователь не найден'})

    db.execute("UPDATE users SET telegram_id=? WHERE id=?", (tg_id, user['id']))
    db.commit()
    log_action(user['id'], 'bot_linked', f'tg:{tg_id}:{tg_username}')
    return jsonify({'ok': True})


@app.route('/api/bot/link_code', methods=['POST'])
def bot_link_code():
    """Привязка через 6-значный код с сайта (для QR-кода в будущем).
    Сайт генерирует код, сохраняет в БД, бот проверяет его здесь."""
    if not _bot_auth():
        return jsonify({'error': 'Forbidden'}), 403

    data  = request.get_json() or {}
    code  = data.get('code', '').strip().upper()
    tg_id = str(data.get('tg_id', '')).strip()

    if not code or not tg_id:
        return jsonify({'ok': False, 'error': 'Нужны code и tg_id'})

    db  = get_db()
    row = db.execute(
        "SELECT user_id FROM link_codes WHERE code=? AND expires_at > datetime('now') AND used=0",
        (code,)
    ).fetchone()

    if not row:
        return jsonify({'ok': False, 'error': 'Код неверный или истёк'})

    user_id = row['user_id']
    db.execute("UPDATE users SET telegram_id=? WHERE id=?", (tg_id, user_id))
    db.execute("UPDATE link_codes SET used=1 WHERE code=?", (code,))
    db.commit()
    log_action(user_id, 'bot_linked_qr', f'tg:{tg_id}')
    return jsonify({'ok': True})


@app.route('/api/bot/expiring_licenses', methods=['GET'])
def bot_expiring_licenses():
    """Список пользователей с лицензиями, истекающими ровно через N дней.
    Используется ботом для отправки уведомлений."""
    if not _bot_auth():
        return jsonify({'error': 'Forbidden'}), 403

    days = int(request.args.get('days', 3))
    db   = get_db()

    rows = db.execute("""
        SELECT l.product, l.expires_at, u.telegram_id, u.email, u.full_name
        FROM licenses l
        JOIN users u ON u.id = l.user_id
        WHERE l.is_active = 1
          AND u.telegram_id IS NOT NULL AND u.telegram_id != ''
          AND date(l.expires_at) = date('now', '+' || ? || ' days')
    """, (days,)).fetchall()

    result = [{
        'telegram_id': r['telegram_id'],
        'email':       r['email'],
        'name':        r['full_name'] or r['email'].split('@')[0],
        'product':     r['product'],
        'expires_at':  r['expires_at'],
        'days_left':   days,
    } for r in rows]

    return jsonify({'users': result})


# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
