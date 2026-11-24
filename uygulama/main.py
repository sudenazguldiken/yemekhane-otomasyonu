import json
import os
import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional

import bcrypt
import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from db import okuma_baglantisi, yazma_baglantisi

app = FastAPI(title="Kampüs Yemekhane API", root_path="/api")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
rdb = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

OTURUM_TTL = 3600 * 8  # 8 saat
MENU_CACHE_TTL = 300   # 5 dakika


class GirisIstegi(BaseModel):
    eposta: str
    sifre: str


class RezervasyonIstegi(BaseModel):
    menu_id: int


class BakiyeIstegi(BaseModel):
    tutar: float = Field(..., ge=10, le=1000)


class DegerlendirmeIstegi(BaseModel):
    menu_id: int
    puan: int = Field(..., ge=1, le=5)
    yorum: Optional[str] = ""


class MenuOlusturIstegi(BaseModel):
    yemekhane_id: int
    tarih: date
    ogun_id: int
    ogrenci_fiyati: float = Field(..., ge=0)
    personel_fiyati: float = Field(..., ge=0)
    yemek_idler: List[int]


def aktif_kullanici_getir(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Oturum açmanız gerekiyor.")
    token = authorization.replace("Bearer ", "").strip()
    veri = rdb.get(f"oturum:{token}")
    if not veri:
        raise HTTPException(status_code=401, detail="Oturum süresi dolmuş veya geçersiz.")
    kullanici = json.loads(veri)
    kullanici["token"] = token
    return kullanici


def menu_onbellegi_temizle(yemekhane_id: Optional[int] = None, tarih: Optional[str] = None):
    if yemekhane_id and tarih:
        rdb.delete(f"menu:{yemekhane_id}:{tarih}")
    else:
        for anahtar in rdb.scan_iter("menu:*"):
            rdb.delete(anahtar)


@app.get("/saglik")
def saglik():
    redis_durum = rdb.ping()
    return {"durum": "ok", "redis": redis_durum}


@app.post("/giris")
def giris(istek: GirisIstegi):
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, sifre_hash, rol, bakiye FROM kullanicilar WHERE eposta = %s",
                (istek.eposta.strip().lower(),),
            )
            k = cur.fetchone()
    finally:
        conn.close()

    if not k or not bcrypt.checkpw(istek.sifre.encode("utf-8"), k["sifre_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")

    token = uuid.uuid4().hex
    oturum_verisi = {
        "id": k["id"],
        "ad": k["ad"],
        "soyad": k["soyad"],
        "eposta": k["eposta"],
        "rol": k["rol"],
    }
    rdb.setex(f"oturum:{token}", OTURUM_TTL, json.dumps(oturum_verisi, ensure_ascii=False))
    return {"token": token, "kullanici": {**oturum_verisi, "bakiye": float(k["bakiye"])}}


@app.post("/cikis")
def cikis(kullanici=Depends(aktif_kullanici_getir)):
    rdb.delete(f"oturum:{kullanici['token']}")
    return {"mesaj": "Çıkış yapıldı."}


@app.get("/ben")
def ben(kullanici=Depends(aktif_kullanici_getir)):
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, rol, bakiye FROM kullanicilar WHERE id = %s",
                (kullanici["id"],),
            )
            k = cur.fetchone()
    finally:
        conn.close()
    if not k:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı.")
    k["bakiye"] = float(k["bakiye"])
    return k


@app.get("/referanslar")
def referanslar(response: Response, kullanici=Depends(aktif_kullanici_getir)):
    cache_key = "ref:genel"
    onbellek = rdb.get(cache_key)
    if onbellek:
        response.headers["X-Cache"] = "HIT"
        return json.loads(onbellek)

    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, ad, kapasite FROM yemekhaneler ORDER BY id")
            yemekhaneler = cur.fetchall()
            cur.execute("SELECT id, ad, TIME_FORMAT(son_saat, '%%H:%%i') AS son_saat FROM ogunler ORDER BY id")
            ogunler = cur.fetchall()
            cur.execute(
                """
                SELECT y.id, y.ad, yk.ad AS kategori, y.kalori, y.vejetaryen
                FROM yemekler y
                JOIN yemek_kategorileri yk ON yk.id = y.kategori_id
                ORDER BY yk.id, y.ad
                """
            )
            yemekler = cur.fetchall()
    finally:
        conn.close()

    sonuc = {"yemekhaneler": yemekhaneler, "ogunler": ogunler, "yemekler": yemekler}
    rdb.setex(cache_key, 600, json.dumps(sonuc, ensure_ascii=False, default=str))
    response.headers["X-Cache"] = "MISS"
    return sonuc


