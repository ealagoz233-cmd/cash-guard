"""
receivables.py  —  Alacak yaşlandırmasını nakit modeline bağlayan katman
────────────────────────────────────────────────────────────────────────
Uygulamada alacak yaşlandırması bugüne kadar yalnızca bir grafikti: ekranda
duruyor ama hiçbir hesabı beslemiyordu. Oysa gecikme, nakit modelinin
girdilerinden biri ve tek bir sürgüyle ("tahsilat gecikme olasılığı %30")
giriliyordu — yani 92 gün gecikmiş 3,15 milyonluk bir müşteri ile hepsi
zamanında ödeyen bir defter aynı sürgüye düşüyordu.

Bu modül yaşlandırmadan üç şey türetir:

  1. **Beklenen tahsil edilememe (şüpheli alacak).** Alacak yaşlandıkça tahsil
     olasılığı düşer. Kova başına ayrılan karşılık oranları sektörde yerleşik
     bir konvansiyondur; burada da konvansiyon olarak kullanılıyor ve
     `DEFAULT_BUCKETS` ile değiştirilebilir. Bu, modeldeki gecikmeden FARKLI
     bir şeydir: gecikmiş para sonunda gelir, şüpheli alacak hiç gelmez.

  2. **DSO (alacak devir günü).** Denetlenebilir tek sayı: bakiye ÷ aylık
     faturalanan gelir × 30.

  3. **Sürgü karşılıkları.** Monte Carlo'da bir ayın tahsilatından kayan
     beklenen oran tam olarak `delay_prob × delay_severity` çarpımıdır. Bu
     modül yaşlandırmadan bir "beklenen kayma oranı" üretip aynı çarpıma
     oturtur, böylece iki dünya tek bir büyüklükte buluşur.

BİLEREK OTOMATİK EZMİYOR: türetilen değerler sürgülerin yerine geçmez, öneri
olarak sunulur ve kullanıcı tek tıkla uygular. Sebebi ilkesel — yaşlandırma
verisi çoğu şirkette eksik ya da tutarsızdır (bkz. `dso_conflict`), ve sessizce
uygulanan bir varsayım kullanıcının göremediği bir varsayımdır.

TUTARSIZLIK TESPİTİ: Yaşlandırma "ortalama fatura vadesini 68 gün aşmış" derken
bakiyeden hesaplanan DSO 39 gün çıkıyorsa ikisi aynı anda doğru olamaz — defter
eksik listelenmiştir ya da bakiye yanlıştır. Modül bunu yutmaz, işaretler.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from modules import scenario
from utils.format import as_float as _as_float

# Bir ayı kaç gün sayıyoruz (DSO için). Takvim ayı 30,44 ama sektörde 30
# yerleşik ve tabloların elle doğrulanmasını kolaylaştırıyor.
DAYS_PER_MONTH = 30


@dataclass(frozen=True)
class Bucket:
    """Tek bir yaşlandırma kovası."""
    name: str            # arayüzde görünen ad
    lo: int              # alt sınır (gün, dahil)
    hi: int | None       # üst sınır (gün, dahil); None = sınırsız
    loss_rate: float     # bu kovadaki paranın tahsil edilememe oranı
    lag_months: int      # tahsilatın kaç ay sonraya sarktığı (1 = gelecek ay)

    def contains(self, overdue_days: float) -> bool:
        if overdue_days < self.lo:
            return False
        return self.hi is None or overdue_days <= self.hi


# Karşılık oranları sektörde yerleşik yaşlandırma konvansiyonudur (şüpheli
# alacak karşılığı); kesin bir doğa yasası değil, kabul görmüş bir başlangıç
# noktasıdır. Kendi tahsilat geçmişin varsa bu tablo değiştirilmelidir.
DEFAULT_BUCKETS: tuple[Bucket, ...] = (
    Bucket("Vadesinde", -10_000, 0, 0.02, 1),
    Bucket("1–30 gün gecikmiş", 1, 30, 0.05, 1),
    Bucket("31–60 gün gecikmiş", 31, 60, 0.10, 2),
    Bucket("61–90 gün gecikmiş", 61, 90, 0.25, 3),
    Bucket("90+ gün gecikmiş", 91, None, 0.50, 4),
)

# Sürgü tavanları tek kaynaktan (modules/scenario.py) okunur, kopyalanmaz.
# Türetilen öneri bu aralığın dışına taşarsa kırpılır: kullanıcının
# ayarlayamayacağı bir değeri "şunu uygula" diye önermek uygulanamaz bir
# öneridir. Kopyalansaydı sürgü genişletildiğinde burası eski tavanda kırpmaya
# devam eder ve `clamped` uyarısı yanlış sınırı raporlardı.
DELAY_PROB_MAX = scenario.oran_sinirlari("gecikme")[1]
DELAY_SEVERITY_MAX = scenario.oran_sinirlari("kayan")[1]


@dataclass
class BucketRow:
    """Bir kovanın doldurulmuş hâli."""
    name: str
    amount: float
    share: float          # deftere oranı (0–1)
    loss_rate: float
    expected_loss: float
    lag_months: int


@dataclass
class ImpliedStress:
    """Yaşlandırmadan türetilen sürgü önerisi."""
    delay_prob: float
    delay_severity: float
    expected_slip_rate: float   # bir ayın tahsilatından kayması beklenen oran

    @property
    def as_slider_percents(self) -> tuple[int, int]:
        """Arayüzdeki sürgülerin konuştuğu birim: tam sayı yüzde."""
        return round(self.delay_prob * 100), round(self.delay_severity * 100)

    @property
    def achievable_slip_rate(self) -> float:
        """Sürgülerin FİİLEN üretebildiği kayma oranı (çarpım)."""
        return self.delay_prob * self.delay_severity

    @property
    def clamped(self) -> bool:
        """
        Sürgüler ölçülen kaymayı taşıyabiliyor mu?

        Defter çok bozuksa türetilen değerler sürgü tavanını aşar ve kırpılır;
        o zaman simülasyon gerçek durumdan DAHA İYİMSER olur. Bunu söylemeden
        geçmek, kullanıcıya "en kötü senaryoyu kurdum" yanılgısı yaşatırdı.
        """
        return self.achievable_slip_rate + 1e-9 < self.expected_slip_rate


@dataclass
class AgingProfile:
    """Alacak defterinin yaşlandırma röntgeni."""
    total: float
    rows: list[BucketRow] = field(default_factory=list)
    expected_loss: float = 0.0
    overdue_amount: float = 0.0
    weighted_overdue_days: float = 0.0
    dso: float | None = None                 # bakiye ÷ aylık gelir × 30
    listed_amount: float = 0.0               # kalem kalem listelenen tutar

    @property
    def unlisted_amount(self) -> float:
        """Bakiyenin kalem kalem listelenmeyen kısmı — tanımı gereği türetilir."""
        return max(0.0, self.total - self.listed_amount)

    @property
    def overdue_share(self) -> float:
        return self.overdue_amount / self.total if self.total else 0.0

    @property
    def expected_loss_share(self) -> float:
        return self.expected_loss / self.total if self.total else 0.0

    @property
    def dso_conflict(self) -> bool:
        """
        Yaşlandırma ile bakiye birbirini tutuyor mu?

        Yaşlandırma "ortalama fatura vadesini N gün aşmış" diyorsa, alacakların
        defterde ortalama EN AZ N gün beklediği anlamına gelir; dolayısıyla DSO
        da en az N olmalıdır (vade süresinin kendisi bunun üstüne biner). DSO
        daha küçük çıkıyorsa ikisi aynı anda doğru olamaz: ya bakiye eksik ya da
        yaşlandırma listesi defterin tamamını temsil etmiyordur.

        Eşitsizlik kurulurken vade süresi hakkında hiçbir varsayım yapılmıyor —
        bilinmeyen bir sayıyı tahmin etmek yerine, onsuz da geçerli olan alt
        sınır kullanılıyor.
        """
        if self.dso is None or self.total <= 0:
            return False
        return self.dso < self.weighted_overdue_days


def _classify(overdue_days: float, buckets: tuple[Bucket, ...]) -> Bucket:
    """Bir alacağı kovasına yerleştirir; hiçbirine girmezse en yaşlısına."""
    for b in buckets:
        if b.contains(overdue_days):
            return b
    return buckets[-1]


def age(
    receivables,
    total_outstanding: float | None = None,
    monthly_revenue: float | None = None,
    buckets: tuple[Bucket, ...] = DEFAULT_BUCKETS,
) -> AgingProfile:
    """
    Alacak listesini yaşlandırma profiline çevirir.

    `receivables`  : [{"amount": ..., "overdue_days": ...}, ...]
    `total_outstanding` : defterin toplam bakiyesi. Listelenen kalemlerin
        toplamından büyükse aradaki fark "listelenmemiş" sayılır ve en iyimser
        kovaya (vadesinde) yazılır — listelenmeyen alacağı kötü varsaymak,
        görmediğimiz bir şey hakkında karamsar bir iddia üretmek olurdu.
    `monthly_revenue` : DSO için faturalanan aylık gelir (tahsilat değil).

    Liste boşsa ya da tamamen bozuksa toplamı sıfır bir profil döner; çağıran
    tarafın `total` kontrolü yapması yeterlidir, istisna fırlatılmaz.
    """
    kalemler = []
    for ham in (receivables or []):
        if not isinstance(ham, dict):
            continue
        tutar = _as_float(ham.get("amount"))
        if tutar <= 0:
            continue
        kalemler.append((tutar, _as_float(ham.get("overdue_days"))))

    listelenen = sum(t for t, _ in kalemler)
    toplam = _as_float(total_outstanding, listelenen) or listelenen
    listelenmeyen = max(0.0, toplam - listelenen)

    # Kova başına toplama
    tutarlar = {b.name: 0.0 for b in buckets}
    for tutar, gun in kalemler:
        tutarlar[_classify(gun, buckets).name] += tutar
    if listelenmeyen > 0:
        tutarlar[buckets[0].name] += listelenmeyen

    toplam = sum(tutarlar.values())
    rows = [
        BucketRow(
            name=b.name,
            amount=tutarlar[b.name],
            share=(tutarlar[b.name] / toplam) if toplam else 0.0,
            loss_rate=b.loss_rate,
            expected_loss=tutarlar[b.name] * b.loss_rate,
            lag_months=b.lag_months,
        )
        for b in buckets
    ]

    gecikmis = sum(r.amount for r, b in zip(rows, buckets) if b.lo > 0)
    # Ağırlıklı ortalama gecikme yalnızca LİSTELENEN kalemlerden hesaplanır:
    # listelenmeyen bakiyenin yaşı bilinmiyor, sıfır saymak ortalamayı
    # gerçekte olmadığı kadar iyi gösterirdi.
    agirlikli = (sum(t * g for t, g in kalemler) / listelenen) if listelenen else 0.0

    dso = None
    if monthly_revenue and monthly_revenue > 0 and toplam > 0:
        dso = toplam / monthly_revenue * DAYS_PER_MONTH

    return AgingProfile(
        total=toplam,
        rows=rows,
        expected_loss=sum(r.expected_loss for r in rows),
        overdue_amount=gecikmis,
        weighted_overdue_days=agirlikli,
        dso=dso,
        listed_amount=listelenen,
    )


def implied_stress(profile: AgingProfile) -> ImpliedStress:
    """
    Yaşlandırmayı Monte Carlo sürgülerine çevirir.

    Bağlantı noktası tek bir büyüklük: simülasyonda bir ayın tahsilatından
    kayması beklenen oran `delay_prob × delay_severity` çarpımıdır. Yaşlandırma
    tarafındaki karşılığı, gelecek aydan SONRAYA sarkması beklenen para payıdır
    (kova gecikmesi 2 ay ve üzeri olanlar). İki taraf bu çarpımda buluşur.

    Çarpımı iki sürgüye bölmek bir konvansiyondur ve öyle olduğu söylenmelidir.
    `delay_prob`ın doğal çıpası gecikmiş alacak payıdır ("bir ayda gecikme
    yaşama olasılığı"), `delay_severity` ise çarpımı tutturan kalandır. Ama bu
    çıpa tek başına yeterli değil: gecikmiş payın tamamı sarkıyorsa şiddet 1'e
    dayanır ve sürgü tavanını (%80) deler; o zaman çarpım ölçülen kaymanın
    ALTINDA kalır — yani gereksiz yere iyimser bir simülasyon kurulur. Bu yüzden
    olasılık, şiddetin tavana sığması için gerekiyorsa yukarı çekilir.

    Kırpma yalnızca kayma iki tavanın çarpımını (%80 × %80 = %64) aştığında
    devreye girer; bu durumda `clamped` işaretlenir ve arayüz söyler.
    """
    if profile.total <= 0:
        return ImpliedStress(0.0, 0.0, 0.0)

    # Gelecek aydan sonraya sarkması beklenen pay
    kayma = sum(r.share for r in profile.rows if r.lag_months >= 2)
    if kayma <= 0:
        return ImpliedStress(0.0, 0.0, 0.0)

    # Çıpa gecikmiş pay; ama şiddetin tavana sığması için gereken alt sınırın
    # altına da düşemez. İkisinin büyüğü alınır, sonra olasılık tavanı uygulanır.
    olasilik = min(max(profile.overdue_share, kayma / DELAY_SEVERITY_MAX),
                   DELAY_PROB_MAX)
    siddet = min(kayma / olasilik, DELAY_SEVERITY_MAX)
    return ImpliedStress(delay_prob=olasilik, delay_severity=siddet,
                         expected_slip_rate=kayma)
