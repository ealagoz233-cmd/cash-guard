"""
test_scenario.py — URL'de taşınan senaryonun dayanıklılığı.

Adres çubuğu kullanıcının (ve internetteki herkesin) elindedir. Bu testlerin
asıl amacı tek bir garantiyi sabitlemek: BOZUK BİR URL UYGULAMAYI ÇÖKERTEMEZ.
Sürgü sınırlarının app.py ile aynı kaldığını da burada kontrol ediyoruz —
ayrışırlarsa paylaşılan link sessizce başka bir senaryo gösterir.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import scenario as sc


def test_empty_url_gives_defaults():
    """Parametresiz açılış varsayılan senaryoyu vermeli."""
    assert sc.from_query_params({}) == sc.varsayilanlar()


def test_valid_params_are_read():
    s = sc.from_query_params({"kredi": "5000000", "vade": "36", "faiz": "2.5"})
    assert s["kredi"] == 5_000_000
    assert s["vade"] == 36
    assert s["faiz"] == 2.5
    # Dokunulmayanlar varsayılanda kalmalı
    assert s["oynaklik"] == 10


def test_out_of_range_values_are_clamped():
    """Aralık dışı değer reddedilmez, kırpılır — kullanıcı boş ekran görmesin."""
    s = sc.from_query_params({"kredi": "999999999", "vade": "1", "faiz": "-5"})
    assert s["kredi"] == 30_000_000     # üst sınır
    assert s["vade"] == 6               # alt sınır
    assert s["faiz"] == 0.0


def test_garbage_url_never_crashes():
    """
    Asıl güvence: adres çubuğuna ne yazılırsa yazılsın uygulama ayakta kalır
    ve TAM bir senaryo döner.
    """
    kotu = [
        {"kredi": "abc"},
        {"kredi": ""},
        {"kredi": None},
        {"kredi": "1e400"},                 # sonsuza taşan
        {"kredi": "nan"},
        {"vade": ["36", "48"]},             # liste değer
        {"bilinmeyen_anahtar": "x"},
        {"kredi": "5_000_000"},             # alt çizgili
        {"faiz": "3,5"},                    # Türkçe ondalık
        {"kredi": "<script>alert(1)</script>"},
        {"vade": "0x10"},
    ]
    for qp in kotu:
        s = sc.from_query_params(qp)
        assert set(s) == set(sc.varsayilanlar()), f"{qp}: eksik alan"
        for a in sc.ALANLAR:
            assert a.alt <= s[a.anahtar] <= a.ust, f"{qp}: {a.anahtar} aralık dışı"


def test_roundtrip_is_stable():
    """URL'e yaz, geri oku: senaryo değişmemeli."""
    s = sc.from_query_params({"kredi": "7500000", "vade": "48", "faiz": "1.5"})
    assert sc.from_query_params(sc.to_query_params(s)) == s


def test_defaults_are_omitted_from_url():
    """Link kısa kalsın: yalnızca değiştirilenler yazılır."""
    assert sc.to_query_params(sc.varsayilanlar()) == {}
    s = sc.varsayilanlar() | {"kredi": 5_000_000}
    assert sc.to_query_params(s) == {"kredi": "5000000"}


def test_slider_bounds_match_the_app():
    """
    Sürgü sınırları app.py ile AYNI olmalı.

    Ayrışırlarsa paylaşılan bir link sessizce başka bir senaryo gösterir:
    URL 30M kredi der, sürgü 20M'de durur, kullanıcı farkı göremez.
    app.py'yi metin olarak okuyup sınırları karşılaştırıyoruz — kırılgan ama
    sessiz ayrışmadan iyidir.
    """
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kaynak = open(os.path.join(kok, "app.py"), encoding="utf-8").read()

    beklenen = {
        "kredi": (0, 30_000_000),
        "vade": (6, 60),
        "gelirdus": (0, 40),
        "gecikme": (0, 80),
        "kayan": (0, 80),
        "giderart": (0, 40),
        "oynaklik": (5, 40),
    }
    for anahtar, (alt, ust) in beklenen.items():
        alan = sc._ALAN_HARITASI[anahtar]
        assert (alan.alt, alan.ust) == (alt, ust), (
            f"{anahtar}: scenario.py ({alan.alt}, {alan.ust}) app.py ile uyuşmuyor"
        )

    # app.py'de gerçekten bu sınırlarla sürgü var mı (kaba ama etkili kontrol)
    assert "0, 30_000_000" in kaynak, "kredi sürgüsünün sınırı değişmiş olabilir"
    assert '"Vade (ay)", 6, 60' in kaynak, "vade sürgüsünün sınırı değişmiş olabilir"


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
