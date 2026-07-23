"""
test_data_integrity.py  —  Mock veri ve yükleme yolu koruma testleri
─────────────────────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q      (veya: python tests/test_data_integrity.py)

İki şeyi korur:

1) MOCK VERİ TUTARLILIĞI — data/mock_company_data.json'daki 12 aylık tablonun
   ortalamaları, dosyanın başındaki skaler değerlerle BİREBİR aynı olmalı ve
   kasa yürüyüşü tam tutmalı. Tutmazsa ekranda KPI kartı ile grafik altı yazı
   farklı sayı gösterir (bir kez oldu: KPI "400.000", yazı "450.000" diyordu).

2) YÜKLEME YOLU DAYANIKLILIĞI — kullanıcı CSV'sinde sütun eksikse uygulama
   çökmemeli (bir kez KeyError ile çöküyordu) ve mock şirketin verisi
   kullanıcının verisiymiş gibi ekranda kalmamalı.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.data_io import (REQUIRED_HISTORY_COLS, _to_float, load_mock,
                             normalize_history, parse_uploaded,
                             parse_uploaded_files, safe_display_name)
from utils.theme import esc, expense_label, kpi_card

DEBT_SERVICE_KEY = "existing_monthly_debt_service"


class FakeUpload(io.StringIO):
    """Streamlit'in UploadedFile'ını taklit eder (.name + okunabilir akış)."""

    def __init__(self, name: str, content: str):
        super().__init__(content)
        self.name = name


class BinaryUpload(io.BytesIO):
    """
    Streamlit'in GERÇEKTE verdiği şey: bayt akışı.

    FakeUpload metin (str) taşıyor ve bu yüzden kodlama sorunlarını hiç
    göstermiyordu — gerçek yüklemede kodlamayı pandas çözüyor. Türk Excel'i
    cp1254 + noktalı virgül yazdığı için o yol sınanmamış kalmıştı.
    """

    def __init__(self, name: str, content: bytes):
        super().__init__(content)
        self.name = name


# ══════════════════════════════════════════════════════════════════════════
#  1) Mock veri tutarlılığı
# ══════════════════════════════════════════════════════════════════════════
def test_history_averages_match_scalars():
    """Tablo ortalamaları üst düzey skalerlerle birebir aynı olmalı."""
    d = load_mock()
    h = pd.DataFrame(d["history"])
    assert h["revenue"].mean() == d["avg_monthly_revenue"]
    assert h["collections"].mean() == d["avg_monthly_collections"]
    assert h["fixed_expense"].mean() == d["avg_monthly_fixed_expense"]


def test_history_cash_walk_is_exact():
    """cash_end[t] = cash_end[t-1] + tahsilat − gider − borç servisi (sapma sıfır)."""
    d = load_mock()
    h = d["history"]
    ds = d[DEBT_SERVICE_KEY]
    for prev, cur in zip(h, h[1:]):
        expected = prev["cash_end"] + cur["collections"] - cur["fixed_expense"] - ds
        assert cur["cash_end"] == expected, (
            f"{cur['month']}: yazan {cur['cash_end']:,} ≠ hesaplanan {expected:,}")


def test_final_cash_matches_current_cash():
    """Tablonun son ayı, uygulamanın kullandığı mevcut kasa ile aynı olmalı."""
    d = load_mock()
    assert d["history"][-1]["cash_end"] == d["current_cash"]


def test_collections_below_revenue():
    """Hikâyenin özü: tahsilat her ay faturalanan gelirin altında kalmalı."""
    for row in load_mock()["history"]:
        assert row["collections"] < row["revenue"], f"{row['month']} boşluğu ters"


def test_expense_breakdown_sums_to_fixed_expense():
    """Gider kırılımı toplamı, aylık sabit gidere eşit olmalı (donut ortasındaki rakam)."""
    d = load_mock()
    assert sum(d["expense_breakdown"].values()) == d["avg_monthly_fixed_expense"]


# ══════════════════════════════════════════════════════════════════════════
#  2) Yükleme yolu dayanıklılığı
# ══════════════════════════════════════════════════════════════════════════
def test_minimal_csv_does_not_crash_chart():
    """Sadece revenue+fixed_expense olan CSV eskiden KeyError ile çökertiyordu."""
    d = parse_uploaded(FakeUpload("k.csv", "revenue,fixed_expense\n5000000,4000000\n4800000,4100000\n"))
    hist = pd.DataFrame(d["history"])
    assert REQUIRED_HISTORY_COLS <= set(hist.columns)   # grafik çizilebilir
    assert list(hist["collections"]) == list(hist["revenue"])  # tahsilat=gelir varsayımı


def test_upload_does_not_leak_mock_company():
    """Kullanıcı verisi yüklenince mock şirketin grafikleri ekranda kalmamalı."""
    d = parse_uploaded(FakeUpload("s.csv", "alan,deger\ncurrent_cash,500000\n"
                                           "avg_monthly_revenue,900000\n"
                                           "avg_monthly_fixed_expense,850000\n"))
    assert d["company_name"] == "Yüklenen Şirket Verisi"
    for leaked in ("history", "top_receivables", "expense_breakdown"):
        assert leaked not in d, f"mock '{leaked}' kullanıcı verisine sızdı"
    assert d["current_cash"] == 500000


def test_non_numeric_row_is_skipped():
    """Biçim A'da sayıya çevrilemeyen satır dosyayı geçersiz kılmamalı."""
    d = parse_uploaded(FakeUpload("m.csv", "alan,deger\nnot,bu bir metin\ncurrent_cash,750000\n"))
    assert d is not None and d["current_cash"] == 750000


