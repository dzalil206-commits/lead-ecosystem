# bot_main.py — Главный бот TG Lead Wareon (@TGLeadWareonBot)
# aiogram 3.x  |  python-dotenv  |  aiohttp
#
# Запуск:  python3 bot_main.py
# На Bothost: указать файл bot_main.py, установить зависимости из requirements_bot.txt

import asyncio
import logging
import os

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# ─────────────────────────── Настройки ───────────────────────────
BOT_TOKEN        = os.environ.get('BOT_MAIN_TOKEN', '')
API_BASE         = os.environ.get('SITE_API_BASE', 'https://tgleadwareon.ru')
BOT_SECRET       = os.environ.get('BOT_MAIN_SECRET', '')
REVIEW_BOT_TOKEN = os.environ.get('REVIEW_BOT_TOKEN', '')
SITE_URL         = 'https://tgleadwareon.ru'
SUPPORT_URL      = 'https://t.me/TGLeadSupportBot'

bot = Bot(token=BOT_TOKEN, parse_mode='HTML')
dp  = Dispatcher(storage=MemoryStorage())


# ─────────────────────────── FSM состояния ───────────────────────
class States(StatesGroup):
    waiting_email  = State()   # ждём email для привязки аккаунта
    waiting_review = State()   # ждём текст отзыва
    review_rating  = State()   # ждём звёздную оценку


# ─────────────────────────── API helper ──────────────────────────
async def api(method: str, path: str, **kwargs) -> dict:
    """Делает запрос к API сайта с Bot-Secret."""
    kwargs.setdefault('headers', {})['X-Bot-Secret'] = BOT_SECRET
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with getattr(s, method)(f'{API_BASE}{path}', **kwargs) as r:
                return await r.json()
    except Exception as e:
        logging.error(f'API {method.upper()} {path} — {e}')
        return {}


# ─────────────────────────── Клавиатуры ──────────────────────────
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='📜 Мои лицензии',    callback_data='licenses'),
            InlineKeyboardButton(text='💳 Купить/продлить', callback_data='buy'),
        ],
        [
            InlineKeyboardButton(text='🔑 Получить ключ',   callback_data='get_key'),
            InlineKeyboardButton(text='🎁 Рефералы',         callback_data='referral'),
        ],
        [
            InlineKeyboardButton(text='⭐ Оставить отзыв',  callback_data='review'),
            InlineKeyboardButton(text='💬 Поддержка',        url=SUPPORT_URL),
        ],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='← Главное меню', callback_data='menu')]
    ])


def kb_buy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='⛏️ Miner — 490₽/мес',  url=f'{SITE_URL}/buy/miner'),
            InlineKeyboardButton(text='📨 Sender — 990₽/мес', url=f'{SITE_URL}/buy/sender'),
        ],
        [
            InlineKeyboardButton(text='🚀 Start — 990₽/мес',  url=f'{SITE_URL}/buy/start'),
            InlineKeyboardButton(text='⚡ Pro — 2 490₽/мес',  url=f'{SITE_URL}/buy/pro'),
        ],
        [
            InlineKeyboardButton(text='🏆 Scale — 6 990₽/мес', url=f'{SITE_URL}/buy/scale'),
        ],
        [InlineKeyboardButton(text='← Назад', callback_data='menu')],
    ])


def kb_stars() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='⭐ 1', callback_data='star_1'),
            InlineKeyboardButton(text='⭐⭐ 2', callback_data='star_2'),
            InlineKeyboardButton(text='⭐⭐⭐ 3', callback_data='star_3'),
        ],
        [
            InlineKeyboardButton(text='⭐⭐⭐⭐ 4', callback_data='star_4'),
            InlineKeyboardButton(text='⭐⭐⭐⭐⭐ 5', callback_data='star_5'),
        ],
        [InlineKeyboardButton(text='← Отмена', callback_data='menu')],
    ])


