"""Sesli komut çözümleyici: Türkçe cümleden (öğrenci, işlem, tarih, saat ...) çıkarır.

Ücretsiz, kural tabanlı. Örnekler:
  "Ahmet ile önümüzdeki cuma beşi çeyrek geçe 90 dakika ders koy"   -> ekle
  "Ayşe ile 8 hafta boyunca her perşembe 18'de"                      -> ekle (tekrarlı)
  "Ayşe'nin dersi bitti, konusu üslü sayılar, ödevi sayfa 40"        -> yapildi
  "Mehmet bugün gelmedi" / "Mehmet'in yarınki dersini sil"           -> iptal
  "Ali'nin cumartesi 17'deki dersini pazar 15'e kaydır"              -> tasi
  "Ayşe kesirlerde işlem hatası yaptı"                               -> hata
"""
import re
from datetime import date, timedelta
from difflib import SequenceMatcher

_TR = str.maketrans('çğıöşüâîû', 'cgiosuaiu')

MONTHS = ['ocak', 'subat', 'mart', 'nisan', 'mayis', 'haziran', 'temmuz',
          'agustos', 'eylul', 'ekim', 'kasim', 'aralik']
# Uzun adlar önce: "cumartesi" "cuma" ile, "pazartesi" "pazar" ile başlar
DAYS = {'cumartesi': 5, 'pazartesi': 0, 'carsamba': 2, 'persembe': 3,
        'cuma': 4, 'sali': 1, 'pazar': 6}

# Sayı kelimeleri 1-59 ("on yedi", "onyedi", "kirk bes"); "dord" = dördü/dörde
_ONES = {'bir': 1, 'iki': 2, 'uc': 3, 'dort': 4, 'dord': 4, 'bes': 5, 'alti': 6,
         'yedi': 7, 'sekiz': 8, 'dokuz': 9}
_TENS = {'on': 10, 'yirmi': 20, 'otuz': 30, 'kirk': 40, 'elli': 50}
NUM_WORDS = {**_ONES, **_TENS}
for _t, _tv in _TENS.items():
    for _o, _ov in _ONES.items():
        NUM_WORDS[f'{_t} {_o}'] = NUM_WORDS[f'{_t}{_o}'] = _tv + _ov
_WORDS = '|'.join(sorted(NUM_WORDS, key=len, reverse=True))
NUM = rf'\d{{1,2}}|{_WORDS}'
MIN = rf'\d{{2}}|' + '|'.join(sorted((w for w, v in NUM_WORDS.items() if v >= 10), key=len, reverse=True))
DAY_RE = '|'.join(DAYS)

CANCEL_WORDS = ('iptal', 'gelmedi', 'gelmeyecek', 'gelemedi', 'gelemeyecek', 'olmayacak', 'olmadi',
                'yapilmadi', 'yapilmayacak', 'yapamadik', 'yapamayacag', 'yapmadik', 'yapmayacag',
                'vazgec', 'sil')
DONE_WORDS = ('yapild', 'yaptim', 'yaptik', 'yapmis', 'bitti', 'bitir', 'tamamla', 'islendi',
              'isledi', 'gerceklesti', 'verdim', 'verildi', 'girdik', 'girdim', 'geldi')
MOVE_WORDS = ('tasi', 'ertele', 'kaydir', 'degistir', 'aktar')
MOVE_SHORT = ('al', 'alalim', 'alin', 'cek', 'cekelim', 'cekin')  # yalnızca mevcut dersten söz ediliyorsa
# Hata Haritası: tetikleyici kökler ve tür anahtar kelimeleri (kodlar app.MISTAKE_TYPES ile aynı)
MISTAKE_TRIGGERS = ('hata', 'yanilgi', 'eksik', 'eksig')
MISTAKE_WORDS = {'on bilgi': 'onbilgi', 'onbilgi': 'onbilgi', 'kavram': 'kavram', 'islem': 'islem',
                 'okuma': 'okuma', 'anlama': 'okuma', 'dikkat': 'dikkat', 'ozensiz': 'dikkat',
                 'strateji': 'strateji', 'yontem': 'strateji', 'gosterim': 'gosterim', 'sembol': 'gosterim'}