def test_unreadable_file_returns_none():
    """Alakasız dosya None dönmeli ki uygulama mock veriye düşsün."""
    assert parse_uploaded(FakeUpload("x.csv", "a,b\n1,2\n")) is None


# ══════════════════════════════════════════════════════════════════════════
#  2b) Yüklenen veri, yeni analiz katmanlarını da besliyor mu
# ══════════════════════════════════════════════════════════════════════════
# Yaşlandırma, Altman ve haftalık ufuk yalnızca demo veride çalışsaydı dört
# özellik de vitrin süsü olurdu. Buradaki testler o yolun ucunu tutuyor.
_ALACAK_CSV = ("musteri,tutar,gecikme_gun\n"
               "Büyük Bayi A.Ş.,3.000.000,120\n"
               "Küçük Bayi Ltd.,1.000.000,10\n")

_BILANCO_CSV = ("alan,deger\n"
                "current_cash,4200000\n"
                "avg_monthly_revenue,7200000\n"
                "avg_monthly_fixed_expense,5950000\n"
                "sector,Üretim / Tekstil\n"
                "as_of,2026-06-30\n"
                "company_name,Test Sanayi A.Ş.\n"
                "total_assets,60000000\n"
                "current_assets,30000000\n"
                "current_liabilities,20000000\n"
                "total_liabilities,28000000\n"
                "retained_earnings,20000000\n"
                "annual_depreciation,2800000\n"
                "gider_personel,2650000\n"
                "gider_kira_ve_isletme,780000\n"
                "gider_hammadde_ve_tedarik,2520000\n")


def test_uploaded_receivables_feed_the_aging_engine():
    """Biçim C yüklenince yaşlandırma paneli gerçek veriyle çalışmalı."""
    from modules.receivables import age

    d = parse_uploaded(FakeUpload("alacak.csv", _ALACAK_CSV))
    assert d is not None
    assert len(d["top_receivables"]) == 2
    # Bakiye verilmediyse kalemlerin toplamı bakiye sayılır.
    assert d["receivables_outstanding"] == 4_000_000
    # En büyük kalem başta olmalı (grafik ve CFO metni buna güveniyor)
    assert d["top_receivables"][0]["amount"] == 3_000_000

    p = age(d["top_receivables"], d["receivables_outstanding"], 2_000_000)
    assert p.expected_loss == 3_000_000 * 0.50 + 1_000_000 * 0.05
    assert p.dso == 60.0


