"""
test_ai_cfo.py — CFO ajanının motor seçimi ve LLM istek gövdesi.

Neden bu dosya var: LLM yolu uzun süre hiç koşmadı. Anahtar olmadığı için
_ask_claude/_ask_openai/_ask_gemini bir kez bile çağrılmamıştı — yani kural
tabanlı motor dışındaki her şey repoda "umarım çalışır" halinde duruyordu.

Buradaki testler gerçek API'ye çıkmaz (ağ yok, anahtar yok). Sahte bir istemci
enjekte edip ŞUNLARI doğrular:
  • hangi motorun hangi sırayla seçildiği,
  • bir motor patlayınca gerçekten bir sonrakine düşüldüğü,
  • Claude'a giden istek gövdesinin doğru olduğu — özellikle temperature
    GÖNDERİLMEDİĞİ. Opus 4.8 bu parametreyi reddediyor (400); mevcut OpenAI
    çağrısındaki desen kopyalansaydı her istek hata dönerdi ve bunu ancak
    canlıda görürdük.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import ai_cfo
from modules.ai_cfo import RuthlessCFO


def _ctx():
    """advise()'ın dokunduğu asgari alanlar."""
    return {
        "currency_symbol": "TL",
        "current_cash": 4_200_000,
        "net_operating": -640_000,
        "monthly_net": -850_000,
        "ruin_probability": 0.63,
        "expected_ruin_month": 7,
        "loan_amount": 10_000_000,
        "relief_months": 5,
        "default_with_loan": 14,
        "debt_service": 950_000,
        "runway_months": 5,
        "trend_runway_months": 3,
        "top_receivables": [],
        "expense_breakdown": {},
    }


class _Blok:
    def __init__(self, tip, metin=""):
        self.type, self.text = tip, metin


class _SahteClaude:
    """anthropic.Anthropic yerine geçen kayıt tutucu."""
    son_istek = None

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        _SahteClaude.son_istek = kwargs
        # Gerçek yanıt gibi: önce bir thinking bloğu, sonra metin.
        return types.SimpleNamespace(content=[
            _Blok("thinking"),
            _Blok("text", "  Kasa eriyor. Üç ayın var.  "),
        ])


def _claude_kurulu(monkeypatch_env: dict):
    """Ortamı Claude seçilecek şekilde ayarlar; eski değerleri döndürür."""
    onceki = {k: os.environ.get(k) for k in monkeypatch_env}
    os.environ.update({k: v for k, v in monkeypatch_env.items() if v is not None})
    for k, v in monkeypatch_env.items():
        if v is None:
            os.environ.pop(k, None)
    return onceki


def _geri_yukle(onceki: dict):
    for k, v in onceki.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_claude_request_omits_temperature():
    """
    Opus 4.8 temperature/top_p/top_k kabul etmiyor — gönderilirse 400.
    Bu test o parametrelerin gövdeye sızmadığını garanti eder.
    """
    onceki = _claude_kurulu({"ANTHROPIC_API_KEY": "test-anahtar"})
    eski_sdk, eski_bayrak = getattr(ai_cfo, "anthropic", None), ai_cfo._HAS_CLAUDE
    ai_cfo.anthropic = types.SimpleNamespace(Anthropic=_SahteClaude)
    ai_cfo._HAS_CLAUDE = True
    try:
        metin = RuthlessCFO(prefer="claude")._ask_claude(_ctx())
        istek = _SahteClaude.son_istek
        for yasak in ("temperature", "top_p", "top_k"):
            assert yasak not in istek, f"Opus 4.8'e {yasak} gönderilemez (400 döner)"
        assert istek["model"] == ai_cfo.CLAUDE_MODEL
        assert istek["system"] == ai_cfo.SYSTEM_PROMPT
        assert istek["messages"][0]["role"] == "user"
        assert "MONTE CARLO" in istek["messages"][0]["content"], "sayılar promptta yok"
        # thinking bloğu atlanmalı, metin kırpılmalı
        assert metin == "Kasa eriyor. Üç ayın var."
    finally:
        ai_cfo.anthropic, ai_cfo._HAS_CLAUDE = eski_sdk, eski_bayrak
        _geri_yukle(onceki)


