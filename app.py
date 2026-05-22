import os, uuid, sqlite3, random, string, io, asyncio, threading, concurrent.futures
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
import requests

# ---------- НАСТРОЙКИ ----------
SECRET_KEY       = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
ADMIN_SECRET_KEY = os.environ.get('ADMIN_SECRET_KEY', '')
USDT_WALLET      = os.environ.get('USDT_WALLET', '')
ADMIN_ID            = int(os.environ.get('ADMIN_ID', '5062414502'))
DATABASE            = os.environ.get('DATABASE', 'lead_ecosystem.db')
SUPPORT_USERNAME    = '@TGLeadSupportBot'
YOOKASSA_SHOP_ID    = os.environ.get('YOOKASSA_SHOP_ID', '')
YOOKASSA_SECRET_KEY = os.environ.get('YOOKASSA_SECRET_KEY', '')
REVIEW_BOT_TOKEN    = os.environ.get('REVIEW_BOT_TOKEN', '')
BASE_URL            = os.environ.get('BASE_URL', 'http://localhost:5000')

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
    ]:
        try:
            db.execute(sql)
        except Exception:
            pass
    db.commit()


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
    """Вернуть первый SOCKS5-прокси пользователя для Telethon, или None."""
    row = db.execute(
        "SELECT * FROM proxies WHERE user_id=? AND type='socks5' AND is_active=1 LIMIT 1",
        (user_id,)
    ).fetchone()
    if not row:
        return None
    return {
        'proxy_type': 'socks5',
        'addr': row['host'],
        'port': row['port'],
        'username': row['username'] or None,
        'password': row['password'] or None,
        'rdns': True,
    }

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
        client = TelegramClient(session_path, api_id_int, api_hash, proxy=proxy)
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
        client = TelegramClient(auth['session_path'], auth['api_id'], auth['api_hash'])
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
        client = TelegramClient(auth['session_path'], auth['api_id'], auth['api_hash'])
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

# ---------- ПОКУПКА ----------
PRODUCT_PRICES = {
    'miner':  {'rub': 490,  'usdt': 8,  'days': 30},
    'sender': {'rub': 990,  'usdt': 15, 'days': 30},
}

@app.route('/buy/<product>', methods=['GET', 'POST'])
@app.route('/buy', methods=['GET', 'POST'])
@login_required
def buy(product='miner'):
    if product not in PRODUCT_PRICES:
        product = 'miner'
    info = PRODUCT_PRICES[product]

    if request.method == 'POST':
        db = get_db()
        # Пробуем создать платёж через ЮKassa
        confirm_url, payment_id = create_yookassa_payment(
            info['rub'], current_user.id, product, info['days']
        )
        if confirm_url:
            db.execute(
                "INSERT INTO payments (user_id, product, amount, status) VALUES (?, ?, ?, ?)",
                (current_user.id, product, info['rub'], payment_id)
            )
            db.commit()
            log_action(current_user.id, 'payment_initiated', f'{product}:{info["rub"]}rub')
            return redirect(confirm_url)
        # Фолбэк: ЮKassa не настроена — фиксируем вручную
        db.execute(
            "INSERT INTO payments (user_id, product, amount, status) VALUES (?, ?, ?, 'pending')",
            (current_user.id, product, info['rub'])
        )
        db.commit()
        flash('Платёж зафиксирован. Лицензия будет выдана после подтверждения оплаты.', 'info')
        return redirect(url_for('dashboard'))

    return render_template(
        'buy.html', product=product,
        selected_plan='Pro', billing_period='1 месяц',
        amount_rub=info['rub'], amount_usdt=info['usdt'],
        usdt_wallet=USDT_WALLET,
    )


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
            client = TelegramClient(session_path, api_id, api_hash, proxy=proxy)
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


# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