def test_uploaded_balance_sheet_feeds_the_altman_score():
    """Biçim A'daki düz bilanço anahtarları iç içe sözlüğe toplanmalı."""
    from modules import zscore

    d = parse_uploaded(FakeUpload("bilanco.csv", _BILANCO_CSV))
    assert d is not None
    assert d["balance_sheet"]["total_assets"] == 60_000_000
    r = zscore.from_company(d)
    assert r.available, f"skor üretilmedi: {r.missing_fields}"
    # Sektörde "Üretim" geçiyor → imalatçı modeli seçilmeli
    assert r.model_key == zscore.Z_PRIME.key


def test_uploaded_text_fields_are_not_treated_as_broken_numbers():
    """
    Şirket adı, sektör ve tarih sayı değil. Bunlara "sayıya çevrilemedi" uyarısı
    vermek, doğru şeyi yapan kullanıcıyı cezalandırmak olurdu.
    """
    d = parse_uploaded(FakeUpload("bilanco.csv", _BILANCO_CSV))
    assert d["company_name"] == "Test Sanayi A.Ş."
    assert d["as_of"] == "2026-06-30"
    assert "Üretim" in d["sector"]


def test_uploaded_expense_breakdown_makes_the_weekly_view_informative():
    """`gider_*` anahtarları olmadan haftalık tablo ay-içi bilgi taşıyamaz."""
    from modules import weekly

    d = parse_uploaded(FakeUpload("bilanco.csv", _BILANCO_CSV))
    assert set(d["expense_breakdown"]) == {"personel", "kira_ve_isletme",
                                           "hammadde_ve_tedarik"}
    plan = weekly.build(d["current_cash"], d["avg_monthly_collections"],
                        d.get("expense_breakdown"),
                        d["avg_monthly_fixed_expense"], 0,
                        start=weekly.parse_start(d["as_of"]))
    assert plan.informative
    assert plan.weeks[0].start.isoformat() == "2026-07-01"


def test_several_files_merge_into_one_company():
    """
    Mizan, aylık rapor ve yaşlandırma kaynak sistemlerde ayrı dosyalardır.
    Kullanıcıyı elle birleştirmeye zorlamak yerine hepsi kabul edilir; her
    dosya kendi alanlarını yazar, dokunmadığına karışmaz.
    """
    d = parse_uploaded_files([
        FakeUpload("a.csv", _BILANCO_CSV),
        FakeUpload("c.csv", _ALACAK_CSV),
        FakeUpload("b.csv", "month,revenue,fixed_expense,collections\n"
                            "2026-01,7000000,5900000,6600000\n"
                            "2026-02,7100000,5950000,6700000\n"),
    ])
    assert d is not None
    assert d["balance_sheet"]["total_assets"] == 60_000_000   # A'dan
    assert len(d["top_receivables"]) == 2                     # C'den
    assert len(d["history"]) == 2                             # B'den
    assert d["avg_monthly_revenue"] == 7_050_000              # B, A'yı ezdi


def test_merging_is_order_independent_for_untouched_fields():
    """Sıra, bir dosyanın DOKUNMADIĞI alanı etkilememeli."""
    ileri = parse_uploaded_files([FakeUpload("a.csv", _BILANCO_CSV),
                                  FakeUpload("c.csv", _ALACAK_CSV)])
    geri = parse_uploaded_files([FakeUpload("c.csv", _ALACAK_CSV),
                                 FakeUpload("a.csv", _BILANCO_CSV)])
    assert ileri["balance_sheet"] == geri["balance_sheet"]
    assert ileri["top_receivables"] == geri["top_receivables"]
    assert ileri["current_cash"] == geri["current_cash"]


