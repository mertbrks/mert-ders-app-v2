"""Hata Haritası analizi: öğrencinin hata kayıtlarından örüntü ve tekrar önerisi çıkarır.

Saf fonksiyonlar (veritabanı yok): app.py satırları tarihe çevirip buraya verir.
"""
from datetime import date, timedelta
from voice import normalize

# Aralıklı tekrar basamakları (gün). Hata tekrar görülmeden yapılan her ders bir basamak ilerletir;
# son basamağı da geçen hata "oturdu" sayılır ve listeden düşer.
TEKRAR_ARALIKLARI = (1, 3, 7, 14)


def tekrar_tarihi(son_gorulme, temiz_ders):
    """Son görülme + basamak aralığı; hata oturduysa None."""
    if temiz_ders >= len(TEKRAR_ARALIKLARI):
        return None
    return son_gorulme + timedelta(days=TEKRAR_ARALIKLARI[temiz_ders])


def _ay_once(d):
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def hata_haritasi(kayitlar, ders_gunleri, bugun, gun=90):
    """kayitlar: [(tür, konu, tarih)], ders_gunleri: yapılan derslerin tarihleri.

    -> {'turler': [{'tur', 'sayi', 'konular': [(konu, sayi)], 'bu_ay', 'gecen_ay'}] (çoktan aza),
        'tekrar': [{'tur', 'konu', 'sayi', 'son', 'tarih'}] (vadesi gelenler, çoktan aza)}
    """
    sinir = bugun - timedelta(days=gun)
    bu_ay, gecen_ay = bugun.replace(day=1), _ay_once(bugun)
    turler, gruplar = {}, {}
    for tur, konu, tarih in sorted(kayitlar, key=lambda k: k[2]):
        anahtar = normalize(konu or '')
        g = gruplar.setdefault((tur, anahtar), {'tur': tur, 'konu': None, 'sayi': 0, 'son': tarih})
        g['sayi'] += 1
        g['son'] = tarih
        g['konu'] = konu or g['konu']  # en son yazılış gösterilir
        if tarih < sinir:
            continue
        t = turler.setdefault(tur, {'tur': tur, 'sayi': 0, 'konular': {}, 'bu_ay': 0, 'gecen_ay': 0})
        t['sayi'] += 1
        if konu:
            t['konular'][anahtar] = (konu, t['konular'].get(anahtar, ('', 0))[1] + 1)
        if tarih >= bu_ay:
            t['bu_ay'] += 1
        elif tarih >= gecen_ay:
            t['gecen_ay'] += 1

    for t in turler.values():
        t['konular'] = sorted(t['konular'].values(), key=lambda k: -k[1])

    tekrar = []
    for g in gruplar.values():
        temiz = sum(1 for d in ders_gunleri if g['son'] < d <= bugun)
        vade = tekrar_tarihi(g['son'], temiz)
        if vade and vade <= bugun:
            tekrar.append({**g, 'tarih': vade})
    return {'turler': sorted(turler.values(), key=lambda t: -t['sayi']),
            'tekrar': sorted(tekrar, key=lambda g: (-g['sayi'], g['son']))}


ISTEKLER = {
    'soru': ('Aşağıdaki verilere göre bu öğrenciye, en sık yaptığı hata türlerini ve konuları hedefleyen 5 matematik sorusu hazırla. '
             'Seviyeye uygun olsun. Her sorunun altına kısa çözümünü ve hangi hatayı yakalamaya yönelik olduğunu yaz.'),
    'rapor': ('Aşağıdaki verilere göre veliye WhatsApp\'tan gönderilecek kısa bir aylık rapor yaz: 5-7 cümle, sıcak ama somut. '
              'Öğrenciden "öğrenciniz" diye bahset, isim kullanma. İlerlemeyi, tekrar eden hataları ve önümüzdeki ay neye çalışacağımızı anlat.'),
}


