document.addEventListener('DOMContentLoaded', () => {

  // Intersection Observer reveal
  const revealElems = document.querySelectorAll('[data-reveal]');
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('revealed');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15 });
    revealElems.forEach(el => io.observe(el));
  } else {
    revealElems.forEach(el => el.classList.add('revealed'));
  }

  // Parallax hero
  const hero = document.querySelector('.hero');
  const heroCard = document.querySelector('.hero-card');
  if (hero && heroCard) {
    hero.addEventListener('mousemove', (e) => {
      const rect = hero.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width - 0.5;
      const y = (e.clientY - rect.top) / rect.height - 0.5;
      heroCard.style.transform = `rotateX(${y * -6}deg) rotateY(${x * 8}deg)`;
    });
    hero.addEventListener('mouseleave', () => {
      heroCard.style.transform = 'rotateX(0) rotateY(0)';
    });
  }

  // Pricing toggle
  const billingToggle = document.querySelector('.billing-toggle');
  const toggleThumb = document.querySelector('.toggle-thumb');
  const priceElems = document.querySelectorAll('[data-price-month]');
  if (billingToggle && toggleThumb && priceElems.length) {
    let yearly = false;
    const updatePrices = () => {
      priceElems.forEach(el => {
        const month = el.getAttribute('data-price-month');
        const year = el.getAttribute('data-price-year');
        el.textContent = yearly && year ? year : month;
      });
      toggleThumb.style.transform = yearly ? 'translateX(18px)' : 'translateX(0)';
      billingToggle.querySelectorAll('.billing-label').forEach(label => {
        label.classList.toggle('active', label.dataset.billing === (yearly ? 'year' : 'month'));
      });
    };
    billingToggle.addEventListener('click', () => {
      yearly = !yearly;
      updatePrices();
    });
    updatePrices();
  }

  // FAQ accordion
  document.querySelectorAll('.faq-item').forEach(item => {
    const btn = item.querySelector('.faq-question');
    btn?.addEventListener('click', () => {
      item.classList.toggle('open');
    });
  });

  // Reviews carousel
  document.querySelectorAll('.reviews-carousel').forEach(carousel => {
    const cards = carousel.querySelectorAll('.review-card');
    if (!cards.length) return;
    let index = 0;
    const show = (i) => {
      cards.forEach((c, idx) => c.classList.toggle('active', idx === i));
    };
    show(0);
    const next = () => {
      index = (index + 1) % cards.length;
      show(index);
    };
    let timer = setInterval(next, 6000);
    carousel.addEventListener('mouseenter', () => clearInterval(timer));
    carousel.addEventListener('mouseleave', () => { timer = setInterval(next, 6000); });
    carousel.querySelector('.carousel-arrow.prev')?.addEventListener('click', () => {
      index = (index - 1 + cards.length) % cards.length;
      show(index);
    });
    carousel.querySelector('.carousel-arrow.next')?.addEventListener('click', () => {
      next();
    });
  });

  // Add-ons calculator
  const addons = document.querySelectorAll('[data-addon-price]');
  const basePriceEl = document.querySelector('[data-base-price]');
  const totalEl = document.querySelector('[data-total-price]');
  if (addons.length && basePriceEl && totalEl) {
    const calc = () => {
      let base = parseInt(basePriceEl.dataset.basePrice || '0', 10) || 0;
      let addonsSum = 0;
      addons.forEach(input => {
        if (input.checked) addonsSum += parseInt(input.dataset.addonPrice || '0', 10) || 0;
      });
      const total = base + addonsSum;
      totalEl.textContent = total.toLocaleString('ru-RU');
    };
    addons.forEach(input => input.addEventListener('change', calc));
    calc();
  }

  // Counters
  const counterElems = document.querySelectorAll('[data-counter]');
  if (counterElems.length) {
    const format = (value) => value.toLocaleString('ru-RU');
    const runCounter = (el) => {
      const target = parseInt(el.dataset.counter || '0', 10) || 0;
      let current = 0;
      const duration = 1200;
      const start = performance.now();
      const step = (now) => {
        const progress = Math.min((now - start) / duration, 1);
        current = Math.floor(target * progress);
        el.textContent = format(current);
        if (progress < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    };
    const co = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          runCounter(entry.target);
          co.unobserve(entry.target);
        }
      });
    }, { threshold: 0.3 });
    counterElems.forEach(el => co.observe(el));
  }

  // Typing text
  document.querySelectorAll('[data-typing]').forEach(el => {
    const phrases = (el.dataset.typing || '').split('|').map(s => s.trim()).filter(Boolean);
    if (!phrases.length) return;
    let phraseIndex = 0;
    let charIndex = 0;
    let deleting = false;
    const speed = 90;
    const pause = 1400;
    const tick = () => {
      const current = phrases[phraseIndex];
      if (!deleting) {
        charIndex++;
        el.textContent = current.slice(0, charIndex);
        if (charIndex === current.length) {
          deleting = true;
          setTimeout(tick, pause);
          return;
        }
      } else {
        charIndex--;
        el.textContent = current.slice(0, charIndex);
        if (charIndex === 0) {
          deleting = false;
          phraseIndex = (phraseIndex + 1) % phrases.length;
        }
      }
      setTimeout(tick, deleting ? speed * 0.6 : speed);
    };
    tick();
  });
});

