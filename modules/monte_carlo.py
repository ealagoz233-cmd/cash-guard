"""
monte_carlo.py  —  MODÜL 2: Monte Carlo Kasa Stres Testi
─────────────────────────────────────────────────────────
Amaç: Kasayı sabit sayılarla değil, piyasa oynaklığıyla test etmek. Önümüzdeki
12 ay için on binlerce (10.000–50.000) rastgele ekonomik senaryo üretip her
birinde nakit yolunu simüle eder ve "12 ay içinde kasanın sıfırlanma (batma)
olasılığını" verir.

Stres değişkenleri (her ay, her senaryo için bağımsız çekilir):
    • Gelir düşüşü        : gelir, ortalama `income_drop` kadar düşük bir çarpanla
                            (normal dağılım, oynaklık `volatility`) gerçekleşir.
    • Tahsilat gecikmesi  : her ay `delay_prob` olasılıkla o ayın tahsilatının
                            bir kısmı (`delay_severity`) sonraki aya kayar
                            (bu ay eksilir, gelecek ay eklenir — nakit kayması).
    • Gider artışı        : sabit giderler, kur/enflasyon şokuyla ortalama
                            `expense_inflation` kadar yukarı çarpanla gerçekleşir.

Çekirdek sıralı döngü utils.performance_utils.simulate_paths'e devredilir
(numba varsa JIT, yoksa vektörize NumPy). Bu modül şok matrislerini kurar,
sonuçları özetler (batma olasılığı, yüzdelik bantlar, iflas ayı dağılımı) ve
çizim için örnek yollar döndürür.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from utils.performance_utils import simulate_paths, ACCELERATION

# Fan chart'ta çizilecek örnek yol sayısı. Grafik ne kadarını çizecekse sonuç
# nesnesi o kadarını taşısın diye tek yerde tanımlı: eskiden burada 300 yol
# saklanıyor, app.py'de ilk 150'si çiziliyordu — yarısı boşuna taşınıyordu.
PLOT_SAMPLE_PATHS = 150


@dataclass
class StressParams:
    """Monte Carlo için tüm parametreler (UI sürgülerinden beslenir)."""
    current_cash: float
    monthly_revenue: float           # normal (şoksuz) aylık tahsilat
    monthly_fixed_expense: float     # normal aylık sabit gider
    monthly_debt_service: float      # sabit aylık borç servisi (mevcut + yeni)
    # Stres değişkenleri (ondalık, ör. 0.10 = %10):
    income_drop: float = 0.10        # beklenen ortalama gelir düşüşü
    volatility: float = 0.12         # gelir oynaklığı (std sapma)
    delay_prob: float = 0.30         # bir ayda tahsilat gecikme olasılığı
    delay_severity: float = 0.35     # geciken ayda tahsilatın kayan oranı
    expense_inflation: float = 0.15  # beklenen ortalama gider artışı
    # Simülasyon boyutu:
    months: int = 12
    n_iter: int = 10_000
    seed: int | None = 42


@dataclass
class StressResult:
    """Özetlenmiş Monte Carlo çıktısı."""
    ruin_probability: float                 # 0–1 arası batma olasılığı
    sample_paths: np.ndarray                # (k, months) çizim için örnek yollar
    percentiles: dict = field(default_factory=dict)  # p5/p25/p50/p75/p95 yolları
    ruin_month_hist: np.ndarray = None      # her ay için ilk-temerrüt sayısı
    median_end_cash: float = 0.0            # 12. ay medyan kasa
    p5_end_cash: float = 0.0                # kötü senaryo (p5) 12. ay kasa
    expected_ruin_month: float | None = None
    n_iter: int = 0
    acceleration: str = ACCELERATION


def _build_shock_matrices(p: StressParams, rng: np.random.Generator):
    """
    Gelir ve gider için (n_iter, months) şok matrislerini üretir.

    Stres, 1. aydan itibaren tam güçle binmez; zamana YAYILIR (ramp). Böylece
    erken aylar bugüne yakın, ileri aylar giderek daha stresli olur. Bu hem
    daha gerçekçidir (kriz aniden değil, birikerek gelir) hem de batma
    olasılığının sürgülere anlamlı biçimde duyarlı kalmasını sağlar — aksi
    halde ortalama tek hamlede kayıp senaryoyu %100 batışa saplar.
    """
    n, m = p.n_iter, p.months
    ramp = np.arange(1, m + 1) / m               # (m,) 0<...<=1 doğrusal artış

    # ── Gelir/tahsilat: ortalama çarpan (1 − income_drop·ramp) ────────────
    income_mean = 1.0 - p.income_drop * ramp     # (m,) aya göre kayan ortalama
    income_factor = rng.normal(income_mean, p.volatility, size=(n, m))
    income_factor = np.clip(income_factor, 0.0, None)   # negatif tahsilat olmaz
    revenue = p.monthly_revenue * income_factor

    # ── Tahsilat gecikmesi: bazı aylarda tahsilatın bir kısmı sonraki aya kayar
    delayed = rng.random((n, m)) < p.delay_prob         # bu ay gecikme var mı
    shift = revenue * p.delay_severity * delayed         # kayan tutar
    revenue = revenue - shift                            # bu ay eksilir
    # kayan tutarı bir sonraki aya taşı (son ay ufuk dışına düşer, kaybolur)
    revenue[:, 1:] += shift[:, :-1]

    # ── Gider: ortalama çarpan (1 + expense_inflation·ramp) ───────────────
    exp_mean = 1.0 + p.expense_inflation * ramp
    exp_factor = rng.normal(exp_mean, p.volatility * 0.6, size=(n, m))
    exp_factor = np.clip(exp_factor, 0.0, None)
    expense = p.monthly_fixed_expense * exp_factor

    return revenue, expense


def run(p: StressParams) -> StressResult:
    """Modül 2 ana fonksiyonu: simülasyonu koşturup StressResult döndürür."""
    rng = np.random.default_rng(p.seed)
    revenue, expense = _build_shock_matrices(p, rng)

    # Sıralı nakit yolu (numba/NumPy) — path-dependent temerrüt tespiti
    paths, ruined, ruin_month = simulate_paths(
        p.current_cash, revenue, expense, p.monthly_debt_service
    )

    ruin_prob = float(ruined.mean())

    # ── Yüzdelik bantları (fan chart için) ────────────────────────────────
    pct = np.percentile(paths, [5, 25, 50, 75, 95], axis=0)
    percentiles = {"p5": pct[0], "p25": pct[1], "p50": pct[2],
                   "p75": pct[3], "p95": pct[4]}

    # ── Çizim için örnek ham yollar ───────────────────────────────────────
    k = min(PLOT_SAMPLE_PATHS, p.n_iter)
    idx = rng.choice(p.n_iter, size=k, replace=False)
    sample = paths[idx]

    # ── İflas ayı dağılımı (0-index -> 1..months histogramı) ──────────────
    hist = np.zeros(p.months, dtype=int)
    ruined_months = ruin_month[ruined]
    if ruined_months.size:
        counts = np.bincount(ruined_months, minlength=p.months)
        hist = counts[: p.months]
        expected_ruin_month = float(ruined_months.mean() + 1)  # 1-index'e çevir
    else:
        expected_ruin_month = None

    end_cash = paths[:, -1]
    return StressResult(
        ruin_probability=ruin_prob,
        sample_paths=sample,
        percentiles=percentiles,
        ruin_month_hist=hist,
        median_end_cash=float(np.median(end_cash)),
        p5_end_cash=float(np.percentile(end_cash, 5)),
        expected_ruin_month=expected_ruin_month,
        n_iter=p.n_iter,
        acceleration=ACCELERATION,
    )
