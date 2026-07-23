"""
loan_sweep.py  —  "Kaç lira?" sorusunun cevabı: kredi tutarı taraması
─────────────────────────────────────────────────────────────────────
Uygulamanın merkez sorusu "kredi çekeyim mi?" ama arayüz bunu TEK NOKTADA
cevaplıyordu: sürgüyü 10 milyona çekiyorsun, o tutarın sonucunu görüyorsun.
Doğru soru ise iki katmanlı — *çekmeli miyim* ve *kaç lira*. İkincisi sürgüyü
elle 12 kez oynatarak aranıyordu ve aradaki eğri hiç görünmüyordu.

Bu modül tutarı sıfırdan üst sınıra kadar tarar ve her tutar için iki farklı
soruyu birlikte cevaplar:

  • **12 ay stokastik:** o tutarla batma olasılığı ne? (Monte Carlo)
  • **Uzun vade deterministik:** o tutar iflası öteliyor mu, öne mi çekiyor?
    (`relief_months`, işaretli)

İkisini AYNI tabloda tutmak bu modülün asıl amacı. Yalnızca batma olasılığına
bakan bir "optimizasyon" tam da uygulamanın uyardığı hatayı yapardı: nakit
enjeksiyonu ilk 12 ayı neredeyse her zaman rahatlatır, taksit yükü ise daha
sonra vurur. Bu yüzden en düşük riskli tutar aynı zamanda bir borç tuzağı
olabilir ve `SweepResult.best_is_a_trap` bunu ayrıca işaretler.

ORTAK RASTGELE SAYILAR: bütün tutarlar aynı tohumla koşulur. Aksi hâlde eğri
Monte Carlo gürültüsüyle tırtıklı çıkar ve "en iyi tutar" koşudan koşuya
değişir — kullanıcı da rastgeleliği sinyal sanar.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from modules import loan_simulator as ls
from modules import scenario
from modules.monte_carlo import StressParams, run

# Arayüzdeki kredi sürgüsünün üst sınırı — tek kaynaktan okunur, kopyalanmaz.
DEFAULT_MAX_AMOUNT = scenario.sinirlar("kredi")[1]
DEFAULT_STEPS = 13

# Bu eşiğin altındaki iyileşme "gürültü" sayılır. 10.000 iterasyonda Monte
# Carlo'nun kendi standart hatası ~0.5 puan; ortak rastgele sayılar bunu büyük
# ölçüde götürse de yarım puanlık bir kazanç için milyonlarca lira borçlanmayı
# "en iyi seçenek" diye sunmak sorumsuzluk olurdu.
MEANINGFUL_GAIN_PP = 1.0


@dataclass(frozen=True)
class SweepPoint:
    """Tek bir kredi tutarının iki ayrı ufuktaki sonucu."""
    amount: float
    installment: float
    total_interest: float
    ruin_probability: float          # 12 ay, stokastik
    relief_months: int               # işaretli: + öteler, − öne çeker
    default_with_loan: int | None    # deterministik iflas ayı (24 ay ufkunda)

    @property
    def ruin_pct(self) -> float:
        return self.ruin_probability * 100

    @property
    def is_trap(self) -> bool:
        """Para alındı ama iflas öne çekildi mi?"""
        return self.amount > 0 and self.relief_months < 0


@dataclass
class SweepResult:
    """Tarama sonucu; `points` tutara göre artan sırada."""
    points: list[SweepPoint] = field(default_factory=list)
    n_iter: int = 0

    @property
    def baseline(self) -> SweepPoint | None:
        """Kredisiz hâl (tutar = 0). Her karşılaştırmanın çıpası."""
        return next((p for p in self.points if p.amount == 0), None)

    @property
    def best(self) -> SweepPoint | None:
        """12 aylık batma olasılığını en aza indiren tutar."""
        return min(self.points, key=lambda p: p.ruin_probability) if self.points else None

    @property
    def gain_pp(self) -> float:
        """En iyi tutarın kredisiz hâle göre kazandırdığı puan (pozitif = iyileşme)."""
        b, z = self.best, self.baseline
        if b is None or z is None:
            return 0.0
        return (z.ruin_probability - b.ruin_probability) * 100

    @property
    def borrowing_helps(self) -> bool:
        """
        Borçlanmak 12 ayda anlamlı bir iyileşme sağlıyor mu?

        "En iyi" tutar her zaman vardır (bir minimum bulunur); önemli olan o
        minimumun kredisiz hâlden ölçülebilir biçimde iyi olup olmadığıdır.
        """
        b = self.best
        return b is not None and b.amount > 0 and self.gain_pp >= MEANINGFUL_GAIN_PP

    @property
    def best_is_a_trap(self) -> bool:
        """
        12 ayın en iyisi, uzun vadede iflası ÖNE çekiyor mu?

        Nakit enjeksiyonu ilk 12 ayı neredeyse her zaman rahatlatır; taksit yükü
        sonra vurur. Bu yüzden "en düşük riskli tutar" bir tuzak olabilir ve
        arayüz bunu söylemeden o tutarı önermemelidir.
        """
        b = self.best
        return bool(b and b.is_trap and self.borrowing_helps)


def amount_grid(max_amount: float = DEFAULT_MAX_AMOUNT,
                steps: int = DEFAULT_STEPS) -> list[float]:
    """
    Taranacak tutarlar. Sıfır HER ZAMAN dahildir: "hiç çekmemek" bir seçenek
    değil, karşılaştırmanın çıpasıdır.
    """
    steps = max(2, int(steps))
    adim = float(max_amount) / (steps - 1)
    return [round(i * adim) for i in range(steps)]


def sweep(
    stress: StressParams,
    loan: ls.LoanScenario,
    max_amount: float = DEFAULT_MAX_AMOUNT,
    steps: int = DEFAULT_STEPS,
) -> SweepResult:
    """
    Kredi tutarını tarar.

    `stress` KREDİSİZ taban olmalıdır (kasa = mevcut kasa, borç servisi = mevcut
    servis); tutara göre kasa ve taksit burada eklenir. `loan` ise vade ve faizi
    taşıyan şablondur — içindeki `loan_amount` yok sayılır, her adımda ezilir.

    Maliyet: adım sayısı kadar Monte Carlo (varsayılan 13). Hepsi aynı tohumu
    paylaşır, dolayısıyla eğri gürültüsüz ve tekrarlanabilirdir.
    """
    noktalar: list[SweepPoint] = []

    for tutar in amount_grid(max_amount, steps):
        det = ls.simulate(replace(loan, loan_amount=float(tutar)))
        taksit = det["installment"]

        # `full=False`: tarama yalnızca batma olasılığını okuyor; fan chart
        # bantları ve örnek yollar 13 koşuda da üretilip atılıyordu.
        mc = run(replace(
            stress,
            current_cash=stress.current_cash + tutar,
            monthly_debt_service=stress.monthly_debt_service + taksit,
        ), full=False)

        noktalar.append(SweepPoint(
            amount=float(tutar),
            installment=taksit,
            total_interest=det["total_interest"],
            ruin_probability=mc.ruin_probability,
            relief_months=det["relief_months"],
            default_with_loan=det["default_with_loan"],
        ))

    return SweepResult(points=noktalar, n_iter=stress.n_iter)
