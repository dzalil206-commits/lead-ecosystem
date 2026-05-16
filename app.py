import os, sqlite3, random, string, io, asyncio, threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, send_file, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient
import requests

# ---------- НАСТРОЙКИ ----------
SECRET_KEY = 'ваш-секретный-ключ-сюда'
DATABASE = 'lead_ecosystem.db'
ADMIN_ID = 5062414502
SUPPORT_USERNAME = '@TGLeadSupportBot'
USDT_WALLET = "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
ADMIN_SECRET_KEY = "мой_секретный_ключ_2026"

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
login_manager = LoginManager(app)
login_manager.login_view = 'login'

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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    db.commit()

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
    return render_template('cases.html')

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
        name = request.form.get('name', '')
        email = request.form.get('email', '')
        message = request.form.get('message', '')
        flash('Сообщение отправлено! Мы ответим в ближайшее время.', 'success')
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
    email = request.form.get('email', '')
    return send_file('static/checklist.pdf', as_attachment=True)

@app.route('/generator')
def generator():
    return render_template('generator.html')

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
# Выдаём пробную лицензию Miner на 3 дня
license_key = generate_license_key()
expires_at = datetime.now() + timedelta(days=3)
db.execute("INSERT INTO licenses (user_id, license_key, product, price, expires_at, is_active) VALUES (?, ?, ?, ?, ?, 1)",
           (user_id, license_key, 'Miner', 0, expires_at))
db.commit()


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