// ═══════════════════════════════════════════════════════════
// TG ACCOUNT ACTIVATION — многошаговый диалог
// ═══════════════════════════════════════════════════════════

function tgShowStatus(msg, type) {
    // type: 'error' | 'info' | 'success'
    const el = document.getElementById('accountStatusMsg');
    if (!el) return;
    const colors = {
        error:   { bg: 'rgba(224,85,85,0.12)', border: 'rgba(224,85,85,0.3)', color: '#e05555' },
        info:    { bg: 'rgba(212,165,116,0.1)', border: 'rgba(212,165,116,0.3)', color: 'var(--accent-light)' },
        success: { bg: 'rgba(59,232,140,0.08)', border: 'rgba(59,232,140,0.2)', color: '#3be88c' },
    };
    const c = colors[type] || colors.info;
    el.style.background = c.bg;
    el.style.border = `1px solid ${c.border}`;
    el.style.color = c.color;
    el.textContent = msg;
    el.style.display = 'block';
}

function tgHideStatus() {
    const el = document.getElementById('accountStatusMsg');
    if (el) el.style.display = 'none';
}

function tgSetStep(n) {
    document.getElementById('accountStep1').style.display = n === 1 ? '' : 'none';
    document.getElementById('accountStep2').style.display = n === 2 ? '' : 'none';
    document.getElementById('accountStep3').style.display = n === 3 ? '' : 'none';
    // Индикатор
    [1, 2, 3].forEach(i => {
        const dot = document.getElementById('stepDot' + i);
        if (dot) dot.style.background = i <= n ? 'var(--accent)' : 'rgba(255,255,255,0.1)';
    });
    tgHideStatus();
}

function tgSetLoading(btnId, loading) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    if (loading) {
        btn.dataset.origText = btn.textContent;
        btn.textContent = '⏳ Загрузка...';
        btn.disabled = true;
    } else {
        btn.textContent = btn.dataset.origText || btn.textContent;
        btn.disabled = false;
    }
}

function tgRestart() {
    tgSetStep(1);
    document.getElementById('tgCode').value = '';
    document.getElementById('tgPassword').value = '';
    document.getElementById('passwordFieldWrap').style.display = 'none';
    document.getElementById('codeFieldWrap').style.display = '';
}

