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


# Şok matrislerini BELİRLEYEN alanlar. Buradaki her alan matrisin içeriğine
# girer; listede OLMAYAN iki alan (`current_cash`, `monthly_debt_service`)
# yalnızca nakit yolunu kaydırır, şoku değiştirmez. Kredi taraması tam da bu iki
# alanı oynattığı için 13 koşunun hepsi AYNI şoku kullanabilir.
SHOCK_FIELDS = (
    "monthly_revenue", "monthly_fixed_expense",
    "income_drop", "volatility", "delay_prob", "delay_severity",
    "expense_inflation", "months", "n_iter", "seed",
)


def _shock_signature(p: StressParams) -> tuple:
    return tuple(getattr(p, f) for f in SHOCK_FIELDS)


@dataclass(frozen=True)
class Shocks:
    """
    Bir kez kurulup birden çok koşuda paylaşılabilen şok matrisleri.

    `signature` bilerek taşınıyor: yanlış parametrelerle kurulmuş bir matrisi
    yeniden kullanmak sessizce YANLIŞ bir batma olasılığı üretirdi ve hiçbir
    test bunu yakalayamazdı — çünkü sonuç hâlâ makul görünen bir sayı olurdu.
    `run` imzayı karşılaştırır ve uymuyorsa istisna atar.
    """
    revenue: np.ndarray
    expense: np.ndarray
    signature: tuple

    def matches(self, p: StressParams) -> bool:
        return self.signature == _shock_signature(p)


# ── Ham çekimler: parametrelerden BAĞIMSIZ olan kısım ─────────────────────
# Şok matrisi iki adımdır: (1) rastgele sayıları çek, (2) parametrelerle
# dönüştür. `rng.normal(ortalama, sapma)` aslında `ortalama + sapma·z` demek ve
# aynı tohumda AYNI z'leri kullanır — yani çekim yalnızca (n_iter, months, seed)
# üçlüsüne bağlıdır, hiçbir stres sürgüsüne değil.
#
# Bunu ayırmanın kazancı tornado'da: beş sürgünün on bir koşusu bugüne kadar
# milyonlarca rastgele sayıyı on bir kez üretiyordu, oysa hepsi aynı çekimi
# paylaşabilir. Dahası, modülün baştan beri verdiği "ortak rastgele sayılar"
# sözü artık bir yan etki değil, kodda görünen bir yapı.
DRAW_FIELDS = ("months", "n_iter", "seed")


def _draw_signature(p: StressParams) -> tuple:
    return tuple(getattr(p, f) for f in DRAW_FIELDS)


@dataclass(frozen=True)
class Draws:
    """Ham rastgele çekimler; hiçbir stres parametresi içermez."""
    income_z: np.ndarray      # gelir şoku için standart normaller
    delay_u: np.ndarray       # gecikme kurası için düzgün dağılım
    expense_z: np.ndarray     # gider şoku için standart normaller
    signature: tuple

    def matches(self, p: StressParams) -> bool:
        return self.signature == _draw_signature(p)


def build_draws(p: StressParams,
                rng: np.random.Generator | None = None) -> Draws:
    """
    Ham çekimleri üretir. Sıra ÖNEMLİ: gelir → gecikme → gider.

    `rng` dışarıdan verilirse tüketim onun üzerinden yapılır ve akış tam olarak
    eski koddaki kadar ilerler; böylece `run` içindeki örnek yol seçimi (aynı
    üreticiden çekilir) bit düzeyinde aynı kalır.
    """
    rng = np.random.default_rng(p.seed) if rng is None else rng
    boyut = (p.n_iter, p.months)
    return Draws(
        income_z=rng.standard_normal(boyut),
        delay_u=rng.random(boyut),
        expense_z=rng.standard_normal(boyut),
        signature=_draw_signature(p),
    )


def build_shocks(p: StressParams, draws: Draws | None = None) -> Shocks:
    """
    Şok matrislerini bir kez kurup paylaşılabilir hâlde döndürür.

    Kredi taraması varsayılan 13 tutarı dener ve her tutarda yalnızca kasa ile
    taksit değişir; şok matrisleri her seferinde sıfırdan üretiliyordu. Ölçüm:
    50.000 iterasyonda tarama süresinin dörtte üçünden fazlası bu tekrar eden
    matris kurulumuydu (rastgele sayı üretimi + kırpma), sıralı nakit döngüsü
    değil.

    `draws` verilirse ham çekim de atlanır — sürgüsü değişen ama boyutu aynı
    kalan koşular (tornado) için.
    """
    if draws is not None and not draws.matches(p):
        raise ValueError(
            "Paylaşılan ham çekimler bu boyutta üretilmemiş. "
            f"Eşleşmesi gereken alanlar: {', '.join(DRAW_FIELDS)}."
        )
    d = build_draws(p) if draws is None else draws
    revenue, expense = _build_shock_matrices(p, d)
    return Shocks(revenue=revenue, expense=expense, signature=_shock_signature(p))


