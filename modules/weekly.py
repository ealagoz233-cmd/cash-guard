"""
weekly.py  —  13 haftalık likidite ufku (ay-içi nakit çukurları)
─────────────────────────────────────────────────────────────────
Uygulamanın bütün hesapları AYLIK. Aylık hesap şunu söyleyemez: ay artıda
kapansa bile ayın 5'inde maaş çıkarken kasa dibi görebilir. Kurumsal likidite
kontrolünün fiili standardı bu yüzden 13 haftalık (yaklaşık 90 gün) tablodur —
aylık ortalama, ay içindeki en kritik günü ortalamanın altında saklar.

Bu modül aylık toplamları bir nakit TAKVİMİNE dağıtır ve haftalık kasa yolunu
çıkarır. Dağıtım varsayımları açıkça listelenmiştir (`DEFAULT_CALENDAR`), çünkü
sonucu belirleyen şey tutarlar değil, tutarların ayın hangi gününe düştüğüdür.

  • Sabit tarihli kalemler  : kira ayın 1'i, maaş 5'i, kredi taksiti 15'i,
                              enerji/lojistik 20'si — tamamı o gün tek seferde.
  • Yayılan kalemler        : hammadde, pazarlama ve TAHSİLAT aya eşit yayılır.
                              Tahsilat için bu bilinçli olarak nötr bir
                              varsayımdır: gerçek tahsilat takvimi bilinmiyorsa
                              onu bir güne yığmak, olmayan bir bilgiyi
                              varmış gibi göstermek olurdu.

DÜRÜSTLÜK ŞARTI: Gider dağılımı yoksa (kullanıcı kendi verisini yüklediğinde
öyle olur) bütün gider tek kalem hâlinde aya eşit yayılır. O durumda haftalık
eğri, aylık çizginin daha ince çizilmiş hâlidir ve HİÇBİR yeni bilgi taşımaz.
`WeeklyPlan.informative` bunu işaretler; arayüz de "bu görünüm şu an ek bilgi
vermiyor" demek zorundadır — aksi hâlde kullanıcı, olmayan bir hassasiyete
güvenir.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from utils.format import as_float as _as_float

DEFAULT_WEEKS = 13
DAYS_PER_WEEK = 7

# `expense_breakdown` anahtarı → ayın kaçında ödendiği. None = aya yayılmış.
# Anahtarlar mock verideki adlarla eşleşir; tanınmayan bir kalem yayılmış
# sayılır (güvenli varsayılan: uydurma bir ödeme günü atamaktansa yay).
DEFAULT_CALENDAR: dict[str, int | None] = {
    "kira_ve_isletme": 1,
    "personel": 5,
    "enerji_ve_lojistik": 20,
    "hammadde_ve_tedarik": None,
    "pazarlama": None,
}

# Borç servisi gider dağılımının parçası değil, ayrı bir çıkış.
DEBT_SERVICE_DAY = 15


@dataclass
class WeekRow:
    """Tek bir haftanın nakit özeti."""
    index: int                  # 1..weeks
    start: date
    inflow: float
    outflow: float
    closing_cash: float

    @property
    def end(self) -> date:
        """Hafta bitişi — tanımı gereği başlangıç + 6 gün, saklanmaz."""
        return self.start + timedelta(days=DAYS_PER_WEEK - 1)

    @property
    def net(self) -> float:
        return self.inflow - self.outflow


@dataclass
class WeeklyPlan:
    """13 haftalık likidite tablosu."""
    weeks: list[WeekRow] = field(default_factory=list)
    opening_cash: float = 0.0
    dated_items: list[str] = field(default_factory=list)

    # ── Ortak "yeterli veri var mı" sözleşmesi (utils/sufficiency.py) ─────
    @property
    def available(self) -> bool:
        """
        Tablo ay-içi bilgi taşıyor mu? (bkz. modül başlığı)

        Ayrı bir alan olarak saklanıyordu ve `bool(dated_items)` ile aynı şeydi.
        İki alan tek gerçeği kodlayınca bir yol onları tutarsız bırakabilir; o
        yolda kaybolan şey de tam olarak "bu görünüm bilgi taşımıyor" dürüstlük
        uyarısı olurdu.
        """
        return bool(self.dated_items)

    @property
    def missing_fields(self) -> list[str]:
        """
        Tarihli kalem yoksa eksik olan şey gider DAĞILIMIDIR: ödeme günleri
        kalem adından bulunuyor (bkz. `dated_items`). Tablo yine kurulur, ama
        ay-içi çukuru gösteremez.
        """
        return [] if self.available else ["expense_breakdown"]

    # `informative` sözleşmenin ortak adından ÖNCE vardı ve API cevabında da
    # yayınlanıyor; adı sabit kalsın diye takma ad olarak duruyor. Tek gerçek
    # `available`, dolayısıyla ikisi ayrışamaz.
    @property
    def informative(self) -> bool:
        """`available` ile aynı — API şemasında bu adla yayınlanıyor."""
        return self.available

    @property
    def min_week(self) -> WeekRow | None:
        return min(self.weeks, key=lambda w: w.closing_cash) if self.weeks else None

    @property
    def first_negative(self) -> WeekRow | None:
        return next((w for w in self.weeks if w.closing_cash < 0), None)

    @property
    def end_cash(self) -> float:
        return self.weeks[-1].closing_cash if self.weeks else self.opening_cash

    @property
    def intramonth_gap(self) -> float:
        """
        Ay-içi çukurun, dönem sonu kasasına göre ne kadar derin olduğu.

        Aylık model yalnızca dönem sonunu görür. Bu fark, "aylık bakınca
        görünmeyen ama fiilen yaşanan" en kötü ana ne kadar indiğini söyler.
        """
        dip = self.min_week
        return 0.0 if dip is None else self.end_cash - dip.closing_cash


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def parse_start(as_of, fallback: date | None = None) -> date:
    """
    "2026-06-30" gibi bir veri tarihinden projeksiyonun BAŞLADIĞI günü verir
    (verinin ertesi günü). Çevrilemezse `fallback`, o da yoksa bugün.

    Tarih dışarıdan geliyor ve kullanıcı dosyasından gelebilir; bozuk bir dize
    yüzünden uygulama patlamamalı.

    Dönüş HER ZAMAN `date`tir. `datetime` de bir `date` alt sınıfı olduğu için
    saatli bir değer eskiden olduğu gibi geçip gidiyordu; pandas'ın okuduğu bir
    tarih sütunu (`Timestamp`) tam olarak öyle gelir ve `WeekRow.start` saatli
    kalıyordu — API'de `"2026-07-01T00:00:00"`, arayüzde saatli hafta etiketi.
    """
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    if isinstance(as_of, date):
        return as_of + timedelta(days=1)
    try:
        y, m, d = (int(p) for p in str(as_of).split("-")[:3])
        return date(y, m, d) + timedelta(days=1)
    except (ValueError, TypeError):
        return fallback or date.today()


def build(
    current_cash: float,
    monthly_collections: float,
    expense_breakdown: dict | None,
    monthly_fixed_expense: float,
    monthly_debt_service: float = 0.0,
    start: date | None = None,
    weeks: int = DEFAULT_WEEKS,
    payment_calendar: dict[str, int | None] | None = None,
) -> WeeklyPlan:
    """
    Aylık toplamları güne dağıtıp haftalık kasa yolunu kurar.

    `expense_breakdown` verilmezse ya da toplamı sıfırsa `monthly_fixed_expense`
    tek kalem olarak aya yayılır — sonuç geçerlidir ama ay-içi bilgi taşımaz
    (`informative=False`).

    Aylık tutarlar aya YAYILIRKEN o ayın gerçek gün sayısı kullanılır: 28 günlük
    şubatta günlük tutar 31 günlük ocaktakinden yüksektir. Sabitlenmiş 30 gün,
    ay sınırlarında kasayı sistematik olarak kaydırırdı.
    """
    takvim = dict(DEFAULT_CALENDAR if payment_calendar is None else payment_calendar)
    start = start or date.today()
    n_gun = max(1, int(weeks)) * DAYS_PER_WEEK

    # ── Gider kalemlerini kur ─────────────────────────────────────────────
    kalemler = {k: _as_float(v) for k, v in (expense_breakdown or {}).items()
                if _as_float(v) > 0}
    if not kalemler:
        # Dağılım yok: tek kalem, aya yayılmış. Bilgi taşımaz ama tablo kurulur.
        kalemler = {"toplam_gider": _as_float(monthly_fixed_expense)}
        takvim = {"toplam_gider": None}

    tarihli = [k for k in kalemler if takvim.get(k) is not None]
    if _as_float(monthly_debt_service) > 0:
        tarihli.append("borc_servisi")

    gunluk_giris = [0.0] * n_gun
    gunluk_cikis = [0.0] * n_gun
    tahsilat = _as_float(monthly_collections)
    borc = _as_float(monthly_debt_service)

    for i in range(n_gun):
        gun = start + timedelta(days=i)
        ay_gun_sayisi = _last_day(gun.year, gun.month)
        son_gun = ay_gun_sayisi

        # Tahsilat: nötr varsayım — aya eşit yayılır.
        gunluk_giris[i] += tahsilat / ay_gun_sayisi

        for ad, tutar in kalemler.items():
            odeme_gunu = takvim.get(ad)
            if odeme_gunu is None:
                gunluk_cikis[i] += tutar / ay_gun_sayisi
            # Ödeme günü o ayda yoksa (ör. 31'i şubatta) ayın son gününe düşer.
            elif gun.day == min(int(odeme_gunu), son_gun):
                gunluk_cikis[i] += tutar

        if borc > 0 and gun.day == min(DEBT_SERVICE_DAY, son_gun):
            gunluk_cikis[i] += borc

    # ── Haftalara topla ───────────────────────────────────────────────────
    kasa = _as_float(current_cash)
    satirlar: list[WeekRow] = []
    for h in range(int(weeks)):
        dilim = slice(h * DAYS_PER_WEEK, (h + 1) * DAYS_PER_WEEK)
        giris = sum(gunluk_giris[dilim])
        cikis = sum(gunluk_cikis[dilim])
        kasa += giris - cikis
        satirlar.append(WeekRow(
            index=h + 1,
            start=start + timedelta(days=h * DAYS_PER_WEEK),
            inflow=giris, outflow=cikis, closing_cash=kasa,
        ))

    return WeeklyPlan(
        weeks=satirlar,
        opening_cash=_as_float(current_cash),
        dated_items=tarihli,
    )