async function tgSendCode() {
    const phone   = (document.getElementById('tgPhone')?.value || '').trim();
    const api_id  = (document.getElementById('tgApiId')?.value || '').trim();
    const api_hash = (document.getElementById('tgApiHash')?.value || '').trim();

    if (!phone || !api_id || !api_hash) {
        tgShowStatus('Заполните все три поля', 'error');
        return;
    }

    tgSetLoading('btnSendCode', true);
    tgHideStatus();

    try {
        const res  = await fetch('/sender/send_code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone, api_id, api_hash }),
        });
        const data = await res.json();

        if (data.error) {
            tgShowStatus(data.error, 'error');
            return;
        }
        if (data.already_authed) {
            tgShowStatus(`✅ Аккаунт ${data.phone} уже авторизован!`, 'success');
            tgAddAccountRow(data.phone);
            setTimeout(() => {
                document.getElementById('modalAddAccount').classList.remove('open');
                tgSetStep(1);
            }, 2000);
            return;
        }
        // Успешно отправили код → шаг 2
        const subtitle = document.getElementById('accountStep2Subtitle');
        if (subtitle) subtitle.textContent = `Код отправлен на ${phone}. Введите его ниже.`;
        tgSetStep(2);
    } catch (e) {
        tgShowStatus('Ошибка сети. Попробуйте ещё раз.', 'error');
    } finally {
        tgSetLoading('btnSendCode', false);
    }
}

