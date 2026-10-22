// Mert Ders Takip — ortak arayüz davranışları
(function () {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

    // CSRF başlığıyla JSON POST
    window.postJSON = function (url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify(body)
        }).then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.success === false) {
                throw new Error(data.error || 'İşlem tamamlanamadı.');
            }
            return data;
        });
    };

    // Modallar: data-open="id" açar, data-close kapatır, arka plana tıklayınca kapanır
    window.openModal = function (id) {
        const dlg = document.getElementById(id);
        if (dlg && !dlg.open) dlg.showModal();
        return dlg;
    };

    document.addEventListener('click', function (e) {
        const opener = e.target.closest('[data-open]');
        if (opener) {
            openModal(opener.dataset.open);
            return;
        }
        const closer = e.target.closest('[data-close]');
        if (closer) {
            closer.closest('dialog')?.close();
            return;
        }
        if (e.target.tagName === 'DIALOG' && e.target.open) {
            const r = e.target.getBoundingClientRect();
            const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
            if (!inside) e.target.close();
        }
    });

    // Onay isteyen formlar: data-confirm="mesaj"
    document.addEventListener('submit', function (e) {
        const msg = e.target.dataset.confirm;
        if (msg && !window.confirm(msg)) e.preventDefault();
    });

    // Tema: açık (varsayılan) / koyu — seçim tarayıcıda saklanır
    const root = document.documentElement;
    const themeMeta = document.querySelector('meta[name="theme-color"]');

    function syncThemeUi() {
        const dark = root.dataset.theme === 'dark';
        if (themeMeta) themeMeta.content = dark ? '#0E0D0B' : '#F4F1EA';
        document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
            btn.setAttribute('aria-label', dark ? 'Açık temaya geç' : 'Koyu temaya geç');
        });
    }

    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
            root.classList.add('theme-switching');
            if (next === 'dark') root.dataset.theme = 'dark';
            else delete root.dataset.theme;
            try { localStorage.setItem('theme', next); } catch (e) {}
            syncThemeUi();
            document.dispatchEvent(new CustomEvent('themechange', { detail: next }));
            setTimeout(() => root.classList.remove('theme-switching'), 250);
        });
    });
    syncThemeUi();

    // Mobil menü
    const menuBtn = document.querySelector('.menu-btn');
    const nav = document.getElementById('main-nav');
    if (menuBtn && nav) {
        menuBtn.addEventListener('click', function () {
            const open = nav.classList.toggle('open');
            menuBtn.setAttribute('aria-expanded', String(open));
        });
    }

    // Bildirimler 5 sn sonra kaybolur, × ile hemen kapanır
    function dismiss(el) {
        el.classList.add('leaving');
        setTimeout(() => el.remove(), 220);
    }
    document.querySelectorAll('.flash').forEach(function (el) {
        el.querySelector('button')?.addEventListener('click', () => dismiss(el));
        if (!el.classList.contains('danger')) setTimeout(() => dismiss(el), 5000);
    });

    // Sayfa içinden bildirim göstermek için
    window.toast = function (message, kind) {
        let box = document.querySelector('.flashes');
        if (!box) {
            box = document.createElement('div');
            box.className = 'flashes';
            box.setAttribute('role', 'status');
            document.body.appendChild(box);
        }
        const el = document.createElement('div');
        el.className = 'flash ' + (kind || 'info');
        const p = document.createElement('p');
        p.textContent = message;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'icon-btn';
        btn.setAttribute('aria-label', 'Kapat');
        btn.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
        btn.addEventListener('click', () => dismiss(el));
        el.append(p, btn);
        box.appendChild(el);
        if (kind !== 'danger') setTimeout(() => dismiss(el), 5000);
    };
})();