def test_claude_is_preferred_when_all_keys_present():
    """Üç anahtar da varsa sıra Claude → OpenAI → Gemini olmalı."""
    onceki = _claude_kurulu({
        "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "b", "GOOGLE_API_KEY": "c",
    })
    bayraklar = (ai_cfo._HAS_CLAUDE, ai_cfo._HAS_OPENAI, ai_cfo._HAS_GEMINI)
    ai_cfo._HAS_CLAUDE = ai_cfo._HAS_OPENAI = ai_cfo._HAS_GEMINI = True
    try:
        assert RuthlessCFO().available_llms() == ["claude", "openai", "gemini"]
    finally:
        ai_cfo._HAS_CLAUDE, ai_cfo._HAS_OPENAI, ai_cfo._HAS_GEMINI = bayraklar
        _geri_yukle(onceki)


def test_broken_llm_falls_back_to_rule_engine():
    """
    LLM patlarsa uygulama boş kalmamalı — kural tabanlı motora düşmeli.
    Kullanıcı bir API hatası yüzünden bomboş bir rapor görmemeli.
    """
    onceki = _claude_kurulu({"ANTHROPIC_API_KEY": "test-anahtar"})
    eski_sdk, eski_bayrak = getattr(ai_cfo, "anthropic", None), ai_cfo._HAS_CLAUDE

    class _Patlayan:
        def __init__(self, api_key=None):
            raise RuntimeError("API çöktü")

    ai_cfo.anthropic = types.SimpleNamespace(Anthropic=_Patlayan)
    ai_cfo._HAS_CLAUDE = True
    try:
        tavsiye = RuthlessCFO(prefer="claude").advise(_ctx())
        assert tavsiye.source == "Kural Tabanlı Motor"
        assert tavsiye.text.strip(), "fallback boş metin döndürdü"
        assert "AKSİYON PLANI" in tavsiye.text
    finally:
        ai_cfo.anthropic, ai_cfo._HAS_CLAUDE = eski_sdk, eski_bayrak
        _geri_yukle(onceki)


def test_secret_is_found_in_env_and_in_streamlit_secrets():
    """
    Anahtar hem ortam değişkeninden hem st.secrets'tan bulunabilmeli.

    Streamlit Cloud, secrets.toml'un YALNIZCA kök seviyesini ortam değişkeni
    olarak yayınlıyor. Kullanıcı anahtarı bir TOML bölümüne koyarsa os.getenv
    onu göremez; uygulama sessizce kural tabanlı motorda kalır, kullanıcı ise
    anahtarı eklediği için LLM'in çalıştığını sanır.
    """
    import types

    onceki = _claude_kurulu({"ANTHROPIC_API_KEY": "ortamdan"})
    try:
        assert ai_cfo._secret("ANTHROPIC_API_KEY") == "ortamdan"
    finally:
        _geri_yukle(onceki)

    onceki = _claude_kurulu({"ANTHROPIC_API_KEY": None})
    eski_st = sys.modules.get("streamlit")
    sahte = types.SimpleNamespace()
    sys.modules["streamlit"] = sahte
    try:
        sahte.secrets = {"ANTHROPIC_API_KEY": "kokten"}
        assert ai_cfo._secret("ANTHROPIC_API_KEY") == "kokten"

        # Bölüm içine konmuş anahtar — os.getenv'in GÖREMEDİĞİ durum
        sahte.secrets = {"genel": {"ANTHROPIC_API_KEY": "bolumden"}}
        assert ai_cfo._secret("ANTHROPIC_API_KEY") == "bolumden"

        sahte.secrets = {}
        assert ai_cfo._secret("ANTHROPIC_API_KEY") is None
    finally:
        if eski_st is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = eski_st
        _geri_yukle(onceki)


def test_secret_lookup_survives_without_streamlit():
    """
    Bu modül Streamlit olmadan da çalışabilmeli (testler ve ileride bir API
    sunucusu için). Sır arama streamlit yokken sessizce None dönmeli.
    """
    onceki = _claude_kurulu({"ANTHROPIC_API_KEY": None})
    eski_st = sys.modules.get("streamlit")
    sys.modules["streamlit"] = None      # import'u kasten patlat
    try:
        assert ai_cfo._secret("ANTHROPIC_API_KEY") is None
    finally:
        if eski_st is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = eski_st
        _geri_yukle(onceki)


def test_no_keys_means_rule_engine():
    """Anahtar yokken LLM listesi boş olmalı — arayüz butonu buna bakıyor."""
    onceki = _claude_kurulu({
        "ANTHROPIC_API_KEY": None, "OPENAI_API_KEY": None,
        "GOOGLE_API_KEY": None, "GEMINI_API_KEY": None,
    })
    try:
        cfo = RuthlessCFO()
        assert cfo.available_llms() == []
        assert cfo.advise(_ctx()).source == "Kural Tabanlı Motor"
    finally:
        _geri_yukle(onceki)