def _build_shock_matrices(p: StressParams, draws: Draws):
    """
    Gelir ve gider için (n_iter, months) şok matrislerini üretir.

    Stres, 1. aydan itibaren tam güçle binmez; zamana YAYILIR (ramp). Böylece
    erken aylar bugüne yakın, ileri aylar giderek daha stresli olur. Bu hem
    daha gerçekçidir (kriz aniden değil, birikerek gelir) hem de batma
    olasılığının sürgülere anlamlı biçimde duyarlı kalmasını sağlar — aksi
    halde ortalama tek hamlede kayıp senaryoyu %100 batışa saplar.
    """
    m = p.months
    ramp = np.arange(1, m + 1) / m               # (m,) 0<...<=1 doğrusal artış

    # Aşağıdaki adımlar bilerek YERİNDE (in-place) yazıldı. Her `a = b * c`
    # satırı (n_iter, months) boyutunda yeni bir dizi ayırıyor; 50.000
    # iterasyonda bu dizilerin her biri ~4,8 MB. Deyimsel hâlinde on kadar
    # geçici dizi kuruluyordu ve tornado bunu on bir kez tekrarlıyordu — hesabın
    # kendisi değil, bellek trafiği baskındı.
    #
    # Çarpanların sırası değişse de sonuç BİREBİR aynı kalır (IEEE754'te çarpma
    # değişmeli) ve `test_raw_draws_are_exactly_what_numpy_would_have_drawn`
    # sonucu NumPy'ın kendi `rng.normal` çağrısıyla karşılaştırarak bunu
    # kilitliyor.

    # ── Gelir/tahsilat: ortalama çarpan (1 − income_drop·ramp) ────────────
    # `rng.normal(ortalama, sapma)` ile BİREBİR aynı: NumPy de içeride tam
    # olarak `ortalama + sapma·z` hesaplıyor. Ayrı yazmanın sebebi z'yi
    # paylaşılabilir kılmak (bkz. Draws).
    income_mean = 1.0 - p.income_drop * ramp     # (m,) aya göre kayan ortalama
    revenue = draws.income_z * p.volatility
    revenue += income_mean
    np.clip(revenue, 0.0, None, out=revenue)     # negatif tahsilat olmaz
    revenue *= p.monthly_revenue

    # ── Tahsilat gecikmesi: bazı aylarda tahsilatın bir kısmı sonraki aya kayar
    shift = revenue * p.delay_severity                 # kayan tutar (aday)
    shift *= draws.delay_u < p.delay_prob              # gecikme yoksa sıfırlanır
    revenue -= shift                                     # bu ay eksilir
    # kayan tutarı bir sonraki aya taşı (son ay ufuk dışına düşer, kaybolur)
    revenue[:, 1:] += shift[:, :-1]

    # ── Gider: ortalama çarpan (1 + expense_inflation·ramp) ───────────────
    exp_mean = 1.0 + p.expense_inflation * ramp
    expense = draws.expense_z * (p.volatility * 0.6)
    expense += exp_mean
    np.clip(expense, 0.0, None, out=expense)
    expense *= p.monthly_fixed_expense

    return revenue, expense


# Yalnızca-özet koşuların batma olasılığı burada saklanır. Anahtar parametre
# demetinin TAMAMI olduğu için yanlış eşleşme mümkün değil; tohum sabitken koşu
# deterministik olduğundan önbellekten dönen sayı yeniden hesaplananla birebir
# aynıdır. Kazanç: manşet Monte Carlo, tornado'nun tabanı ve taramanın sıfır
# noktası AYNI senaryoyu ölçüyor ve üç kez koşuyordu; artık bir kez koşuyor.
#
# Bellekte yalnızca birer float durur (matris değil), o yüzden tavan cömert.
_RUIN_MEMO: dict[tuple, float] = {}
_MEMO_MAX = 512


def _summary_result(ruin_prob: float, p: StressParams) -> StressResult:
    """Yalnızca-özet koşunun döndürdüğü nesne (tek yerde kurulsun)."""
    return StressResult(
        ruin_probability=ruin_prob,
        sample_paths=np.empty((0, p.months)),
        n_iter=p.n_iter,
        acceleration=ACCELERATION,
    )


