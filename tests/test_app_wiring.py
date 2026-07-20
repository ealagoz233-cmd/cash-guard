"""
test_app_wiring.py — Arayüz kablolaması gerçekten bağlı mı?

Diğer test dosyaları modüllerin MANTIĞINI koruyor: test_store.py defteri
(tekrar eden ad, tavan, bozuk dosya), test_scenario.py URL'de taşınan
senaryoyu (kırpma, çöp parametre). Burada korunan başka bir şey: o mantığın
app.py'ye DOĞRU bağlanmış olması.

İkisi ayrı hata sınıfı. store.kaydet() kusursuz çalışırken butonun yanlış
değişkeni göndermesi, ya da from_query_params() doğru ayrıştırırken sonucun
sürgüye hiç verilmemesi mümkündür — modül testleri ikisini de görmez, ikisi
de kullanıcıya bozuk bir ürün olarak çıkar.

Kapsanan iki zincir:
  URL -> sürgü -> motor -> defter tablosu   (paylaşılan link doğru senaryoyu
                                             gösteriyor ve kaydedilebiliyor)
  sürgü -> URL                              (paylaşılan link güncel)

Streamlit'in kendi AppTest motoruyla app.py baştan sona koşturulur; tarayıcı
yok, bu yüzden CI'da da güvenilir. (Tarayıcıdan doğrulama denendi ve
bırakıldı: veri ızgarası sanallaştırılmış bir kapta çiziliyor, satır metni
DOM'a hiç düşmüyor — dışarıdan okunabilen tek şey satır sayısına göre
hesaplanmış yükseklikti. Bu yol o boşluğu kapatıyor.)
"""
import os
import sys

from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import store

_KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_KOK, "app.py")

# Geri yükleme testi için hazır kayıt (test_store.py'dekiyle aynı biçim).
_SENARYO = {"kredi": 10_000_000, "vade": 24, "faiz": 3.5}
_OZET = {"batma_yuzde": 94.3, "iflas_ayi": 8, "aylik_net": -100_000}

# Tam bir uygulama koşusu ~5 sn (10.000 senaryoluk Monte Carlo dahil).
_TIMEOUT = 180

_KAYITLI_AD = "Tarayıcı testi · 15M kredi"
_onbellek = {}


def _uygulama(**qp):
    """Uygulamayı koştur. Adlandırılmış argümanlar URL parametresi olur."""
    at = AppTest.from_file(_APP, default_timeout=_TIMEOUT)
    for k, v in qp.items():
        at.query_params[k] = str(v)
    at.run()
    assert not at.exception, f"uygulama açılışta patladı: {at.exception}"
    return at


def _surgu(at, parca):
    for s in at.slider:
        if parca in s.label:
            return s
    raise AssertionError(f"'{parca}' sürgüsü yok. Mevcut: {[s.label for s in at.slider]}")


def _buton(at, parca):
    """Etikete göre buton bul — indeks kullanmak, araya buton eklenince kırılır."""
    for b in at.button:
        if parca in b.label:
            return b
    raise AssertionError(f"'{parca}' butonu yok. Mevcut: {[b.label for b in at.button]}")


def _kaydet(at, ad):
    at.text_input[0].set_value(ad).run()
    _buton(at, "Senaryoyu Kaydet").click().run()
    assert not at.exception, f"kaydetme patladı: {at.exception}"
    return at


def _kayitli():
    """
    Bir senaryo kaydedilmiş uygulama. Kurulum pahalı, o yüzden bir kez koşup
    saklanıyor. pytest fixture'ı yerine sade fonksiyon: bu repoda her test
    dosyası pytest OLMADAN da koşabilmeli (aşağıdaki __main__ bloğu).
    """
    if "kayitli" not in _onbellek:
        _onbellek["kayitli"] = _kaydet(_uygulama(), _KAYITLI_AD)
    return _onbellek["kayitli"]


def _defter_indirme(at):
    """Sayfada PDF indirme butonu da var — özellikle defterinkini ara."""
    return [d for d in at.get("download_button") if "Defteri" in d.label]


def test_kaydetme_tabloyu_dogurur():
    """
    Kaydet'e basmak tabloyu ekrana getirmeli ve satır kullanıcının VERDİĞİ
    adı taşımalı — otomatik adı değil.
    """
    at = _kayitli()
    assert len(at.dataframe) == 1, "kaydetmeden sonra tablo çizilmedi"
    tablo = at.dataframe[0].value
    assert len(tablo) == 1, f"1 satır bekleniyordu, {len(tablo)} var"
    assert tablo.iloc[0]["Senaryo"] == _KAYITLI_AD


def test_kaydetme_ozeti_de_tasir():
    """Tabloda ad kadar sonuçlar da olmalı, yoksa karşılaştırma anlamsız."""
    tablo = _kayitli().dataframe[0].value
    assert "Kredi" in tablo.columns
    assert tablo.iloc[0]["Batma %"] > 0, "batma olasılığı taşınmamış"