_ANAHTARSIZ = {"ANTHROPIC_API_KEY": None, "OPENAI_API_KEY": None,
               "GOOGLE_API_KEY": None, "GEMINI_API_KEY": None}


def test_anahtarsiz_kurulum_uyari_gostermez():
    """
    Anahtarsız çalışmak bir arıza DEĞİL, halka açık demonun bilinçli
    varsayılanı. Orada uyarı göstermek demoyu bozukmuş gibi gösterirdi —
    bu yüzden sebep None olmalı.
    """
    onceki = _claude_kurulu(dict(_ANAHTARSIZ))
    try:
        sonuc = RuthlessCFO().advise(_ctx())
        assert sonuc.source == "Kural Tabanlı Motor"
        assert sonuc.reason is None, f"anahtarsız kurulumda uyarı çıktı: {sonuc.reason}"
    finally:
        _geri_yukle(onceki)


def test_anahtar_varken_sessiz_kalmaz():
    """
    Anahtar tanımlıysa kullanıcı gerçekten LLM istemiştir. Sessizce yerel
    motora düşmek onu karanlıkta bırakır: paket mi, anahtar mı, çağrı mı
    sorunlu — hiçbiri görünmezdi. Sebep, çözümü işaret etmeli.
    """
    onceki = _claude_kurulu(dict(_ANAHTARSIZ, ANTHROPIC_API_KEY="test-anahtar"))
    eski = ai_cfo._HAS_CLAUDE
    ai_cfo._HAS_CLAUDE = False        # paket yokmuş gibi
    try:
        sebep = RuthlessCFO().advise(_ctx()).reason
        assert sebep, "anahtar varken sebep verilmedi"
        assert "paket" in sebep, f"sebep çözümü işaret etmiyor: {sebep}"
    finally:
        ai_cfo._HAS_CLAUDE = eski
        _geri_yukle(onceki)


def test_ilk_saglayici_patlarsa_digerine_duser():
    """
    Gerçek senaryonun testi: Secrets'ta kredisi bitmiş bir ANTHROPIC_API_KEY ve
    çalışan bir GOOGLE_API_KEY birlikte duruyor. Claude patlayınca zincir
    Gemini'ye düşmeli — kural tabanlı motora DEĞİL, çünkü hâlâ çalışan bir
    sağlayıcı var.
    """
    onceki = _claude_kurulu(dict(_ANAHTARSIZ,
                                 ANTHROPIC_API_KEY="kredisiz",
                                 GOOGLE_API_KEY="calisan"))
    eski_sdk = getattr(ai_cfo, "genai", None)
    eski_bayraklar = (ai_cfo._HAS_CLAUDE, ai_cfo._HAS_GEMINI)
    ai_cfo._HAS_CLAUDE = ai_cfo._HAS_GEMINI = True

    def _patlayan(*a, **k):
        raise RuntimeError("credit balance is too low")

    ai_cfo.genai = types.SimpleNamespace(
        configure=lambda **k: None,
        GenerativeModel=lambda *a, **k: types.SimpleNamespace(
            generate_content=lambda p: types.SimpleNamespace(text="Gemini planı")),
    )
    eski_claude = ai_cfo.RuthlessCFO._ask_claude
    ai_cfo.RuthlessCFO._ask_claude = _patlayan
    try:
        sonuc = RuthlessCFO().advise(_ctx())
        assert sonuc.source == "Gemini", f"zincir Gemini'ye düşmedi: {sonuc.source}"
        assert sonuc.reason is None, "çalışan sağlayıcı varken uyarı çıktı"
    finally:
        ai_cfo.RuthlessCFO._ask_claude = eski_claude
        ai_cfo.genai = eski_sdk
        ai_cfo._HAS_CLAUDE, ai_cfo._HAS_GEMINI = eski_bayraklar
        _geri_yukle(onceki)


def test_sebep_metni_anahtari_sizdirmaz():
    """
    Sebep arayüze çıkıyor. Sağlayıcı bir gün anahtarı hata metnine koyarsa
    ekrana düşmemeli — sızıntı, bozuk özellikten çok daha pahalıdır.
    """
    kirli = "401 hata: sk-ant-api03-COKGIZLI-ANAHTAR gecersiz, AIzaSyABCDEFGHIJK de"
    temiz = ai_cfo._anahtari_gizle(kirli)
    assert "sk-ant-api03-COKGIZLI-ANAHTAR" not in temiz
    assert "AIzaSyABCDEFGHIJK" not in temiz
    assert "401 hata" in temiz, "mesajın tamamı silinmiş, teşhis kalmamış"


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