def _menu_listesi_hazirla(yemekhane_id: int, tarih_str: str, response: Response):
    cache_key = f"menu:{yemekhane_id}:{tarih_str}"
    onbellek = rdb.get(cache_key)
    if onbellek:
        response.headers["X-Cache"] = "HIT"
        return json.loads(onbellek)

    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id AS menu_id, m.yemekhane_id, yh.ad AS yemekhane,
                       DATE_FORMAT(m.tarih, '%%Y-%%m-%%d') AS tarih,
                       o.id AS ogun_id, o.ad AS ogun,
                       m.ogrenci_fiyati, m.personel_fiyati,
                       d.kapasite, d.rezerve, d.bos_yer, d.doluluk_yuzde,
                       DATE_FORMAT(d.rezervasyon_son, '%%Y-%%m-%%d %%H:%%i') AS rezervasyon_son,
                       (NOW() > d.rezervasyon_son) AS sure_doldu,
                       COALESCE(p.oy_sayisi, 0) AS oy_sayisi,
                       p.ortalama_puan
                FROM menuler m
                JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
                JOIN ogunler o ON o.id = m.ogun_id
                JOIN v_menu_doluluk d ON d.menu_id = m.id
                LEFT JOIN v_menu_puan p ON p.menu_id = m.id
                WHERE m.yemekhane_id = %s AND m.tarih = %s
                ORDER BY o.id
                """,
                (yemekhane_id, tarih_str),
            )
            menuler = cur.fetchall()

            for m in menuler:
                cur.execute(
                    """
                    SELECT yemek, kategori, kalori, vejetaryen, alerjenler
                    FROM v_menu_detay
                    WHERE menu_id = %s
                    ORDER BY kategori_sira, yemek
                    """,
                    (m["menu_id"],),
                )
                yemekler = cur.fetchall()
                m["yemekler"] = yemekler
                m["toplam_kalori"] = sum(y["kalori"] for y in yemekler)
                m["ogrenci_fiyati"] = float(m["ogrenci_fiyati"])
                m["personel_fiyati"] = float(m["personel_fiyati"])
                m["doluluk_yuzde"] = float(m["doluluk_yuzde"])
                m["ortalama_puan"] = float(m["ortalama_puan"]) if m["ortalama_puan"] is not None else None
                m["sure_doldu"] = bool(m["sure_doldu"])
    finally:
        conn.close()

    rdb.setex(cache_key, MENU_CACHE_TTL, json.dumps(menuler, ensure_ascii=False, default=str))
    response.headers["X-Cache"] = "MISS"
    return menuler


@app.get("/menuler")
def menuler(
    response: Response,
    yemekhane_id: int,
    tarih: Optional[str] = None,
    baslangic: Optional[str] = None,
    bitis: Optional[str] = None,
    kullanici=Depends(aktif_kullanici_getir),
):
    sonuc = []
    if tarih:
        sonuc = _menu_listesi_hazirla(yemekhane_id, tarih, response)
    elif baslangic and bitis:
        t1 = date.fromisoformat(baslangic)
        t2 = date.fromisoformat(bitis)
        gun = t1
        while gun <= t2:
            sonuc.extend(_menu_listesi_hazirla(yemekhane_id, gun.isoformat(), response))
            gun += timedelta(days=1)

    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT menu_id, durum FROM rezervasyonlar WHERE kullanici_id = %s AND durum IN ('aktif', 'kullanildi')",
                (kullanici["id"],),
            )
            rez_harita = {r["menu_id"]: r for r in cur.fetchall()}
    finally:
        conn.close()

    for m in sonuc:
        m["kullanici_fiyati"] = m["ogrenci_fiyati"] if kullanici["rol"] == "ogrenci" else m["personel_fiyati"]
        m["mevcut_rezervasyon"] = rez_harita.get(m["menu_id"])
    return sonuc


@app.post("/rezervasyonlar")
def rezervasyon_yap(istek: RezervasyonIstegi, kullanici=Depends(aktif_kullanici_getir)):
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("CALL sp_rezervasyon_yap(%s, %s)", (kullanici["id"], istek.menu_id))
            sonuc = cur.fetchone()
            cur.execute("SELECT yemekhane_id, DATE_FORMAT(tarih, '%%Y-%%m-%%d') AS tarih FROM menuler WHERE id = %s", (istek.menu_id,))
            m = cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=400, detail=getattr(e, "args", ["", str(e)])[-1])
    finally:
        conn.close()

    if m:
        menu_onbellegi_temizle(m["yemekhane_id"], m["tarih"])
    return {"mesaj": "Rezervasyon oluşturuldu.", **sonuc}


@app.get("/rezervasyonlarim")
def rezervasyonlarim(kullanici=Depends(aktif_kullanici_getir)):
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.menu_id, r.durum, r.ucret,
                       DATE_FORMAT(m.tarih, '%%Y-%%m-%%d') AS tarih,
                       yh.ad AS yemekhane, o.ad AS ogun,
                       (r.durum = 'aktif' AND NOW() <= d.rezervasyon_son) AS iptal_edilebilir,
                       dg.puan AS verilen_puan, dg.yorum AS verilen_yorum
                FROM rezervasyonlar r
                JOIN menuler m ON m.id = r.menu_id
                JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
                JOIN ogunler o ON o.id = m.ogun_id
                JOIN v_menu_doluluk d ON d.menu_id = m.id
                LEFT JOIN degerlendirmeler dg ON dg.menu_id = r.menu_id AND dg.kullanici_id = r.kullanici_id
                WHERE r.kullanici_id = %s
                ORDER BY m.tarih DESC, o.id
                """,
                (kullanici["id"],),
            )
            kayitlar = cur.fetchall()
            for k in kayitlar:
                k["ucret"] = float(k["ucret"])
                k["iptal_edilebilir"] = bool(k["iptal_edilebilir"])
            return kayitlar
    finally:
        conn.close()


