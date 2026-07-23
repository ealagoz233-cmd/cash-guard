"""
test_weekly.py  —  13 haftalık likidite ufkunun testleri
────────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q  (veya: python tests/test_weekly.py)

Haftalık tablonun tek işi, aylık ortalamanın sakladığı ay-içi çukuru
göstermek. İki risk var ve ikisi de sessizdir:

  1. Para KAYBOLABİLİR. Aylık toplamı güne dağıtırken ay uzunluğu yanlış
     kullanılırsa haftalık toplamlar aylık gerçeği tutmaz; tablo makul görünür
     ama kasa yolu yanlıştır.
  2. Tablo BİLGİSİZ olabilir. Gider dağılımı yoksa her şey aya yayılır ve
     haftalık eğri, aylık çizginin ince çizilmiş hâlinden ibaret kalır. Bunu
     söylemeyen bir arayüz, olmayan bir hassasiyete güven yaratır.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import weekly
from modules.data_io import load_mock

# Şubat 2026 tam 28 gün = TAM 4 hafta. Ay ile hafta sınırlarının çakıştığı bu
# nadir pencere, "aylık toplam korunuyor mu" sorusunu kalansız test ettiriyor.
_SUBAT = date(2026, 2, 1)

_GIDERLER = {"personel": 2_650_000, "kira_ve_isletme": 780_000,
             "hammadde_ve_tedarik": 1_620_000, "pazarlama": 520_000,
             "enerji_ve_lojistik": 380_000}
_TOPLAM_GIDER = sum(_GIDERLER.values())     # 5.950.000


def _plan(**over):
    kw = dict(current_cash=4_200_000, monthly_collections=6_800_000,
              expense_breakdown=_GIDERLER, monthly_fixed_expense=_TOPLAM_GIDER,
              monthly_debt_service=950_000, start=_SUBAT, weeks=4)
    kw.update(over)
    return weekly.build(**kw)


# ── Para korunumu ─────────────────────────────────────────────────────────
def test_a_full_month_of_weeks_reproduces_the_monthly_totals():
    """
    4 hafta tam olarak şubatı kaplıyor: haftalık giriş/çıkış toplamları aylık
    rakamların ta kendisi olmalı. Tutmuyorsa güne dağıtımda para kayboluyordur.
    """
    p = _plan()
    assert abs(sum(w.inflow for w in p.weeks) - 6_800_000) < 1e-6
    assert abs(sum(w.outflow for w in p.weeks) - (_TOPLAM_GIDER + 950_000)) < 1e-6


def test_closing_cash_is_the_running_sum_of_weekly_net():
    """Kapanış kasası, haftalık netlerin yürüyen toplamı olmalı."""
    p = _plan()
    kasa = p.opening_cash
    for w in p.weeks:
        kasa += w.net
        assert abs(w.closing_cash - kasa) < 1e-6
    assert abs(p.end_cash - kasa) < 1e-6


def test_spreading_uses_the_real_length_of_each_month():
    """
    Sabit 30 gün varsaymak, 28 ve 31 günlük aylarda kasayı sistematik olarak
    kaydırır. 28 günlük şubatta günlük tahsilat 31 günlük ocaktakinden yüksek.
    """
    subat = weekly.build(0, 2_800_000, {}, 0, 0, start=date(2026, 2, 1), weeks=1)
    ocak = weekly.build(0, 2_800_000, {}, 0, 0, start=date(2026, 1, 1), weeks=1)
    assert abs(subat.weeks[0].inflow - 2_800_000 / 28 * 7) < 1e-6
    assert abs(ocak.weeks[0].inflow - 2_800_000 / 31 * 7) < 1e-6
    assert subat.weeks[0].inflow > ocak.weeks[0].inflow


# ── Takvim ────────────────────────────────────────────────────────────────
def test_dated_payments_land_in_the_week_that_contains_their_day():
    """
    Maaş ayın 5'inde çıkıyorsa 1. haftada (1–7 Şubat) görünmeli; kira da 1'inde.
    Bu tablonun bütün varlık sebebi bu — yanlış haftaya düşerse çukur kayar.
    """
    p = _plan(monthly_collections=0, monthly_debt_service=0,
              expense_breakdown={"personel": 2_650_000, "kira_ve_isletme": 780_000})
    assert abs(p.weeks[0].outflow - (2_650_000 + 780_000)) < 1e-6
    assert all(w.outflow == 0 for w in p.weeks[1:])


def test_debt_service_lands_on_its_own_day():
    """Borç servisi (ayın 15'i) 3. haftaya (15–21 Şubat) düşmeli."""
    p = _plan(monthly_collections=0, expense_breakdown={"x": 0},
              monthly_fixed_expense=0, monthly_debt_service=950_000)
    assert abs(p.weeks[2].outflow - 950_000) < 1e-6


def test_a_payment_day_that_does_not_exist_falls_on_the_last_day():
    """
    Ayın 31'inde ödenen bir kalem şubatta yoktur. Ödemeyi düşürmek parayı
    kaybetmek, bir sonraki aya atmak da takvimi kaydırmak olurdu; ayın son
    gününe çekilir.
    """
    p = weekly.build(0, 0, {"kira_ve_isletme": 100_000}, 0, 0,
                     start=_SUBAT, weeks=4,
                     payment_calendar={"kira_ve_isletme": 31})
    assert abs(p.weeks[3].outflow - 100_000) < 1e-6   # 22–28 Şubat
    assert sum(w.outflow for w in p.weeks) == 100_000


def test_unknown_expense_keys_are_spread_not_guessed():
    """Tanınmayan kaleme uydurma bir ödeme günü atamak yerine aya yay."""
    p = weekly.build(0, 0, {"bilinmeyen_kalem": 2_800_000}, 0, 0,
                     start=_SUBAT, weeks=4)
    assert all(abs(w.outflow - 700_000) < 1e-6 for w in p.weeks)


# ── Bilgi taşıyor mu ──────────────────────────────────────────────────────
def test_without_an_expense_breakdown_the_view_admits_it_adds_nothing():
    """
    Her şey aya yayılmışsa haftalık eğri, aylık çizginin ince hâlidir. Bunu
    söylemeyen arayüz olmayan bir hassasiyete güven yaratır.
    """
    p = weekly.build(4_200_000, 6_800_000, None, 5_950_000, 0,
                     start=_SUBAT, weeks=4)
    assert p.informative is False
    assert p.dated_items == []
    # Tablo yine de kurulmalı — "bilgisiz" demek "bozuk" demek değil.
    assert len(p.weeks) == 4


def test_debt_service_alone_makes_the_view_informative():
    """Gider dağılımı olmasa bile taksit tarihli bir çıkış: çukur gerçektir."""
    p = weekly.build(4_200_000, 6_800_000, None, 5_950_000, 950_000,
                     start=_SUBAT, weeks=4)
    assert p.informative is True
    assert "borc_servisi" in p.dated_items


# ── Özet metrikler ────────────────────────────────────────────────────────
def test_min_week_and_intramonth_gap_measure_the_hidden_trough():
    """
    `intramonth_gap`, aylık modelin GÖREMEDİĞİ derinliktir: dönem sonu kasası
    ile en dip hafta arasındaki fark.
    """
    p = _plan()
    dip = p.min_week
    assert dip is not None
    assert dip.closing_cash == min(w.closing_cash for w in p.weeks)
    assert abs(p.intramonth_gap - (p.end_cash - dip.closing_cash)) < 1e-6
    assert p.intramonth_gap >= 0


def test_first_negative_is_none_when_cash_never_goes_below_zero():
    assert _plan().first_negative is None


def test_first_negative_finds_the_earliest_breach():
    p = _plan(current_cash=3_000_000, monthly_collections=0)
    ilk = p.first_negative
    assert ilk is not None
    assert all(w.closing_cash >= 0 for w in p.weeks if w.index < ilk.index)


# ── Tarih ayrıştırma ──────────────────────────────────────────────────────
def test_projection_starts_the_day_after_the_data_date():
    """Veri 30 Haziran itibarıyla; projeksiyon 1 Temmuz'da başlamalı."""
    assert weekly.parse_start("2026-06-30") == date(2026, 7, 1)
    assert weekly.parse_start(date(2026, 6, 30)) == date(2026, 7, 1)


def test_a_broken_date_falls_back_instead_of_crashing():
    """Tarih kullanıcı dosyasından gelebilir; bozuk dize uygulamayı düşürmemeli."""
    geri = date(2026, 1, 1)
    for kotu in ("abc", "", None, "2026-13-45", 42):
        assert weekly.parse_start(kotu, fallback=geri) == geri


# ── Dayanıklılık ──────────────────────────────────────────────────────────
def test_garbage_amounts_do_not_crash_the_table():
    p = weekly.build("abc", None, {"personel": "yok", "kira_ve_isletme": -5},
                     float("nan"), None, start=_SUBAT, weeks=4)
    assert len(p.weeks) == 4
    assert all(w.outflow == 0 for w in p.weeks)


def test_week_count_is_respected_and_never_zero():
    assert len(weekly.build(0, 0, {}, 0, 0, start=_SUBAT, weeks=13).weeks) == 13
    assert weekly.DEFAULT_WEEKS == 13


# ── Demo şirketi ──────────────────────────────────────────────────────────
def test_demo_company_has_a_trough_the_monthly_model_cannot_see():
    """
    Demo şirketinde aylık net −100.000 — aylık bakınca üç ayda kasa neredeyse
    sabit görünür. Haftalık bakınca maaş/kira haftaları kasayı milyonlarca lira
    aşağı çekiyor. Bu farkın var olması, modülün eklenme gerekçesidir.
    """
    d = load_mock()
    p = weekly.build(
        d["current_cash"], d["avg_monthly_collections"], d["expense_breakdown"],
        d["avg_monthly_fixed_expense"], d["existing_monthly_debt_service"],
        start=weekly.parse_start(d["as_of"]),
    )
    assert p.informative
    assert len(p.weeks) == 13
    assert p.intramonth_gap > 1_000_000, (
        "ay-içi çukur kayboldu — haftalık görünüm aylık modele ek bilgi vermiyor")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"test_weekly: {len(fns)} test geçti")
