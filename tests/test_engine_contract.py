"""
test_engine_contract.py  —  Motor modüllerinin ORTAK sözleşmesi
────────────────────────────────────────────────────────────────
Uygulamanın en sık verdiği karar hesabın kendisi değil, ondan önceki karar:
"bu panel için yeterli veri var mı?" Cevap her yerde aynı olmalı — uydurma
yapma, paneli gizle, neyin eksik olduğunu söyle.

Ama bu cevap dört motor modülünde DÖRT ayrı dille yazılmıştı:

    zscore      : `available` + `missing_fields`
    receivables : örtük — çağıran `profile.total > 0` yazmak zorundaydı
    weekly      : `informative`
    runway      : fonksiyonun kendisi `None` dönüyordu

Dört deyimin bedeli iki katmanlı. Arayüz ve API her panel için o modüle özgü
deyimi hatırlamak zorundaydı; unutan bir çağrı yerinde panel sıfıra bölünmüş
oranlarla ya da bomboş çizilirdi. Ve yeni bir modül eklendiğinde BEŞİNCİ bir
deyim icat etmesini engelleyen hiçbir şey yoktu.

Bu dosya sözleşmeyi kilitler: her motor sonucu `available` ve `missing_fields`
konuşur, iki durum (veri yok / veri var ama sinyal yok) birbirine karışmaz.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import receivables, runway, weekly, zscore
from modules.data_io import load_mock
from utils.sufficiency import DataSufficiency, missing_label

MOCK = load_mock()


def _zengin():
    """Dört motorun da veriyle DOLU çalıştığı hâl."""
    return {
        "zscore": zscore.from_company(MOCK),
        "receivables": receivables.age(
            MOCK["top_receivables"], MOCK["receivables_outstanding"],
            MOCK["avg_monthly_revenue"]),
        "weekly": weekly.build(
            MOCK["current_cash"], MOCK["avg_monthly_collections"],
            MOCK["expense_breakdown"], MOCK["avg_monthly_fixed_expense"],
            MOCK["existing_monthly_debt_service"],
            start=weekly.parse_start(MOCK["as_of"])),
        "runway": runway.trend_runway(
            MOCK["history"], MOCK["current_cash"],
            MOCK["existing_monthly_debt_service"]),
    }


def _bos():
    """Aynı dört motorun HİÇ veri verilmediği hâl."""
    # `weekly` için borç servisi de SIFIR: taksitin ödendiği gün tek başına
    # ay-içi bilgidir, dolayısıyla gider dağılımı olmadan da tablo konuşur.
    return {
        "zscore": zscore.from_company({}),
        "receivables": receivables.age([], 0.0, 0.0),
        "weekly": weekly.build(1_000_000, 500_000, None, 400_000, 0.0),
        "runway": runway.trend_runway([], 1_000_000, 100_000),
    }


@pytest.mark.parametrize("ad", sorted(_zengin()))
def test_every_engine_answers_the_same_two_questions(ad):
    """Dört motor da `available` ve `missing_fields` konuşmalı."""
    for hal in (_zengin(), _bos()):
        sonuc = hal[ad]
        assert isinstance(sonuc, DataSufficiency), f"{ad} sözleşmeye uymuyor"
        assert isinstance(sonuc.available, bool)
        assert isinstance(sonuc.missing_fields, list)
        assert all(isinstance(a, str) for a in sonuc.missing_fields)


@pytest.mark.parametrize("ad", sorted(_zengin()))
def test_full_data_makes_every_engine_speak(ad):
    """Demo şirketi dört panelin de dolu olduğu senaryodur; biri susarsa hata."""
    sonuc = _zengin()[ad]
    assert sonuc.available, f"{ad} dolu veriyle susuyor"
    assert sonuc.missing_fields == [], f"{ad} olmayan eksik bildiriyor"


@pytest.mark.parametrize("ad", sorted(_bos()))
def test_empty_data_makes_every_engine_stay_silent(ad):
    """Veri yoksa panel açılmamalı — uydurma sayı, hiç sayı olmamasından kötüdür."""
    assert _bos()[ad].available is False, f"{ad} boş veriyle konuşuyor"


def test_missing_fields_are_names_the_user_can_actually_fill():
    """
    Eksik alan adları kullanıcının ŞABLONDA doldurabileceği adlar olmalı.

    Aksi hâlde ekranda "şunu doldur" denen alan yükleyicide yoktur ve kullanıcı
    kendi yazamayacağı bir adı arar — panelin susma sebebini de hiç öğrenemez.
    """
    from modules.data_io import (BICIM_A_GRUPLARI, BICIM_B_YAZAR,
                                 BICIM_C_YAZAR, GIDER_HEDEF)

    yazilabilir = {GIDER_HEDEF, *BICIM_B_YAZAR, *BICIM_C_YAZAR}
    for grup in BICIM_A_GRUPLARI:
        yazilabilir |= set(grup.alanlar)
        if grup.hedef:
            yazilabilir.add(grup.hedef)
    # runway'in okuduğu geçmiş SÜTUNLARI 'history' tablosunun içindedir.
    yazilabilir |= set(runway.TREND_FIELDS)

    for ad, sonuc in _bos().items():
        bilinmeyen = set(sonuc.missing_fields) - yazilabilir
        assert not bilinmeyen, f"{ad} doldurulamayan alan istiyor: {bilinmeyen}"


def test_missing_label_reads_as_a_single_line():
    """Arayüzün göstereceği tek satır; eksik yoksa tire."""
    assert missing_label(_zengin()["zscore"]) == "—"
    assert "," in missing_label(_bos()["receivables"])
