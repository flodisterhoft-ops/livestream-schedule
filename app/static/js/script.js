
document.addEventListener("DOMContentLoaded", function () {
    let scrollPos = sessionStorage.getItem('scrollPos');
    if (scrollPos) { window.scrollTo(0, scrollPos); sessionStorage.removeItem('scrollPos'); }

    let flash = document.getElementById('flashMsg');
    if (flash) {
        setTimeout(() => { flash.classList.add('fade-out'); }, 500);
        setTimeout(() => { flash.style.display = 'none'; }, 1000);
    }

    syncAddOptions();
    initMonthNav();
});

document.body.addEventListener("confetti", function (evt) {
    var type = evt.detail.value;
    if (type === "simple") {
        confetti({ particleCount: 80, spread: 60, origin: { y: 0.6 } });
    } else if (type === "thankyou") {
        confetti({ particleCount: 150, spread: 100, origin: { y: 0.6 }, startVelocity: 30 });
        showFlash("Thank you! 🙌");
    }
});

function showFlash(msg) {
    let f = document.getElementById('flashMsg');
    if (!f) {
        f = document.createElement('div');
        f.id = 'flashMsg';
        f.style.cssText = "background: rgba(0,0,0,0.65); color:white; padding:30px; border-radius:16px; margin-bottom:15px; text-align:center; font-weight:800; font-size:1.5rem; backdrop-filter:blur(4px); border:1px solid rgba(255,255,255,0.1); position:fixed; top:50%; left:50%; transform:translate(-50%, -50%); z-index:9999; min-width:300px;";
        document.body.appendChild(f);
    }
    f.innerText = msg;
    f.style.display = 'block';
    f.classList.remove('fade-out');
    f.style.opacity = '1';
    f.style.maxHeight = '1000px';

    setTimeout(() => { f.classList.add('fade-out'); }, 500);
    setTimeout(() => { f.style.display = 'none'; }, 1000);
}

function initMonthNav() {
    const nav = document.getElementById('monthNav');
    const cards = document.querySelectorAll('.card');
    const months = new Set();
    cards.forEach(c => months.add(c.getAttribute('data-month')));
    const sortedMonths = Array.from(months).sort((a, b) => new Date(a) - new Date(b));
    if (sortedMonths.length === 0) { nav.style.display = 'none'; return; }

    sortedMonths.forEach(m => {
        const btn = document.createElement('div');
        btn.className = 'month-pill';
        btn.innerText = m;
        btn.onclick = () => selectMonth(m);
        nav.appendChild(btn);
    });

    const nowStr = new Date().toLocaleString('default', { month: 'short', year: 'numeric' });
    if (months.has(nowStr)) selectMonth(nowStr);
    else selectMonth(sortedMonths[sortedMonths.length - 1]);
}

function selectMonth(mStr) {
    document.querySelectorAll('.month-pill').forEach(b => {
        if (b.innerText === mStr) b.classList.add('active');
        else b.classList.remove('active');
    });
    document.querySelectorAll('.card').forEach(c => {
        if (c.getAttribute('data-month') === mStr) c.classList.remove('hidden-month');
        else c.classList.add('hidden-month');
    });
    applyFocus();
}

function wipeSelectedMonth() {
    const monthInput = document.getElementById('adminMonthInput');
    if (!monthInput) return;

    const val = monthInput.value;
    if (confirm('Are you sure you want to WIPE all events for ' + val + '?')) {
        const f = document.createElement('form');
        f.method = 'POST';
        f.action = '/wipe_month';

        const i = document.createElement('input');
        i.type = 'hidden';
        i.name = 'gen_month';
        i.value = val;

        f.appendChild(i);
        document.body.appendChild(f);
        f.submit();
    }
}

function saveScrollPosition() { sessionStorage.setItem('scrollPos', window.scrollY); }
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

function openTitleModal(dateStr, currentTitle, eventType) {
    document.getElementById('titleDateInput').value = dateStr;
    document.getElementById('titleTextInput').value = currentTitle;
    if (eventType) {
        document.getElementById('titleTypeSelect').value = eventType;
    }
    syncTitleOptions(); // Ensure correct visibility on open
    document.getElementById('titleModal').style.display = 'flex';
}

function syncTitleOptions() {
    const sel = document.getElementById('titleTypeSelect');
    const box = document.getElementById('customTitleBox');
    if (!sel || !box) return;

    // Show custom title box ONLY if 'Custom' is selected
    if (sel.value === 'Custom') {
        box.classList.remove('hidden');
    } else {
        box.classList.add('hidden');
    }
}
function syncAddOptions() {
    const sel = document.getElementById('eventTypeSelect');
    const box = document.getElementById('customRoleBox');
    if (!sel || !box) return;
    box.classList.toggle('hidden', sel.value !== 'Custom');
}
function openAddModal() { document.getElementById('addModal').style.display = 'flex'; syncAddOptions(); }
function openDateModal(oldDate, oldRawDate) {
    document.getElementById('dateModalOldDate').value = oldDate;
    document.getElementById('dateModalNewDate').value = oldRawDate;
    document.getElementById('dateModal').style.display = 'flex';
}
function openUserMenu() { document.getElementById('userMenuModal').style.display = 'flex'; }