async function tgVerifyCode() {
    const code     = (document.getElementById('tgCode')?.value || '').trim();
    const password = (document.getElementById('tgPassword')?.value || '').trim();
    const show2fa  = document.getElementById('passwordFieldWrap')?.style.display !== 'none';

    if (!show2fa && !code) {
        tgShowStatus('Введите код из Telegram', 'error');
        return;
    }
    if (show2fa && !password) {
        tgShowStatus('Введите пароль 2FA', 'error');
        return;
    }

    tgSetLoading('btnVerifyCode', true);
    tgHideStatus();

    try {
        const body = show2fa ? { password } : { code };
        const res  = await fetch('/sender/verify_code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();

        if (data.need_2fa) {
            // Показать поле 2FA вместо поля кода
            document.getElementById('codeFieldWrap').style.display = 'none';
            document.getElementById('passwordFieldWrap').style.display = '';
            const sub = document.getElementById('accountStep2Subtitle');
            if (sub) sub.textContent = 'Требуется двухфакторная аутентификация.';
            tgShowStatus('🔐 Введите пароль облачного хранилища Telegram', 'info');
            return;
        }
        if (data.error) {
            tgShowStatus(data.error, 'error');
            return;
        }
        if (data.success) {
            // Успех → шаг 3
            const phone = data.phone || '';
            const txt = document.getElementById('accountSuccessText');
            if (txt) txt.textContent = `Аккаунт ${phone} успешно активирован!`;
            tgSetStep(3);
            tgAddAccountRow(phone);
        }
    } catch (e) {
        tgShowStatus('Ошибка сети. Попробуйте ещё раз.', 'error');
    } finally {
        tgSetLoading('btnVerifyCode', false);
    }
}

function tgAddAccountRow(phone) {
    // Добавить строку в таблицу аккаунтов без перезагрузки страницы
    const body = document.getElementById('accountsTableBody');
    if (!body) return;
    // Убрать строку "пусто"
    const emptyRow = document.getElementById('accountsEmptyRow');
    if (emptyRow) emptyRow.remove();

    // Проверить, нет ли уже строки с таким номером
    const existing = [...body.querySelectorAll('td')].find(td => td.textContent.trim() === phone);
    if (existing) {
        // Обновить статус
        const row = existing.closest('tr');
        const statusTd = row?.querySelectorAll('td')[1];
        if (statusTd) statusTd.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#3be88c;margin-right:6px;"></span>Активен';
        return;
    }

    const today = new Date().toISOString().slice(0, 10);
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td style="font-weight:500;">${phone}</td>
        <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#3be88c;margin-right:6px;"></span>Активен</td>
        <td>—</td>
        <td style="color:var(--text-muted);font-size:13px;">${today}</td>
        <td style="text-align:right;">
            <button class="btn btn-ghost btn-sm delete-account-btn"
                    data-phone="${phone}"
                    title="Удалить аккаунт"
                    style="color:#e05555;opacity:0.7;">✕</button>
        </td>
    `;
    body.appendChild(tr);

    // Сбросить модал после закрытия
    document.getElementById('modalAddAccount').addEventListener('click', function onClose(e) {
        if (e.target.dataset.modalClose !== undefined || e.target.closest('[data-modal-close]')) {
            tgSetStep(1);
            document.getElementById('tgPhone').value = '';
            document.getElementById('tgApiId').value = '';
            document.getElementById('tgApiHash').value = '';
            document.getElementById('tgCode').value = '';
            document.getElementById('tgPassword').value = '';
            this.removeEventListener('click', onClose);
        }
    });
}

// Сброс модала при закрытии
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('modalAddAccount');
    if (!modal) return;
    modal.querySelectorAll('[data-modal-close]').forEach(el => {
        el.addEventListener('click', () => {
            setTimeout(() => tgSetStep(1), 300);
        });
    });
});

// ─── Удаление аккаунта ───────────────────────────────────────────

document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.delete-account-btn');
    if (!btn) return;

    const phone     = btn.dataset.phone || '';
    const accountId = btn.dataset.accountId;

    if (!confirm(`Удалить аккаунт ${phone}? Сессионный файл будет удалён.`)) return;

    btn.disabled = true;
    btn.textContent = '...';

    try {
        const res  = await fetch('/sender/delete_account', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: accountId }),
        });
        const data = await res.json();

        if (data.success) {
            const row = btn.closest('tr');
            row?.remove();
            // Если строк не осталось — показать заглушку
            const body = document.getElementById('accountsTableBody');
            if (body && !body.querySelector('tr')) {
                body.innerHTML = `<tr id="accountsEmptyRow"><td colspan="5" style="text-align:center;color:var(--text-muted);padding:20px;">Аккаунты ещё не добавлены</td></tr>`;
            }
        } else {
            alert(data.error || 'Ошибка удаления');
            btn.disabled = false;
            btn.textContent = '✕';
        }
    } catch {
        alert('Ошибка сети');
        btn.disabled = false;
        btn.textContent = '✕';
    }
});

// Мобильное меню
const navToggle = document.querySelector('.nav-toggle');
const navMain = document.querySelector('.nav-main');
const headerActions = document.querySelector('.header-actions');

if (navToggle) {
    navToggle.addEventListener('click', () => {
        navMain.classList.toggle('nav-open');
        headerActions.classList.toggle('nav-open');
    });
}

// Reviews carousel
const carousel = document.querySelector('[data-carousel]');
if (carousel) {
    const track = carousel.querySelector('.carousel-track');
    const cards = Array.from(track.querySelectorAll('.review-card'));
    const prevBtn = carousel.querySelector('[data-carousel-prev]');
    const nextBtn = carousel.querySelector('[data-carousel-next]');
    let index = 0;
    let timer;

    function show(i) {
        index = (i + cards.length) % cards.length;
        cards.forEach((card, idx) => {
            card.classList.toggle('active', idx === index);
        });
    }

    function next() { show(index + 1); }
    function startAuto() { timer = setInterval(next, 5000); }
    function stopAuto() { clearInterval(timer); }

    if (prevBtn) prevBtn.addEventListener('click', () => { stopAuto(); show(index - 1); startAuto(); });
    if (nextBtn) nextBtn.addEventListener('click', () => { stopAuto(); show(index + 1); startAuto(); });

    carousel.addEventListener('mouseenter', stopAuto);
    carousel.addEventListener('mouseleave', startAuto);

    show(0);
    startAuto();
}
