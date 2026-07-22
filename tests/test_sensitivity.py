"""
test_sensitivity.py  —  Tornado (duyarlılık) analizinin testleri
────────────────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q   (veya: python tests/test_sensitivity.py)

Tornado'nun tek işi sıralama yapmak: "en çok şu sürgü oynatıyor". Sıralama
yanlışsa kullanıcı yanlış yere yatırım yapar, üstelik bunu fark etmesi imkânsızdır
— çıktı hep makul görünür. Bu yüzden burada üç şey korunuyor:

  1. Sürgü sınırları arayüzle ayrışmasın (ulaşılamayan değer önerilmesin).
  2. Ortak rastgele sayılar korunsun (aynı girdi → aynı sayı, aynı sıra).
  3. Ekonomik yön doğru olsun (gider artışı riski artırır, azaltmaz).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import scenario
from modules.monte_carlo import StressParams, run
from modules.sensitivity import (DEFAULT_DELTA, DRIVERS, NEGLIGIBLE_SWING_PP,
                                 tornado)


def _params(**over) -> StressParams:
    """Demo şirketine yakın, testte hızlı koşan bir taban senaryo."""
    base = dict(
        current_cash=4_200_000,
        monthly_revenue=6_800_000,
        monthly_fixed_expense=5_950_000,
        monthly_debt_service=950_000,
        income_drop=0.06, volatility=0.10,
        delay_prob=0.30, delay_severity=0.25,
        expense_inflation=0.10,
        months=12, n_iter=4_000, seed=42,
    )
    base.update(over)
    return StressParams(**base)


# ── Arayüzle sınır uyumu ──────────────────────────────────────────────────
def test_driver_bounds_match_the_real_sliders():
    """
    Tornado'nun oynattığı aralık, kullanıcının sürgüde gerçekten ayarlayabildiği
    aralık olmalı. Ayrışırsa "gideri %45'e çıkarsa" gibi ulaşılamayan bir uç
    gösterilir ve öneri uygulanamaz hâle gelir.
    """
    alanlar = {a.anahtar: a for a in scenario.ALANLAR}
    for drv in DRIVERS:
        alan = alanlar[drv.url_key]           # eşleşme yoksa KeyError: kasıtlı
        assert drv.lo == alan.alt / 100.0, f"{drv.key} alt sınırı sürgüden farklı"
        assert drv.hi == alan.ust / 100.0, f"{drv.key} üst sınırı sürgüden farklı"


def test_every_driver_is_a_real_stress_param():
    """Sürgü adı StressParams'ta yoksa `replace` sessizce değil, gürültüyle patlar."""
    p = _params()
    for drv in DRIVERS:
        assert hasattr(p, drv.key), f"StressParams'ta {drv.key} alanı yok"


# ── Sözleşme ──────────────────────────────────────────────────────────────
def test_returns_one_impact_per_driver():
    res = tornado(_params())
    assert len(res.impacts) == len(DRIVERS)
    assert {i.key for i in res.impacts} == {d.key for d in DRIVERS}


def test_impacts_are_sorted_by_absolute_swing():
    """Tornado grafiğinin tamamı bu sıralamaya dayanıyor."""
    res = tornado(_params())
    swings = [abs(i.swing) for i in res.impacts]
    assert swings == sorted(swings, reverse=True)


def test_base_probability_equals_a_plain_monte_carlo_run():
    """
    Tornado'nun tabanı, aynı parametrelerle koşulan normal simülasyonun ta
    kendisi olmalı. Olmazsa arayüzde manşet sayı ile tornado'nun ortası farklı
    çıkar ve kullanıcı hangisine inanacağını bilemez.
    """
    p = _params()
    assert tornado(p).base_probability == run(p).ruin_probability


def test_is_reproducible():
    """Ortak rastgele sayılar: iki koşu bit-bit aynı olmalı."""
    a, b = tornado(_params()), tornado(_params())
    assert a.base_probability == b.base_probability
    assert [(i.key, i.swing) for i in a.impacts] == [(i.key, i.swing) for i in b.impacts]


# ── Ekonomik yön ──────────────────────────────────────────────────────────
def test_expense_inflation_pushes_risk_up_not_down():
    """Gider artışı batma olasılığını artırmalı; ters çıkarsa model bozuktur."""
    res = tornado(_params())
    exp = next(i for i in res.impacts if i.key == "expense_inflation")
    assert exp.swing > 0, "gider artışı riski düşürüyor görünüyor"
    assert exp.low_probability <= exp.high_probability


def test_income_drop_pushes_risk_up_not_down():
    res = tornado(_params())
    inc = next(i for i in res.impacts if i.key == "income_drop")
    assert inc.swing > 0, "gelir düşüşü riski düşürüyor görünüyor"


# ── Kırpma dürüstlüğü ─────────────────────────────────────────────────────
def test_clamps_to_slider_bounds_and_reports_the_value_actually_used():
    """
    Taban değer sınırdaysa ±delta simetrik uygulanamaz. Kırpılan uç, sonuçta
    FİİLEN kullanılan değer olarak görünmeli — "5 puan oynattım" deyip 0 puan
    oynatmak sessiz bir yalan olurdu.
    """
    res = tornado(_params(income_drop=0.0))         # sürgü en solda
    inc = next(i for i in res.impacts if i.key == "income_drop")
    assert inc.low_value == 0.0                      # aşağı yer yok
    assert inc.high_value == DEFAULT_DELTA           # yukarı tam delta

    res_hi = tornado(_params(volatility=0.40))       # sürgü en sağda
    vol = next(i for i in res_hi.impacts if i.key == "volatility")
    assert vol.high_value == 0.40
    assert vol.low_value == 0.40 - DEFAULT_DELTA


def test_zero_delta_means_nothing_moves():
    """delta=0'da hiçbir sürgü oynamaz; tüm etkiler sıfır ve 'ölü' sayılmalı."""
    res = tornado(_params(), delta=0.0)
    assert all(i.swing == 0.0 for i in res.impacts)
    assert all(i.negligible for i in res.impacts)
    assert res.top is None, "hiçbir etki yokken 'en etkili sürgü' gösterilmemeli"


def test_negligible_threshold_is_in_percentage_points():
    """`negligible`, yüzde puanı eşiğiyle karşılaştırılmalı (0–1 ile değil)."""
    res = tornado(_params())
    for i in res.impacts:
        assert i.negligible == (abs(i.swing_pp) < NEGLIGIBLE_SWING_PP)


def test_top_is_the_first_impact_when_it_matters():
    res = tornado(_params())
    assert res.top is res.impacts[0]
    assert abs(res.top.swing_pp) >= NEGLIGIBLE_SWING_PP


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"test_sensitivity: {len(fns)} test geçti")