@app.post("/rezervasyonlar/{rez_id}/iptal")
def rezervasyon_iptal(rez_id: int, kullanici=Depends(aktif_kullanici_getir)):
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.yemekhane_id, DATE_FORMAT(m.tarih, '%%Y-%%m-%%d') AS tarih
                FROM rezervasyonlar r JOIN menuler m ON m.id = r.menu_id
                WHERE r.id = %s
                """,
                (rez_id,),
            )
            m = cur.fetchone()
            cur.execute("CALL sp_rezervasyon_iptal(%s, %s)", (kullanici["id"], rez_id))
            sonuc = cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=400, detail=getattr(e, "args", ["", str(e)])[-1])
    finally:
        conn.close()

    if m:
        menu_onbellegi_temizle(m["yemekhane_id"], m["tarih"])
    return {"mesaj": "Rezervasyon iptal edildi ve ücret iade edildi.", **sonuc}


@app.post("/degerlendirmeler")
def degerlendirme_yap(istek: DegerlendirmeIstegi, kullanici=Depends(aktif_kullanici_getir)):
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM rezervasyonlar WHERE kullanici_id = %s AND menu_id = %s AND durum = 'kullanildi'",
                (kullanici["id"], istek.menu_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail="Yalnızca kullandığınız menüleri değerlendirebilirsiniz.")
            cur.execute(
                """
                INSERT INTO degerlendirmeler (kullanici_id, menu_id, puan, yorum)
                VALUES (%s, %s, %s, %s)
                """,
                (kullanici["id"], istek.menu_id, istek.puan, istek.yorum),
            )
            cur.execute("SELECT yemekhane_id, DATE_FORMAT(tarih, '%%Y-%%m-%%d') AS tarih FROM menuler WHERE id = %s", (istek.menu_id,))
            m = cur.fetchone()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Bu menü için zaten değerlendirme yaptınız.")
    finally:
        conn.close()

    if m:
        menu_onbellegi_temizle(m["yemekhane_id"], m["tarih"])
    return {"mesaj": "Değerlendirmeniz kaydedildi."}


@app.get("/bakiye")
def bakiye_getir(kullanici=Depends(aktif_kullanici_getir)):
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT bakiye FROM kullanicilar WHERE id = %s", (kullanici["id"],))
            k = cur.fetchone()
            cur.execute(
                """
                SELECT id, tur, tutar, aciklama,
                       DATE_FORMAT(olusturma, '%%Y-%%m-%%d %%H:%%i') AS olusturma
                FROM bakiye_hareketleri
                WHERE kullanici_id = %s
                ORDER BY id DESC LIMIT 30
                """,
                (kullanici["id"],),
            )
            hareketler = cur.fetchall()
            for h in hareketler:
                h["tutar"] = float(h["tutar"])
            return {"bakiye": float(k["bakiye"]), "hareketler": hareketler}
    finally:
        conn.close()


@app.post("/bakiye/yukle")
def bakiye_yukle(istek: BakiyeIstegi, kullanici=Depends(aktif_kullanici_getir)):
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("CALL sp_bakiye_yukle(%s, %s)", (kullanici["id"], istek.tutar))
            sonuc = cur.fetchone()
            return {"mesaj": "Bakiye yüklendi.", **sonuc}
    except Exception as e:
        raise HTTPException(status_code=400, detail=getattr(e, "args", ["", str(e)])[-1])
    finally:
        conn.close()


@app.post("/yonetim/menuler")
def yonetim_menu_kaydet(istek: MenuOlusturIstegi, kullanici=Depends(aktif_kullanici_getir)):
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekiyor.")
    if not istek.yemek_idler:
        raise HTTPException(status_code=400, detail="En az bir yemek seçmelisiniz.")

    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO menuler (yemekhane_id, tarih, ogun_id, ogrenci_fiyati, personel_fiyati)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ogrenci_fiyati = VALUES(ogrenci_fiyati),
                    personel_fiyati = VALUES(personel_fiyati)
                """,
                (istek.yemekhane_id, istek.tarih, istek.ogun_id, istek.ogrenci_fiyati, istek.personel_fiyati),
            )
            cur.execute(
                "SELECT id FROM menuler WHERE yemekhane_id = %s AND tarih = %s AND ogun_id = %s",
                (istek.yemekhane_id, istek.tarih, istek.ogun_id),
            )
            menu_id = cur.fetchone()["id"]
            cur.execute("DELETE FROM menu_yemekleri WHERE menu_id = %s", (menu_id,))
            for yid in istek.yemek_idler:
                cur.execute("INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)", (menu_id, yid))
    finally:
        conn.close()

    menu_onbellegi_temizle(istek.yemekhane_id, istek.tarih.isoformat())
    return {"mesaj": "Menü başarıyla kaydedildi.", "menu_id": menu_id}