def test_silme_ve_indirme_ancak_kayit_varken_cikar():
    """Boş defterde silme/indirme butonu görünmemeli; kayıt gelince gelmeli."""
    bos = _uygulama()
    assert not [b for b in bos.button if "Sil" in b.label], "boş defterde Sil butonu var"
    assert not bos.dataframe, "boş defterde tablo çizilmiş"
    assert not _defter_indirme(bos), "boş defterde İndir butonu var"

    at = _kayitli()
    assert _buton(at, "Sil"), "kayıt varken Sil butonu yok"
    assert _defter_indirme(at), "kayıt varken Defteri İndir butonu yok"


def test_adsiz_kaydetme_otomatik_ad_uretir():
    """
    Ad yazmadan Kaydet'e basmak çökmemeli; sürgülerden okunan bir ad üretmeli.
    Kullanıcıların çoğu ad yazmaz — bu, istisna değil varsayılan yol.
    """
    at = _kaydet(_uygulama(), "")
    ad = at.dataframe[0].value.iloc[0]["Senaryo"]
    assert ad.strip(), "otomatik ad boş"
    assert "ay" in ad, f"otomatik ad vadeyi içermiyor: {ad!r}"


def test_ikinci_senaryo_yan_yana_gelir():
    """
    Defterin varlık sebebi: iki seçeneği yan yana görmek. Tek satır kalırsa
    ikinci kayıt birinciyi eziyor demektir.
    """
    at = _kaydet(_kaydet(_uygulama(), "Kredili"), "Kredisiz")
    tablo = at.dataframe[0].value
    assert len(tablo) == 2, f"iki senaryo yan yana gelmedi ({len(tablo)} satır)"
    assert list(tablo["Senaryo"]) == ["Kredili", "Kredisiz"]


def test_silme_kaydi_gercekten_dusurur():
    """Sil'e basmak kaydı düşürmeli ve boş defter mesajına dönmeli."""
    at = _kaydet(_uygulama(), "Gidici")
    _buton(at, "Sil").click().run()
    assert not at.exception, f"silme patladı: {at.exception}"
    assert not at.dataframe, "silmeden sonra tablo hâlâ duruyor"


def test_url_surguleri_gercekten_oynatir():
    """
    Paylaşılan linkin tek vaadi bu: karşı taraf AYNI senaryoyu görsün.
    scenario.py'nin ayrıştırması test_scenario.py'de; burada o sonucun
    sürgülere bağlandığını doğruluyoruz.
    """
    at = _uygulama(kredi=0, vade=60, faiz=7.5)
    assert _surgu(at, "Kredi Miktarı").value == 0
    assert _surgu(at, "Vade").value == 60
    assert _surgu(at, "Faiz").value == 7.5


def test_bozuk_url_uygulamayi_cokertmez():
    """
    Adres çubuğu internetteki herkesin elinde. Çöp parametre uygulamayı
    çökertmemeli; sürgüler geçerli aralıkta kalmalı.
    """
    at = _uygulama(kredi="abc", vade=-999, faiz="", bilinmeyen="x")
    assert not at.exception, f"bozuk URL patlattı: {at.exception}"
    kredi = _surgu(at, "Kredi Miktarı")
    assert kredi.value >= 0, f"sürgü geçersiz değere düştü: {kredi.value}"
    assert _surgu(at, "Vade").value >= 6


def test_surgu_oynatinca_url_guncellenir():
    """
    Link paylaşılabilir olsun diye durum adres çubuğuna yazılıyor. Sürgü
    oynayıp URL eski kalırsa kullanıcı yanlış senaryoyu paylaşır.
    """
    at = _uygulama()
    _surgu(at, "Vade").set_value(48).run()
    assert not at.exception
    assert dict(at.query_params).get("vade") in ("48", ["48"]), \
        f"URL güncellenmedi: {dict(at.query_params)}"


def test_urlden_gelen_senaryo_deftere_dogru_kaydedilir():
    """
    İki özelliğin kesiştiği yer: linkle gelen senaryoyu kaydetmek. Zincirin
    (URL -> sürgü -> motor -> defter) herhangi bir halkası kopsa tablo
    varsayılan krediyi gösterirdi.
    """
    at = _kaydet(_uygulama(kredi=0, vade=60), "Kredisiz · linkten")
    satir = at.dataframe[0].value.iloc[0]
    assert satir["Senaryo"] == "Kredisiz · linkten"
    assert satir["Kredi"] == 0, f"URL'deki kredi deftere ulaşmadı: {satir['Kredi']}"


def _defter_yukleyici(at):
    """Sayfada CSV/Excel yükleyici de var — defterinkini etiketinden ayır."""
    for f in at.get("file_uploader"):
        if "defteri geri yükle" in f.label:
            return f
    raise AssertionError("defter yükleyicisi bulunamadı")


