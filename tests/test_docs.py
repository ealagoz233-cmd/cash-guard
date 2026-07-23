"""
test_docs.py  —  README'nin kendi hakkında söylediği sayılar doğru mu
─────────────────────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q     (veya: python tests/test_docs.py)

README bir kez "39 test, üç dosyada" derken gerçek sayı 108'e çıkmıştı ve kimse
fark etmedi — çünkü hiçbir şey o cümleyi kontrol etmiyordu. Bayat doküman, yanlış
dokümandır: okuyan kişi projenin ne kadar test edildiğini olduğundan küçük görür.

Bu dosya README'deki test sayılarını fiilen sayılan testlerle karşılaştırır. Yeni
bir test dosyası eklendiğinde CI kırmızıya döner ve README'yi güncellemek zorunda
kalırsın. Sayım için pytest'i çağırmak yerine AST kullanılır: test toplamak için
testin kendisini çalıştırmak gerekmesin.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TESTS_DIR = ROOT / "tests"
README = ROOT / "README.md"


def _count_tests(path: Path) -> int:
    """Bir dosyadaki üst seviye `test_*` fonksiyonlarını sayar."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1 for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


def _actual_counts() -> dict[str, int]:
    return {p.name: _count_tests(p) for p in sorted(TESTS_DIR.glob("test_*.py"))}


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_total_test_count_is_current():
    """README'nin manşet sayısı ('**108 test**') gerçek toplamı tutmalı."""
    actual = sum(_actual_counts().values())
    m = re.search(r"\*\*(\d+) test\*\*", _readme())
    assert m, "README'de '**N test**' manşeti bulunamadı"
    claimed = int(m.group(1))
    assert claimed == actual, (
        f"README {claimed} test diyor ama tests/ altında {actual} test var. "
        f"README'deki Testler bölümünü güncelle."
    )


def test_readme_lists_every_test_file_with_right_count():
    """Her test dosyası README tablosunda doğru sayıyla anılmalı."""
    text = _readme()
    for name, count in _actual_counts().items():
        assert f"`{name}`" in text, f"{name} README'deki test tablosunda yok"
        m = re.search(rf"`{re.escape(name)}` \((\d+)\)", text)
        assert m, f"{name} için README'de '(sayı)' yazmıyor"
        assert int(m.group(1)) == count, (
            f"README {name} için {m.group(1)} test diyor, gerçek sayı {count}"
        )


def test_readme_file_tree_test_count_is_current():
    """Dosya ağacındaki 'N test, M dosya' yorumu da bayatlamamalı."""
    counts = _actual_counts()
    m = re.search(r"# (\d+) test, (\d+) dosya", _readme())
    assert m, "README dosya ağacında 'N test, M dosya' yorumu bulunamadı"
    assert int(m.group(1)) == sum(counts.values())
    assert int(m.group(2)) == len(counts)


# ── Kaynak dosyaları da ağaçta olmalı ─────────────────────────────────────
# Test sayıları bağlıydı ama kaynak dosyalar değildi ve boşluk kendini
# gösterdi: `utils/sufficiency.py` eklendi, ağaca girmedi, hiçbir şey itiraz
# etmedi. Ağaçta olmayan bir modül, okuyan için var olmayan bir modüldür —
# üstelik mimariyi anlatan bölüm tam da o ağaç.
KAYNAK_KLASORLERI = ("modules", "utils")


def _kaynak_dosyalari() -> list[Path]:
    return sorted(
        p for klasor in KAYNAK_KLASORLERI
        for p in (ROOT / klasor).glob("*.py")
        if p.name != "__init__.py"
    )


def test_readme_file_tree_lists_every_source_module():
    """`modules/` ve `utils/` altındaki her modül dosya ağacında anılmalı."""
    text = _readme()
    eksik = [f"{p.parent.name}/{p.name}" for p in _kaynak_dosyalari()
             if p.name not in text]
    assert not eksik, (
        f"README dosya ağacında olmayan modüller: {', '.join(eksik)}. "
        f"Ağaç mimariyi anlatıyor; orada olmayan modül okuyan için yoktur."
    )


