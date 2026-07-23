"""
test_runway.py  —  Nakit ömrü hesaplarının testleri
────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q     (veya: python tests/test_runway.py)

Kritik olan, trend runway'in STATİK runway'den anlamlı biçimde ayrışması ve tek
bir anormal ayın sonucu ele geçirememesi. Theil–Sen'e geçilmesinin sebebi buydu:
en küçük kareler, demo verisindeki tek aykırı ay yüzünden eğimi −28K yerine
−40K gösteriyor, dolayısıyla manşet rakamı o tek ay belirliyordu.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.data_io import load_mock
from modules.runway import (MIN_MONTHS_FOR_TREND, _theil_sen, static_runway,
                            trend_runway)


def _flat_history(n=12, collections=6_000_000, expense=5_500_000):
    return [{"month": f"{i}", "collections": collections, "fixed_expense": expense}
            for i in range(n)]


# ── Statik runway ─────────────────────────────────────────────────────────
def test_static_runway_is_cash_over_burn():
    assert static_runway(4_200_000, -100_000) == 42.0


def test_static_runway_is_none_when_not_burning():
    assert static_runway(4_200_000, 0) is None
    assert static_runway(4_200_000, 250_000) is None


# ── Theil–Sen dayanıklılığı ───────────────────────────────────────────────
def test_theil_sen_recovers_clean_slope():
    x = np.arange(10, dtype=float)
    y = 1_000_000 - 25_000 * x
    slope, intercept = _theil_sen(x, y)
    assert abs(slope + 25_000) < 1e-6
    assert abs(intercept - 1_000_000) < 1e-6


def test_theil_sen_resists_an_outlier_that_breaks_least_squares():
    """Tek aykırı ay: en küçük kareler sapar, Theil–Sen gerçek eğimde kalır."""
    x = np.arange(12, dtype=float)
    y = 1_000_000 - 25_000 * x
    y[0] += 400_000                      # ilk ayda toplu tahsilat (anormal)

    ts_slope, _ = _theil_sen(x, y)
    ls_slope = float(np.polyfit(x, y, 1)[0])

    assert abs(ts_slope + 25_000) < 1_000, f"Theil–Sen saptı: {ts_slope:,.0f}"
    assert abs(ls_slope + 25_000) > 5_000, "bu veri en küçük kareleri saptırmalıydı"


# ── Trend runway ──────────────────────────────────────────────────────────
def test_trend_runway_is_shorter_than_static_when_deteriorating():
    """Durum bozuluyorsa trend runway statik runway'den KISA olmalı."""
    d = load_mock()
    cash, ds = d["current_cash"], d["existing_monthly_debt_service"]
    monthly_net = (d["avg_monthly_collections"] - d["avg_monthly_fixed_expense"] - ds)

    static = static_runway(cash, monthly_net)
    trend = trend_runway(d["history"], cash, ds)

    assert trend.available and trend.months is not None
    assert trend.slope_per_month < 0, "demo verisinde faaliyet nakdi bozulmalı"
    assert trend.months < static, f"trend {trend.months} ay, statik {static:.0f} ay"


def test_stable_company_trend_matches_static():
    """Bozulma yoksa (düz seri) trend runway statik runway'e yakın çıkmalı."""
    hist = _flat_history()                       # her ay 500.000 faaliyet nakdi
    trend = trend_runway(hist, current_cash=3_000_000, debt_service=600_000)
    assert abs(trend.slope_per_month) < 1e-6     # eğim sıfır
    # Aylık net = 500.000 − 600.000 = −100.000 -> 3.000.000 / 100.000 = 30 ay
    assert trend.months == 30


def test_improving_company_has_no_trend_ruin():
    """Faaliyet nakdi iyileşiyorsa ufukta temerrüt olmamalı."""
    hist = [{"month": f"{i}", "collections": 6_000_000 + 50_000 * i,
             "fixed_expense": 5_800_000} for i in range(12)]
    trend = trend_runway(hist, current_cash=2_000_000, debt_service=300_000)
    assert trend.slope_per_month > 0
    assert trend.months is None


def test_insufficient_data_says_so_instead_of_vanishing():
    """
    Az gözlem ya da eksik sütunla trend konuşulmaz — ama sonuç yine döner.

    Eskiden fonksiyonun kendisi `None` dönüyordu ve bu, "kasa ufuk içinde
    sıfırlanmıyor" anlamına gelen `months=None` ile aynı `if` içinde eleniyordu:
    iyi haber ile eksik veri ekranda tek ve sessiz bir boşluğa dönüşüyordu.
    """
    for gecmis in ([],
                   _flat_history(MIN_MONTHS_FOR_TREND - 1),
                   [{"month": f"{i}", "revenue": 100} for i in range(12)]):
        trend = trend_runway(gecmis, 1_000_000, 100_000)
        assert trend.available is False
        assert trend.months is None
        assert trend.missing_fields, "eksik olan alan adıyla söylenmeli"


def test_module_docstring_quotes_the_real_demo_ladder():
    """
    `runway.py` başlığındaki "42 ay / 10 ay" merdiveni demo verisiyle tutmalı.

    Serbest bırakıldığında bayatladı: demo verisi yeniden kalibre edildi, cümle
    eski rakamı (14 ay) söylemeye devam etti. README'nin kendi test sayısını
    testle bağladığımız gerekçenin aynısı — kendi hakkında yanlış konuşan bir
    modül, kullanıcıya yanlış konuşan bir uygulamanın provasıdır.
    """
    import re

    import modules.runway as rw

    d = load_mock()
    ds = d["existing_monthly_debt_service"]
    net = d["avg_monthly_collections"] - d["avg_monthly_fixed_expense"] - ds

    m = re.search(r"statik hesap ~(\d+) ay derken trend hesabı ~(\d+) ay",
                  rw.__doc__)
    assert m, "modül başlığındaki merdiven cümlesi bulunamadı"
    assert int(m.group(1)) == round(static_runway(d["current_cash"], net))
    assert int(m.group(2)) == trend_runway(d["history"], d["current_cash"], ds).months


def test_no_ruin_on_the_horizon_is_not_confused_with_missing_data():
    """`available=True, months=None` iyi haberdir; eksik veriyle karışmamalı."""
    hist = [{"month": f"{i}", "collections": 6_000_000 + 50_000 * i,
             "fixed_expense": 5_800_000} for i in range(12)]
    trend = trend_runway(hist, current_cash=2_000_000, debt_service=300_000)
    assert trend.available is True and trend.months is None
    assert trend.missing_fields == []


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
