// Навигация (скролл к секции)
document.querySelectorAll('.scroll-to').forEach(btn => {
    btn.addEventListener('click', () => {
        const targetId = btn.dataset.target;
        const el = document.querySelector(targetId);
        if (el) {
            window.scrollTo({
                top: el.offsetTop - 72,
                behavior: 'smooth'
            });
        }
    });
});

// Billing toggle (monthly / yearly)
const billingToggle = document.querySelector('.billing-toggle');
if (billingToggle) {
    const labels = billingToggle.querySelectorAll('.billing-label');
    const thumb = billingToggle.querySelector('.toggle-thumb');
    const pricing = document.querySelector('.pricing-grid');
    const prices = pricing ? pricing.querySelectorAll('.price-main') : [];

    function setBilling(mode) {
        billingToggle.dataset.mode = mode;
        labels.forEach(label => {
            label.classList.toggle('active', label.dataset.billing === mode);
        });
        if (mode === 'yearly') {
            thumb.style.transform = 'translateX(18px)';
        } else {
            thumb.style.transform = 'translateX(0)';
        }
        prices.forEach(el => {
            const monthly = el.getAttribute('data-price-monthly');
            const yearly = el.getAttribute('data-price-yearly');
            el.textContent = (mode === 'yearly' ? yearly : monthly) + '₽';
        });
        if (pricing) {
            pricing.setAttribute('data-billing-state', mode);
        }
    }

    labels.forEach(label => {
        label.addEventListener('click', () => {
            const mode = label.dataset.billing;
            setBilling(mode);
        });
    });

    setBilling('monthly');
}

// FAQ accordion
document.querySelectorAll('.faq-item').forEach(item => {
    const q = item.querySelector('.faq-question');
    q.addEventListener('click', () => {
        const open = item.classList.contains('open');
        document.querySelectorAll('.faq-item').forEach(i => i.classList.remove('open'));
        if (!open) item.classList.add('open');
    });
});

// CountUp counters
const counterEls = document.querySelectorAll('[data-countup]');
if (counterEls.length) {
    const observer = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const el = entry.target;
                const target = parseInt(el.getAttribute('data-countup'), 10);
                const duration = 1600;
                const start = performance.now();

                function tick(now) {
                    const progress = Math.min((now - start) / duration, 1);
                    const value = Math.floor(target * progress);
                    el.textContent = value.toLocaleString('ru-RU');
                    if (progress < 1) requestAnimationFrame(tick);
                }
                requestAnimationFrame(tick);
                observer.unobserve(el);
            }
        });
    }, { threshold: 0.4 });

    counterEls.forEach(el => observer.observe(el));
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
        track.style.transform = `translateX(-${index * 100}%)`;
    }

    function next() {
        show(index + 1);
    }

    function startAuto() {
        timer = setInterval(next, 5000);
    }

    function stopAuto() {
        clearInterval(timer);
    }

    prevBtn.addEventListener('click', () => { stopAuto(); show(index - 1); startAuto(); });
    nextBtn.addEventListener('click', () => { stopAuto(); show(index + 1); startAuto(); });

    carousel.addEventListener('mouseenter', stopAuto);
    carousel.addEventListener('mouseleave', startAuto);

    show(0);
    startAuto();
}

// Intersection Observer для reveal-эффектов
const revealEls = document.querySelectorAll('.section, .step-card, .card-hover, .counter-card, .case-card');
if (revealEls.length) {
    revealEls.forEach(el => el.setAttribute('data-reveal', ''));
    const revealObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                revealObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.15 });

    revealEls.forEach(el => revealObserver.observe(el));
}

// Параллакс карточек по движению мыши
document.querySelectorAll('[data-parallax]').forEach(el => {
    const strength = 16;
    let rect = el.getBoundingClientRect();

    function handleMove(e) {
        const x = e.clientX - rect.left - rect.width / 2;
        const y = e.clientY - rect.top - rect.height / 2;
        const dx = (x / rect.width) * strength;
        const dy = (y / rect.height) * strength;
        el.style.transform = `translate3d(${-dx}px, ${-dy}px, 0)`;
    }

    function reset() {
        el.style.transform = 'translate3d(0,0,0)';
    }

    window.addEventListener('resize', () => { rect = el.getBoundingClientRect(); });
    el.addEventListener('mousemove', handleMove);
    el.addEventListener('mouseleave', reset);
});

// Псевдо-печатающийся текст в мини‑чате
const typingEl = document.querySelector('[data-typing]');
if (typingEl) {
    const fullText = typingEl.textContent.trim();
    let i = 0;
    typingEl.textContent = '';

    function type() {
        if (i <= fullText.length) {
            typingEl.textContent = fullText.slice(0, i);
            i++;
            setTimeout(type, 40);
        } else {
            setTimeout(() => {
                i = 0;
                typingEl.textContent = '';
                setTimeout(type, 400);
            }, 1800);
        }
    }
    type();
}

// Modal open / close
document.querySelectorAll('[data-modal-open]').forEach(btn => {
    btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-modal-open');
        const modal = document.getElementById(id);
        if (modal) modal.classList.add('open');
    });
});

document.querySelectorAll('[data-modal-close]').forEach(btn => {
    btn.addEventListener('click', () => {
        const modal = btn.closest('.modal');
        if (modal) modal.classList.remove('open');
    });
});

// Закрытие модалки по ESC
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'));
    }
});

// Payment method switch
document.querySelectorAll('.payment-methods input[name="method"]').forEach(input => {
    input.addEventListener('change', () => {
        const method = input.value;
        document.querySelectorAll('.payment-details').forEach(block => {
            const m = block.getAttribute('data-method');
            block.classList.toggle('hidden', m !== method);
        });
    });
});