def test_readme_file_tree_has_no_module_that_was_deleted():
    """
    Tersi de geçerli: silinen bir modül ağaçta durmaya devam etmemeli.

    Bayat dokümanın iki yönü var ve ikincisi daha sinsi — okuyan, var olmayan
    bir dosyayı arar ve deponun kendisinden şüphe eder.
    """
    gercek = {p.name for p in _kaynak_dosyalari()}
    # Ağaçtaki `<isim>.py  # açıklama` satırlarını topla (yalnızca ağaç bloğu).
    agac = re.search(r"cash-guard/\n(.*?)\n```", _readme(), re.S)
    assert agac, "README'de dosya ağacı bloğu bulunamadı"
    anilan = set(re.findall(r"([a-z_]+\.py)", agac.group(1)))
    kok_dosyalar = {"app.py", "api.py"}
    hayalet = {ad for ad in anilan - gercek - kok_dosyalar
               if not ad.startswith("test_")}
    assert not hayalet, f"README ağacında artık var olmayan dosyalar: {hayalet}"


# ══════════════════════════════════════════════════════════════════════════
#  README'NİN DEMO SAYILARI
# ══════════════════════════════════════════════════════════════════════════
# Test sayıları ve dosya ağacı bağlıydı; README'nin okuyanı asıl ikna eden
# sayıları — %94,3 batma, ₺5,5M şüpheli alacak, ₺1,81M ay-içi dip, taramanın
# %9,9'u, 25 aylık tuzak — bağlı DEĞİLDİ. Yani bu depoda üç kez yaşanmış olan
# şey (kalibrasyon değişir, README eski rakamı söylemeye devam eder) tam da en
# görünür, en çok alıntılanan cümlelerde serbest kalmıştı.
#
# Bu blok o cümlelerden sayıyı REGEX'LE SÖKER ve motoru koşturup karşılaştırır.
# Beklenen değer testin içine YAZILMIYOR: iki taraf da hesaptan geliyor. Test
# "README ile motor aynı şeyi söylüyor mu" diye soruyor, "ikisi de benim
# ezberimi tekrarlıyor mu" diye değil — üçüncü bir kopya eklemek, bayatlayacak
# üçüncü bir yer eklemek olurdu.
_DEMO: dict[str, float] | None = None


def _demo() -> dict[str, float]:
    """Demo şirketin varsayılan senaryodaki bütün manşet sayıları (bir kez)."""
    global _DEMO
    if _DEMO is not None:
        return _DEMO

    from modules import loan_simulator as ls
    from modules import loan_sweep, monte_carlo as mc, receivables
    from modules import runway, scenario, weekly, zscore
    from modules.data_io import load_mock

    d = load_mock()
    kasa = d["current_cash"]
    tahsilat = d["avg_monthly_collections"]
    gider = d["avg_monthly_fixed_expense"]
    servis = d["existing_monthly_debt_service"]

    # Sürgü varsayılanları tek kaynaktan okunur; README de o ekranı anlatıyor.
    v = scenario.varsayilanlar()
    taban = mc.StressParams(
        kasa, tahsilat, gider, servis,
        income_drop=v["gelirdus"] / 100, volatility=v["oynaklik"] / 100,
        delay_prob=v["gecikme"] / 100, delay_severity=v["kayan"] / 100,
        expense_inflation=v["giderart"] / 100)

    r = mc.run(taban)
    p = receivables.age(d["top_receivables"], d["receivables_outstanding"],
                        d["avg_monthly_revenue"])
    w = weekly.build(kasa, tahsilat, d["expense_breakdown"], gider, servis,
                     start=weekly.parse_start(d["as_of"]))
    tarama = loan_sweep.sweep(taban, ls.LoanScenario(
        kasa, tahsilat, gider, servis, 0.0, v["vade"], v["faiz"] / 100))

    _DEMO = {
        "batma_pct": r.ruin_probability * 100,
        "iflas_ayi": r.expected_ruin_month,
        "z_skor": zscore.from_company(d).score,
        "supheli": p.expected_loss,
        "statik_ay": runway.static_runway(kasa, tahsilat - gider - servis),
        "trend_ay": runway.trend_runway(d["history"], kasa, servis).months,
        "hafta_son": w.end_cash,
        "hafta_dip": w.min_week.closing_cash,
        "hafta_fark": w.intramonth_gap,
        "tarama_taban_pct": tarama.baseline.ruin_probability * 100,
        "tarama_en_iyi_pct": tarama.best.ruin_probability * 100,
        "tarama_one_ay": abs(tarama.best.relief_months),
    }
    return _DEMO