def test_one_unrecognised_file_does_not_discard_the_good_ones():
    """Üç dosyadan biri bozuksa diğer ikisinin verisi çöpe gitmemeli."""
    d = parse_uploaded_files([FakeUpload("cop.csv", "a,b\n1,2\n"),
                              FakeUpload("a.csv", _BILANCO_CSV)])
    assert d is not None
    assert d["current_cash"] == 4_200_000


def test_full_template_round_trips_into_every_optional_group():
    """
    Şablonun tek işi var: indir, doldur, yükle. Opsiyonel gruplar (bilanço,
    gider dağılımı, alacak) şablonda görünüp ayrıştırıcıya ULAŞMAZSA kullanıcı
    o alanları doldurup hiçbir şeyin değişmediğini görür.
    """
    from modules.data_io import (BICIM_A_BILANCO, ornek_sablon)

    class _Dosya(io.BytesIO):
        name = "cash_guard_sablon.csv"

    d = parse_uploaded(_Dosya(ornek_sablon()))
    assert d is not None
    assert set(d["balance_sheet"]) == set(BICIM_A_BILANCO)
    assert d["expense_breakdown"], "gider dağılımı şablondan okunmadı"
    assert d["receivables_outstanding"] > 0


def test_uploader_can_supply_every_field_the_zscore_needs():
    """
    Yükleyicinin bilanço grubu ile Z-score'un istediği alanlar ayrışmamalı.

    Ayrışırsa CSV yükleyen kullanıcı o alanı DOLDURAMAZ: şablonda yoktur,
    ayrıştırıcı okumaz ve skor kalıcı olarak boş kalır — üstelik ekranda
    "eksik alan" diye kendi yazamayacağı bir ad görür.
    """
    from modules.data_io import BICIM_A_BILANCO
    from modules.zscore import REQUIRED_FIELDS

    # `annual_sales` ve `ebit_annual` bilerek yok: ikisi de aylık skalerlerden
    # türetiliyor (bkz. zscore.from_company), kullanıcıdan ayrıca istenmiyor.
    turetilenler = {"annual_sales", "ebit_annual"}
    istenen = set(REQUIRED_FIELDS) - turetilenler
    assert istenen <= set(BICIM_A_BILANCO), (
        f"yükleyici bu alanları kabul etmiyor: {istenen - set(BICIM_A_BILANCO)}")


def test_mock_only_keys_cover_every_writable_group():
    """
    Bir biçimin yazabildiği her alan, mock'tan arındırılan listede olmalı.

    Aksi hâlde kullanıcı o grubu yüklediğinde örnek şirketin değeri ekranda
    kalır ve kendi verisi sanılır — yükleyicinin baştan beri engellediği hata.
    """
    from modules.data_io import (BICIM_A_ALACAK, BICIM_A_METIN,
                                 _MOCK_ONLY_KEYS)

    yazilabilir = ({"history", "top_receivables", "expense_breakdown",
                    "balance_sheet"} | set(BICIM_A_ALACAK) | set(BICIM_A_METIN))
    assert yazilabilir <= set(_MOCK_ONLY_KEYS)


def test_every_format_a_group_reaches_the_template_and_the_parser():
    """
    Alan tablosuna eklenen bir grup, ŞABLONDA da AYRIŞTIRICIDA da görünmeli.

    Üçü (tablo, şablon, ayrıştırıcı) elle senkron tutuluyordu; unutmanın
    belirtisi sessizdi — kullanıcı ya alanı hiç göremiyor ya da doldurup
    hiçbir şeyin değişmediğini görüyordu. Bu test o üçlüyü birbirine bağlar.
    """
    from modules.data_io import BICIM_A_GRUPLARI, ornek_sablon

    class _Dosya(io.BytesIO):
        name = "cash_guard_sablon.csv"

    sablon = ornek_sablon().decode("utf-8")
    d = parse_uploaded(_Dosya(ornek_sablon()))
    assert d is not None

    for grup in BICIM_A_GRUPLARI:
        hedef = d.get(grup.hedef, {}) if grup.hedef else d
        for alan in grup.alanlar:
            assert f"\n{alan}," in sablon, f"{alan} şablonda yok"
            if grup.metin:
                continue      # metin alanları şablonda bilerek boş bırakılır
            assert alan in hedef, f"{alan} ayrıştırıcıya ulaşmıyor"


