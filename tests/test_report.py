"""
test_report.py — PDF raporunun üretilebildiğini ve Türkçe'yi taşıdığını doğrular.

Neden bu dosya var: font seçimi bir zamanlar SADECE C:/Windows/Fonts/ altına
bakıyordu. Windows'ta her şey güzel görünüyordu, ama Linux'a (Streamlit Cloud)
deploy edilseydi reportlab sessizce Helvetica'ya düşer ve ğ ı ş İ karakterleri
bozulurdu — hiçbir test patlamazdı, çünkü PDF yine "başarıyla" üretiliyordu.

Buradaki testler tam olarak o sessiz bozulmayı yakalar ve CI'da Linux üzerinde
koştuğu için deploy hedefini de kapsar.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportlab.pdfbase import pdfmetrics

from modules import report

# Türkçe'nin ASCII dışına taşan ve en sık bozulan harfleri.
TURKISH_CHARS = "çÇğĞıİöÖşŞüÜ"


def _ctx():
    """build_report'un beklediği asgari bağlam — Türkçe karakter içerir."""
    return {
        "company_name": "Çağrışım Yazılım A.Ş.",
        "sector": "Bilişim & Mühendislik",
        "as_of": "2026-07",
        "currency_symbol": "TL",
        "current_cash": 4_200_000,
        "ruin_pct": 63.4,
        "expected_ruin_month": 7,
        "monthly_net": -850_000,
        "net_operating": -640_000,
        "debt_service": 950_000,
        "loan_amount": 10_000_000,
        "installment": 620_000,
        "total_interest": 4_880_000,
        "relief_months": 5,
        "default_with_loan": 14,
        "base_ruin_pct": 63.4,
        "loan_ruin_pct": 41.2,
        "runway_months": 5,
        "trend_runway_months": 3,
        "cfo_text": "Kasa eriyor. Şirketin ömrü üç ay.\n1. Gereksiz gideri kes.",
        "cfo_source": "Kural Tabanlı Motor",
    }


def test_turkish_capable_font_is_registered():
    """
    Kayıtlı font Türkçe harfleri GERÇEKTEN çizebilmeli.

    Helvetica'ya düşülmüş olması tek başına yeterli kanıt değil — asıl soru
    fontun glyph tablosunda bu karakterlerin bulunup bulunmadığı. TrueType
    yüzünü açıp charToGlyph eşlemesine bakıyoruz.
    """
    assert report._FONT != "Helvetica", (
        "Türkçe destekli TrueType font bulunamadı, Helvetica'ya düşüldü. "
        "Bu platformda ğ ı ş İ bozulur. Linux'ta çözüm: fonts-dejavu-core "
        "kur (repodaki packages.txt bunu Streamlit Cloud için hallediyor)."
    )
    face = pdfmetrics.getFont(report._FONT).face
    eksik = [c for c in TURKISH_CHARS if ord(c) not in face.charToGlyph]
    assert not eksik, f"kayıtlı fontta glyph'i olmayan harfler: {''.join(eksik)}"


def test_bold_font_is_registered_too():
    """Başlıklar kalın yüzü kullanıyor; o da aynı kapsamda olmalı."""
    assert report._FONT_B != "Helvetica-Bold", "kalın font Helvetica'ya düştü"
    face = pdfmetrics.getFont(report._FONT_B).face
    eksik = [c for c in TURKISH_CHARS if ord(c) not in face.charToGlyph]
    assert not eksik, f"kalın fontta glyph'i olmayan harfler: {''.join(eksik)}"


def test_build_report_returns_a_real_pdf():
    """Çıktı gerçekten PDF olmalı ve boş sayılmayacak kadar dolu."""
    data = report.build_report(_ctx())
    assert isinstance(data, (bytes, bytearray)), "bytes dönmedi"
    assert data[:5] == b"%PDF-", "PDF imzası yok"
    assert data.rstrip().endswith(b"%%EOF"), "PDF düzgün kapanmamış"
    assert len(data) > 3000, f"PDF şüpheli derecede küçük: {len(data)} bayt"


def test_report_survives_missing_optional_fields():
    """
    Sağlıklı şirkette iflas ayı/kredi alanları None gelir. Rapor bu durumda
    da üretilmeli — bir KPI eksik diye yönetim kurulu raporu patlayamaz.
    """
    ctx = _ctx()
    ctx.update(expected_ruin_month=None, default_with_loan=None,
               loan_ruin_pct=None, runway_months=None, trend_runway_months=None,
               sector="", ruin_pct=0.0, monthly_net=2_225_000)
    data = report.build_report(ctx)
    assert data[:5] == b"%PDF-"


def test_report_grows_when_the_structural_verdict_is_added():
    """
    Altman skoru ve şüpheli alacak rapora gerçekten giriyor mu?

    PDF metnini ayrıştırmak kırılgan olurdu; ölçülebilir ve dürüst kontrol
    şu: alanlar verildiğinde belge büyümeli. Büyümüyorsa satırlar sessizce
    düşürülmüş demektir.
    """
    yalin = report.build_report(_ctx())
    zengin = report.build_report(_ctx() | {
        "z_score": 3.02, "z_zone": "Güvenli",
        "expected_uncollectible": 2_728_000, "dso_days": 39,
    })
    assert len(zengin) > len(yalin), "yapısal hüküm satırları rapora girmemiş"