def _sayi(metin: str) -> float:
    """
    README'deki Türkçe biçimli sayıyı float'a çevirir.

    '1,81' -> 1.81 (virgül ondalık), '40.552' -> 40552 (nokta binlik).
    """
    if "," in metin:
        return float(metin.replace(".", "").replace(",", "."))
    return float(metin.replace(".", "")) if metin.count(".") == 1 and \
        len(metin.rsplit(".", 1)[1]) == 3 else float(metin)


def _iddia(desen: str) -> tuple[float, ...]:
    """README'den bir cümleyi bulup içindeki sayıları söker."""
    m = re.search(desen, _readme(), re.S)
    assert m, f"README'de şu iddia bulunamadı (cümle değişmiş olabilir): {desen}"
    return tuple(_sayi(g) for g in m.groups())


def test_readme_headline_ruin_probability_matches_the_engine():
    """Manşet sayı: '12 ayda kasanın sıfırlanma olasılığı: %94.3'."""
    (iddia,) = _iddia(r"kasanın sıfırlanma olasılığı:\s*\*\*%(\d+[.,]\d)\*\*")
    gercek = _demo()["batma_pct"]
    assert abs(iddia - gercek) < 0.05, \
        f"README %{iddia} diyor, motor %{gercek:.1f} veriyor"


def test_readme_cash_life_ladder_matches_the_three_engines():
    """Merdivenin üç basamağı üç ayrı motordan gelir; üçü de tutmalı."""
    statik, trend, mcarlo = _iddia(
        r"bugünkü yakım sabit kalırsa \*\*~(\d+) ay\*\*.*?"
        r"eğilimi sürerse \*\*~(\d+) ay\*\*.*?temerrüt \*\*(\d+)\. ay\*\*")
    d = _demo()
    assert round(d["statik_ay"]) == statik, f"statik: {d['statik_ay']:.1f} ay"
    assert d["trend_ay"] == trend, f"trend: {d['trend_ay']} ay"
    assert round(d["iflas_ayi"]) == mcarlo, f"Monte Carlo: {d['iflas_ayi']:.1f}"
    # Merdivenin kendisi iddia: her basamak bir öncekinden kısa olmalı.
    assert statik > trend > mcarlo


def test_readme_loan_sweep_numbers_match_the_sweep():
    """
    README'nin en iddialı cümlesi: risk düşerken iflas ÖNE geliyor.

    Üç sayı da taramadan gelir ve ancak birlikte anlam taşır — biri bayatlarsa
    cümle "öneri motoru değil, tuzak detektörü" iddiasını çürütür.
    """
    taban, en_iyi, one = _iddia(
        r"\*\*%(\d+[.,]\d) → %(\d+[.,]\d)\*\* düşüyor ama iflas \*\*(\d+) ay öne\*\*")
    d = _demo()
    assert abs(taban - d["tarama_taban_pct"]) < 0.05
    assert abs(en_iyi - d["tarama_en_iyi_pct"]) < 0.05
    assert one == d["tarama_one_ay"], \
        f"README {one:.0f} ay diyor, tarama {d['tarama_one_ay']} ay veriyor"
    # Ve tuzağın kendisi: risk gerçekten düşmeli, iflas gerçekten öne gelmeli.
    assert en_iyi < taban and one > 0