# ─────────────────────────── /start ──────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    tg_id = str(message.from_user.id)

    # Deep link для QR-привязки: /start link_XXXXXX
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith('link_'):
        await _handle_link_code(message, state, args[1][5:])
        return

    data = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if data.get('found'):
        name     = data['user']['name']
        licenses = data.get('licenses', [])
        active   = [l for l in licenses if not l['is_expired']]

        status = f'✅ Активных лицензий: <b>{len(active)}</b>' if active else '❌ Нет активных лицензий'

        await message.answer(
            f'👋 Привет, <b>{name}</b>!\n\n'
            f'{status}\n\n'
            f'Выбери раздел:',
            reply_markup=kb_main()
        )
    else:
        await message.answer(
            '👋 Привет! Я главный бот <b>TG Lead Wareon</b>.\n\n'
            'Управляйте лицензиями, получайте ключи и следите за подпиской '
            'прямо здесь — без захода на сайт.\n\n'
            '📧 Введите <b>email</b>, с которым вы зарегистрированы на сайте:\n\n'
            '<i>Ещё нет аккаунта? Зарегистрируйтесь: tgleadwareon.ru/register</i>'
        )
        await state.set_state(States.waiting_email)


async def _handle_link_code(message: Message, state: FSMContext, code: str):
    """Привязка через QR-код (deep link)."""
    tg_id = str(message.from_user.id)
    result = await api('post', '/api/bot/link_code', json={'code': code, 'tg_id': tg_id})

    if result.get('ok'):
        data = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
        name = data.get('user', {}).get('name', 'пользователь')
        await message.answer(
            f'✅ Аккаунт успешно привязан!\n\nПривет, <b>{name}</b>! Добро пожаловать.',
            reply_markup=kb_main()
        )
    else:
        err = result.get('error', 'Неизвестная ошибка')
        await message.answer(
            f'❌ Не удалось привязать по QR-коду: {err}\n\n'
            f'Введите email вручную:'
        )
        await state.set_state(States.waiting_email)


# ─────────────────────────── Привязка по email ───────────────────
@dp.message(States.waiting_email)
async def process_email(message: Message, state: FSMContext):
    email = message.text.strip().lower()

    if '@' not in email or '.' not in email:
        await message.answer('❌ Введите корректный email-адрес:')
        return

    tg_id       = str(message.from_user.id)
    tg_username = message.from_user.username or ''

    result = await api('post', '/api/bot/link', json={
        'email':       email,
        'tg_id':       tg_id,
        'tg_username': tg_username,
    })

    if result.get('ok'):
        data = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
        name = data.get('user', {}).get('name', email.split('@')[0])
        await state.clear()
        await message.answer(
            f'✅ <b>Аккаунт привязан!</b>\n\n'
            f'Привет, <b>{name}</b>! Теперь управляйте подпиской прямо здесь.',
            reply_markup=kb_main()
        )
    else:
        err = result.get('error', '')
        if 'не найден' in err:
            await message.answer(
                f'❌ Аккаунт <code>{email}</code> не найден.\n\n'
                f'Зарегистрируйтесь на сайте:\n'
                f'<a href="{SITE_URL}/register">tgleadwareon.ru/register</a>\n\n'
                f'Затем введите email снова:'
            )
        else:
            await message.answer(f'⚠️ Ошибка: {err}\n\nПопробуйте ещё раз:')


# ─────────────────────────── Главное меню ────────────────────────
@dp.callback_query(F.data == 'menu')
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if data.get('found'):
        licenses = data.get('licenses', [])
        active   = [l for l in licenses if not l['is_expired']]
        status   = f'✅ Активных: <b>{len(active)}</b>' if active else '❌ Нет активных лицензий'
        text     = f'📋 <b>Главное меню</b>\n\n{status}\n\nВыбери раздел:'
    else:
        text = '📋 <b>Главное меню</b>\n\nВыбери раздел:'

    await call.message.edit_text(text, reply_markup=kb_main())