// Sesli komut: Safari/Chrome konuşma tanıma -> /api/voice önizleme -> onay
(function () {
    const btn = document.getElementById('voiceBtn');
    const modal = document.getElementById('voiceModal');
    if (!btn || !modal) return;
    const form = document.getElementById('voiceForm');
    const text = document.getElementById('voiceText');
    const status = document.getElementById('voiceStatus');
    const summary = document.getElementById('voiceSummary');
    const submit = document.getElementById('voiceSubmit');
    const listenBtn = document.getElementById('voiceListen');
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    let checked = null;  // onaylanmaya hazır metin
    let rec = null;
    let silenceTimer = null;
    let maxTimer = null;

    function today() {
        const d = new Date();
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }

    function show(msg, isError) {
        summary.textContent = msg;
        summary.classList.toggle('is-error', !!isError);
        summary.hidden = !msg;
    }

    function reset() {
        checked = null;
        submit.textContent = 'Kontrol et';
        show('');
    }

    function listen() {
        if (!Recognition) {
            status.textContent = 'Bu tarayıcı sesi tanımıyor; yazın ya da klavyedeki mikrofonu kullanın';
            listenBtn.hidden = true;
            text.focus();
            return;
        }
        stopListening();
        const r = new Recognition();
        rec = r;
        r.lang = 'tr-TR';
        r.interimResults = true;
        r.continuous = false;
        r.onstart = () => { if (rec === r) { modal.classList.add('listening'); status.textContent = 'Dinliyorum…'; } };
        r.onresult = (e) => {
            if (rec !== r) return;
            text.value = Array.from(e.results).map((x) => x[0].transcript).join(' ');
            reset();
            // iOS Safari konuşma bitince kendiliğinden durmayabiliyor: cümle bitti ya da 1,5 sn sessizlik -> kapat
            clearTimeout(silenceTimer);
            if (e.results[e.results.length - 1].isFinal) finish();
            else silenceTimer = setTimeout(finish, 1500);
        };
        r.onerror = (e) => {
            if (rec !== r) return;
            stopListening();
            status.textContent = e.error === 'not-allowed'
                ? 'Mikrofon izni verilmedi (Ayarlar › Safari › Mikrofon)'
                : 'Duyamadım, tekrar deneyin ya da yazın';
        };
        r.onend = () => { if (rec === r) finish(); };
        reset();
        text.value = '';
        r.start();
        maxTimer = setTimeout(finish, 20000);  // ne olursa olsun 20 sn sonra mikrofonu bırak
    }

    // Mikrofonu bırakır; stop() + abort() ikisi birden, Safari bazen yalnız birini dinlemiyor
    function stopListening() {
        clearTimeout(silenceTimer);
        clearTimeout(maxTimer);
        modal.classList.remove('listening');
        if (status.textContent === 'Dinliyorum…') status.textContent = 'Söyleyin';
        const r = rec;
        rec = null;
        if (!r) return;
        try { r.stop(); } catch (e) {}
        try { r.abort(); } catch (e) {}
    }

    function finish() {
        stopListening();
        if (modal.open && text.value.trim()) check();
    }

    function check() {
        stopListening();
        const value = text.value.trim();
        if (!value) return;
        submit.disabled = true;
        postJSON('/api/voice', { text: value, today: today() })
            .then((data) => {
                checked = value;
                show(data.summary);
                submit.textContent = 'Onayla';
            })
            .catch((err) => show(err.message, true))
            .finally(() => { submit.disabled = false; });
    }

    btn.addEventListener('click', () => { openModal('voiceModal'); listen(); });
    listenBtn.addEventListener('click', listen);
    text.addEventListener('input', reset);
    modal.addEventListener('close', stopListening);  // ×, Esc, dışarı tıklama hepsi buraya düşer
    window.addEventListener('pagehide', stopListening);
    document.addEventListener('visibilitychange', () => { if (document.hidden) stopListening(); });

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        stopListening();
        if (checked === null || checked !== text.value.trim()) return check();
        submit.disabled = true;
        postJSON('/api/voice', { text: checked, today: today(), confirm: true })
            .then((data) => {
                // 'Yapıldı' ise aynı pencerede konu + hata paneli; diğerlerinde sonuç flash olarak gelir
                if (data.lesson_id) showLesson(data);
                else location.reload();
            })
            .catch((err) => { show(err.message, true); submit.disabled = false; });
    });

    const lessonBox = document.getElementById('voiceLesson');
    const topicInput = document.getElementById('voiceTopic');
    const boxes = lessonBox.querySelectorAll('input[name="mistakes"]');
    let lessonId = null;

    function showLesson(data) {
        lessonId = data.lesson_id;
        document.getElementById('voiceDone').textContent = data.summary.replace('işaretlenecek', 'işaretlendi');
        topicInput.value = data.topic || '';
        boxes.forEach((b) => b.checked = false);
        document.getElementById('voiceMain').hidden = true;
        document.getElementById('voiceFoot').hidden = true;
        lessonBox.hidden = false;
        topicInput.focus();
    }

    document.getElementById('voiceLessonSkip').addEventListener('click', () => location.reload());
    document.getElementById('voiceLessonSave').addEventListener('click', function () {
        const types = [...boxes].filter((b) => b.checked).map((b) => b.value);
        const topic = topicInput.value.trim();
        if (!types.length && !topic) return location.reload();
        this.disabled = true;
        postJSON('/api/mistakes', { lesson_id: lessonId, topic, types, flash: true })
            .then(() => location.reload())
            .catch((err) => { toast(err.message, 'danger'); this.disabled = false; });
    });
    // Panel açıkken pencere kapanırsa da sayfa yenilensin (ders durumu değişti)
    modal.addEventListener('close', () => { if (lessonId) location.reload(); });
})();