def test_readme_uncollectible_claim_matches_the_aging_engine():
    """'Demo şirkette bu ₺5,5 milyon — kasadaki paradan fazla.'"""
    (iddia,) = _iddia(r"Demo şirkette bu \*\*₺(\d+,\d) milyon\*\*")
    d = _demo()
    assert abs(iddia - d["supheli"] / 1e6) < 0.05, \
        f"README ₺{iddia}M diyor, yaşlandırma ₺{d['supheli']/1e6:.2f}M veriyor"
    # Cümlenin devamı da bir iddia: "kasadaki paradan fazla".
    from modules.data_io import load_mock
    assert d["supheli"] > load_mock()["current_cash"]


def test_readme_intramonth_dip_matches_the_weekly_table():
    """Üç sayı: dönem sonu kasası, en dip hafta ve aradaki fark."""
    son, dip, fark = _iddia(
        r"\*\*₺(\d+,\d+)M\*\* görünürken.*?\*\*₺(\d+,\d+)M\*\*'ye iniyor.*?"
        r"\*\*₺(\d+,\d+)M\*\*")
    d = _demo()
    assert abs(son - d["hafta_son"] / 1e6) < 0.01, f"dönem sonu: {d['hafta_son']:,.0f}"
    assert abs(dip - d["hafta_dip"] / 1e6) < 0.01, f"dip: {d['hafta_dip']:,.0f}"
    assert abs(fark - d["hafta_fark"] / 1e6) < 0.01, f"fark: {d['hafta_fark']:,.0f}"
    # Fark türetilmiş bir sayı; cümle kendi içinde de tutarlı olmalı.
    assert abs((son - dip) - fark) < 0.02


def test_readme_altman_contradiction_matches_both_engines():
    """
    Uygulamanın tezi tek cümlede: bilanço 'güvenli', nakit 'batıyor'.

    İki sayı iki ayrı motordan gelir; biri kayarsa cümle tezini kaybeder.
    """
    (skor,) = _iddia(r"demo şirkete \*\*(\d+\.\d+), güvenli bölge\*\*")
    (batma,) = _iddia(r"modeli \*\*%(\d+[.,]\d) batma\*\* diyor")
    d = _demo()
    assert abs(skor - d["z_skor"]) < 0.005, f"Altman: {d['z_skor']:.3f}"
    assert abs(batma - d["batma_pct"]) < 0.05
    # Çelişkinin kendisi iddia: skor güvenli eşiğin üstünde, batma olasılığı yüksek.
    from modules import zscore
    assert d["z_skor"] > zscore.Z_PRIME.safe_above
    assert d["batma_pct"] > 60


def test_readme_theil_sen_comparison_matches_the_real_fit():
    """
    'En küçük kareler −40.552 verirken Theil–Sen −28.211 buluyor.'

    Bu cümle bir yöntem tercihini savunuyor; sayılar bayatlarsa savunma
    dayanaksız kalır. İkisi de demo verisinden yeniden hesaplanabilir.
    """
    import numpy as np
    import pandas as pd

    from modules import runway
    from modules.data_io import load_mock

    ols_iddia, ts_iddia = _iddia(
        r"en küçük kareler −([\d.]+) ₺/ay veriyor, Theil–Sen ise\s*"
        r"−([\d.]+) ₺/ay")

    h = pd.DataFrame(load_mock()["history"])
    net = (h["collections"] - h["fixed_expense"]).to_numpy(dtype=float)
    x = np.arange(len(net), dtype=float)
    ts = runway._theil_sen(x, net)[0]
    ols = float(np.polyfit(x, net, 1)[0])

    assert abs(abs(ols) - ols_iddia) < 1, f"en küçük kareler: {ols:,.0f} ₺/ay"
    assert abs(abs(ts) - ts_iddia) < 1, f"Theil–Sen: {ts:,.0f} ₺/ay"
    # Cümlenin asıl iddiası: en küçük kareler eğimi belirgin biçimde ABARTIYOR.
    assert abs(ols) > abs(ts) * 1.3


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
