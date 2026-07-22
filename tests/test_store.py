"""
test_store.py — Senaryo defterinin sözleşmesi.

En kritik iki garanti:
  1) Kullanıcı kazara bir analizini KAYBETMEZ (aynı ad üzerine yazmaz).
  2) Dışarıdan gelen bozuk bir defter dosyası uygulamayı ÇÖKERTMEZ —
     içe aktarma yolu, yükleme kadar güvenilmez bir girdi kapısıdır.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import store

_SENARYO = {"kredi": 10_000_000, "vade": 24, "faiz": 3.5}
_OZET = {"batma_yuzde": 94.3, "iflas_ayi": 8, "aylik_net": -100_000}


def test_kaydet_adds_without_mutating_input():
    """Girdi listesi bozulmamalı — çağıran taraf sürprize uğramasın."""
    defter = []
    yeni = store.kaydet(defter, "Baz", _SENARYO, _OZET)
    assert defter == [], "girdi listesi değiştirilmiş"
    assert len(yeni) == 1
    assert yeni[0]["ad"] == "Baz"
    assert yeni[0]["senaryo"]["kredi"] == 10_000_000


def test_duplicate_name_does_not_overwrite():
    """
    Aynı adı ikinci kez kullanmak eskisini SİLMEMELİ.
    Kullanıcı saatlerce uğraştığı bir analizi kazara kaybetmemeli.
    """
    d = store.kaydet([], "Baz", _SENARYO, _OZET)
    d = store.kaydet(d, "Baz", _SENARYO | {"kredi": 0}, _OZET)
    d = store.kaydet(d, "Baz", _SENARYO | {"kredi": 5_000_000}, _OZET)
    assert [k["ad"] for k in d] == ["Baz", "Baz (2)", "Baz (3)"]
    assert d[0]["senaryo"]["kredi"] == 10_000_000, "ilk kayıt ezilmiş"


def test_old_notebooks_without_the_new_column_still_load():
    """
    "En dip hafta" sütunu sonradan eklendi. Kullanıcının diskinde o alan
    OLMAYAN defterler var ve geri yüklendiklerinde tablo çökmemeli — kalıcılık
    kullanıcıda olduğu için eski dosyalar süresiz yaşar.
    """
    eski = store.kaydet([], "Eski kayıt", _SENARYO, _OZET)   # en_dip_hafta yok
    tablo = store.karsilastirma_tablosu(eski)
    assert tablo[0]["En dip hafta"] is None
    assert tablo[0]["Batma %"] == 94.3


def test_weekly_trough_is_carried_into_the_comparison_table():
    """Kredi karşılaştırmasının asıl sorusu: taksit yükü ay-içi dibi ne yapıyor?"""
    d = store.kaydet([], "Kredili", _SENARYO, _OZET | {"en_dip_hafta": -450_000})
    assert store.karsilastirma_tablosu(d)[0]["En dip hafta"] == -450_000


def test_name_is_sanitised_and_never_empty():
    d = store.kaydet([], "   ", _SENARYO, _OZET)
    assert d[0]["ad"].strip(), "boş ad kabul edilmiş"
    d = store.kaydet([], "a" * 200, _SENARYO, _OZET)
    assert len(d[0]["ad"]) <= store.MAX_AD
    d = store.kaydet([], "Kötü\x00\x07ad", _SENARYO, _OZET)
    assert "\x00" not in d[0]["ad"] and "\x07" not in d[0]["ad"]


def test_book_is_capped():
    """Defter sınırsız büyümemeli (oturum belleği)."""
    d = []
    for i in range(store.MAX_KAYIT + 8):
        d = store.kaydet(d, f"S{i}", _SENARYO, _OZET)
    assert len(d) == store.MAX_KAYIT
    assert d[-1]["ad"] == f"S{store.MAX_KAYIT + 7}", "en yeni kayıt kaybolmuş"


def test_sil_removes_only_the_named_one():
    d = store.kaydet(store.kaydet([], "A", _SENARYO, _OZET), "B", _SENARYO, _OZET)
    d = store.sil(d, "A")
    assert [k["ad"] for k in d] == ["B"]
    # Olmayan adı silmek sorun olmamalı
    assert store.sil(d, "yok") == d


def test_export_import_roundtrip():
    d = store.kaydet(store.kaydet([], "Baz", _SENARYO, _OZET),
                     "Kredisiz", _SENARYO | {"kredi": 0}, _OZET)
    geri = store.ice_aktar(store.disa_aktar(d))
    assert [k["ad"] for k in geri] == ["Baz", "Kredisiz"]
    assert geri[1]["senaryo"]["kredi"] == 0


def test_corrupt_import_never_crashes():
    """
    İçe aktarma, dosya yükleme kadar güvenilmez bir kapı. Ne gelirse gelsin
    liste dönmeli — istisna değil.
    """
    kotu = [
        b"", b"{", b"null", b"[]", b"3",
        b'{"kayitlar": "metin"}',
        b'{"kayitlar": [1, 2, 3]}',
        b'{"kayitlar": [{"ad": "x"}]}',                 # senaryo yok
        b'{"kayitlar": [{"senaryo": "dizi degil"}]}',
        b'\xff\xfe\x00gecersiz utf8',
        json_bomba := b'{"kayitlar": [' + b'{"ad":"x","senaryo":{}},' * 100 + b'{"ad":"y","senaryo":{}}]}',
    ]
    for ham in kotu:
        sonuc = store.ice_aktar(ham)
        assert isinstance(sonuc, list), f"{ham[:24]!r} liste döndürmedi"
        assert len(sonuc) <= store.MAX_KAYIT
    # Çok kayıtlı dosya da tavana kırpılmalı
    assert len(store.ice_aktar(json_bomba)) == store.MAX_KAYIT


def test_imported_names_are_deduplicated():
    """Dosyada aynı ad tekrarlıysa içe aktarma sonrası da ayrışmalı."""
    ham = b'{"kayitlar": [{"ad":"A","senaryo":{}},{"ad":"A","senaryo":{}}]}'
    adlar = [k["ad"] for k in store.ice_aktar(ham)]
    assert len(set(adlar)) == len(adlar), f"tekrar eden ad: {adlar}"


def test_comparison_table_shape():
    d = store.kaydet([], "Baz", _SENARYO, _OZET)
    satir = store.karsilastirma_tablosu(d)[0]
    assert satir["Senaryo"] == "Baz"
    assert satir["Kredi"] == 10_000_000
    assert satir["Batma %"] == 94.3


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
