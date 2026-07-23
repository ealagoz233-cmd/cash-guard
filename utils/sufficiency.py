"""
sufficiency.py — "Bu panel için yeterli veri var mı?" sorusunun TEK sözleşmesi.

Uygulamanın en sık verdiği karar, hesabın kendisi değil ondan önceki karar:
kullanıcı bu alanları doldurmadıysa panel ne göstermeli? Cevap her yerde aynı —
uydurma yapma, paneli gizle ve NEYİN eksik olduğunu söyle. Ama bu cevap üç motor
modülünde üç ayrı dille yazılmıştı:

    zscore      : `available` (score is not None) + `missing_fields`
    receivables : örtük — çağıranın `profile.total > 0` yazması gerekiyordu
    weekly      : `informative`

Üçü aynı soruyu soruyor, üçü farklı isim kullanıyordu. Bedeli iki katmanlı:
arayüz ve API her panel için o modüle özgü deyimi hatırlamak zorundaydı (biri
`total > 0` kontrolünü unutursa panel sıfıra bölmeye ya da boş grafiğe düşerdi)
ve yeni bir modül eklendiğinde DÖRDÜNCÜ bir deyim icat etmemesini sağlayan
hiçbir şey yoktu.

Bu dosya ortak sözleşmeyi tanımlar; modüller alan adlarını DEĞİŞTİRMEDEN
(`informative` ve `total` yerinde duruyor, API cevap şeması bozulmuyor) ortak
adları da sağlar. `tests/test_engine_contract.py` her motor sonucunun
sözleşmeye uyduğunu kilitler — dördüncü deyim artık test kırar.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DataSufficiency(Protocol):
    """Her motor sonucunun cevaplaması gereken iki soru."""

    @property
    def available(self) -> bool:
        """Bu sonuç bir şey söylüyor mu? False ise panel gizlenmeli."""

    @property
    def missing_fields(self) -> list[str]:
        """
        Eksik olan kullanıcı alanları (varsa).

        Boş liste "eksik yok" demek — `available=False` iken bile boş olabilir:
        bazı paneller alan eksikliğinden değil, verinin bilgi taşımamasından
        susar (bkz. weekly: gider dağılımı verilmiş ama hiçbiri tarihli değil).
        Bu yüzden liste bir GEREKÇE değil, doldurulacak alanların adresidir.
        """


def missing_label(sonuc: DataSufficiency) -> str:
    """Eksik alanları kullanıcıya gösterilecek tek satıra çevirir."""
    eksik = list(getattr(sonuc, "missing_fields", ()) or ())
    return ", ".join(eksik) if eksik else "—"
