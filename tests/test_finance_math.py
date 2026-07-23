"""
test_finance_math.py  —  Finansal çekirdeğin doğruluk testleri
───────────────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q     (veya: python tests/test_finance_math.py)

Uygulamanın verdiği hüküm ("bu kredi seni 23 ay erken batırır") tamamen bu
matematiğe dayanıyor. Burada üç şey doğrulanır:

  1) KREDİ MATEMATİĞİ  — annüite formülü elle hesaplanmış değerlerle, itfa
     tablosu ise kendi iç tutarlılığıyla (anapara toplamı = kredi, bakiye = 0)
     karşılaştırılır.
  2) MONTE CARLO DEĞİŞMEZLERİ — olasılık [0,1] aralığında mı, aynı tohum aynı
     sonucu veriyor mu, histogram batan senaryo sayısıyla tutuyor mu ve
     "stres artınca batma olasılığı düşemez" davranışı korunuyor mu.
  3) HIZLANDIRMA EŞDEĞERLİĞİ — vektörize NumPy çekirdeği, naif referans döngüyle
     BİREBİR aynı sonucu vermeli. cumsum/argmax optimizasyonu buradaki asıl
     risktir: hızlıdır ama sessizce yanlış olabilir, bu test onu kilitler.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import loan_simulator as ls
from modules import monte_carlo as mc
from utils.performance_utils import HAS_NUMBA, simulate_paths


def _scenario(**over):
    """Testler için makul bir temel senaryo; alanlar kwargs ile ezilir."""
    base = dict(current_cash=4_200_000, monthly_revenue=6_800_000,
                monthly_fixed_expense=5_950_000, existing_debt_service=950_000,
                loan_amount=10_000_000, loan_term_months=24,
                monthly_interest_rate=0.035, horizon_months=24)
    base.update(over)
    return ls.LoanScenario(**base)


def _params(**over):
    base = dict(current_cash=4_200_000, monthly_revenue=6_800_000,
                monthly_fixed_expense=5_950_000, monthly_debt_service=950_000,
                months=12, n_iter=2_000, seed=42)
    base.update(over)
    return mc.StressParams(**base)


def _taze():
    """
    Batma olasılığı önbelleğini boşaltır.

    Bunu çağırmayan bir test, hızlandırmayı ölçtüğünü sanırken kendi önceki
    koşusunun önbellek kaydını okur ve HER ZAMAN geçer — yani asıl doğrulamak
    istediği eşitliği hiç doğrulamaz.
    """
    mc._RUIN_MEMO.clear()


# ══════════════════════════════════════════════════════════════════════════
#  1) Kredi matematiği
# ══════════════════════════════════════════════════════════════════════════
def test_annuity_matches_hand_calculation():
    """A = P·r·(1+r)ⁿ/((1+r)ⁿ−1). 100.000 TL, %1/ay, 12 ay -> 8.884,88 TL."""
    got = ls.monthly_installment(100_000, 0.01, 12)
    assert abs(got - 8884.88) < 0.01, f"taksit {got:.2f}, beklenen 8884.88"


def test_zero_interest_is_straight_division():
    """Faizsiz kredide taksit = anapara / vade, toplam faiz = 0."""
    assert ls.monthly_installment(120_000, 0.0, 12) == 10_000
    sched = ls.amortization_schedule(120_000, 0.0, 12)
    assert sched["interest"].sum() == 0
    assert abs(sched["balance"].iloc[-1]) < 1e-6


def test_amortization_pays_off_exactly():
    """İtfa tablosu: anapara payları krediye eşit olmalı, bakiye sıfırlanmalı."""
    P, r, n = 10_000_000, 0.035, 24
    sched = ls.amortization_schedule(P, r, n)
    assert len(sched) == n
    assert abs(sched["principal"].sum() - P) < 1.0, "anapara toplamı krediyi tutmuyor"
    assert abs(sched["balance"].iloc[-1]) < 1.0, "vade sonunda bakiye sıfır değil"


def test_total_interest_equals_payments_minus_principal():
    """Toplam faiz = ödenen toplam − anapara (bağımsız yoldan doğrulama)."""
    P, r, n = 10_000_000, 0.035, 24
    sched = ls.amortization_schedule(P, r, n)
    assert abs(sched["interest"].sum() - (sched["payment"].sum() - P)) < 1.0


def test_no_loan_is_a_no_op():
    """Kredi 0 ise taksit 0, öteleme etkisi 0 ve iki eğri birebir aynı olmalı."""
    res = ls.simulate(_scenario(loan_amount=0))
    assert res["installment"] == 0
    assert res["total_interest"] == 0
    assert res["relief_months"] == 0
    assert np.allclose(res["df"]["cash_with_loan"], res["df"]["cash_without_loan"])


def test_cash_projection_matches_hand_calculation():
    """Kredisiz nakit yolu: her ay kasa += tahsilat − gider − borç servisi."""
    res = ls.simulate(_scenario(loan_amount=0, horizon_months=3))
    monthly = 6_800_000 - 5_950_000 - 950_000          # −100.000
    expected = [4_200_000 + monthly * t for t in range(4)]  # t=0 başlangıç dahil
    assert list(res["df"]["cash_without_loan"]) == expected


def test_relief_is_negative_when_loan_pulls_default_forward():
    """Borç tuzağı: taksit yükü iflası öne çekiyorsa relief NEGATİF olmalı."""
    res = ls.simulate(_scenario())
    assert res["relief_months"] < 0, "demo senaryosu borç tuzağı göstermeli"
    assert res["default_with_loan"] is not None


def test_higher_interest_never_reduces_installment():
    """Faiz arttıkça taksit azalamaz (monotonluk)."""
    taksitler = [ls.monthly_installment(1_000_000, r, 24)
                 for r in (0.0, 0.01, 0.02, 0.05, 0.08)]
    assert taksitler == sorted(taksitler)


def test_loan_term_longer_than_horizon_is_handled():
    """Vade (60 ay) grafik ufkundan (24 ay) uzunsa taksit tüm ufka uygulanmalı."""
    res = ls.simulate(_scenario(loan_term_months=60, horizon_months=24))
    inst = res["installment"]
    df = res["df"]
    # Her ay: kredili kasa değişimi = kredisiz değişim − taksit
    d_loan = np.diff(df["cash_with_loan"])
    d_base = np.diff(df["cash_without_loan"])
    assert np.allclose(d_loan, d_base - inst)


# ══════════════════════════════════════════════════════════════════════════
#  2) Monte Carlo değişmezleri
# ══════════════════════════════════════════════════════════════════════════
def test_ruin_probability_is_a_probability():
    assert 0.0 <= mc.run(_params()).ruin_probability <= 1.0


def test_same_seed_is_reproducible():
    a, b = mc.run(_params()), mc.run(_params())
    assert a.ruin_probability == b.ruin_probability
    assert np.array_equal(a.percentiles["p50"], b.percentiles["p50"])


def test_summary_only_run_gives_the_identical_probability():
    """
    `full=False` yalnızca boşa yapılan işi (fan chart bantları, örnek yollar,
    dönem sonu istatistikleri) atlar; batma olasılığı BİREBİR aynı kalmalı.

    Tornado ve kredi taraması onlarca koşuyu bu yolda yapıyor. Yol ayrışırsa
    ekrandaki manşet ile o iki panelin tabanı sessizce farklılaşır.
    """
    p = _params()
    _taze()
    hizli = mc.run(p, full=False)     # ÖNCE: önbellekten değil, gerçekten koşsun
    _taze()
    tam = mc.run(p)
    assert hizli.ruin_probability == tam.ruin_probability
    assert hizli.n_iter == tam.n_iter
    # Atlanan alanlar gerçekten üretilmemeli, yoksa tasarruf da yok
    assert hizli.sample_paths.size == 0
    assert hizli.percentiles == {}


# ── Paylaşılan şok matrisleri ve olasılık önbelleği ───────────────────────
def test_shared_shocks_give_the_identical_probability():
    """
    Paylaşılan matrisle koşmak, matrisi kendi kuran koşuyla BİREBİR aynı
    olasılığı vermeli. Kredi taramasının 13 adımının tamamı bu yolda koşuyor;
    yol ayrışırsa tarama eğrisi manşet sayıyla tutmaz hâle gelir.
    """
    p = _params()
    _taze()
    tek_basina = mc.run(p, full=False).ruin_probability
    _taze()
    paylasimli = mc.run(p, full=False, shocks=mc.build_shocks(p)).ruin_probability
    assert paylasimli == tek_basina


def test_shared_shocks_cover_the_two_fields_the_sweep_moves():
    """
    Taramanın oynattığı iki alan (kasa, borç servisi) şoku DEĞİŞTİRMEZ; bu
    yüzden matris paylaşılabilir. Sözleşme buysa, bu iki alan değişince
    paylaşımlı ve tek başına koşu yine aynı sayıyı vermeli.
    """
    taban = _params()
    shocks = mc.build_shocks(taban)
    tasinan = replace(taban, current_cash=taban.current_cash + 10_000_000,
                      monthly_debt_service=taban.monthly_debt_service + 400_000)
    _taze()
    tek_basina = mc.run(tasinan, full=False).ruin_probability
    _taze()
    paylasimli = mc.run(tasinan, full=False, shocks=shocks).ruin_probability
    assert paylasimli == tek_basina


@pytest.mark.parametrize("alan,deger", [
    ("income_drop", 0.25), ("volatility", 0.30), ("delay_prob", 0.55),
    ("delay_severity", 0.60), ("expense_inflation", 0.30),
    ("n_iter", 3_000), ("seed", 7), ("months", 18),
    ("monthly_revenue", 9_000_000), ("monthly_fixed_expense", 1_000_000),
])
def test_shared_shocks_refuse_parameters_they_were_not_built_for(alan, deger):
    """
    Şoku belirleyen bir alan değişmişse eski matris SESSİZCE kullanılmamalı.

    Cezası çöken bir program değil, makul görünen yanlış bir olasılık olurdu:
    kullanıcı hiçbir belirti görmeden başka bir senaryonun sayısına bakardı.
    """
    taban = _params()
    shocks = mc.build_shocks(taban)
    with pytest.raises(ValueError):
        mc.run(replace(taban, **{alan: deger}), full=False, shocks=shocks)


def test_raw_draws_are_exactly_what_numpy_would_have_drawn():
    """
    Şok matrisi iki adıma ayrıldı: ham çekim + parametre dönüşümü. Ayrımın
    dayandığı eşitlik `rng.normal(ortalama, sapma) == ortalama + sapma·z` ve bu
    eşitlik BİREBİR olmalı — yaklaşık değil.

    Bozulursa hiçbir şey çökmez: tornado sessizce başka bir senaryonun
    sayılarını gösterir. Bu yüzden referans, NumPy'ın kendi çağrısıdır.
    """
    p = _params()
    m = p.months
    ramp = np.arange(1, m + 1) / m

    ref = np.random.default_rng(p.seed)
    gelir_ref = np.clip(
        ref.normal(1.0 - p.income_drop * ramp, p.volatility, size=(p.n_iter, m)),
        0.0, None) * p.monthly_revenue
    u_ref = ref.random((p.n_iter, m))
    gider_ref = np.clip(
        ref.normal(1.0 + p.expense_inflation * ramp, p.volatility * 0.6,
                   size=(p.n_iter, m)), 0.0, None) * p.monthly_fixed_expense
    idx_ref = ref.choice(p.n_iter, size=min(mc.PLOT_SAMPLE_PATHS, p.n_iter),
                         replace=False)

    yeni = np.random.default_rng(p.seed)
    d = mc.build_draws(p, yeni)
    gelir, gider = mc._build_shock_matrices(p, d)
    idx = yeni.choice(p.n_iter, size=min(mc.PLOT_SAMPLE_PATHS, p.n_iter),
                      replace=False)

    # Gecikme kayması gelire binmiş durumda; ham gelirin kendisi yerine
    # üreticinin AYNI noktada olduğunu ve gider ile uniform'un tuttuğunu ara.
    assert np.array_equal(d.delay_u, u_ref)
    assert np.array_equal(gider, gider_ref)
    assert np.array_equal(idx, idx_ref), (
        "üretici farklı noktada kaldı: full=True koşusunun çizdiği örnek "
        "yollar sessizce değişirdi")
    kayma = gelir_ref * p.delay_severity * (u_ref < p.delay_prob)
    beklenen = gelir_ref - kayma
    beklenen[:, 1:] += kayma[:, :-1]
    assert np.array_equal(gelir, beklenen)


@pytest.mark.parametrize("alan,deger", [
    ("income_drop", 0.25), ("volatility", 0.30), ("delay_prob", 0.55),
    ("delay_severity", 0.60), ("expense_inflation", 0.30),
    ("current_cash", 9_000_000), ("monthly_debt_service", 1_400_000),
    ("monthly_revenue", 7_400_000), ("monthly_fixed_expense", 5_100_000),
])
def test_shared_draws_serve_every_slider_the_tornado_moves(alan, deger):
    """
    Ham çekim yalnızca boyuta (n_iter, months, seed) bağlıdır; hiçbir stres
    sürgüsüne değil. Sözleşme buysa, paylaşılan çekimle koşan bir senaryo kendi
    çekimini yapanla BİREBİR aynı olasılığı vermeli.

    Tornado'nun on bir koşusunun tamamı bu yolda; ayrışırsa barlar manşet
    sayının ölçtüğünden başka bir şeyi ölçer.
    """
    taban = _params()
    draws = mc.build_draws(taban)
    senaryo = replace(taban, **{alan: deger})
    _taze()
    tek_basina = mc.run(senaryo, full=False).ruin_probability
    _taze()
    paylasimli = mc.run(senaryo, full=False, draws=draws).ruin_probability
    assert paylasimli == tek_basina


@pytest.mark.parametrize("alan,deger", [
    ("n_iter", 3_000), ("months", 18), ("seed", 7),
])
def test_shared_draws_refuse_a_different_shape(alan, deger):
    """Boyutu belirleyen üç alan değiştiyse eski çekim sessizce kullanılmamalı."""
    taban = _params()
    draws = mc.build_draws(taban)
    with pytest.raises(ValueError):
        mc.run(replace(taban, **{alan: deger}), full=False, draws=draws)


def test_probability_memo_returns_what_a_fresh_run_computes():
    """Önbellekten dönen sayı, yeniden hesaplananla birebir aynı olmalı."""
    p = _params(current_cash=3_000_000)
    _taze()
    ilk = mc.run(p, full=False).ruin_probability
    onbellekten = mc.run(p, full=False).ruin_probability
    _taze()
    yeniden = mc.run(p, full=False).ruin_probability
    assert onbellekten == ilk == yeniden


def test_full_run_fills_the_memo_for_the_summary_path():
    """
    Manşet koşusu (full=True) ile tornado'nun tabanı ve taramanın sıfır noktası
    AYNI senaryoyu ölçüyor. Manşet sonucu önbelleğe yazmazsa aynı hesap üç kez
    yapılır.
    """
    p = _params(current_cash=3_300_000)
    _taze()
    tam = mc.run(p)
    assert mc._RUIN_MEMO, "full=True koşusu önbelleğe yazmalı"
    assert mc.run(p, full=False).ruin_probability == tam.ruin_probability


def test_memo_stays_out_of_the_way_when_there_is_no_seed():
    """
    Tohumsuz koşu tanımı gereği her seferinde farklıdır; önbelleğe alınırsa
    kullanıcı rastgeleliği kapatmadığı hâlde donmuş bir sayı görür.
    """
    p = _params(seed=None)
    _taze()
    mc.run(p, full=False)
    assert not mc._RUIN_MEMO


def test_ruin_histogram_matches_ruined_count():
    """Histogram toplamı, batan senaryo sayısına birebir eşit olmalı."""
    p = _params()
    res = mc.run(p)
    assert res.ruin_month_hist.sum() == round(res.ruin_probability * p.n_iter)


def test_expected_ruin_month_within_horizon():
    res = mc.run(_params())
    if res.expected_ruin_month is not None:
        assert 1 <= res.expected_ruin_month <= _params().months


def test_percentile_bands_are_ordered():
    """p5 ≤ p25 ≤ p50 ≤ p75 ≤ p95 — fan chart bantları kesişmemeli."""
    pcs = mc.run(_params()).percentiles
    for lo, hi in (("p5", "p25"), ("p25", "p50"), ("p50", "p75"), ("p75", "p95")):
        assert np.all(pcs[lo] <= pcs[hi]), f"{lo} > {hi}"


def test_sample_paths_shape_matches_plot_constant():
    """Sonuç, grafiğin çizeceği kadar yol taşımalı — ne eksik ne fazla."""
    p = _params()
    res = mc.run(p)
    assert res.sample_paths.shape == (min(mc.PLOT_SAMPLE_PATHS, p.n_iter), p.months)


def test_more_stress_never_lowers_ruin_probability():
    """
    Davranışsal değişmez: aynı tohumla gelir düşüşü arttıkça batma olasılığı
    azalamaz. Sürgüler ters bağlanırsa (bir kez olabilecek bir hata) bu kırılır.
    """
    olasiliklar = [mc.run(_params(income_drop=d)).ruin_probability
                   for d in (0.0, 0.10, 0.25, 0.40)]
    assert olasiliklar == sorted(olasiliklar), olasiliklar


def test_healthy_company_never_ruins():
    """Kasası dolu, nakit üreten şirket batmamalı (yanlış pozitif kontrolü)."""
    res = mc.run(_params(current_cash=500_000_000, monthly_revenue=20_000_000,
                         monthly_fixed_expense=5_000_000, monthly_debt_service=0,
                         income_drop=0.0, volatility=0.05, expense_inflation=0.0))
    assert res.ruin_probability == 0.0
    assert res.expected_ruin_month is None


# ══════════════════════════════════════════════════════════════════════════
#  3) Hızlandırılmış çekirdek = naif referans
# ══════════════════════════════════════════════════════════════════════════
def _reference_paths(cash0, revenue, expense, debt_service):
    """Okunması kolay, optimize edilmemiş referans: doğruluğun ölçütü budur."""
    n, m = revenue.shape
    paths = np.zeros((n, m))
    ruined = np.zeros(n, dtype=bool)
    ruin_month = np.full(n, -1, dtype=np.int32)
    for i in range(n):
        cash = cash0
        for t in range(m):
            cash += revenue[i, t] - expense[i, t] - debt_service
            paths[i, t] = cash
            if cash <= 0.0 and ruin_month[i] == -1:
                ruin_month[i] = t
                ruined[i] = True
    return paths, ruined, ruin_month


def test_vectorized_kernel_matches_reference():
    """
    cumsum/argmax hilesi ile naif döngü aynı sonucu vermeli.

    'Batıştan sonra toparlanan' senaryolar özellikle test edilir: bunlar
    optimizasyonun sessizce yanlış olabileceği tek yerdir.
    """
    rng = np.random.default_rng(7)
    revenue = rng.normal(6_800_000, 1_500_000, size=(500, 12))
    expense = rng.normal(5_950_000, 400_000, size=(500, 12))
    args = (4_200_000, revenue, expense, 950_000.0)

    got_paths, got_ruined, got_month = simulate_paths(*args)
    exp_paths, exp_ruined, exp_month = _reference_paths(*args)

    assert np.allclose(got_paths, exp_paths)
    assert np.array_equal(got_ruined, exp_ruined)
    assert np.array_equal(got_month, exp_month)
    # Testin gerçekten ilginç bir veri kümesi gördüğünü doğrula:
    assert exp_ruined.any() and not exp_ruined.all(), "veri kümesi ayrıştırıcı değil"
    toparlayan = ((exp_paths[:, -1] > 0) & exp_ruined).sum()
    assert toparlayan > 0, "batıp toparlanan senaryo yok — asıl uç durum test edilmedi"


def test_kernel_and_numpy_agree():
    """
    Sıralı çekirdek ile vektörize NumPy sürümü aynı sonucu vermeli.

    Bu test HER ZAMAN koşar, numba kurulu olmasa bile: @njit dekoratörü numba
    yokken no-op'a döner, yani _simulate_paths_kernel saf Python olarak çalışır.
    Eskiden numba yoksa sessizce atlanıyordu ve çekirdek hiç koşmadan repoda
    duruyordu — 300x12'lik veri kümesi saf Python'da da anlık bittiği için
    atlamanın hiçbir gerekçesi yokmuş.

    numba KURULUYSA aynı test derlenmiş yolu doğrular; ikisi arasındaki tek
    fark hız, sonuç bit-bit aynı olmalı.
    """
    from utils.performance_utils import _simulate_paths_kernel, _simulate_paths_numpy
    rng = np.random.default_rng(11)
    rev = np.ascontiguousarray(rng.normal(6_800_000, 1_500_000, size=(300, 12)))
    exp = np.ascontiguousarray(rng.normal(5_950_000, 400_000, size=(300, 12)))
    a = _simulate_paths_kernel(4_200_000.0, rev, exp, 950_000.0)
    b = _simulate_paths_numpy(4_200_000.0, rev, exp, 950_000.0)
    assert np.allclose(a[0], b[0]), "nakit yolları uyuşmuyor"
    assert np.array_equal(a[1], b[1]), "batan senaryo maskesi uyuşmuyor"
    assert np.array_equal(a[2], b[2]), "ilk temerrüt ayı uyuşmuyor"
    # Veri kümesi ayrıştırıcı mı — hepsi battıysa test hiçbir şey kanıtlamaz.
    assert a[1].any() and not a[1].all(), "veri kümesi ayrıştırıcı değil"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
                passed += 1
            except AssertionError as e:
                print(f"  ✗ {name}\n      {e}")
                failed += 1
    print(f"\n{passed} geçti, {failed} kaldı")
    sys.exit(1 if failed else 0)