def _memo_key(p: StressParams) -> tuple | None:
    """Tohum yoksa koşu deterministik değildir; önbelleğe girmemeli."""
    if p.seed is None:
        return None
    return (*_shock_signature(p), p.current_cash, p.monthly_debt_service)


def run(p: StressParams, full: bool = True,
        shocks: Shocks | None = None,
        draws: Draws | None = None) -> StressResult:
    """
    Modül 2 ana fonksiyonu: simülasyonu koşturup StressResult döndürür.

    `full=False` yalnızca batma olasılığını hesaplar; fan chart bantları, örnek
    yollar ve dönem sonu kasa istatistikleri atlanır. Tornado ve kredi taraması
    onlarca koşu yapıyor ve bu alanların HİÇBİRİNİ okumuyor — 50.000 iterasyonda
    yüzdelik hesabı tek başına ~20 ms, yani boşa yapılan iş toplam sürenin
    dörtte birine yaklaşıyordu.

    Paylaşımın iki kademesi var:
      • `shocks` — matrisler hazır (tarama: yalnızca kasa ve taksit değişiyor).
      • `draws`  — ham çekimler hazır, dönüşüm burada yapılıyor (tornado:
        sürgüler değişiyor ama boyut aynı).
    İkisinin de parametrelerle uyuştuğu doğrulanır: uyuşmazlığın cezası çöken
    bir program değil, makul görünen yanlış bir olasılık olurdu.

    Batma olasılığı bütün yollarda BİREBİR aynıdır: atlanan adımların hepsi
    `ruined` hesaplandıktan sonra gelir; paylaşılan çekimler de aynı tohumdan
    ve aynı sırayla üretilmiştir.
    """
    if shocks is not None and not shocks.matches(p):
        raise ValueError(
            "Paylaşılan şok matrisleri bu parametrelerle kurulmamış. "
            "Yalnızca current_cash ve monthly_debt_service değişebilir; "
            "diğer alanlardan biri değiştiyse build_shocks yeniden çağrılmalı."
        )
    if draws is not None and not draws.matches(p):
        raise ValueError(
            "Paylaşılan ham çekimler bu boyutta üretilmemiş. "
            f"Eşleşmesi gereken alanlar: {', '.join(DRAW_FIELDS)}."
        )

    memo_key = _memo_key(p)
    # Yalnızca özet isteniyorsa önbellek cevabı tamdır; `full=True` zaten
    # dizileri de üretmek zorunda olduğu için koşuyu atlayamaz — ama sonucunu
    # önbelleğe YAZAR, çünkü manşet koşunun ölçtüğü olasılığı tornado ile
    # tarama da soruyor.
    if not full and memo_key is not None and memo_key in _RUIN_MEMO:
        return _summary_result(_RUIN_MEMO[memo_key], p)

    rng = np.random.default_rng(p.seed)
    if shocks is not None:
        revenue, expense = shocks.revenue, shocks.expense
    else:
        # `draws` verilmemişse üretici BURADAN besleniyor; böylece akış tam
        # olarak eski koddaki kadar ilerler ve aşağıdaki örnek yol seçimi bit
        # düzeyinde aynı kalır.
        d = build_draws(p, rng) if draws is None else draws
        revenue, expense = _build_shock_matrices(p, d)

    # Sıralı nakit yolu (numba/NumPy) — path-dependent temerrüt tespiti
    paths, ruined, ruin_month = simulate_paths(
        p.current_cash, revenue, expense, p.monthly_debt_service
    )

    ruin_prob = float(ruined.mean())
    if memo_key is not None:
        if len(_RUIN_MEMO) >= _MEMO_MAX:
            _RUIN_MEMO.clear()       # LRU'ya gerek yok: tavan zaten çok uzakta
        _RUIN_MEMO[memo_key] = ruin_prob
    if not full:
        return _summary_result(ruin_prob, p)

    # ── Yüzdelik bantları (fan chart için) ────────────────────────────────
    pct = np.percentile(paths, [5, 25, 50, 75, 95], axis=0)
    percentiles = {"p5": pct[0], "p25": pct[1], "p50": pct[2],
                   "p75": pct[3], "p95": pct[4]}

    # ── Çizim için örnek ham yollar ───────────────────────────────────────
    # `rng` matris kurulumundan sonraki konumunda; paylaşılan matris verilmişse
    # o adım atlandığı için ÇEKİLEN 150 yolun kimlikleri farklı olur. İstatistik
    # değil çizim örneği olduğu için bu bir fark değil, aynı dağılımdan başka bir
    # örnek — batma olasılığı, bantlar ve dönem sonu kasa aynen aynı kalır.
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
