"""
test_api.py — HTTP katmanının sözleşmesi.

Testler gerçek uygulamayı FastAPI'nin TestClient'ı üzerinden çağırır: istek
doğrulaması, yönlendirme ve JSON'a çevirme dahil tüm zincir koşar.

İki şeyi birden koruyorlar:
  1) API'nin Streamlit arayüzüyle AYNI motoru kullandığı (sayılar ayrışmasın),
  2) dışarı açıldığında kötü girdiyle çökmediği/istismar edilmediği.

fastapi kurulu değilse dosya atlanır — çekirdek uygulama API olmadan da
çalışmalı, o yüzden ana test matrisi bu bağımlılığı zorunlu tutmuyor.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fastapi.testclient import TestClient

    from api import MAX_ITER, MAX_ITER_SENSITIVITY, app

    client = TestClient(app)
    _API_VAR = True
except Exception:                     # fastapi/httpx yok
    _API_VAR = False


_SIRKET = {
    "current_cash": 4_200_000,
    "monthly_revenue": 6_800_000,
    "monthly_fixed_expense": 5_950_000,
    "monthly_debt_service": 950_000,
}


def test_health_endpoint():
    if not _API_VAR:
        return
    y = client.get("/health")
    assert y.status_code == 200
    assert y.json()["status"] == "ok"


def test_simulate_returns_a_probability():
    if not _API_VAR:
        return
    y = client.post("/simulate", json=_SIRKET)
    assert y.status_code == 200, y.text
    d = y.json()
    assert 0.0 <= d["ruin_probability"] <= 1.0
    assert d["n_iter"] == 10_000
    # Bu şirket batıyor: aylık net negatif olmalı
    assert d["monthly_net"] < 0


def test_same_seed_gives_same_answer_over_http():
    """
    Determinizm HTTP katmanından da geçmeli: aynı gövde iki kez gönderilince
    aynı sayı dönmeli. Aksi halde paylaşılan bir sonuç tekrar üretilemez.
    """
    if not _API_VAR:
        return
    a = client.post("/simulate", json=_SIRKET).json()
    b = client.post("/simulate", json=_SIRKET).json()
    assert a["ruin_probability"] == b["ruin_probability"]


def test_iteration_cap_is_enforced():
    """
    Sınırsız iterasyon, tek istekle sunucunun CPU'sunu tüketmeye davetiyedir.
    Tavanın üstü 422 ile reddedilmeli — sessizce kırpılmamalı, çünkü kullanıcı
    istediğinden farklı bir hesap yaptığını bilmeli.
    """
    if not _API_VAR:
        return
    y = client.post("/simulate", json=_SIRKET | {"n_iter": MAX_ITER + 1})
    assert y.status_code == 422
    assert client.post("/simulate", json=_SIRKET | {"n_iter": MAX_ITER}).status_code == 200


def test_invalid_input_is_rejected_not_crashed():
    if not _API_VAR:
        return
    kotu = [
        {},                                        # zorunlu alanlar yok
        _SIRKET | {"current_cash": -1},            # negatif kasa
        _SIRKET | {"volatility": 5},               # oran 0-1 dışında
        _SIRKET | {"months": 0},
        _SIRKET | {"current_cash": "abc"},
    ]
    for govde in kotu:
        y = client.post("/simulate", json=govde)
        assert y.status_code == 422, f"{govde} icin 422 bekleniyordu, {y.status_code} geldi"


def test_sensitivity_endpoint_ranks_the_drivers():
    if not _API_VAR:
        return
    y = client.post("/sensitivity", json=_SIRKET)
    assert y.status_code == 200, y.text
    d = y.json()
    assert 0.0 <= d["base_probability"] <= 1.0
    surgular = d["drivers"]
    assert len(surgular) == 5
    # Sıralama sözleşmesi: drivers[0] en büyük kaldıraç olmalı.
    swings = [abs(s["swing"]) for s in surgular]
    assert swings == sorted(swings, reverse=True)


def test_sensitivity_has_a_lower_iteration_cap():
    """
    Bu uç tek istekte ~11 simülasyon koşar; /simulate'in tavanını aynen
    uygulamak yarım milyon senaryoluk tek istek demek olurdu.
    """
    if not _API_VAR:
        return
    assert MAX_ITER_SENSITIVITY < MAX_ITER
    ust = _SIRKET | {"n_iter": MAX_ITER_SENSITIVITY + 1}
    assert client.post("/sensitivity", json=ust).status_code == 422
    tam = _SIRKET | {"n_iter": MAX_ITER_SENSITIVITY}
    assert client.post("/sensitivity", json=tam).status_code == 200


def test_receivables_endpoint_matches_the_engine():
    """
    Yaşlandırma da motorun bir parçası: HTTP katmanı kendi hesabını yapmaya
    başlarsa arayüz ile API aynı defter için farklı şüpheli alacak gösterir.
    """
    if not _API_VAR:
        return
    from modules import receivables as rc

    defter = [{"customer": "A", "amount": 3_000_000, "overdue_days": 120},
              {"customer": "B", "amount": 1_000_000, "overdue_days": 10}]
    y = client.post("/receivables", json={
        "receivables": defter, "total_outstanding": 4_000_000,
        "monthly_revenue": 2_000_000,
    })
    assert y.status_code == 200, y.text
    d = y.json()
    dogrudan = rc.age(defter, 4_000_000, 2_000_000)
    assert d["expected_loss"] == dogrudan.expected_loss
    assert d["dso"] == dogrudan.dso
    assert len(d["buckets"]) == len(rc.DEFAULT_BUCKETS)
    assert set(d["implied"]) == {"delay_prob", "delay_severity", "expected_slip_rate",
                                 "achievable_slip_rate", "clamped"}


def test_receivables_endpoint_survives_an_empty_book():
    """Boş defter geçerli bir girdi: 422 değil, sıfırlı bir profil dönmeli."""
    if not _API_VAR:
        return
    y = client.post("/receivables", json={"receivables": []})
    assert y.status_code == 200, y.text
    assert y.json()["total"] == 0
    assert y.json()["dso_conflict"] is False


def test_loan_endpoint_returns_json_serialisable_numbers():
    """
    Motor içeride numpy dizileri de üretiyor; bunlar JSON'a çevrilemez.
    Uç nokta yalnızca skalerleri döndürmeli, yoksa 500 alırız.
    """
    if not _API_VAR:
        return
    y = client.post("/loan", json={
        "current_cash": 4_200_000, "monthly_revenue": 6_800_000,
        "monthly_fixed_expense": 5_950_000, "existing_debt_service": 950_000,
        "loan_amount": 10_000_000, "loan_term_months": 24,
        "monthly_interest_rate": 0.035, "horizon_months": 24,
    })
    assert y.status_code == 200, y.text
    d = y.json()
    assert d["installment"] > 0
    assert "relief_months" in d
    for anahtar, deger in d.items():
        assert isinstance(deger, (int, float, type(None))), f"{anahtar} skaler değil"


def test_advise_always_answers():
    """
    Anahtar yoksa bile kural tabanlı motor devreye girmeli — API boş cevap
    dönmemeli.
    """
    if not _API_VAR:
        return
    y = client.post("/advise", json={
        "current_cash": 4_200_000, "net_operating": -640_000,
        "monthly_net": -850_000, "ruin_probability": 0.63,
        "expected_ruin_month": 7, "loan_amount": 10_000_000,
        "relief_months": 5, "debt_service": 950_000,
    })
    assert y.status_code == 200, y.text
    d = y.json()
    assert d["text"].strip(), "boş tavsiye"
    assert d["source"]


def test_api_and_engine_agree():
    """
    API ile motorun DOĞRUDAN çağrısı aynı sonucu vermeli.

    Asıl risk buydu: HTTP katmanı zamanla kendi hesabını yapmaya başlarsa
    Streamlit arayüzü ile API aynı şirket için farklı sayı gösterir.
    """
    if not _API_VAR:
        return
    from modules import monte_carlo as mc

    dogrudan = mc.run(mc.StressParams(**_SIRKET))
    uzerinden = client.post("/simulate", json=_SIRKET).json()
    assert uzerinden["ruin_probability"] == dogrudan.ruin_probability


def test_api_arayuz_yigini_olmadan_ayaga_kalkar():
    """
    API'nin dağıtımı yalın kalmalı: streamlit / plotly / reportlab kurulu
    olmasa da ayağa kalkabilmeli. (pandas ve numpy hariç — onları motorun
    kendisi kullanıyor, arayüz bağımlılığı değiller.)

    Ölçülmüş bir sınır: ai_cfo.py biçimlendirme için utils.theme'i çağırıyordu,
    o da modül seviyesinde streamlit import ediyordu — yani HTTP API sırf para
    birimi yazdırmak için koca bir arayüz çatısı kuruyordu. Saf biçimlendirme
    utils/format.py'ye alındı.

    Engelleyici `find_spec` kullanır: `find_module`/`load_module` Python 3.12'de
    kaldırıldı ve sessizce hiçbir şey engellemez — o haliyle yazılan bir test
    her koşulda yeşil yanar, yani hiçbir şeyi korumaz.
    """
    if not _API_VAR:
        return

    yasak = {"streamlit", "plotly", "reportlab"}

    class _Engel:
        def find_spec(self, ad, yol=None, hedef=None):
            if ad.split(".")[0] in yasak:
                raise ImportError(f"{ad} bu testte bilerek engellendi")
            return None

    engel = _Engel()

    # Zinciri sıfırdan kurmak için hem proje modüllerini HEM DE yasaklı
    # paketleri sys.modules'ten düşür. Yasaklıları düşürmezsek import onları
    # önbellekten bulur, find_spec'e hiç uğramaz ve test her koşulda yeşil
    # yanar — yani hiçbir şeyi korumaz.
    def _ilgili(ad: str) -> bool:
        kok = ad.split(".")[0]
        return (ad == "api" or ad.startswith(("modules", "utils"))
                or kok in yasak)

    onceki = {a: m for a, m in sys.modules.items() if _ilgili(a)}
    for ad in onceki:
        del sys.modules[ad]
    sys.meta_path.insert(0, engel)
    try:
        import api as taze_api
        assert taze_api.app is not None
        yollar = {r.path for r in taze_api.app.routes if hasattr(r, "path")}
        assert {"/health", "/simulate", "/sensitivity", "/receivables",
                "/loan", "/advise"} <= yollar
    finally:
        sys.meta_path.remove(engel)
        for ad in [a for a in sys.modules if _ilgili(a)]:
            del sys.modules[ad]
        sys.modules.update(onceki)


if __name__ == "__main__":
    if not _API_VAR:
        print("  fastapi kurulu değil — API testleri atlandı")
        sys.exit(0)
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