def test_indirilen_defter_geri_yuklenebilir():
    """
    Kalıcılık vaadinin tamamı bu: kullanıcı defterini indirir, yarın geri
    yükler. Yükleme kablosu koparsa dosya elinde kalır ama işe yaramaz.

    Not: indirme butonunun ürettiği baytlara AppTest'ten erişilemiyor (yalnızca
    mock bir medya URL'i veriliyor), o yüzden dosya butonun kullandığı aynı
    fonksiyonla (store.disa_aktar) üretiliyor.
    """
    dosya = store.disa_aktar(store.kaydet([], "Dünkü senaryo", _SENARYO, _OZET))

    at = _uygulama()
    _defter_yukleyici(at).set_value(("cash_guard_senaryolar.json", dosya,
                                     "application/json")).run()
    assert not at.exception, f"geri yükleme patladı: {at.exception}"
    assert at.dataframe, "geri yüklenen defter tabloya dönüşmedi"
    assert at.dataframe[0].value.iloc[0]["Senaryo"] == "Dünkü senaryo"


def test_geri_yukleme_sonraki_kaydi_ezmez():
    """
    Gerçek bir hatanın nöbetçisi (bu test yazılırken bulundu ve düzeltildi).

    Yüklenen dosya, seçili kaldığı sürece her yeniden koşuda yükleyiciden geri
    gelir. İçe aktarma koşulsuz yapılırsa arada kaydedilen senaryo ezilir:
    kullanıcı kaydını tabloda görür, sonra bir sürgü oynatır ve kaydı sessizce
    kaybolur — projenin en temel vaadinin (analizini KAYBETMEZSİN) ihlali.
    """
    dosya = store.disa_aktar(store.kaydet([], "Dünkü", _SENARYO, _OZET))

    at = _uygulama()
    _defter_yukleyici(at).set_value(("d.json", dosya, "application/json")).run()
    at = _kaydet(at, "Bugünkü")
    assert list(at.dataframe[0].value["Senaryo"]) == ["Dünkü", "Bugünkü"]

    # Kritik kısım: kaydettikten SONRAKİ etkileşimlerde de durmalı.
    _surgu(at, "Vade").set_value(36).run()
    at.run()
    assert list(at.dataframe[0].value["Senaryo"]) == ["Dünkü", "Bugünkü"], \
        "geri yükleme, sonradan kaydedilen senaryoyu ezdi"


def test_bozuk_defter_dosyasi_cokertmez_ve_uyarir():
    """
    Yükleme, en güvenilmez girdi kapısı. store.ice_aktar() bozuk içeriğe
    dayanıklı (test_store.py); burada app.py'nin o boş sonucu kullanıcıya
    HATA olarak gösterdiğini doğruluyoruz — sessizce yutmadığını.
    """
    at = _uygulama()
    _defter_yukleyici(at).set_value(("bozuk.json", b"{bu json degil",
                                     "application/json")).run()
    assert not at.exception, f"bozuk dosya patlattı: {at.exception}"
    assert at.error, "bozuk dosya için kullanıcıya hata gösterilmedi"
    assert not at.dataframe, "bozuk dosyadan tablo çizilmiş"


def test_anahtar_eklenince_onbellek_takilmaz():
    """
    Gerçek bir hatanın nöbetçisi (canlıda yaşandı).

    CFO cevabı önbellekli ve anahtar yalnızca senaryo sayılarından üretiliyordu.
    Sonuç: anahtarsız açılışta kural tabanlı cevap önbelleğe yazılıyor, sonra
    Secrets'a anahtar ekleniyor ve HİÇBİR ŞEY DEĞİŞMİYOR — sayılar aynı olduğu
    için önbellek isabet ediyor. Kullanıcı her şeyi doğru yapmış ama karşısında
    hâlâ "Kural Tabanlı Motor" duruyor.

    Sağlayıcı kümesi de önbellek anahtarına girmeli.
    """
    import types

    from modules import ai_cfo

    def _rozet(at):
        metin = " ".join(m.value for m in at.markdown if m.value)
        return "Gemini" if "Gemini" in metin else "Kural Tabanlı Motor"

    eski_env = {k: os.environ.get(k) for k in
                ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
                 "GEMINI_API_KEY")}
    eski_sdk, eski_bayrak = getattr(ai_cfo, "genai", None), ai_cfo._HAS_GEMINI
    for k in eski_env:
        os.environ.pop(k, None)
    try:
        # 1) Anahtarsız koşu — kural tabanlı cevap önbelleğe yazılır.
        assert _rozet(_uygulama()) == "Kural Tabanlı Motor"

        # 2) Anahtar eklenir (kullanıcının Secrets'a yazması). Sayılar aynı.
        os.environ["GOOGLE_API_KEY"] = "test-anahtar"
        ai_cfo._HAS_GEMINI = True
        ai_cfo.genai = types.SimpleNamespace(
            configure=lambda **k: None,
            GenerativeModel=lambda *a, **k: types.SimpleNamespace(
                generate_content=lambda p: types.SimpleNamespace(text="Gemini planı")),
        )

        assert _rozet(_uygulama()) == "Gemini", (
            "anahtar eklendi ama cevap değişmedi — önbellek sağlayıcı kümesini "
            "hesaba katmıyor, kullanıcı doğru yaptığı halde takılı kalır")
    finally:
        ai_cfo.genai, ai_cfo._HAS_GEMINI = eski_sdk, eski_bayrak
        for k, v in eski_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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