def claude_istegi(tur, sinif, harita, konular, tur_adlari):
    """Claude'a yapıştırılacak metin. Gizlilik: ad, veli, iletişim ve serbest notlar girmez; yalnızca sınıf, konu, hata türü."""
    satirlar = [ISTEKLER[tur], '', f'Sınıf/seviye: {sinif or "belirtilmedi"}']
    if konular:
        satirlar.append('Son 30 günde işlenen konular: ' + ', '.join(konular))
    if harita['turler']:
        satirlar.append('Son 90 günde tekrar eden hatalar:')
        for t in harita['turler']:
            konu = ', '.join(f'{k} ({n})' for k, n in t['konular'][:3])
            satirlar.append(f"- {tur_adlari.get(t['tur'], t['tur'])}: {t['sayi']} kez"
                            f"{' — ' + konu if konu else ''} (bu ay {t['bu_ay']}, geçen ay {t['gecen_ay']})")
    if harita['tekrar']:
        satirlar.append('Henüz oturmamış, tekrar edilmesi gerekenler: ' + ', '.join(
            tur_adlari.get(g['tur'], g['tur']) + (f" ({g['konu']})" if g['konu'] else '') for g in harita['tekrar'][:5]))
    return '\n'.join(satirlar)


if __name__ == '__main__':
    # Kendi kendini sınama: python hata.py
    T = date(2026, 10, 22)
    D = lambda m, d: date(2026, m, d)
    assert tekrar_tarihi(D(10, 1), 0) == D(10, 2)
    assert tekrar_tarihi(D(10, 1), 2) == D(10, 8)
    assert tekrar_tarihi(D(10, 1), 3) == D(10, 15)
    assert tekrar_tarihi(D(10, 1), 4) is None

    k = [('islem', 'Üslü sayılar', D(10, 20)), ('islem', 'üslü sayılar', D(10, 21)), ('islem', 'Kesirler', D(9, 10)),
         ('dikkat', None, D(10, 5)), ('kavram', 'Kesirler', D(6, 1))]  # 6/1: 90 günün dışında
    r = hata_haritasi(k, [D(10, 5), D(10, 12), D(10, 19)], T)
    assert [t['tur'] for t in r['turler']] == ['islem', 'dikkat'], r['turler']
    islem = r['turler'][0]
    assert islem['sayi'] == 3 and islem['bu_ay'] == 2 and islem['gecen_ay'] == 1
    assert islem['konular'] == [('üslü sayılar', 2), ('Kesirler', 1)], islem['konular']
    tek = {(g['tur'], g['konu']): g for g in r['tekrar']}
    # Üslü: 21 Ekim'den sonra ders yok -> basamak 0 -> 22 Ekim vadeli
    assert tek[('islem', 'üslü sayılar')]['tarih'] == T and tek[('islem', 'üslü sayılar')]['sayi'] == 2
    # Dikkat: 5 Ekim'den sonra 2 temiz ders -> 5 + 7 = 12 Ekim, vadesi geçmiş
    assert tek[('dikkat', None)]['tarih'] == D(10, 12)
    # Kesirler/islem: 10 Eylül'den sonra 3 temiz ders -> 14 gün -> 24 Eylül, vadeli
    assert ('islem', 'Kesirler') in tek
    # Kavram: 1 Haziran'dan sonra 3 ders, 15 Haziran vadeli (90 gün dışı olsa da tekrar listesinde)
    assert ('kavram', 'Kesirler') in tek
    assert r['tekrar'][0]['konu'] == 'üslü sayılar'
    # 4 temiz dersten sonra düşer
    r2 = hata_haritasi([('okuma', 'X', D(9, 1))], [D(9, 2), D(9, 9), D(9, 16), D(9, 23)], T)
    assert r2['tekrar'] == []
    assert hata_haritasi([], [], T) == {'turler': [], 'tekrar': []}

    adlar = {'islem': 'İşlem hatası', 'dikkat': 'Dikkat', 'kavram': 'Kavram yanılgısı'}
    m = claude_istegi('rapor', '8. sınıf', r, ['Üslü sayılar', 'Kesirler'], adlar)
    assert m.startswith(ISTEKLER['rapor']) and 'Sınıf/seviye: 8. sınıf' in m
    assert '- İşlem hatası: 3 kez — üslü sayılar (2), Kesirler (1) (bu ay 2, geçen ay 1)' in m, m
    assert '- Dikkat: 1 kez (bu ay 1, geçen ay 0)' in m, m
    assert 'İşlem hatası (üslü sayılar)' in m.splitlines()[-1]
    assert claude_istegi('soru', '', {'turler': [], 'tekrar': []}, [], adlar).endswith('Sınıf/seviye: belirtilmedi')
    print('hata.py: tüm testler geçti')
