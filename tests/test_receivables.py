"""
test_receivables.py  —  Alacak yaşlandırma katmanının testleri
──────────────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q  (veya: python tests/test_receivables.py)

Bu modülün çıktısı doğrudan kullanıcının göreceği para tutarına dönüşüyor
("bu alacakların ~2,7 milyonu muhtemelen hiç gelmeyecek"). Yanlış olması hâlinde
sonuç makul görünmeye devam eder, o yüzden burada aritmetiğin kendisi, kova
sınırları ve bozuk veriye dayanıklılık ayrı ayrı korunuyor.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.data_io import load_mock
from modules.receivables import (DEFAULT_BUCKETS, DELAY_PROB_MAX,
                                 DELAY_SEVERITY_MAX, age, implied_stress)


def _kalem(amount, overdue_days):
    return {"customer": "X", "amount": amount, "overdue_days": overdue_days}


# ── Kova sınırları ────────────────────────────────────────────────────────
def test_bucket_edges_land_where_the_labels_say():
    """
    Sınır günleri (30/31, 60/61, 90/91) kovaların adında yazıyor; kodun onlarla
    aynı fikirde olmadığı bir durum kullanıcıya asla görünmez ama tüm karşılık
    hesabını kaydırır.
    """
    beklenen = [
        (0, "Vadesinde"), (1, "1–30 gün gecikmiş"), (30, "1–30 gün gecikmiş"),
        (31, "31–60 gün gecikmiş"), (60, "31–60 gün gecikmiş"),
        (61, "61–90 gün gecikmiş"), (90, "61–90 gün gecikmiş"),
        (91, "90+ gün gecikmiş"), (5_000, "90+ gün gecikmiş"),
    ]
    for gun, ad in beklenen:
        p = age([_kalem(100, gun)])
        dolu = [r.name for r in p.rows if r.amount > 0]
        assert dolu == [ad], f"{gun}. gün {dolu} kovasına düştü, {ad} bekleniyordu"


def test_not_yet_due_receivables_are_not_counted_as_overdue():
    """Vadesi gelmemiş (negatif gecikme) alacak 'gecikmiş' sayılmamalı."""
    p = age([_kalem(1_000, -15)])
    assert p.overdue_amount == 0
    assert p.overdue_share == 0.0


# ── Aritmetik ─────────────────────────────────────────────────────────────
def test_expected_loss_is_the_weighted_sum_of_bucket_rates():
    """Elle hesaplanabilir bir örnek: iki kova, iki oran."""
    p = age([_kalem(1_000_000, 10), _kalem(2_000_000, 120)])
    # 1.000.000 × %5  +  2.000.000 × %50
    assert p.expected_loss == 50_000 + 1_000_000
    assert p.total == 3_000_000


def test_shares_sum_to_one():
    p = age([_kalem(1_000, 10), _kalem(3_000, 70), _kalem(6_000, 200)])
    assert abs(sum(r.share for r in p.rows) - 1.0) < 1e-12


def test_dso_is_balance_over_monthly_revenue():
    p = age([_kalem(9_000_000, 40)], monthly_revenue=6_000_000)
    assert p.dso == 45.0                      # 9M / 6M × 30
    assert age([_kalem(100, 1)]).dso is None  # gelir verilmezse DSO yok
    assert age([_kalem(100, 1)], monthly_revenue=0).dso is None


def test_unlisted_balance_goes_to_the_most_optimistic_bucket():
    """
    Listelenmeyen bakiye hakkında bildiğimiz hiçbir şey yok. Kötü varsaymak,
    görmediğimiz bir şey hakkında karamsar bir iddia üretmek olurdu.
    """
    p = age([_kalem(1_000_000, 120)], total_outstanding=5_000_000)
    assert p.listed_amount == 1_000_000
    assert p.unlisted_amount == 4_000_000
    assert p.total == 5_000_000
    vadesinde = next(r for r in p.rows if r.name == "Vadesinde")
    assert vadesinde.amount == 4_000_000


def test_weighted_overdue_days_ignores_the_unlisted_part():
    """
    Listelenmeyen bakiyenin yaşı bilinmiyor; sıfır saymak ortalamayı gerçekte
    olmadığı kadar iyi gösterirdi.
    """
    p = age([_kalem(1_000_000, 100)], total_outstanding=10_000_000)
    assert p.weighted_overdue_days == 100.0


# ── Tutarsızlık tespiti ───────────────────────────────────────────────────
def test_flags_a_balance_that_cannot_be_true_with_the_aging():
    """
    Yaşlandırma "ortalama 100 gün gecikmiş" derken DSO 30 gün çıkıyorsa ikisi
    aynı anda doğru olamaz. Bunu yutmak, kullanıcının veri hatasını sayıya
    çevirip ona geri satmak demektir.
    """
    p = age([_kalem(3_000_000, 100)], monthly_revenue=3_000_000)  # DSO = 30
    assert p.weighted_overdue_days == 100
    assert p.dso == 30
    assert p.dso_conflict is True


def test_consistent_data_is_not_flagged():
    p = age([_kalem(9_000_000, 20)], monthly_revenue=3_000_000)   # DSO = 90 > 20
    assert p.dso_conflict is False


def test_no_conflict_claimed_without_a_dso():
    """DSO yoksa iddia da yok — bilmediğimiz şey hakkında uyarı üretmemeli."""
    assert age([_kalem(1_000, 300)]).dso_conflict is False


# ── Sürgü karşılıkları ────────────────────────────────────────────────────
def test_implied_sliders_reproduce_the_measured_slip_when_not_clamped():
    """
    Bağlantı noktası tek bir büyüklük: `delay_prob × delay_severity`, ölçülen
    kayma oranını vermeli. Vermezse yaşlandırma ile simülasyon farklı şeyler
    söylemeye başlar.
    """
    # %40'ı 70 gün gecikmiş (3 ay sarkıyor), %60'ı vadesinde
    p = age([_kalem(400, 70), _kalem(600, 0)])
    s = implied_stress(p)
    assert abs(s.expected_slip_rate - 0.40) < 1e-12
    assert not s.clamped
    assert abs(s.achievable_slip_rate - s.expected_slip_rate) < 1e-9


def test_implied_sliders_stay_inside_the_real_slider_range():
    """Ayarlanamayan bir değeri 'şunu uygula' diye önermek uygulanamaz bir öneridir."""
    p = age([_kalem(1_000, 200)])                  # defterin tamamı çürük
    s = implied_stress(p)
    assert 0.0 <= s.delay_prob <= DELAY_PROB_MAX
    assert 0.0 <= s.delay_severity <= DELAY_SEVERITY_MAX
    a, b = s.as_slider_percents
    assert isinstance(a, int) and isinstance(b, int)


def test_clamping_is_reported_not_hidden():
    """
    Kırpma devredeyse simülasyon gerçek durumdan DAHA İYİMSER olur; kullanıcı
    'en kötüyü kurdum' sanmamalı.
    """
    s = implied_stress(age([_kalem(1_000, 200)]))
    assert s.expected_slip_rate == 1.0
    assert s.achievable_slip_rate == DELAY_PROB_MAX * DELAY_SEVERITY_MAX
    assert s.clamped is True


def test_a_clean_book_implies_no_stress():
    s = implied_stress(age([_kalem(1_000, 0), _kalem(2_000, -5)]))
    assert s.delay_prob == 0.0
    assert s.delay_severity == 0.0
    assert not s.clamped


# ── Bozuk veriye dayanıklılık ─────────────────────────────────────────────
def test_garbage_input_returns_an_empty_profile_instead_of_crashing():
    """
    Bu liste kullanıcının yüklediği dosyadan gelebilir. Çökmek, uygulamayı ölü
    bir hata sayfasına çevirir — daha önce bir kez oldu.
    """
    for kotu in (None, [], "abc", [None], [{"amount": "yok"}],
                 [{"amount": float("nan"), "overdue_days": 5}],
                 [{"amount": -100, "overdue_days": 5}], [42]):
        p = age(kotu)
        assert p.total == 0
        assert p.expected_loss == 0
        assert p.dso_conflict is False
        assert implied_stress(p).delay_prob == 0.0


def test_missing_overdue_days_is_treated_as_not_overdue():
    """Gün bilgisi yoksa en iyimser kova; uydurma bir yaş atamak yalan olurdu."""
    p = age([{"amount": 1_000}])
    assert p.total == 1_000
    assert p.overdue_amount == 0


# ── Gerçek demo verisi ────────────────────────────────────────────────────
def test_mock_company_book_is_fully_itemised():
    """
    Demo şirketinde bakiyenin tamamı kalem kalem listelenmiş olmalı; aksi halde
    ekrandaki yaşlandırma grafiği defterin yalnızca bir kısmını gösterir ve
    "listelenmemiş" dilimi sessizce en iyimser kovaya yazılır.
    """
    d = load_mock()
    p = age(d["top_receivables"], d["receivables_outstanding"],
            d["avg_monthly_revenue"], d.get("avg_collection_days"))
    assert p.unlisted_amount == 0
    assert p.total == d["receivables_outstanding"]
    # Defterin tamamı gecikmiş — "alacaklar şişmiş" hikâyesinin sayısal karşılığı
    assert p.overdue_share == 1.0
    assert p.expected_loss > 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"test_receivables: {len(fns)} test geçti")
