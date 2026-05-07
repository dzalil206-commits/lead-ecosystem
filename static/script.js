document.addEventListener('DOMContentLoaded', () => {
  // Mobile nav toggle
  const navToggle = document.querySelector('.nav-toggle');
  const header = document.querySelector('.site-header');
  navToggle?.addEventListener('click', () => {
    document.body.classList.toggle('nav-open');
  });

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