@app.route('/admin/activate_user/<int:user_id>')
def admin_activate_user(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    db = get_db()
    db.execute("UPDATE sender_accounts SET is_active = 1 WHERE user_id = ?", (user_id,))
    db.commit()
    flash('Все аккаунты пользователя активированы.', 'success')
    return redirect(url_for('admin_dashboard'))

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
    return render_template('dashboard.html',
                           active_licenses_count=active_licenses_count,
                           total_leads_collected=total_leads_collected,
                           total_messages_sent=total_messages_sent,
                           miner_license=miner_license,
                           sender_license=sender_license,
                           sender_accounts=sender_accounts,
                           proxies=proxies)
    licenses = db.execute("SELECT * FROM licenses WHERE user_id = ? AND is_active = 1", (current_user.id,)).fetchall()
user_licenses = []
for lic in licenses:
    days_left = (datetime.strptime(lic['expires_at'], '%Y-%m-%d %H:%M:%S.%f') - datetime.now()).days
    user_licenses.append({
        'product': lic['product'],
        'created_at': lic['created_at'],
        'expires_at': lic['expires_at'],
        'days_left': max(0, days_left),
        'is_expired': days_left <= 0
    })

# ---------- ДОБАВЛЕНИЕ АККАУНТА ----------
@app.route('/sender_add_account', methods=['POST'])
@login_required
def sender_add_account():
    phone = request.form['phone'].strip()
    api_id = request.form['api_id'].strip()
    api_hash = request.form['api_hash'].strip()
    
    db = get_db()
    db.execute("INSERT INTO sender_accounts (user_id, phone, api_id, api_hash, is_active) VALUES (?, ?, ?, ?, 0)",
               (current_user.id, phone, api_id, api_hash))
    db.commit()
    
    flash(f'Аккаунт {phone} добавлен. Для активации откройте бота @TGLeadWareonVerifBot и отправьте /verify', 'info')
    return redirect(url_for('dashboard'))

@app.route('/sender_add_proxy', methods=['POST'])
@login_required
def sender_add_proxy():
    db = get_db()
    db.execute("INSERT INTO proxies (user_id, type, host, port, username, password) VALUES (?, ?, ?, ?, ?, ?)",
               (current_user.id, request.form['type'], request.form['host'],
                int(request.form['port']), request.form.get('username', ''), request.form.get('password', '')))
    db.commit()
    flash('Прокси добавлен.', 'success')
    return redirect(url_for('dashboard'))

# ---------- ПОКУПКА ----------
@app.route('/buy/<product>', methods=['GET', 'POST'])
@app.route('/buy', methods=['GET', 'POST'])
@login_required
def buy(product='miner'):
    if product not in ['miner', 'sender']:
        product = 'miner'
    if request.method == 'POST':
        method = request.form.get('method', 'card')
        db = get_db()
        amount_rub = 990 if product == 'sender' else 490
        db.execute("INSERT INTO payments (user_id, product, amount) VALUES (?, ?, ?)",
                   (current_user.id, product, amount_rub))
        db.commit()
        flash('Платёж зафиксирован.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('buy.html', product=product, selected_plan='Pro', billing_period='1 месяц',
                           amount_rub=990 if product == 'sender' else 490,
                           amount_usdt=15 if product == 'sender' else 8,
                           usdt_wallet=USDT_WALLET)

# ---------- MINER ----------
@app.route('/miner')
@login_required
def miner():
    db = get_db()
    miner_jobs = db.execute("SELECT * FROM miner_jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (current_user.id,)).fetchall()
    return render_template('miner.html', miner_jobs=miner_jobs)

@app.route('/miner/collect', methods=['POST'])
@login_required
def miner_collect():
    link = request.form['link'].strip()
    db = get_db()
    account = db.execute("SELECT * FROM sender_accounts WHERE user_id = ? AND is_active = 1 LIMIT 1", (current_user.id,)).fetchone()
    if not account:
        flash('Сначала добавьте аккаунт.', 'error')
        return redirect(url_for('miner'))
    flash(f'Сбор из {link} запущен. Результат появится в списке задач.', 'success')
    return redirect(url_for('miner'))

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
        db.execute("UPDATE proxy_pool SET is_sold = 1, sold_to = ?, sold_at = ? WHERE id = ?",
                   (current_user.id, datetime.now(), proxy['id']))
        db.execute("INSERT INTO proxies (user_id, type, host, port, username, password, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)",
                   (current_user.id, proxy['type'], proxy['host'], proxy['port'], proxy['username'], proxy['password']))
    db.commit()
    flash(f'Куплено {len(proxies)} прокси!', 'success')
    return redirect(url_for('dashboard'))

# ---------- АДМИН-ПАНЕЛЬ ----------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        key = request.form.get('key', '')
        if key == ADMIN_SECRET_KEY:
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        flash('Неверный ключ доступа', 'error')
    return render_template('admin_login.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    licenses = db.execute("SELECT * FROM licenses ORDER BY created_at DESC").fetchall()
    return render_template('admin.html', users=users, licenses=licenses)

@app.route('/admin/give_license', methods=['POST'])
def admin_give_license():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    user_email = request.form.get('email', '').strip()
    product = request.form.get('product', 'miner')
    days = int(request.form.get('days', 30))
    db = get_db()
    user_row = db.execute("SELECT id FROM users WHERE email = ?", (user_email,)).fetchone()
    if not user_row:
        flash('Пользователь не найден.', 'error')
        return redirect(url_for('admin_dashboard'))
    user_id = user_row['id']
    license_key = generate_license_key()
    expires_at = datetime.now() + timedelta(days=days)
    db.execute("INSERT INTO licenses (user_id, license_key, product, price, expires_at, is_active) VALUES (?, ?, ?, ?, ?, 1)",
               (user_id, license_key, product, 0, expires_at))
    db.commit()
    flash(f'Лицензия {product} выдана пользователю {user_email} на {days} дней.', 'success')
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

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))

# ---------- API ДЛЯ БОТА ВЕРИФИКАЦИИ ----------
@app.route('/api/check_account')
def api_check_account():
    phone = request.args.get('phone', '').strip().replace(' ', '').replace('+', '')
    telegram_id = request.args.get('telegram_id', '').strip()
    
    db = get_db()
    # Ищем по номеру без пробелов
    all_accounts = db.execute("SELECT * FROM sender_accounts WHERE is_active = 0").fetchall()
    account = None
    for acc in all_accounts:
        db_phone = acc['phone'].replace(' ', '').replace('+', '')
        if db_phone == phone:
            account = acc
            break
    
    if not account:
        return jsonify({'error': 'Аккаунт не найден'}), 404
    
    return jsonify({
        'api_id': account['api_id'],
        'api_hash': account['api_hash']
    })
    
@app.route('/api/activate_account', methods=['POST'])
def api_activate_account():
    data = request.get_json()
    phone = data.get('phone', '').strip().replace(' ', '').replace('+', '')
    code = data.get('code', '').strip()
    
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
    
    return jsonify({'success': True, 'message': f'Аккаунт {acc["phone"]} активирован!'})

# ---------- API ДЛЯ ЗАГРУЗКИ СЕССИЙ ----------
@app.route('/api/download_db')
def api_download_db():
    if not session.get('is_admin'):
        return jsonify({'error': 'Доступ запрещён'}), 403
    return send_file('lead_ecosystem.db', as_attachment=True)

@app.route('/api/upload_session', methods=['POST'])
def api_upload_session():
    phone = request.form.get('phone', '').strip()
    account_id = request.form.get('account_id', '').strip()
    
    if 'session_file' not in request.files:
        return jsonify({'error': 'Файл сессии не найден'}), 400
    
    file = request.files['session_file']
    os.makedirs('sessions', exist_ok=True)
    file.save(f'sessions/{phone}.session')
    
    db = get_db()
    db.execute("UPDATE sender_accounts SET is_active = 1, session_file = ? WHERE id = ?",
               (f'sessions/{phone}.session', account_id))
    db.commit()
    
    return jsonify({'success': True, 'message': f'Сессия {phone} загружена и аккаунт активирован!'})
    
# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