MISTAKE_RE = re.compile(r'\b(' + '|'.join(MISTAKE_WORDS) + r')')
MAX_SUFFIX = 4  # "ahmet|le", "ayse|nin", "cuma|ya": ek en fazla 4 harf
NOTE_RE = re.compile(r'\b(konu(?:su)?|ödev(?:i)?|odev(?:i)?)\b', re.I)
# "not al / not et / not yaz / notlara ekle / Not: ..." — "not aldı" (sınav notu) değil, emir kipi
NOTE_CMD_RE = re.compile(r'\bnot(?:lara|larıma|larima)?\s+(?:al|alın|alin|alır mısın|alir misin|et|yaz|yazın|yazin|'
                         r'ekle|düş|dus|kaydet|tut)\b|\bnot\s*:', re.I)


def normalize(text):
    text = text.replace('I', 'ı').replace('İ', 'i').lower().translate(_TR)
    text = re.sub(r"[’'‘`]", '', text)
    text = re.sub(r'(\d),(\d)', r'\1.\2', text)  # 1,5 saat
    text = re.sub(r'(?<!\d)[.:]|[.:](?!\d)', ' ', text)  # 17:30 / 17.30 kalsın
    text = re.sub(r'[^a-z0-9:. ]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _num(s):
    return int(s) if s.isdigit() else NUM_WORDS[s]


def _kind(suf):
    """Ekten rol: -den/-deki kaynak ('from'), -e/-ye hedef ('to'), diğerleri ''."""
    if suf.endswith('ki') or re.search(r'[dt][ae]n$', suf):
        return 'from'
    if suf.endswith(('a', 'e')) and not suf.endswith(('da', 'de', 'ta', 'te')):
        return 'to'
    return ''


def _take(pattern, text):
    """Eşleşmeleri döndürür, metinde yerlerini boşlukla doldurur (konumlar kaymaz)."""
    found = list(re.finditer(pattern, text))
    for m in found:
        text = text[:m.start()] + ' ' * (m.end() - m.start()) + text[m.end():]
    return found, text


def _extract_notes(raw):
    """'konusu X ödevi Y' kısmını özgün yazımıyla ayırır -> (kalan, konu, ödev)."""
    parts = NOTE_RE.split(raw)
    topic = homework = None
    for key, value in zip(parts[1::2], parts[2::2]):
        value = value.strip(' ,.:;-') or None
        if key.lower().startswith('konu'):
            topic = value
        else:
            homework = value
    return parts[0], topic, homework


def _word_has(word, stem):
    return word.startswith(stem) and len(word) - len(stem) <= MAX_SUFFIX


def _word_near(word, stem):
    # Yanlış duyulan ad: "mehmed" ~ "mehmet" (yalnızca tam eşleşme yoksa)
    return (len(stem) >= 4 and len(word) - len(stem) <= MAX_SUFFIX
            and SequenceMatcher(None, word[:len(stem)], stem).ratio() >= 0.8)


def match_student(text, students):
    """students: [(id, full_name)] -> (eşleşenler, eşleşen kelimeler çıkarılmış metin)."""
    words = text.split()
    for test in (_word_has, _word_near):
        best, best_score, used = [], 0, set()
        for sid, name in students:
            tokens = normalize(name).split()
            hits = {i for i, w in enumerate(words) for t in tokens if test(w, t)}
            score = len({t for t in tokens for w in words if test(w, t)})
            if score > best_score:
                best, best_score, used = [(sid, name)], score, hits
            elif score and score == best_score:
                best.append((sid, name))
                used |= hits
        if best:
            return best, ' '.join(w for i, w in enumerate(words) if i not in used)
    return [], text


def _pm(h, text):
    """'5te' gibi 1-7 arası saatler öğleden sonra sayılır; 'akşam' her zaman."""
    if ('aksam' in text or 'ogleden sonra' in text) and h < 12:
        return h + 12
    if 'sabah' not in text and 1 <= h <= 7:
        return h + 12
    return h


def find_times(text):
    """-> [{'time': (saat, dakika), 'kind', 'pos'}] metindeki sırasıyla."""
    out = []
    quarters, text = _take(rf'\b(?P<h>{NUM})(?:y?[iuea])? (?P<mm>ceyrek|{MIN}) (?P<dir>gece|kala)(?P<suf>\w*)', text)
    for m in quarters:
        h = _num(m['h'])
        mm = 15 if m['mm'] == 'ceyrek' else _num(m['mm'])
        if m['dir'] == 'kala':
            h, mm = h - 1, 60 - mm
        if 0 <= h <= 23 and 0 <= mm <= 59:
            out.append({'time': (_pm(h, text), mm), 'kind': _kind(m['suf']), 'pos': m.start()})

    pattern = (rf'(?P<saat>\bsaat )?\b(?P<h>{NUM})(?:[:. ](?P<m>{MIN}))?(?P<half> ?bucuk)?'
               rf'(?P<suf>[td][ea]ki|[td][ea]n|[td][ea]|y?[ea]|ki)?\b'
               rf'(?P<loose> (?:de|da|te|ta|gibi|civari|sularinda)\b)?')
    loose = []
    for m in re.finditer(pattern, text):
        h_raw, suf = m['h'], m['suf'] or ''
        strong = m['saat'] or m['m'] or m['half']
        if h_raw.isdigit():
            ok = strong or suf or m['loose']
        else:
            # "ona/onda" zamir olabilir; kelime sayı ancak ek ya da "saat" ile saat sayılır
            ok = strong or (suf and h_raw != 'on')
        if not ok:
            if h_raw.isdigit():
                loose.append(m)
            continue
        h = _num(h_raw)
        mm = _num(m['m']) if m['m'] else (30 if m['half'] else 0)
        if h <= 23 and mm <= 59:
            out.append({'time': (_pm(h, text), mm), 'kind': _kind(suf), 'pos': m.start()})

    if not out:
        # Yalın rakam ("cuma 5 ders koy"): tarih/süre/hafta zaten çıkarıldı
        out = [{'time': (_pm(int(m['h']), text), 0), 'kind': '', 'pos': m.start()}
               for m in loose if 1 <= int(m['h']) <= 23]
    if not out and re.search(r'\bogle(?:n|yin)?\b', text):
        out.append({'time': (12, 0), 'kind': '', 'pos': 0})
    return sorted(out, key=lambda t: t['pos'])


def find_dates(text, today, future=True):
    """-> ([{'date', 'kind', 'pos'}], tarihleri çıkarılmış metin)."""
    out = []

    def add(m, d, suf):
        if d:
            out.append({'date': d, 'kind': _kind(suf or ''), 'pos': m.start()})

    def safe(y, mo, d):
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    found, text = _take(rf"\b(\d{{1,2}}) ({'|'.join(MONTHS)})(\w*)", text)
    for m in found:
        d = safe(today.year, MONTHS.index(m[2]) + 1, int(m[1]))
        add(m, d.replace(year=d.year + 1) if d and future and d < today else d, m[3])

    found, text = _take(r'\bayin (\d{1,2})(\w*)', text)
    for m in found:
        d = safe(today.year, today.month, int(m[1]))
        if d and future and d < today:
            d = safe(today.year + today.month // 12, today.month % 12 + 1, int(m[1]))
        add(m, d, m[2])

    found, text = _take(rf'\b(\d+|{_WORDS}) (gun|hafta) sonra(\w*)', text)
    for m in found:
        n = _num(m[1]) * (7 if m[2] == 'hafta' else 1)
        add(m, today + timedelta(days=n), m[3])

    for pattern, delta in ((r'\bhaftaya bugun(\w{0,5})\b', 7),
                           (r'\b(?:yarindan sonra|obur gun|ertesi gun)(\w{0,5})\b', 2),
                           (r'\b(?:evvelsi gun|evvelki gun|onceki gun)(\w{0,5})\b', -2),
                           (r'\bbugun(\w{0,5})\b', 0),
                           (r'\byarin(\w{0,5})\b', 1),
                           (r'\bdun(\w{0,5})\b', -1)):
        found, text = _take(pattern, text)
        for m in found:
            add(m, today + timedelta(days=delta), m[1])

    week = 0
    if re.search(r'\b(?:haftaya|gelecek hafta|onumuzdeki hafta)\b', text):
        week = 1
    elif 'gecen hafta' in text:
        week = -1
    monday = today - timedelta(days=today.weekday())
    found, text = _take(rf'\b(?:(?P<mod>gelecek|onumuzdeki|sonraki|bu|gecen) )?(?P<day>{DAY_RE})'
                        rf'(?P<suf>\w{{0,6}})\b(?: gun(?P<suf2>\w{{0,4}})\b)?', text)
    for m in found:
        wd = DAYS[m['day']]
        if week:
            d = monday + timedelta(days=7 * week + wd)
        elif m['mod'] in ('gelecek', 'onumuzdeki', 'sonraki'):
            d = today + timedelta(days=(wd - today.weekday()) % 7 or 7)
        elif m['mod'] == 'gecen':
            d = today - timedelta(days=(today.weekday() - wd) % 7 or 7)
        elif future:
            d = today + timedelta(days=(wd - today.weekday()) % 7)
        else:
            d = today - timedelta(days=(today.weekday() - wd) % 7)
        add(m, d, m['suf2'] if m['suf2'] is not None else m['suf'])
    return sorted(out, key=lambda d: d['pos']), text


def _take_duration(text):
    found, text = _take(rf'\b(?P<n>\d+(?:\.\d)?|{_WORDS}|yarim)(?P<half> bucuk)? (?P<u>saat|dakika|dk)(?:lik)?\b', text)
    for m in found:
        n = 0.5 if m['n'] == 'yarim' else float(m['n']) if m['n'][0].isdigit() else NUM_WORDS[m['n']]
        minutes = round((n + (0.5 if m['half'] else 0)) * (60 if m['u'] == 'saat' else 1))
        if 10 <= minutes <= 300:
            return minutes, text
    return None, text


def _take_weeks(text):
    found, text = _take(rf'\b(\d+|{_WORDS}) hafta(?:lik)?(?: boyunca)?\b', text)
    for m in found:
        if 1 <= _num(m[1]) <= 52:
            return _num(m[1]), text
    return None, text


def _intent(text):
    words = text.split()
    if any(w.startswith(MISTAKE_TRIGGERS) for w in words):
        return 'hata'
    if any(w.startswith(CANCEL_WORDS) for w in words):
        return 'iptal'
    if any(w.startswith(DONE_WORDS) for w in words):
        return 'yapildi'
    if any(w.startswith(MOVE_WORDS) for w in words):
        return 'tasi'
    # "al/çek" yalnızca mevcut bir ders anılıyorsa taşımadır ("cumaya ders al" = ekle)
    if any(w in MOVE_SHORT for w in words) and (
            re.search(r'\bdersi', text) or re.search(rf'\b(?:{DAY_RE}|bugun|yarin)\w*[dt][ae]n\b', text)):
        return 'tasi'
    return 'ekle'


def _locative_topic(raw, name):
    """'Ayşe üslü sayılarda işlem hatası' -> 'üslü sayılar': hata türünden önceki -de/-da'lı söz öbeği."""
    tokens = raw.split()
    idx = next((i for i, t in enumerate(tokens) if MISTAKE_RE.match(normalize(t)) or
                normalize(t).startswith(MISTAKE_TRIGGERS)), len(tokens))
    name_parts = normalize(name).split()
    skip = ('bugun', 'dun', 'yarin', 'gecen', 'bu', 'ders', *DAYS)
    words = [t for t in tokens[:idx] if (n := normalize(t)) and not n.startswith(skip)
             and not any(_word_has(n, p) for p in name_parts)]
    if not words or not re.search(r'[dt][ae]$', normalize(words[-1])):
        return None
    words[-1] = re.sub(r"['’‘]?[dt][ae]$", '', words[-1], flags=re.I)
    return ' '.join(words).strip(' ,.') or None


def _split_roles(items, ders_pos):
    """Taşımada kaynak/hedef ayrımı: ekler (-den / -e), yoksa 'dersini' kelimesine göre konum."""
    roles = []
    for it in items:
        if it['kind'] in ('from', 'to'):
            roles.append('src' if it['kind'] == 'from' else 'dst')
        else:
            roles.append('src' if ders_pos is not None and it['pos'] < ders_pos else 'dst')
    if len(items) >= 2 and roles[0] == roles[1]:
        roles[0], roles[1] = 'src', 'dst'
    pick = lambda r: next((it for it, role in zip(items, roles) if role == r), None)
    return pick('src'), pick('dst')


def parse_command(raw, students, today):
    """-> dict(action, student, date, time, ...) ya da dict(error)."""
    note = NOTE_CMD_RE.search(raw or '')
    if note:
        # "Not al: X" -> X ; "X not al" -> X (özgün yazımıyla)
        content = raw[note.end():].strip(' ,.:;-') or raw[:note.start()].strip(' ,.:;-')
        if not content:
            return {'error': 'Notun içeriğini duyamadım.'}
        return {'action': 'not', 'content': content[:500]}

    raw, topic, homework = _extract_notes(raw or '')
    text = normalize(raw)
    if not text:
        return {'error': 'Bir şey duyamadım.'}
    found, text = match_student(text, students)
    if not found:
        return {'error': 'Öğrenci adını bulamadım.'}
    if len(found) > 1:
        return {'error': 'Birden fazla öğrenci eşleşti: ' + ', '.join(n for _, n in found) + '. Soyadını da söyleyin.'}

    action = _intent(text)
    if action == 'hata':
        types = list(dict.fromkeys(MISTAKE_WORDS[m] for m in MISTAKE_RE.findall(text)))
        if not types:
            return {'error': 'Hangi tür hata olduğunu anlayamadım (kavram, işlem, dikkat...).'}
        dates, _ = find_dates(text, today, future=False)
        return {'action': 'hata', 'student': found[0], 'types': types,
                'topic': topic or _locative_topic(raw, found[0][1]), 'date': dates[0]['date'] if dates else None}
    is_repeat = re.search(r'\bher\b', text)
    duration, text = _take_duration(text)
    dates, text = find_dates(text, today, future=(action != 'yapildi'))
    weeks, text = _take_weeks(text)
    times = find_times(text)
    cmd = {'action': action, 'student': found[0], 'date': None, 'time': None}

    if action == 'tasi':
        ders = re.search(r'\bders', text)
        ders_pos = ders.start() if ders else None
        src_d, dst_d = _split_roles(dates, ders_pos)
        src_t, dst_t = _split_roles(times, ders_pos)
        if not (dst_d or dst_t):
            return {'error': 'Dersi hangi gün ya da saate taşıyacağımı anlayamadım.'}
        cmd.update(date=src_d and src_d['date'], time=src_t and src_t['time'],
                   to_date=dst_d and dst_d['date'], to_time=dst_t and dst_t['time'])
        return cmd

    cmd.update(date=dates[0]['date'] if dates else None, time=times[0]['time'] if times else None)
    if action == 'ekle':
        if not cmd['date']:
            return {'error': 'Hangi gün olduğunu anlayamadım.'}
        if not cmd['time']:
            return {'error': 'Saati anlayamadım.'}
        cmd.update(duration=duration, weeks=weeks or (4 if is_repeat else 1))
    elif action == 'yapildi':
        cmd.update(topic=topic, homework=homework)
    return cmd


if __name__ == '__main__':
    # Kendi kendini sınama: python voice.py
    S = [(1, 'Ahmet Yılmaz'), (2, 'Ayşe Demir'), (3, 'Ali Kaya'), (4, 'Ahmet Can'), (5, 'Mehmet Öz')]
    T = date(2026, 10, 16)  # Cuma
    D = lambda d: date(2026, 10, d)

    def p(s):
        return parse_command(s, S, T)

    def check(s, **want):
        r = p(s)
        for k, v in want.items():
            assert r.get(k) == v, (s, k, r.get(k), v, r)

    # Ekleme
    check("Ahmet Yılmaz'la cuma günü saat 17:00'de ders koy", action='ekle', student=(1, 'Ahmet Yılmaz'), date=T, time=(17, 0))
    check("ayşeyle haftaya salı beşte ders programla", date=D(20), time=(17, 0))
    check("Ayşe ile pazartesi 18.30'da", date=D(19), time=(18, 30))
    check("Ali'yle yarın sabah 10'da ders", date=D(17), time=(10, 0))
    check("Ali ile cumartesi saat dört buçukta", date=D(17), time=(16, 30))
    check("Ayşe Demir 23 Ekim saat 15", date=D(23), time=(15, 0))
    check("Ayşe ile 25 Ekim'de 17:00'de", date=D(25), time=(17, 0))
    check("Ahmet Yılmaz ile önümüzdeki cuma beşi çeyrek geçe", date=D(23), time=(17, 15))
    check("Ayşe ile yarından sonra altıya çeyrek kala", date=D(18), time=(17, 45))
    check("Ali ile dördü yirmi geçe pazar", date=D(18), time=(16, 20))
    check("Ali ile 3 gün sonra on yedi otuzda", date=D(19), time=(17, 30))
    check("Ali ile her salı saat 16'da 90 dakika", date=D(20), time=(16, 0), weeks=4, duration=90)
    check("Ayşe ile 8 hafta boyunca her perşembe 18'de bir buçuk saat", date=D(22), time=(18, 0), weeks=8, duration=90)
    check("Ayşe ile cuma 1,5 saatlik ders 17'de", date=T, time=(17, 0), duration=90, weeks=1)
    check("Ali ile ayın 25'inde öğlen", date=D(25), time=(12, 0))
    check("Ali ile öğlen birde yarın", date=D(17), time=(13, 0))
    check("Ali ile haftaya bugün dörtte", date=D(23), time=(16, 0))
    check("Ali ile cuma 17 de", time=(17, 0))
    check("Ali ile cuma 5 ders koy", time=(17, 0))
    check("Ali ile cuma saat 9", time=(9, 0))
    check("Ali ile cuma akşam 9'da", time=(21, 0))
    check("Ali ile salı saat beş otuz", date=D(20), time=(17, 30))
    check("Ahmet Can'la cuma 17'ye ders al", action='ekle', time=(17, 0))
    check("Mehmed ile cuma 5te", student=(5, 'Mehmet Öz'), time=(17, 0))
    # Yapıldı
    check("Ayşe'nin dersini yaptım yapıldı diye işaretle", action='yapildi', date=None)
    check("Ayşe'nin çarşamba dersi yapıldı", action='yapildi', date=D(14))
    check("Ayşe'nin dersi bitti, konusu üslü sayılar, ödevi sayfa 40", action='yapildi',
          topic='üslü sayılar', homework='sayfa 40', time=None)
    check("Ali'nin geçen salı dersini verdim", action='yapildi', date=D(13))
    check("Ali dün geldi", action='yapildi', date=D(15))
    # İptal
    check("Ali'nin yarınki dersini iptal et", action='iptal', date=D(17))
    check("Ayşe bugün gelmedi", action='iptal', date=T)
    check("Ali'nin dersini sil", action='iptal', date=None)
    # Taşıma
    check("Ahmet Can'ın cuma dersini salıya ertele", action='tasi', date=T, to_date=D(20), to_time=None)
    check("Ali'nin yarınki dersini 18'e al", action='tasi', date=D(17), time=None, to_time=(18, 0))
    check("Ali'nin cumartesi 17'deki dersini pazar 15'e kaydır", action='tasi',
          date=D(17), time=(17, 0), to_date=D(18), to_time=(15, 0))
    check("Ali'yi cumadan pazartesiye al", action='tasi', date=T, to_date=D(19))
    check("Ali'nin dersini saat 18'e çek", action='tasi', date=None, to_time=(18, 0))
    # Not alma
    check("Not al Ahmet'in velisi cuma arayacak", action='not', content="Ahmet'in velisi cuma arayacak")
    check("not al: yeni test kitabı sipariş et", action='not', content='yeni test kitabı sipariş et')
    check("Ayşe'ye deneme sınavı getirmeyi not al", action='not', content="Ayşe'ye deneme sınavı getirmeyi")
    check("Notlara ekle, cumartesi tatil", action='not', content='cumartesi tatil')
    check("Not: Ali'nin kitabı bende", action='not', content="Ali'nin kitabı bende")
    check("Ali sınavdan iyi not aldı dersi yapıldı", action='yapildi')  # sınav notu, komut değil
    assert 'içeriğini' in p("not al")['error']
    # Hata kaydı
    check("Ayşe kesirlerde işlem hatası yaptı", action='hata', student=(2, 'Ayşe Demir'), types=['islem'], topic='kesirler', date=None)
    check("Ali'nin üslü sayılarda dikkat hatası", types=['dikkat'], topic='üslü sayılar')
    check("Ali bugün denklemlerde işlem ve dikkat hatası yaptı", types=['islem', 'dikkat'], topic='denklemler', date=T)
    check("Ayşe kavram yanılgısı yaşadı", action='hata', types=['kavram'], topic=None)
    check("Ali'de ön bilgi eksiği var", types=['onbilgi'], topic=None)
    check("Ali dün soruyu okuma hatası yaptı, konusu yeni nesil sorular", types=['okuma'], topic='yeni nesil sorular', date=D(15))
    check("Mehmet Öz'ün dersinde özensizlik hatası", student=(5, 'Mehmet Öz'), types=['dikkat'], topic=None)
    assert 'Hangi tür' in p("Ali hata yaptı")['error']
    # Hatalar
    assert 'Birden fazla' in p("Ahmet ile cuma 5te")['error']
    assert 'Saati' in p("Ali ile cuma ders koy")['error']
    assert 'Saati' in p("Ali ile bir ders koy cuma")['error']
    assert 'Öğrenci' in p("cuma 5te ders koy")['error']
    assert 'taşıyacağımı' in p("Ali'nin dersini ertele")['error']
    print('voice.py: tüm testler geçti')