def test_mock_only_list_follows_the_group_table():
    """
    "Taşınmaz" listesi elle sayılmamalı: tabloda `yalniz_mock` işaretli her grup
    otomatik olarak listeye girmeli, çekirdek skalerler ise GİRMEMELİ.

    Çekirdeğin dışarıda kalması bilinçli — onlarsız uygulama hiçbir şey
    hesaplayamaz, o yüzden kullanıcı vermezse örnek değer kalır.
    """
    from modules.data_io import BICIM_A_GRUPLARI, _MOCK_ONLY_KEYS

    for grup in BICIM_A_GRUPLARI:
        for anahtar in grup.tasinmaz_anahtarlar:
            assert anahtar in _MOCK_ONLY_KEYS, f"{anahtar} listede yok"
        if not grup.yalniz_mock:
            assert not (set(grup.alanlar) & set(_MOCK_ONLY_KEYS)), (
                "çekirdek skalerler arındırılırsa uygulama hesap yapamaz")


def test_parse_warnings_come_back_as_data_not_as_screen_writes():
    """
    Uyarılar ayrıştırmanın DÖNÜŞ değerinin parçası olmalı, yan etkisi değil.

    Ekrana doğrudan yazıldıkları sürece ayrıştırma önbelleğe alınamaz (ikinci
    koşuda uyarı sessizce kaybolur) ve modül arayüze bağlı kalır.
    """
    from modules.data_io import parse_files

    sonuc = parse_files([FakeUpload("cop.csv", "a,b\n1,2\n")])
    assert sonuc.data is None
    assert [u.seviye for u in sonuc.uyarilar] == ["error"]
    assert "cop.csv" in sonuc.uyarilar[0].mesaj

    okunamayan = parse_files([FakeUpload("kirik.csv", "alan,deger\ncurrent_cash,abc\n")])
    assert okunamayan.data is not None
    assert any(u.seviye == "warning" for u in okunamayan.uyarilar)


def test_comment_rows_in_the_template_are_skipped_not_warned_about():
    """
    Şablon ve README, grupları '# ── bilanço ──' satırlarıyla ayırıyor. Bunlar
    "sayıya çevrilemedi" uyarısı üretseydi, dokümandaki örneği kopyalayan
    kullanıcı doğru şeyi yaptığı hâlde uyarı alırdı.
    """
    d = parse_uploaded(FakeUpload("y.csv",
                                  "alan,deger\n"
                                  "# ── bölüm başlığı ──,\n"
                                  "\n"
                                  "current_cash,123456\n"))
    assert d is not None and d["current_cash"] == 123456
    # Yorum satırı hiçbir gruba sızmamalı
    assert not any(k.startswith("#") for k in d.get("balance_sheet", {}))
    assert not any(k.startswith("#") for k in d.get("expense_breakdown", {}))


def test_receivables_template_round_trips():
    """Ürettiğimiz alacak şablonu, kendi ayrıştırıcımızdan geçmeli."""
    from modules.data_io import ornek_alacak_sablonu

    ham = ornek_alacak_sablonu().decode("utf-8")
    d = parse_uploaded(FakeUpload("alacaklar.csv", ham))
    assert d is not None, "kendi ürettiğimiz alacak şablonu ayrıştırılamadı"
    assert len(d["top_receivables"]) == len(load_mock()["top_receivables"])
    assert d["receivables_outstanding"] == load_mock()["receivables_outstanding"]


def test_normalize_history_fills_missing_columns():
    df = normalize_history(pd.DataFrame({"revenue": [100, 200]}))
    assert list(df["month"]) == ["1. ay", "2. ay"]
    assert list(df["collections"]) == [100, 200]