# ─────────────────────────── Мои лицензии ────────────────────────
@dp.callback_query(F.data == 'licenses')
async def cb_licenses(call: CallbackQuery):
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await call.answer('Сначала привяжите аккаунт через /start', show_alert=True)
        return

    licenses = data.get('licenses', [])

    if not licenses:
        text = (
            '📜 <b>Мои лицензии</b>\n\n'
            '❌ Активных лицензий нет.\n\n'
            'Приобретите подписку в разделе «Купить/продлить».'
        )
    else:
        ICONS = {'Miner': '⛏️', 'Sender': '📨', 'Start': '🚀', 'Pro': '⚡', 'Scale': '🏆'}
        lines = ['📜 <b>Мои лицензии</b>\n']
        for lic in licenses:
            icon     = ICONS.get(lic['product'], '📦')
            days     = lic['days_left']
            warn     = ' ⚠️' if 0 < days <= 3 else (' ❌' if days == 0 else '')
            expires  = lic['expires_at'][:10]
            lines.append(
                f'{icon} <b>{lic["product"]}</b>{warn}\n'
                f'   ⏳ Осталось: <b>{days} дн.</b> · до {expires}'
            )
        text = '\n\n'.join(lines)

    buttons = [
        [InlineKeyboardButton(text='💳 Продлить', callback_data='buy')],
        [InlineKeyboardButton(text='← Главное меню', callback_data='menu')],
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ─────────────────────────── Получить ключ ───────────────────────
@dp.callback_query(F.data == 'get_key')
async def cb_get_key(call: CallbackQuery):
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await call.answer('Сначала привяжите аккаунт через /start', show_alert=True)
        return

    active = [l for l in data.get('licenses', []) if not l['is_expired']]

    if not active:
        await call.message.edit_text(
            '🔑 <b>Лицензионный ключ</b>\n\n'
            '❌ Активных лицензий нет.\n\n'
            'Приобретите подписку, чтобы получить ключ.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='💳 Купить', callback_data='buy')],
                [InlineKeyboardButton(text='← Назад',  callback_data='menu')],
            ])
        )
        return

    lines = ['🔑 <b>Ваши лицензионные ключи</b>\n',
             '<i>Нажмите на ключ, чтобы скопировать</i>\n']
    for lic in active:
        ICONS = {'Miner': '⛏️', 'Sender': '📨', 'Start': '🚀', 'Pro': '⚡', 'Scale': '🏆'}
        icon = ICONS.get(lic['product'], '📦')
        lines.append(
            f'{icon} <b>{lic["product"]}</b> ({lic["days_left"]} дн.)\n'
            f'<code>{lic["license_key"]}</code>'
        )
    await call.message.edit_text('\n\n'.join(lines), reply_markup=kb_back())


# ─────────────────────────── Купить / продлить ───────────────────
@dp.callback_query(F.data == 'buy')
async def cb_buy(call: CallbackQuery):
    await call.message.edit_text(
        '💳 <b>Купить или продлить</b>\n\n'
        'Выберите тариф — откроется страница оплаты через Lava.top.\n'
        'После оплаты лицензия активируется автоматически.',
        reply_markup=kb_buy()
    )


# ─────────────────────────── Рефералы ────────────────────────────
@dp.callback_query(F.data == 'referral')
async def cb_referral(call: CallbackQuery):
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await call.answer('Сначала привяжите аккаунт через /start', show_alert=True)
        return

    ref_link = data.get('ref_link', '')
    count    = data.get('referral_count', 0)

    promo = (
        f'«Попробуй TG Lead Wareon — инструмент для работы с Telegram-аудиторией. '
        f'3 дня бесплатно: {ref_link}»'
    )

    await call.message.edit_text(
        f'🎁 <b>Реферальная программа</b>\n\n'
        f'👥 Приглашено: <b>{count} чел.</b>\n'
        f'🎁 Бонус: <b>+{count} дн.</b> к лицензии\n'
        f'📈 За каждого нового пользователя — <b>+1 день</b> автоматически\n\n'
        f'🔗 <b>Ваша ссылка:</b>\n<code>{ref_link}</code>\n\n'
        f'📢 <b>Готовый текст для шеринга:</b>\n<i>{promo}</i>',
        reply_markup=kb_back()
    )


# ─────────────────────────── Отзыв ───────────────────────────────
@dp.callback_query(F.data == 'review')
async def cb_review(call: CallbackQuery, state: FSMContext):
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
    if not data.get('found'):
        await call.answer('Сначала привяжите аккаунт через /start', show_alert=True)
        return

    await call.message.edit_text(
        '⭐ <b>Оставить отзыв</b>\n\n'
        'Напишите ваш отзыв о сервисе (несколько предложений).\n'
        'За отзыв вы получите <b>+2 дня</b> к активной лицензии! 🎁',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='← Отмена', callback_data='menu')]
        ])
    )
    await state.set_state(States.waiting_review)


@dp.message(States.waiting_review)
async def process_review_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if len(text) < 20:
        await message.answer('❌ Отзыв слишком короткий. Напишите хотя бы пару предложений:')
        return

    await state.update_data(review_text=text)
    await message.answer('Отлично! Теперь поставьте оценку:', reply_markup=kb_stars())
    await state.set_state(States.review_rating)