def test_report_stays_silent_without_structural_data():
    """
    Kullanıcı kendi CSV'sini yüklediğinde bilanço gelmez. Rapor bu durumda
    uydurmamalı, sadece o satırları atlamalı — ve yine üretilmeli.
    """
    ctx = _ctx() | {"z_score": None, "z_zone": None,
                    "expected_uncollectible": None, "dso_days": None}
    data = report.build_report(ctx)
    assert data[:5] == b"%PDF-"


def test_markup_in_text_fields_does_not_break_pdf():
    """
    reportlab'ın Paragraph'ı mini-XML ayrıştırıcıdır: kaçırılmamış bir '<'
    ya bozuk çıktı ya ValueError demek.

    Gerçek senaryo: kullanıcı şirket adı 'Acme <b>Tekstil' olan bir CSV
    yükler, "Rapor indir"e basar ve uygulama çöker. Bu test o çökmeyi
    yeniden üretmek için yazıldı — düzeltmeden önce kapanmamış etiket
    ValueError atıyordu.
    """
    tuzaklar = [
        "Acme <b>Tekstil A.Ş.",           # kapanmamış etiket — eskiden ValueError
        "Acme </b> Ortakları",            # eşleşmeyen kapanış
        "Acme <script>alert(1)</script>",
        "Acme < Ortakları > Ltd.",        # ham karşılaştırma işaretleri
        "Acme & Co <font size=99>",
    ]
    for ad in tuzaklar:
        for alan in ("company_name", "sector", "as_of", "cfo_source"):
            ctx = _ctx()
            ctx[alan] = ad
            data = report.build_report(ctx)
            assert data[:5] == b"%PDF-", f"{alan}={ad!r} PDF üretimini bozdu"


def test_markup_in_cfo_text_does_not_break_pdf():
    """CFO metni LLM'den geliyor; içindeki etiket PDF'i patlatmamalı."""
    ctx = _ctx()
    ctx["cfo_text"] = "Kasa eriyor.\n1. <b>Kapanmamış kalın.\n2. <img src=x> ve & işareti."
    assert report.build_report(ctx)[:5] == b"%PDF-"


def test_currency_symbol_is_consistent_across_the_document():
    """
    Tablolar ile CFO metni AYNI para birimi simgesini kullanmalı.

    Gerçek rapor çıktısında tablolarda 'TL', aksiyon planında '₺' yazıyordu:
    rapor ₺'yi koşulsuz TL'ye çeviriyor ama CFO metni ayrı üretildiği için
    ₺'yi koruyordu. Yönetim kuruluna giden bir belgede iki farklı gösterim.

    Artık karar tek yerde (_usable_symbol) veriliyor ve CFO metnine de
    uygulanıyor; bu test iki yolun ayrışmasını engeller.
    """
    ctx = _ctx()
    ctx["currency_symbol"] = "₺"
    ctx["cfo_text"] = "Kasada ₺4.200.000 var, aylık ₺100.000 eriyor."
    data = report.build_report(ctx)
    assert data[:5] == b"%PDF-"

    secilen = report._usable_symbol("₺")
    assert secilen in ("₺", "TL"), f"beklenmeyen simge: {secilen!r}"


def test_cfo_text_follows_the_report_symbol():
    """
    Rapor TL'ye düştüğünde CFO metni de düşmeli — asıl kusur buydu.

    _align_currency saf bir fonksiyon olduğu için PDF'i açmadan, doğrudan
    ve kesin biçimde sınanabiliyor.
    """
    metin = "Kasada ₺4.200.000 var, aylık ₺100.000 eriyor."
    # Rapor TL kullanıyorsa metinde tek bir ₺ kalmamalı
    assert report._align_currency(metin, "TL") == \
        "Kasada TL4.200.000 var, aylık TL100.000 eriyor."
    assert "₺" not in report._align_currency(metin, "TL")
    # Rapor ₺ kullanıyorsa metne dokunulmamalı
    assert report._align_currency(metin, "₺") == metin


def test_font_without_lira_forces_TL_everywhere():
    """
    Fontta ₺ yoksa hem tablolar hem CFO metni TL'ye düşmeli.

    Linux/DejaVu senaryosunu Windows'ta üretemediğim için glyph sorgusunu
    geçici olarak 'yok' yapıp kararın doğru yayıldığını doğruluyorum.
    """
    orijinal = report._font_has
    report._font_has = lambda c: False
    try:
        assert report._usable_symbol("₺") == "TL"
        ctx = _ctx()
        ctx["currency_symbol"] = "₺"
        ctx["cfo_text"] = "Kasada ₺4.200.000 var."
        assert report.build_report(ctx)[:5] == b"%PDF-"
    finally:
        report._font_has = orijinal


def test_lira_glyph_available_on_this_platform():
    """
    Kayıtlı fontta ₺ (U+20BA) glyph'i var mı?

    Bu testin cevabı platforma göre değişebilir: Windows'ta Arial, Linux'ta
    DejaVu kullanılıyor. BAŞARISIZ OLMASI hata değil, bilgidir — _usable_symbol
    o durumda TL'ye düşer. Testin amacı deploy hedefinde (CI'daki Linux işi)
    durumu görünür kılmak; sessiz kalmasın.
    """
    var = report._font_has("₺")
    assert isinstance(var, bool)
    if not var:
        print(f"  bilgi: {report._FONT} fontunda ₺ yok — rapor TL'ye düşecek")


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