# ══════════════════════════════════════════════════════════════════════════
#  3) HTML enjeksiyonu — arayüz unsafe_allow_html kullanıyor
# ══════════════════════════════════════════════════════════════════════════
def test_uploaded_filename_cannot_inject_html():
    """
    Yüklenen dosyanın adı sidebar'da HAM HTML içinde gösteriliyor. Bir kez
    kaçırılmadan basılıyordu; `<img src=x onerror=...>.csv` adlı bir dosya
    sayfaya kod enjekte edebiliyordu.
    """
    zararli = "<img src=x onerror=alert(1)>.csv"
    d = parse_uploaded(FakeUpload(zararli, "alan,deger\ncurrent_cash,500000\n"
                                           "avg_monthly_revenue,900000\n"))
    assert "<" not in d["as_of"] and ">" not in d["as_of"], d["as_of"]


def test_safe_display_name_strips_markup_and_limits_length():
    assert "<" not in safe_display_name("<script>alert(1)</script>.csv")
    assert len(safe_display_name("a" * 500)) <= 61          # 60 + kısaltma işareti
    assert safe_display_name("<<<>>>") == "yüklenen dosya"  # her şey elenirse
    assert safe_display_name("2026 Bütçe (nihai).xlsx") == "2026 Bütçe (nihai).xlsx"


