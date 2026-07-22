"""
sensitivity.py  —  Duyarlılık (tornado) analizi: hangi sürgü seni batırıyor?
────────────────────────────────────────────────────────────────────────────
Monte Carlo tek bir sayı verir: "%94.3 batma olasılığı". Ama kullanıcı beş
sürgüyü aynı anda oynatır ve hangisinin o sayıyı yaptığını göremez. Beş
değişkenli bir sistemde "neyi düzeltirsem ne kazanırım" sorusu, tam da yatırım
yapılacak yeri belirlediği için manşet sayıdan daha eylemlidir.

Yöntem — tek değişkenli yerel duyarlılık (one-at-a-time):
    Her sürgü sırayla ±`delta` kadar oynatılır, DİĞERLERİ SABİT tutulur ve
    simülasyon yeniden koşulur. İki uç arasındaki batma olasılığı farkı
    ("swing") o sürgünün etkisidir. Sürgüler etkiye göre büyükten küçüğe
    sıralanınca ortaya klasik tornado grafiği çıkar.

ORTAK RASTGELE SAYILAR (common random numbers) — bu modülün can damarı:
    Bütün koşular AYNI tohumla yapılır. Tohum değişseydi %1'lik bir fark
    parametreden mi Monte Carlo gürültüsünden mi geldiğini ayırt edemezdik;
    10.000 iterasyonda gürültünün standart hatası zaten ~0.5 puan. Aynı tohumla
    iki koşu arasındaki TEK fark oynatılan parametredir, dolayısıyla ölçülen
    swing gürültü değil sinyaldir. Sıralama da bu sayede tekrarlanabilir.

Kırpma dürüstlüğü:
    Sürgülerin gerçek sınırları var (ör. gecikme olasılığı %0–80). Taban değer
    sınıra yakınsa ±delta simetrik uygulanamaz; kırpılır. Bu durumda sonuç
    nesnesi FİİLEN kullanılan alt/üst değeri taşır, çünkü "±5 puan oynattım"
    deyip 3 puan oynatmak sessiz bir yalan olurdu.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from modules.monte_carlo import StressParams, run

# Oynatma adımı: 5 puan (0.05). Sürgüler yüzde puanı cinsinden okunduğu için
# "her sürgüyü 5 puan oynat" kullanıcı için doğrudan anlamlı bir cümledir.
DEFAULT_DELTA = 0.05


@dataclass(frozen=True)
class Driver:
    """Tornado'ya girecek tek bir stres sürgüsünün tanımı."""
    key: str        # StressParams alan adı
    url_key: str    # scenario.ALANLAR'daki karşılığı (sürgünün sınır kaynağı)
    label: str      # arayüzde/rapor da görünecek Türkçe ad
    lo: float       # sürgünün gerçek alt sınırı (ondalık)
    hi: float       # sürgünün gerçek üst sınırı (ondalık)


# Sınırlar app.py'deki sürgülerle birebir aynı olmalı; aksi halde tornado
# kullanıcının asla ulaşamayacağı bir değeri "işte buradan kazanırsın" diye
# gösterir. `url_key` sayesinde bu eşleşme elle değil, testle doğrulanıyor:
# tests/test_sensitivity.py sınırları scenario.ALANLAR'dan okuyup karşılaştırır.
DRIVERS: tuple[Driver, ...] = (
    Driver("income_drop", "gelirdus", "Gelir düşüşü", 0.00, 0.40),
    Driver("delay_prob", "gecikme", "Tahsilat gecikme olasılığı", 0.00, 0.80),
    Driver("delay_severity", "kayan", "Geciken ayda kayan tahsilat", 0.00, 0.80),
    Driver("expense_inflation", "giderart", "Gider artışı", 0.00, 0.40),
    Driver("volatility", "oynaklik", "Piyasa oynaklığı", 0.05, 0.40),
)

# Bu eşiğin altındaki swing "pratikte etkisiz" sayılır. 10.000 iterasyonda
# Monte Carlo'nun kendi standart hatası ~0.5 puan; ortak rastgele sayılar bunu
# büyük ölçüde götürse de 0.2 puanlık bir farkı "etki" diye sunmak abartı olur.
NEGLIGIBLE_SWING_PP = 0.2


@dataclass
class DriverImpact:
    """Tek bir sürgünün batma olasılığı üzerindeki ölçülmüş etkisi."""
    key: str
    label: str
    low_value: float          # fiilen kullanılan alt değer (ondalık)
    high_value: float         # fiilen kullanılan üst değer (ondalık)
    low_probability: float    # alt uçta batma olasılığı (0–1)
    high_probability: float   # üst uçta batma olasılığı (0–1)

    @property
    def swing(self) -> float:
        """Üst uç − alt uç, batma olasılığı puanı olarak (0–1 ölçeğinde)."""
        return self.high_probability - self.low_probability

    @property
    def swing_pp(self) -> float:
        """Swing'in yüzde puanı hâli — arayüz ve raporun konuştuğu birim."""
        return self.swing * 100

    @property
    def negligible(self) -> bool:
        """Bu sürgü şu ayarlarda pratikte ölü mü?"""
        return abs(self.swing_pp) < NEGLIGIBLE_SWING_PP


@dataclass
class TornadoResult:
    """Duyarlılık analizinin tamamı; `impacts` etkiye göre büyükten küçüğe."""
    base_probability: float
    impacts: list[DriverImpact]
    delta: float = DEFAULT_DELTA
    n_iter: int = 0

    @property
    def top(self) -> DriverImpact | None:
        """En etkili sürgü (hepsi ölüyse None)."""
        if not self.impacts:
            return None
        first = self.impacts[0]
        return None if first.negligible else first


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def tornado(base: StressParams, delta: float = DEFAULT_DELTA) -> TornadoResult:
    """
    Her stres sürgüsünü tek tek ±`delta` oynatıp batma olasılığındaki değişimi
    ölçer ve etkiye göre sıralar.

    `base` dışındaki her koşu `base`ın tohumunu aynen kullanır (ortak rastgele
    sayılar); dolayısıyla dönen swing'ler karşılaştırılabilir ve tekrarlanabilir.
    Maliyet: sürgü sayısı × 2 + 1 simülasyon (varsayılan ayarlarda ~11 koşu,
    10.000 iterasyonda toplam onlarca milisaniye).
    """
    base_result = run(base)
    impacts: list[DriverImpact] = []

    for drv in DRIVERS:
        current = float(getattr(base, drv.key))
        low_value = _clamp(current - delta, drv.lo, drv.hi)
        high_value = _clamp(current + delta, drv.lo, drv.hi)

        if low_value == high_value:
            # Sürgü sınıra sıkışmış; oynatacak yer yok. Koşu yapmadan sıfır
            # etkiyle geç — sahte bir fark uydurmaktansa "etkisiz" demek dürüst.
            impacts.append(DriverImpact(
                drv.key, drv.label, low_value, high_value,
                base_result.ruin_probability, base_result.ruin_probability))
            continue

        low = run(replace(base, **{drv.key: low_value}))
        high = run(replace(base, **{drv.key: high_value}))
        impacts.append(DriverImpact(
            drv.key, drv.label, low_value, high_value,
            low.ruin_probability, high.ruin_probability))

    # Mutlak etkiye göre sırala: yönü ne olursa olsun "en çok oynatan" başa.
    impacts.sort(key=lambda i: abs(i.swing), reverse=True)

    return TornadoResult(
        base_probability=base_result.ruin_probability,
        impacts=impacts,
        delta=delta,
        n_iter=base.n_iter,
    )
