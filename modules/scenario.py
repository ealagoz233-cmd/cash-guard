"""
scenario.py  —  Senaryoyu URL'de taşıma (paylaşılabilir analiz)

Kullanıcı sürgüleri ayarlayınca adres çubuğu güncellenir; o linki kopyalayıp
gönderdiğinde karşı taraf AYNI senaryoyu görür. Sunucuda hiçbir şey saklanmaz.

Neden sunucu tarafı kayıt değil: uygulamanın girişi/kimliği yok. Kimliksiz bir
veritabanına kaydetmek, herkesin analizini herkese açmak demekti. URL yöntemi
bu sorunu tamamen ortadan kaldırır — veri kullanıcının kendi linkinde durur.

Tasarım kuralı: BOZUK URL UYGULAMAYI ASLA ÇÖKERTMEZ. Adres çubuğu kullanıcının
(ve internetteki herkesin) elindedir; her değer aralığa kırpılır, çevrilemeyen
değer varsayılana döner. Bu modül bu yüzden Streamlit'ten bağımsız ve saftır.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Alan:
    """Tek bir senaryo değişkeninin sınırları ve varsayılanı."""
    anahtar: str          # URL'de görünecek kısa ad
    tip: type             # int | float
    varsayilan: int | float
    alt: int | float
    ust: int | float


# URL anahtarları bilerek kısa ve Türkçe: link okunabilir kalsın.
# Sınırlar app.py'deki sürgülerle AYNI olmalı; ayrışırsa test yakalar.
ALANLAR: tuple[Alan, ...] = (
    Alan("kredi",     int,   10_000_000, 0,   30_000_000),
    Alan("vade",      int,   24,         6,   60),
    Alan("faiz",      float, 3.5,        0.0, 8.0),
    Alan("gelirdus",  int,   6,          0,   40),
    Alan("gecikme",   int,   30,         0,   80),
    Alan("kayan",     int,   25,         0,   80),
    Alan("giderart",  int,   10,         0,   40),
    Alan("oynaklik",  int,   10,         5,   40),
    Alan("iterasyon", int,   10_000,     10_000, 50_000),
)

_ALAN_HARITASI = {a.anahtar: a for a in ALANLAR}


def varsayilanlar() -> dict[str, int | float]:
    """Hiç parametre yokken kullanılacak senaryo."""
    return {a.anahtar: a.varsayilan for a in ALANLAR}


def sinirlar(anahtar: str) -> tuple[int | float, int | float]:
    """
    Bir sürgünün (alt, üst) sınırı — MUTLAK değerlerle, ör. kredi tutarı.

    Sınırlar burada tanımlı ve motor modülleri bunları kopyalamak yerine
    buradan okumalı. Kopyalandıklarında sessiz bir tuzak oluşuyordu: sürgü
    genişletilir, motor eski tavanda kırpmaya devam eder ve kullanıcı
    ayarlayabildiği bir değerin neden uygulanmadığını göremez.
    """
    alan = _ALAN_HARITASI[anahtar]      # bilinmeyen anahtar KeyError: kasıtlı
    return alan.alt, alan.ust


def oran_sinirlari(anahtar: str) -> tuple[float, float]:
    """
    Yüzde cinsinden girilen bir sürgünün ONDALIK sınırları (%80 → 0.80).

    Motor tarafı oranlarla çalışır, sürgüler yüzde puanıyla; çevirmeyi tek
    yerde yapmak iki tarafın ayrışmasını engeller.
    """
    alt, ust = sinirlar(anahtar)
    return alt / 100.0, ust / 100.0


def _kirp(a: Alan, ham) -> int | float:
    """Tek değeri güvenle çevirir ve aralığa kırpar; olmazsa varsayılan."""
    try:
        deger = a.tip(ham)
    except (TypeError, ValueError):
        return a.varsayilan
    # NaN/sonsuz: karşılaştırma yanıltıcı olur, elle ele
    if deger != deger or deger in (float("inf"), float("-inf")):
        return a.varsayilan
    return min(max(deger, a.alt), a.ust)


def from_query_params(qp) -> dict[str, int | float]:
    """
    Adres çubuğundaki parametreleri geçerli bir senaryoya çevirir.

    Bilinmeyen anahtarlar yok sayılır, bozuk değerler varsayılana döner,
    aralık dışı değerler kırpılır. Her koşulda TAM bir senaryo döner.
    """
    sonuc = varsayilanlar()
    for anahtar, ham in dict(qp).items():
        alan = _ALAN_HARITASI.get(anahtar)
        if alan is None:
            continue
        # Streamlit bazı sürümlerde değeri liste olarak verebiliyor
        if isinstance(ham, (list, tuple)):
            ham = ham[0] if ham else None
        sonuc[anahtar] = _kirp(alan, ham)
    return sonuc


def to_query_params(senaryo: dict) -> dict[str, str]:
    """
    Senaryoyu adres çubuğuna yazılacak biçime çevirir.

    Varsayılandan farklı olanlar yazılır: link kısa kalsın ve "neyi
    değiştirmişim" bakışta görünsün.
    """
    cikti = {}
    for a in ALANLAR:
        deger = senaryo.get(a.anahtar, a.varsayilan)
        if deger != a.varsayilan:
            cikti[a.anahtar] = (f"{deger:g}" if a.tip is float else str(int(deger)))
    return cikti


def _tek_deger(ham) -> str:
    """
    Adres çubuğundaki bir değeri karşılaştırılabilir tek dizeye indirir.

    `from_query_params` liste gelen değeri baştan beri açıyor (Streamlit bazı
    sürümlerde `?kredi=5000000`'ı `["5000000"]` olarak veriyor); burası
    vermiyordu. Sonuç: o sürümlerde karşılaştırma HER koşuda başarısız oluyor,
    fonksiyon `False` dönüyor ve app.py adres çubuğuna yeniden yazıyordu — yani
    bu fonksiyonun tek varlık sebebi olan döngü koruması, korumak istediği
    durumda çalışmıyordu.
    """
    if isinstance(ham, (list, tuple)):
        return str(ham[0]) if ham else ""
    return str(ham)


def ayni_mi(senaryo: dict, qp) -> bool:
    """
    Adres çubuğu zaten bu senaryoyu gösteriyor mu?

    Streamlit'te query_params'a yazmak yeniden çalıştırma tetikleyebiliyor;
    gereksiz yazmayı önlemek sonsuz döngüyü engeller.
    """
    return to_query_params(senaryo) == {k: _tek_deger(v) for k, v in dict(qp).items()}