def test_esc_neutralizes_markup():
    assert esc("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"


def test_kpi_card_escapes_its_fields():
    """KPI kartına giren metin ham HTML olarak yorumlanmamalı."""
    assert "<script>" not in kpi_card("<script>", "<script>", "<script>")


# ══════════════════════════════════════════════════════════════════════════
#  4) Türkçe etiketler (.title() bozuyordu)
# ══════════════════════════════════════════════════════════════════════════
def test_turkish_expense_labels():
    assert expense_label("kira_ve_isletme") == "Kira ve İşletme"
    assert expense_label("hammadde_ve_tedarik") == "Hammadde ve Tedarik"
    assert expense_label("bilinmeyen_kalem") == "Bilinmeyen kalem"   # fallback


def test_every_mock_expense_key_has_a_label():
    """Mock'taki her gider anahtarının düzgün Türkçe karşılığı olmalı."""
    for key in load_mock()["expense_breakdown"]:
        label = expense_label(key)
        assert "_" not in label
        assert " Ve " not in label, f"{key}: .title() bugu geri gelmiş"


# ══════════════════════════════════════════════════════════════════════════
#  Gerçek dünya CSV'leri — Türk Excel'inin ürettiği biçimler
# ══════════════════════════════════════════════════════════════════════════
_KV_SATIRLARI = ("alan,deger\n"
                 "current_cash,5000000\n"
                 "avg_monthly_collections,3000000\n"
                 "avg_monthly_fixed_expense,3500000\n")


def test_turkish_excel_exports_are_accepted():
    """
    Türk Excel'i CSV'yi cp1254 kodlamayla ve NOKTALI VİRGÜLLE yazar.

    İkisi de eskiden dosyayı komple düşürüyordu: kodlama hata veriyordu,
    yanlış ayırıcı ise hata bile atmadan tek sütunlu tablo üretip sessizce
    None döndürüyordu. Hedef kitle Türkiye'deki KOBİ'ler olduğu için bu
    "nadir uç durum" değil, en olası yükleme biçimiydi.
    """
    senaryolar = {
        "UTF-8 düz": _KV_SATIRLARI.encode("utf-8"),
        "UTF-8 BOM": _KV_SATIRLARI.encode("utf-8-sig"),
        "noktalı virgül": _KV_SATIRLARI.replace(",", ";").encode("utf-8"),
        "cp1254": "alan,deger\nşirket_notu,x\ncurrent_cash,5000000\n".encode("cp1254"),
        "sekme": _KV_SATIRLARI.replace(",", "\t").encode("utf-8"),
    }
    for ad, bayt in senaryolar.items():
        d = parse_uploaded(BinaryUpload("rapor.csv", bayt))
        assert d is not None, f"{ad}: dosya reddedildi"
        assert d["current_cash"] == 5_000_000, f"{ad}: kasa yanlış okundu"


def test_turkish_number_format_is_parsed():
    """
    '5.000.000,50' düz float() ile patlıyordu; satır sessizce atlanınca o
    alanda MOCK ŞİRKETİN rakamı ekranda kalıyordu — kullanıcı kendi verisine
    baktığını sanarak başkasının sayısını görüyordu.
    """
    assert _to_float("5.000.000") == 5_000_000
    assert _to_float("5.000.000,50") == 5_000_000.5
    assert _to_float("3,5") == 3.5
    assert _to_float("₺ 1.200 ") == 1_200
    assert _to_float("-100.000") == -100_000
    assert _to_float("1,234.56") == 1234.56      # EN biçimi de bozulmamalı
    assert _to_float(4200) == 4200.0             # zaten sayı olan değer

    for bozuk in ("", "abc", None, True):
        try:
            _to_float(bozuk)
            raise AssertionError(f"{bozuk!r} için hata bekleniyordu")
        except (TypeError, ValueError):
            pass


def test_turkish_numbers_reach_the_result():
    """Ayrıştırma doğru olsa da sonuca yansımazsa bir işe yaramaz."""
    icerik = 'alan,deger\ncurrent_cash,"5.000.000,50"\n'.encode("utf-8")
    d = parse_uploaded(BinaryUpload("rapor.csv", icerik))
    assert d is not None
    assert d["current_cash"] == 5_000_000.5, "mock değeri sızmış olabilir"


def test_unparseable_value_does_not_silently_show_mock_number():
    """
    Sayıya çevrilemeyen bir değer artık sessizce atlanmamalı: o alanda mock
    rakamı kalıyorsa kullanıcı en azından uyarılmalı.
    """
    icerik = b"alan,deger\ncurrent_cash,bilinmiyor\n"
    d = parse_uploaded(BinaryUpload("rapor.csv", icerik))
    # Dosya okunabildi (biçim tanindi) ama değer alınamadı.
    assert d is not None
    mock_kasa = load_mock()["current_cash"]
    # Mock değeri gösteriliyorsa bu bilinçli bir geri düşüş; testin amacı
    # davranışı sabitlemek — sessiz DEĞİŞİM olmasın.
    assert d["current_cash"] == mock_kasa


def test_indirilen_sablon_geri_yuklenebilir():
    """
    Şablonun tek işi var: kullanıcı indirsin, doldursun, yüklesin. Ayrıştırıcı
    ile şablon ayrı yerlerde tanımlansaydı biri değiştiğinde diğeri sessizce
    yanlışa döner, kullanıcı uygulamanın hiç okumadığı bir alanı doldurup
    neden değişmediğini anlayamazdı.

    Bu yüzden gidiş-dönüşün tamamı ölçülüyor: üret → değiştir → ayrıştır.
    """
    from modules.data_io import BICIM_A_ALANLARI, ornek_sablon

    ham = ornek_sablon().decode("utf-8")
    for alan in BICIM_A_ALANLARI:
        assert alan in ham, f"şablonda '{alan}' yok"

    # Kullanıcı kendi rakamını yazmış gibi tek alanı değiştir.
    degistirilmis = ham.replace("current_cash,4200000", "current_cash,9999999")
    assert degistirilmis != ham, "test verisi güncel değil (kasa değeri değişmiş)"

    class _Dosya(io.BytesIO):
        name = "cash_guard_sablon.csv"

    sonuc = parse_uploaded(_Dosya(degistirilmis.encode("utf-8")))
    assert sonuc is not None, "kendi ürettiğimiz şablon ayrıştırılamadı"
    assert sonuc["current_cash"] == 9999999.0, \
        f"kullanıcının yazdığı değer okunmadı: {sonuc['current_cash']}"
    # Dokunulmayan alanlar da şablondan gelmeli, mock'tan sızmamalı.
    assert sonuc["existing_monthly_debt_service"] == 950000.0


_TR_AYLIK_TABLO = (
    "month,revenue,fixed_expense,collections,cash_end\n"
    "2026-01,\"5.000.000\",\"4.000.000\",\"4.500.000\",\"10.000.000\"\n"
    "2026-02,\"5.100.000\",\"4.100.000\",\"4.600.000\",\"10.500.000\"\n"
)


def test_turkish_numbers_are_parsed_in_the_monthly_table_too():
    """
    Biçim A Türkçe sayıyı baştan beri çözüyordu, Biçim B çözmüyordu.

    Aylık tablo Türk Excel'inden çıktığında sütunlar '5.000.000' olarak gelir;
    ham pandas bunu metin okur ve `.mean()` `Cannot perform reduction 'mean'
    with string dtype` diye patlar. Kullanıcı dosyasının komple reddedildiğini
    ve gerekçe olarak İngilizce bir pandas iç hatasını görüyordu — üstelik
    şablonu doğru doldurmuşken. İki biçim aynı sayıyı aynı şekilde okumalı.
    """
    d = parse_uploaded(BinaryUpload("gecmis.csv", _TR_AYLIK_TABLO.encode("utf-8")))
    assert d is not None, "Türkçe biçimli aylık tablo reddedildi"
    assert d["avg_monthly_revenue"] == 5_050_000
    assert d["avg_monthly_fixed_expense"] == 4_050_000
    assert d["avg_monthly_collections"] == 4_550_000
    assert d["current_cash"] == 10_500_000
    assert len(d["history"]) == 2


def test_one_broken_cell_does_not_discard_the_whole_monthly_table():
    """Tek bozuk hücre dosyayı düşürmemeli: o ay atlanır, gerisi okunur."""
    bozuk = _TR_AYLIK_TABLO.replace('"5.100.000"', "bilinmiyor")
    d = parse_uploaded(BinaryUpload("gecmis.csv", bozuk.encode("utf-8")))
    assert d is not None
    assert d["avg_monthly_revenue"] == 5_000_000, "ortalama kalan aydan alınmalı"


def test_a_defaulted_field_never_overwrites_another_files_measurement():
    """
    Biçim A tahsilat satırı taşımıyorsa onu faturalanan gelire EŞİTLİYOR. Bu
    makul bir varsayım — ama aylık tablo gerçek tahsilat ortalamasını zaten
    yazdıysa varsayımın onu ezmemesi gerekir.

    Eskiden eziyordu ve yalnızca dosya sırasına bağlıydı: aynı iki dosya
    B→A yüklenince tahsilat 7.000.000, A→B yüklenince 4.550.000 çıkıyordu.
    Aradaki 2,45 milyonluk fark alacak boşluğunun tamamı — yani modelin
    ölçtüğü şeyin kendisi.
    """
    a = FakeUpload("a.csv", "alan,deger\ncurrent_cash,9000000\n"
                            "avg_monthly_revenue,7000000\n")
    b = FakeUpload("b.csv", "month,revenue,fixed_expense,collections\n"
                            "2026-01,5000000,4000000,4500000\n"
                            "2026-02,5100000,4100000,4600000\n")
    ileri = parse_uploaded_files([b, a])
    geri = parse_uploaded_files([
        FakeUpload("a.csv", a.getvalue()), FakeUpload("b.csv", b.getvalue())])

    assert ileri["avg_monthly_collections"] == 4_550_000, \
        "A'nın varsayılanı B'nin ölçümünü ezdi"
    assert ileri["avg_monthly_collections"] == geri["avg_monthly_collections"]


def test_collections_still_fall_back_to_revenue_when_nobody_supplies_them():
    """Varsayım kaldırılmadı, yalnızca ölçümün önüne geçmemesi sağlandı."""
    d = parse_uploaded(FakeUpload("a.csv", "alan,deger\n"
                                           "avg_monthly_revenue,7000000\n"))
    assert d["avg_monthly_collections"] == 7_000_000


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
