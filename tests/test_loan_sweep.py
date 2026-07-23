"""
test_loan_sweep.py  —  Kredi tutarı taramasının testleri
────────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q  (veya: python tests/test_loan_sweep.py)

Bu modülün tehlikesi bir "optimizasyon" gibi görünmesi. Yalnızca 12 aylık batma
olasılığına bakan bir öneri, uygulamanın uyardığı hatanın ta kendisini yapardı:
nakit enjeksiyonu ilk 12 ayı neredeyse her zaman rahatlatır, taksit yükü sonra
vurur. Bu yüzden testler iki şeyi birlikte koruyor — en iyi tutarın bulunması ve
o tutarın bir tuzak olduğunda ISRARLA işaretlenmesi.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import loan_simulator as ls
from modules.loan_sweep import (DEFAULT_MAX_AMOUNT, MEANINGFUL_GAIN_PP,
                                amount_grid, sweep)
from modules.monte_carlo import StressParams


def _stres(**over) -> StressParams:
    base = dict(current_cash=4_200_000, monthly_revenue=6_800_000,
                monthly_fixed_expense=5_950_000, monthly_debt_service=950_000,
                income_drop=0.06, volatility=0.10, delay_prob=0.30,
                delay_severity=0.25, expense_inflation=0.10,
                months=12, n_iter=4_000, seed=42)
    base.update(over)
    return StressParams(**base)


def _kredi(**over) -> ls.LoanScenario:
    base = dict(current_cash=4_200_000, monthly_revenue=6_800_000,
                monthly_fixed_expense=5_950_000, existing_debt_service=950_000,
                loan_amount=0.0, loan_term_months=24,
                monthly_interest_rate=0.035, horizon_months=24)
    base.update(over)
    return ls.LoanScenario(**base)


# ── Izgara ────────────────────────────────────────────────────────────────
def test_zero_is_always_on_the_grid():
    """
    "Hiç çekmemek" bir seçenek değil, karşılaştırmanın ÇIPASI. Izgarada
    olmazsa kredisiz hâlle kıyas yapılamaz ve her sonuç "kredi çek" der.
    """
    for adim in (2, 5, 13, 40):
        assert amount_grid(30_000_000, adim)[0] == 0


def test_grid_spans_the_whole_slider_range():
    g = amount_grid(30_000_000, 13)
    assert g[-1] == 30_000_000
    assert len(g) == 13
    assert g == sorted(g), "ızgara artan sırada olmalı"


def test_grid_survives_silly_step_counts():
    assert len(amount_grid(1_000_000, 0)) == 2      # en az iki nokta
    assert len(amount_grid(1_000_000, 1)) == 2


# ── Sözleşme ──────────────────────────────────────────────────────────────
def test_baseline_is_the_no_loan_case():
    r = sweep(_stres(), _kredi(), steps=5)
    assert r.baseline is not None
    assert r.baseline.amount == 0
    assert r.baseline.installment == 0
    assert r.baseline.total_interest == 0


def test_baseline_matches_a_plain_simulation():
    """
    Taramanın sıfır noktası, kredisiz koşulan normal simülasyonun ta kendisi
    olmalı; yoksa ekrandaki manşet ile eğrinin başlangıcı farklı çıkar.
    """
    from modules.monte_carlo import run

    p = _stres()
    assert sweep(p, _kredi(), steps=5).baseline.ruin_probability == run(p).ruin_probability


def test_sharing_the_shock_matrix_changes_no_number():
    """
    Taramanın hız kazancının TAMAMI şu iddiaya dayanıyor: on üç tutar aynı şok
    matrisini paylaşabilir, çünkü değişen tek şey kasa ve taksit.

    İddia doğruysa paylaşımlı ve paylaşımsız koşular BİREBİR aynı olasılığı
    verir. Yanlışsa ceza çöken bir program değil — makul görünen, sessizce
    yanlış bir eğri ve ona bakarak verilen bir borçlanma kararı olurdu.

    Şimdiye kadar yalnızca sıfır noktası (`baseline`) bağlıydı; oysa sıfır
    noktası şokun paylaşıldığı tek tutar DEĞİL, paylaşımın hiç fark yaratmadığı
    tek tutardı. Asıl iddia geri kalan on ikisinde.
    """
    from dataclasses import replace

    from modules.monte_carlo import run

    stres, kredi = _stres(), _kredi()
    paylasimli = sweep(stres, kredi, steps=7)

    for nokta in paylasimli.points:
        det = ls.simulate(replace(kredi, loan_amount=nokta.amount))
        tek_basina = run(replace(
            stres,
            current_cash=stres.current_cash + nokta.amount,
            monthly_debt_service=stres.monthly_debt_service + det["installment"],
        ))
        assert nokta.ruin_probability == tek_basina.ruin_probability, (
            f"{nokta.amount:,.0f} TL: paylaşımlı {nokta.ruin_probability} vs "
            f"tek başına {tek_basina.ruin_probability}")


def test_installment_grows_with_the_amount():
    r = sweep(_stres(), _kredi(), steps=5)
    taksitler = [p.installment for p in r.points]
    assert taksitler == sorted(taksitler)
    assert taksitler[0] == 0 and taksitler[-1] > 0


def test_is_reproducible():
    """Ortak rastgele sayılar: eğri koşudan koşuya değişmemeli."""
    a = sweep(_stres(), _kredi(), steps=7)
    b = sweep(_stres(), _kredi(), steps=7)
    assert [p.ruin_probability for p in a.points] == [p.ruin_probability for p in b.points]
    assert a.best.amount == b.best.amount


def test_the_template_loan_amount_is_ignored():
    """
    `loan` şablonu vade ve faizi taşır; içindeki tutar her adımda ezilmeli.
    Ezilmezse tarama tek bir tutarı 13 kez hesaplar ve bunu kimse fark etmez.
    """
    a = sweep(_stres(), _kredi(loan_amount=0), steps=5)
    b = sweep(_stres(), _kredi(loan_amount=999_000_000), steps=5)
    assert [p.amount for p in a.points] == [p.amount for p in b.points]
    assert [p.ruin_probability for p in a.points] == [p.ruin_probability for p in b.points]


# ── Asıl mesele: tuzak tespiti ────────────────────────────────────────────
def test_the_demo_company_loan_is_flagged_as_a_trap():
    """
    Demo şirketinde her kredi tutarı 12 aylık riski düşürür ama iflası ÖNE
    çeker. Yalnızca olasılığa bakan bir öneri "30 milyon çek" derdi. Bu test,
    uygulamanın o hatayı yapmadığını kilitliyor.
    """
    r = sweep(_stres(), _kredi(), steps=7)
    assert r.borrowing_helps, "12 ayda iyileşme yok — taban senaryo değişmiş"
    assert r.best.amount > 0
    assert r.best.relief_months < 0, "en iyi tutar iflası ötelemiş görünüyor"
    assert r.best_is_a_trap, "borç tuzağı işaretlenmedi"


def test_a_trap_requires_both_conditions():
    """
    `best_is_a_trap` iki şeyi birden istemeli: 12 ayda anlamlı kazanç VE uzun
    vadede öne çekilen iflas. Tek başına biri yeterli olsaydı ya her senaryoda
    yanardı ya da hiç yanmazdı.
    """
    r = sweep(_stres(), _kredi(), steps=5)
    if r.best_is_a_trap:
        assert r.borrowing_helps and r.best.is_trap


def test_a_healthy_company_is_not_told_it_is_trapped():
    """
    Nakit üreten bir şirkette kredi 12 ayı zaten güvenli olan bir tabloyu
    anlamlı biçimde iyileştiremez; uyarı yanlış yere düşmemeli.
    """
    saglikli = _stres(current_cash=40_000_000, monthly_revenue=9_000_000,
                      monthly_fixed_expense=5_000_000, monthly_debt_service=0)
    r = sweep(saglikli, _kredi(current_cash=40_000_000, monthly_revenue=9_000_000,
                               monthly_fixed_expense=5_000_000,
                               existing_debt_service=0), steps=5)
    assert r.baseline.ruin_probability == 0.0
    assert not r.borrowing_helps, "zaten %0 riskte 'kredi işe yarıyor' denemez"
    assert not r.best_is_a_trap


def test_gain_is_measured_against_the_no_loan_case():
    r = sweep(_stres(), _kredi(), steps=5)
    beklenen = (r.baseline.ruin_probability - r.best.ruin_probability) * 100
    assert abs(r.gain_pp - beklenen) < 1e-9
    assert r.borrowing_helps == (r.best.amount > 0 and r.gain_pp >= MEANINGFUL_GAIN_PP)


def test_default_max_amount_matches_the_slider():
    """Taranan üst sınır, kullanıcının sürgüde ulaşabildiği üst sınır olmalı."""
    from modules import scenario

    kredi_alani = next(a for a in scenario.ALANLAR if a.anahtar == "kredi")
    assert DEFAULT_MAX_AMOUNT == kredi_alani.ust


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"test_loan_sweep: {len(fns)} test geçti")
