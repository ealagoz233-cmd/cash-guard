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

_KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_KOK, "app.py")

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
