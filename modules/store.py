"""
store.py  —  Senaryo defteri: kaydet, listele, karşılaştır, dışa/içe aktar

Bir CFO aracının asıl işi tek bir senaryoyu hesaplamak değil, BİRKAÇ senaryoyu
yan yana koymaktır: "10M kredi mi, 5M mi, hiç çekmemek mi?" Bu modül o defteri
tutar.

Kalıcılık kararı (bilinçli):
    Kayıtlar oturumda tutulur ve kullanıcı isterse JSON dosyası olarak indirir.
    Sunucuda kalıcı bir veritabanı YOK, çünkü uygulamanın kimlik doğrulaması
    yok — kimliksiz ortak bir veritabanı, herkesin analizini herkese açmak
    demekti. Ayrıca Streamlit Cloud'un diski kalıcı değil; "kaydettim" deyip
    yeniden başlatmada veriyi kaybetmek, hiç kaydetmemekten daha kötüdür.

    Dosya olarak indirme bu iki sorunu da çözer: veri kullanıcınındır, gerçekten
    kalıcıdır ve taşınabilir. Gerçek hesaplar eklendiğinde bu modülün arayüzü
    (kaydet/listele/sil) aynı kalır, altına bir veritabanı konur.

Bu modül Streamlit'ten bağımsızdır: defter düz bir sözlük listesidir.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

MAX_KAYIT = 20          # defter sınırsız büyümesin (oturum belleği)
MAX_AD = 60


def _simdi() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def temiz_ad(ad: str, defter: list[dict]) -> str:
    """
    Kayıt adını görüntülenebilir hale getirir ve tekilleştirir.

    Ad kullanıcıdan geliyor ve arayüzde gösteriliyor; kontrol karakterleri ve
    aşırı uzunluk temizlenir. Aynı ad ikinci kez kullanılırsa üzerine yazmak
    yerine numaralandırılır — kullanıcı bir analizini kazara kaybetmemeli.
    """
    ad = "".join(ch for ch in str(ad) if ch.isprintable()).strip()
    ad = ad[:MAX_AD] or f"Senaryo {len(defter) + 1}"
    mevcut = {k["ad"] for k in defter}
    if ad not in mevcut:
        return ad
    n = 2
    while f"{ad} ({n})" in mevcut:
        n += 1
    return f"{ad} ({n})"


def kaydet(defter: list[dict], ad: str, senaryo: dict, ozet: dict) -> list[dict]:
    """
    Deftere yeni bir kayıt ekler ve YENİ bir liste döndürür (girdi bozulmaz).

    `senaryo` sürgü değerleri, `ozet` ise o senaryonun sonuçları (batma
    olasılığı vb.) — karşılaştırma tablosu bunları yan yana koyar.
    """
    kayit = {
        "ad": temiz_ad(ad, defter),
        "tarih": _simdi(),
        "senaryo": dict(senaryo),
        "ozet": dict(ozet),
    }
    return ([*defter, kayit])[-MAX_KAYIT:]


def sil(defter: list[dict], ad: str) -> list[dict]:
    """Adı verilen kaydı çıkarır."""
    return [k for k in defter if k.get("ad") != ad]


def karsilastirma_tablosu(defter: list[dict]) -> list[dict]:
    """Defteri, ekranda tablo olarak basılacak düz satırlara çevirir."""
    satirlar = []
    for k in defter:
        s, o = k.get("senaryo", {}), k.get("ozet", {})
        satirlar.append({
            "Senaryo": k.get("ad", "—"),
            "Kredi": s.get("kredi"),
            "Vade": s.get("vade"),
            "Faiz %": s.get("faiz"),
            "Batma %": o.get("batma_yuzde"),
            "İflas ayı": o.get("iflas_ayi"),
            "Aylık net": o.get("aylik_net"),
            "Kaydedildi": k.get("tarih"),
        })
    return satirlar


def disa_aktar(defter: list[dict]) -> bytes:
    """Defteri indirilebilir JSON'a çevirir (kullanıcının kendi yedeği)."""
    return json.dumps(
        {"surum": 1, "kayitlar": defter}, ensure_ascii=False, indent=2
    ).encode("utf-8")


def ice_aktar(ham: bytes | str) -> list[dict]:
    """
    Dışa aktarılmış defteri geri okur.

    Dosya kullanıcıdan geliyor, yani güvenilmez: bozuk JSON, yanlış şema ya da
    beklenmeyen tipler uygulamayı çökertmemeli. Tanınmayan/eksik kayıtlar
    sessizce atlanır, geçerli olanlar alınır.
    """
    try:
        veri = json.loads(ham.decode("utf-8") if isinstance(ham, bytes) else ham)
    except (ValueError, AttributeError, UnicodeDecodeError):
        return []
    kayitlar = veri.get("kayitlar") if isinstance(veri, dict) else veri
    if not isinstance(kayitlar, list):
        return []

    temiz: list[dict] = []
    for k in kayitlar:
        if not isinstance(k, dict):
            continue
        senaryo = k.get("senaryo")
        if not isinstance(senaryo, dict):
            continue
        temiz.append({
            "ad": temiz_ad(k.get("ad", ""), temiz),
            "tarih": str(k.get("tarih", ""))[:32],
            "senaryo": senaryo,
            "ozet": k.get("ozet") if isinstance(k.get("ozet"), dict) else {},
        })
    return temiz[:MAX_KAYIT]