@app.get("/yonetim/rapor")
def yonetim_rapor(kullanici=Depends(aktif_kullanici_getir)):
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekiyor.")
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id AS menu_id, DATE_FORMAT(m.tarih, '%%Y-%%m-%%d') AS tarih,
                       yh.ad AS yemekhane, o.ad AS ogun,
                       d.kapasite, d.rezerve, d.doluluk_yuzde,
                       COALESCE(p.oy_sayisi, 0) AS oy_sayisi, p.ortalama_puan
                FROM menuler m
                JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
                JOIN ogunler o ON o.id = m.ogun_id
                JOIN v_menu_doluluk d ON d.menu_id = m.id
                LEFT JOIN v_menu_puan p ON p.menu_id = m.id
                ORDER BY m.tarih DESC, yh.id, o.id
                LIMIT 30
                """
            )
            satirlar = cur.fetchall()
            for s in satirlar:
                cur.execute("SELECT yemek FROM v_menu_detay WHERE menu_id = %s ORDER BY kategori_sira", (s["menu_id"],))
                s["yemekler"] = cur.fetchall()
                s["doluluk_yuzde"] = float(s["doluluk_yuzde"])
                s["ortalama_puan"] = float(s["ortalama_puan"]) if s["ortalama_puan"] is not None else None
            return satirlar
    finally:
        conn.close()