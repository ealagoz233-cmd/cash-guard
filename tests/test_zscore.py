"""
test_zscore.py  —  Altman Z-score testleri
──────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q  (veya: python tests/test_zscore.py)

Z-score yayımlanmış, sabit katsayılı bir modeldir; buradaki tek gerçek risk
katsayıları ya da bölge eşiklerini yanlış girmek. Yanlış girilse de çıktı makul
bir sayı olmaya devam eder, yani gözle yakalanamaz. Bu yüzden testler elle
hesaplanmış değerlerle karşılaştırıyor.

Ayrıca demo şirketinin "Altman güvenli der, nakit modeli batıyor der" çelişkisi
bir kaza değil, uygulamanın tezi — o yüzden burada kilitleniyor.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import zscore as z
from modules.data_io import load_mock

# Elle hesaplanabilsin diye yuvarlak sayılar:
#   X1 = (60−40)/100 = 0.20   X2 = 20/100 = 0.20   X3 = 10/100 = 0.10
#   X4 = (100−50)/50 = 1.00   X5 = 200/100 = 2.00
_BASIT = {
    "total_assets": 100, "current_assets": 60, "current_liabilities": 40,
    "total_liabilities": 50, "retained_earnings": 20, "ebit_annual": 10,
    "annual_sales": 200,
}


# ── Katsayılar ────────────────────────────────────────────────────────────
def test_z_prime_matches_a_hand_calculation():
    """0.717·0.20 + 0.847·0.20 + 3.107·0.10 + 0.420·1.00 + 0.998·2.00"""
    r = z.compute(_BASIT, z.Z_PRIME)
    beklenen = 0.1434 + 0.1694 + 0.3107 + 0.4200 + 1.9960
    assert abs(r.score - beklenen) < 1e-9
    assert abs(r.score - 3.0395) < 1e-9


def test_z_double_prime_matches_a_hand_calculation():
    """6.56·0.20 + 3.26·0.20 + 6.72·0.10 + 1.05·1.00 — X5 YOK."""
    r = z.compute(_BASIT, z.Z_DOUBLE_PRIME)
    assert abs(r.score - (1.312 + 0.652 + 0.672 + 1.05)) < 1e-9
    assert {c.key for c in r.components} == {"x1", "x2", "x3", "x4"}


def test_components_sum_to_the_score():
    """Bileşen dökümü ekranda gösteriliyor; toplamı skoru vermezse yalan olur."""
    for model in (z.Z_PRIME, z.Z_DOUBLE_PRIME):
        r = z.compute(_BASIT, model)
        assert abs(sum(c.contribution for c in r.components) - r.score) < 1e-12


def test_published_thresholds_are_not_drifted():
    """Bölge sınırları modellerin kendi eşikleri; 'ayarlanmış' bir sürüm yok."""
    assert (z.Z_PRIME.safe_above, z.Z_PRIME.distress_below) == (2.90, 1.23)
    assert (z.Z_DOUBLE_PRIME.safe_above, z.Z_DOUBLE_PRIME.distress_below) == (2.60, 1.10)


# ── Bölgeler ──────────────────────────────────────────────────────────────
def test_zones_split_at_the_thresholds():
    m = z.Z_PRIME
    assert z.zone_of(3.50, m) == z.ZONE_SAFE
    assert z.zone_of(2.00, m) == z.ZONE_GREY
    assert z.zone_of(0.80, m) == z.ZONE_DISTRESS
    # Sınırın tam üstü gri sayılır: eşiği "aşmak" gerekir.
    assert z.zone_of(2.90, m) == z.ZONE_GREY
    assert z.zone_of(1.23, m) == z.ZONE_GREY


def test_distance_to_safe_is_signed():
    """Negatif = güvenli bölgenin içinde. İşaret ters olursa uyarı ters döner."""
    icerde = z.compute(_BASIT, z.Z_PRIME)             # 3.04 > 2.90
    assert icerde.distance_to_safe < 0
    zayif = z.compute(_BASIT | {"ebit_annual": -50}, z.Z_PRIME)
    assert zayif.distance_to_safe > 0


# ── Model seçimi ──────────────────────────────────────────────────────────
def test_manufacturers_get_the_manufacturer_model():
    for sektor in ("Üretim / İhracat (Tekstil)", "imalat sanayi",
                   "Manufacturing", "Ağır Sanayi"):
        assert z.pick_model(sektor) is z.Z_PRIME


def test_others_and_unknown_sectors_get_the_general_model():
    """
    Sektör bilinmiyorsa X5'i (varlık devri) hesaba katmayan varyant seçilir —
    daha az varsayım yapan model.
    """
    for sektor in ("Perakende", "Yazılım", "", None):
        assert z.pick_model(sektor) is z.Z_DOUBLE_PRIME


# ── Eksik veri ────────────────────────────────────────────────────────────
def test_missing_data_produces_no_score_instead_of_a_wrong_one():
    """
    Yarım veriyle hesaplanmış bir iflas skoru, hiç skor olmamasından daha
    tehlikelidir: kullanıcı sayıya bakar, arkasındaki boşluğu görmez.
    """
    r = z.compute({"total_assets": 100}, z.Z_PRIME)
    assert r.score is None
    assert not r.available
    assert "retained_earnings" in r.missing_fields
    # Model kimliği eksik veride de dolu gelmeli (arayüz onu yazıyor)
    assert r.model_name and r.model_fits


def test_x5_model_also_requires_annual_sales():
    eksik = {k: v for k, v in _BASIT.items() if k != "annual_sales"}
    assert not z.compute(eksik, z.Z_PRIME).available
    # X5 kullanmayan varyant aynı veriyle hesaplanabilmeli
    assert z.compute(eksik, z.Z_DOUBLE_PRIME).available


def test_zero_or_negative_total_assets_is_refused():
    for ta in (0, -100):
        r = z.compute(_BASIT | {"total_assets": ta}, z.Z_PRIME)
        assert not r.available
        assert r.missing_fields == ["total_assets"]


def test_garbage_input_does_not_crash():
    for kotu in (None, "abc", 42, {}, {"total_assets": "yok"}):
        assert not z.compute(kotu, z.Z_PRIME).available


def test_debt_free_company_does_not_get_an_infinite_score():
    """
    X4 borçsuz şirkette tanımsız. Sonsuz yerine sonlu tavan: borçsuzluk
    sağlıklıdır ama diğer üç bileşeni anlamsızlaştırmamalı.
    """
    r = z.compute(_BASIT | {"total_liabilities": 0}, z.Z_PRIME)
    assert r.available
    x4 = next(c for c in r.components if c.key == "x4")
    assert x4.ratio == z.MAX_EQUITY_TO_LIABILITIES


# ── Şirket sözlüğünden türetme ────────────────────────────────────────────
def test_from_company_derives_sales_and_ebit_from_the_monthly_scalars():
    """
    Aynı büyüklüğü iki yere yazmak, er geç ikisinin ayrışması demektir. Yıllık
    satış ve FVÖK saklanmaz, aylık skalerlerden türetilir.
    """
    d = {
        "sector": "Üretim",
        "avg_monthly_revenue": 1_000_000,
        "avg_monthly_fixed_expense": 800_000,
        "balance_sheet": {
            "total_assets": 10_000_000, "current_assets": 5_000_000,
            "current_liabilities": 3_000_000, "total_liabilities": 6_000_000,
            "retained_earnings": 1_000_000, "annual_depreciation": 400_000,
        },
    }
    r = z.from_company(d)
    x5 = next(c for c in r.components if c.key == "x5")
    x3 = next(c for c in r.components if c.key == "x3")
    assert x5.ratio == 12_000_000 / 10_000_000            # yıllık satış
    assert x3.ratio == (2_400_000 - 400_000) / 10_000_000  # FVÖK, amortisman düşülmüş


def test_from_company_without_a_balance_sheet_reports_everything_missing():
    """
    Kullanıcı kendi CSV'sini yüklediğinde bilanço gelmez; kart gizlenmeli.

    Eksik alanlar KULLANICININ DİLİNDE bildirilir: `ebit_annual` şablonda yoktur
    (aylık skalerlerden türetilir), o yüzden onun yerine türetildiği alanların
    adı görünür. Ekranda kendi yazamayacağı bir alanı arayan kullanıcı, panelin
    neden sustuğunu hiç öğrenemezdi.
    """
    r = z.from_company({"sector": "Üretim", "avg_monthly_revenue": 1})
    assert not r.available
    ham = set(z.REQUIRED_FIELDS) - set(z._DERIVED_FROM)
    assert ham <= set(r.missing_fields)
    assert "ebit_annual" not in r.missing_fields
    assert "avg_monthly_revenue" in r.missing_fields


# ── Demo şirketi: uygulamanın tezi ────────────────────────────────────────
def test_demo_company_balance_sheet_is_consistent_with_the_scalars():
    """
    Bilanço uydurma değil, mevcut skalerlerle tutarlı kurulmuş olmalı: kasa ve
    alacak dönen varlığın içinde, borç stoku yükümlülüğün içinde olmalı.
    """
    d = load_mock()
    bs = d["balance_sheet"]
    assert bs["current_assets"] >= d["current_cash"] + d["receivables_outstanding"]
    assert bs["total_liabilities"] >= d["existing_debt"]
    assert bs["total_assets"] > bs["current_assets"]      # duran varlık da var
    assert bs["total_assets"] > bs["total_liabilities"]   # özkaynak pozitif
    assert bs["retained_earnings"] <= bs["total_assets"] - bs["total_liabilities"]


def test_demo_company_uses_the_manufacturer_model():
    assert z.from_company(load_mock()).model_key == z.Z_PRIME.key


def test_altman_says_safe_while_the_cash_model_says_ruin():
    """
    Uygulamanın tezi bu çelişkidir ve bir kaza değildir: Altman tahakkuk esaslı
    yıllık bir fotoğraf çeker, nakdin NE ZAMAN geldiğini görmez. Demo şirketi
    kâğıt üstünde kârlıdır ve nakitsiz batar. Bu test bozulursa ekrandaki
    "iki model neden farklı konuşuyor" anlatısı da geçersizleşir.
    """
    from modules import monte_carlo as mc

    d = load_mock()
    skor = z.from_company(d)
    assert skor.zone == z.ZONE_SAFE, f"Altman artık güvenli demiyor: {skor.score}"

    nakit = mc.run(mc.StressParams(
        current_cash=d["current_cash"],
        monthly_revenue=d["avg_monthly_collections"],
        monthly_fixed_expense=d["avg_monthly_fixed_expense"],
        monthly_debt_service=d["existing_monthly_debt_service"],
        income_drop=0.06, volatility=0.10, delay_prob=0.30,
        delay_severity=0.25, expense_inflation=0.10, n_iter=10_000, seed=42,
    ))
    assert nakit.ruin_probability > 0.90, "nakit modeli artık batma demiyor"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"test_zscore: {len(fns)} test geçti")
