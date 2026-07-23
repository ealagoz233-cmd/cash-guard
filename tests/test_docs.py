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


if __name__ == "__main__":
    for fn in (test_readme_total_test_count_is_current,
               test_readme_lists_every_test_file_with_right_count,
               test_readme_file_tree_test_count_is_current,
               test_readme_file_tree_lists_every_source_module,
               test_readme_file_tree_has_no_module_that_was_deleted):
        fn()
        print(f"  ok  {fn.__name__}")
    print("test_docs: hepsi geçti")
