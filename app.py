import os, sqlite3, random, string, io
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode

# ---------- НАСТРОЙКИ (замените на свои) ----------
SECRET_KEY = 'ваш-секретный-ключ-сюда'
DATABASE = 'lead_ecosystem.db'
ADMIN_ID = 123456789
SUPPORT_USERNAME = '@Support'
API_ID = 12345678
API_HASH = "abc123..."
USDT_WALLET = "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ---------- КОНТЕКСТНЫЙ ПРОЦЕССОР ----------
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
            balance INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            trial_used INTEGER DEFAULT 0,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    db.commit()

# ---------- МОДЕЛЬ ПОЛЬЗОВАТЕЛЯ ----------
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

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def generate_license_key():
    chars = string.ascii_uppercase + string.digits
    return f"TGLS-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}"

# ---------- ГЛАВНАЯ СТРАНИЦА ----------
@app.route('/')
def index():
    @app.route('/pricing')
    
def pricing():
    return render_template('pricing.html')

@app.route('/cases')
def cases():
    return render_template('cases.html')

@app.route('/blog')
def blog():
    return render_template('blog.html')

@app.route('/faq')
def faq():
    return render_template('faq.html')

@app.route('/support')
def support():
    return render_template('support.html')
    return render_template('index.html')

# ---------- РЕГИСТРАЦИЯ / ВХОД ----------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        full_name = request.form.get('full_name', '').strip()
        db = get_db()
        if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            flash('Этот email уже зарегистрирован.', 'error')
            return render_template('register.html')
        hashed_pw = generate_password_hash(password)
        db.execute("INSERT INTO users (email, password, full_name) VALUES (?, ?, ?)",
                   (email, hashed_pw, full_name))
        db.commit()
        flash('Регистрация успешна! Войдите.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row and check_password_hash(row['password'], password):
            user = User(row['id'], row['email'], row['full_name'])
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Неверный email или пароль.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# ---------- ЛИЧНЫЙ КАБИНЕТ (ОБНОВЛЁН) ----------
@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    
    # Статистика
    active_licenses_count = db.execute(
        "SELECT COUNT(*) FROM licenses WHERE user_id = ? AND is_active = 1",
        (current_user.id,)
    ).fetchone()[0]
    
    total_leads_collected = db.execute(
        "SELECT COALESCE(SUM(leads_count), 0) FROM miner_jobs WHERE user_id = ?",
        (current_user.id,)
    ).fetchone()[0]
    
    total_messages_sent = db.execute(
        "SELECT COALESCE(total_sent, 0) FROM users WHERE id = ?",
        (current_user.id,)
    ).fetchone()[0]
    
    # Лицензии
    miner_license = db.execute(
        "SELECT * FROM licenses WHERE user_id = ? AND is_active = 1 AND product = 'Miner' ORDER BY expires_at DESC LIMIT 1",
        (current_user.id,)
    ).fetchone()
    
    sender_license = db.execute(
        "SELECT * FROM licenses WHERE user_id = ? AND is_active = 1 AND product = 'Sender' ORDER BY expires_at DESC LIMIT 1",
        (current_user.id,)
    ).fetchone()
    
    # Аккаунты и прокси
    sender_accounts = db.execute(
        "SELECT * FROM sender_accounts WHERE user_id = ?",
        (current_user.id,)
    ).fetchall()
    
    proxies = db.execute(
        "SELECT * FROM proxies WHERE user_id = ?",
        (current_user.id,)
    ).fetchall()
    
    return render_template('dashboard.html',
                           active_licenses_count=active_licenses_count,
                           total_leads_collected=total_leads_collected,
                           total_messages_sent=total_messages_sent,
                           miner_license=miner_license,
                           sender_license=sender_license,
                           sender_accounts=sender_accounts,
                           proxies=proxies)

# ---------- УПРАВЛЕНИЕ АККАУНТАМИ SENDER ----------
@app.route('/sender/add_account', methods=['POST'])
@login_required
def sender_add_account():
    phone = request.form['phone'].strip()
    api_id = request.form['api_id'].strip()
    api_hash = request.form['api_hash'].strip()
    db = get_db()
    db.execute("INSERT INTO sender_accounts (user_id, phone, api_id, api_hash) VALUES (?, ?, ?, ?)",
               (current_user.id, phone, api_id, api_hash))
    db.commit()
    flash(f'Аккаунт {phone} добавлен.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/sender/add_proxy', methods=['POST'])
@login_required
def sender_add_proxy():
    db = get_db()
    db.execute("INSERT INTO proxies (user_id, type, host, port, username, password) VALUES (?, ?, ?, ?, ?, ?)",
               (current_user.id,
                request.form['type'],
                request.form['host'],
                int(request.form['port']),
                request.form.get('username', ''),
                request.form.get('password', '')))
    db.commit()
    flash('Прокси добавлен.', 'success')
    return redirect(url_for('dashboard'))

# ---------- ПОКУПКА (ОБНОВЛЁН) ----------
@app.route('/buy/<product>', methods=['GET', 'POST'])
@app.route('/buy', methods=['GET', 'POST'])
@login_required
def buy(product='miner'):
    if product not in ['miner', 'sender']:
        product = 'miner'
    
    if request.method == 'POST':
        method = request.form.get('method', 'card')
        db = get_db()
        
        # Фиктивная сумма для примера
        amount_rub = 990 if product == 'sender' else 490
        amount_usdt = 15 if product == 'sender' else 8
        
        db.execute("INSERT INTO payments (user_id, product, amount) VALUES (?, ?, ?)",
                   (current_user.id, product, amount_rub))
        db.commit()
        
        flash('Платёж зафиксирован. Ожидайте активацию лицензии.', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('buy.html',
                           product=product,
                           selected_plan='Pro',
                           billing_period='1 месяц',
                           amount_rub=990 if product == 'sender' else 490,
                           amount_usdt=15 if product == 'sender' else 8,
                           usdt_wallet=USDT_WALLET)

# ---------- TG LEAD MINER (ОБНОВЛЁН) ----------
@app.route('/miner')
@login_required
def miner_panel():
    db = get_db()
    miner_jobs = db.execute(
        "SELECT * FROM miner_jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
        (current_user.id,)
    ).fetchall()
    return render_template('miner.html', miner_jobs=miner_jobs)

@app.route('/miner/collect', methods=['POST'])
@login_required
def miner_collect():
    link = request.form['link'].strip()
    db = get_db()
    db.execute("INSERT INTO miner_jobs (user_id, source_link, status, leads_count) VALUES (?, ?, ?, ?)",
               (current_user.id, link, 'В очереди', 0))
    db.commit()
    flash(f'Сбор из {link} добавлен в очередь.', 'success')
    return redirect(url_for('miner_panel'))

# ---------- АДМИН-ПАНЕЛЬ ----------
@app.route('/admin')
@login_required
def admin_panel():
    if current_user.id != ADMIN_ID:
        return "Доступ запрещен", 403
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 20").fetchall()
    licenses = db.execute("SELECT * FROM licenses ORDER BY created_at DESC LIMIT 20").fetchall()
    return render_template('admin.html', users=users, licenses=licenses)
    