@dp.callback_query(States.review_rating, F.data.startswith('star_'))
async def process_review_rating(call: CallbackQuery, state: FSMContext):
    rating    = int(call.data.split('_')[1])
    fsm_data  = await state.get_data()
    text      = fsm_data.get('review_text', '')
    tg_id     = str(call.from_user.id)
    user_data = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
    email     = user_data.get('user', {}).get('email', '')

    result = await api('post', '/api/review_bonus', json={
        'token':       REVIEW_BOT_TOKEN,
        'telegram_id': tg_id,
        'username':    call.from_user.username or '',
        'rating':      rating,
        'text':        text,
        'user_email':  email,
    })

    await state.clear()
    stars = '⭐' * rating
    bonus = result.get('bonus_days', 2)

    await call.message.edit_text(
        f'✅ <b>Отзыв отправлен!</b>\n\n'
        f'{stars}\n'
        f'<i>«{text}»</i>\n\n'
        f'🎁 +{bonus} дня добавлено к вашей лицензии.\nСпасибо!',
        reply_markup=kb_back()
    )


# ─────────────────────────── /help, /key, /ref ───────────────────
@dp.message(Command('help'))
async def cmd_help(message: Message):
    await message.answer(
        '📖 <b>Команды бота</b>\n\n'
        '/start — Главное меню\n'
        '/key — Мои лицензионные ключи\n'
        '/ref — Реферальная ссылка\n'
        '/help — Эта справка\n\n'
        f'🌐 Сайт: {SITE_URL}\n'
        f'💬 Поддержка: {SUPPORT_URL}'
    )


@dp.message(Command('key'))
async def cmd_key(message: Message):
    tg_id = str(message.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await message.answer(
            '❌ Аккаунт не привязан. Введите /start и следуйте инструкции.'
        )
        return

    active = [l for l in data.get('licenses', []) if not l['is_expired']]
    if not active:
        await message.answer('❌ Активных лицензий нет.')
        return

    lines = ['🔑 <b>Ваши ключи:</b>\n']
    for lic in active:
        lines.append(f'<b>{lic["product"]}</b>: <code>{lic["license_key"]}</code>')
    await message.answer('\n'.join(lines))


@dp.message(Command('ref'))
async def cmd_ref(message: Message):
    tg_id = str(message.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await message.answer('❌ Аккаунт не привязан. Введите /start.')
        return

    ref_link = data.get('ref_link', '')
    count    = data.get('referral_count', 0)
    await message.answer(
        f'🎁 <b>Рефералы: {count}</b> · Бонус: +{count} дн.\n\n'
        f'Ваша ссылка:\n<code>{ref_link}</code>'
    )


# ─────────────────────── Scheduler: уведомления ──────────────────
async def _notify_expiring():
    """Каждые 12 часов проверяет истекающие лицензии и уведомляет пользователей."""
    await asyncio.sleep(60)  # небольшая задержка на старте
    while True:
        for days in [3, 1]:
            try:
                result = await api('get', '/api/bot/expiring_licenses', params={'days': days})
                for u in result.get('users', []):
                    tg_id = u.get('telegram_id')
                    if not tg_id:
                        continue
                    try:
                        if days == 3:
                            text = (
                                f'⚠️ <b>Лицензия истекает через 3 дня</b>\n\n'
                                f'Продукт: <b>{u["product"]}</b>\n'
                                f'Дата окончания: {u["expires_at"][:10]}\n\n'
                                f'Продлите сейчас, чтобы не прерывать работу 👇'
                            )
                        else:
                            text = (
                                f'🔴 <b>Лицензия истекает СЕГОДНЯ!</b>\n\n'
                                f'Продукт: <b>{u["product"]}</b>\n\n'
                                f'Продлите прямо сейчас 👇'
                            )
                        await bot.send_message(
                            tg_id,
                            text,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text='💳 Продлить сейчас', callback_data='buy')]
                            ])
                        )
                        logging.info(f'Уведомление отправлено: tg={tg_id} product={u["product"]} days={days}')
                    except Exception as e:
                        logging.warning(f'Не удалось отправить уведомление {tg_id}: {e}')
            except Exception as e:
                logging.error(f'Scheduler error (days={days}): {e}')

        await asyncio.sleep(12 * 3600)  # каждые 12 часов


# ─────────────────────────── Запуск ──────────────────────────────
async def main():
    if not BOT_TOKEN:
        raise ValueError('BOT_MAIN_TOKEN не задан в .env')

    logging.info('🤖 TG Lead Wareon Bot запускается...')
    asyncio.create_task(_notify_expiring())
    await dp.start_polling(bot, allowed_updates=['message', 'callback_query'])


if __name__ == '__main__':
    asyncio.run(main())