function applyFocus() {
    const filter = document.getElementById('focusFilter');
    if (!filter) return;
    let name = filter.value;
    document.querySelectorAll('.card:not(.hidden-month)').forEach(card => {
        const hasPerson = card.getAttribute('data-people').includes(name);
        card.style.display = (name === 'all' || hasPerson) ? 'flex' : 'none';
    });
    document.querySelectorAll('.role-row').forEach(row => {
        if (name !== 'all' && row.getAttribute('data-name') === name) row.classList.add('highlight-yellow');
        else row.classList.remove('highlight-yellow');
    });
}

function toggleLeaderboard() { document.getElementById('lbModal').style.display = 'flex'; updateLbView(); }
function updateLbView() {
    // STATS_DATA must be defined in the HTML
    if (typeof STATS_DATA === 'undefined') return;
    const period = document.getElementById('lbPeriodSelect').value;
    const list = document.getElementById('lb-list');
    list.innerHTML = "";
    let arr = [];

    if (STATS_DATA[period]) {
        for (const [name, data] of Object.entries(STATS_DATA[period])) {
            arr.push({ name: name, count: data.total, sun: data.sunday, fri: data.friday });
        }
    }

    arr.sort((a, b) => b.count - a.count);
    arr.forEach(p => {
        if (p.count > 0) {
            list.innerHTML += `
            <div class="lb-row">
                <div class="lb-name">${p.name}</div>
                <div class="lb-val-group">
                    <span><i class="fas fa-church"></i> ${p.sun}</span>
                    <span><i class="fas fa-book"></i> ${p.fri}</span>
                    <div class="lb-total">${p.count}</div>
                </div>
            </div>`;
        }
    });
    if (arr.length === 0 || arr.every(p => p.count === 0)) list.innerHTML = `<p style="color:var(--muted); margin-top:15px;">No stats for ${period}</p>`;
}

document.body.addEventListener('htmx:beforeRequest', function (evt) {
    document.body.style.cursor = 'progress';
});
document.body.addEventListener('htmx:afterRequest', function (evt) {
    document.body.style.cursor = 'default';

    // Fire confetti on successful confirm actions
    const target = evt.detail.target;
    if (target && evt.detail.successful) {
        const actionType = evt.detail.requestConfig?.parameters?.type;
        if (actionType === 'confirm') {
            confetti({ particleCount: 100, spread: 70, origin: { y: 0.6 } });
        } else if (actionType === 'pickup') {
            confetti({ particleCount: 150, spread: 100, origin: { y: 0.6 }, colors: ['#38bdf8', '#a855f7', '#10b981'] });
            showFlash("Thank you! 🙌");
        }
    }
});

// Toggle tools menu
function toggleToolsMenu() {
    const menu = document.getElementById('toolsMenu');
    if (menu) {
        menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    }
}

// Sync bulk confirm month with admin month input
document.addEventListener('DOMContentLoaded', function () {
    const adminMonthInput = document.getElementById('adminMonthInput');
    const bulkConfirmMonth = document.getElementById('bulkConfirmMonth');

    if (adminMonthInput && bulkConfirmMonth) {
        // Initial sync
        bulkConfirmMonth.value = adminMonthInput.value;

        // Sync on change
        adminMonthInput.addEventListener('change', function () {
            bulkConfirmMonth.value = this.value;
        });
    }
});

// Dark Mode Toggle
function toggleDarkMode() {
    document.body.classList.toggle('light-mode');
    const isLight = document.body.classList.contains('light-mode');
    localStorage.setItem('lightMode', isLight ? 'true' : 'false');
    updateThemeIcons(isLight);
}

function updateThemeIcons(isLight) {
    // Select all theme toggle buttons
    const icons = document.querySelectorAll('.tool-btn[title="Toggle Light/Dark Mode"] i');
    icons.forEach(icon => {
        if (isLight) {
            icon.classList.remove('fa-moon');
            icon.classList.add('fa-sun');
        } else {
            icon.classList.remove('fa-sun');
            icon.classList.add('fa-moon');
        }
    });
}

// Load dark mode preference
document.addEventListener('DOMContentLoaded', function () {
    const isLight = localStorage.getItem('lightMode') === 'true';
    if (isLight) {
        document.body.classList.add('light-mode');
    }
    updateThemeIcons(isLight);
});

// PWA Install Prompt
let deferredPrompt;
window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    // Show install button if you have one
    const installBtn = document.getElementById('installPWA');
    if (installBtn) {
        installBtn.style.display = 'block';
        installBtn.addEventListener('click', () => {
            deferredPrompt.prompt();
            deferredPrompt.userChoice.then((choiceResult) => {
                deferredPrompt = null;
                installBtn.style.display = 'none';
            });
        });
    }
});

