import json
import logging
import os
import uuid
from datetime import date, datetime, time, timedelta
from typing import List, Optional

import bcrypt
import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("yemekhane-api")

app = FastAPI(title="Kampüs Yemekhane API", root_path="/api")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
rdb = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

OTURUM_TTL = 1800  # 30 dakika, her istekte yenilenir


class GirisIstegi(BaseModel):
    eposta: str
    sifre: str


class RezervasyonIstegi(BaseModel):
    menu_id: int
    porsiyon: str = "tam"


class AlerjenGuncelleIstegi(BaseModel):
    alerjen_idler: List[int]


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


def gun_sonu_ttl_saniye() -> int:
    simdi = datetime.now()
    gun_sonu = datetime.combine(simdi.date(), time(23, 59, 59))
    kalan = int((gun_sonu - simdi).total_seconds())
    return max(kalan, 60)


def aktif_kullanici_getir(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Oturum açmanız gerekiyor.")
    token = authorization.replace("Bearer ", "").strip()
    anahtar = f"oturum:{token}"
    veri = rdb.get(anahtar)
    if not veri:
        raise HTTPException(status_code=401, detail="Oturum süresi dolmuş veya geçersiz.")
    rdb.expire(anahtar, OTURUM_TTL)
    kullanici = json.loads(veri)
    kullanici["token"] = token
    return kullanici


def menu_onbellegi_temizle(yemekhane_id: Optional[int] = None, tarih: Optional[str] = None, ogun_id: Optional[int] = None):
    if yemekhane_id and tarih and ogun_id:
        anahtar = f"menu:{yemekhane_id}:{tarih}:{ogun_id}"
        rdb.delete(anahtar)
        logger.info(f"[CACHE INVALIDATE] Redis anahtari silindi: {anahtar}")
    else:
        for anahtar in rdb.scan_iter("menu:*"):
            rdb.delete(anahtar)


@app.get("/saglik")
def saglik():
    return {"durum": "ok", "redis": rdb.ping()}


@app.post("/giris")
def giris(istek: GirisIstegi):
    k = db.kullanici_getir_eposta(istek.eposta)
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
    logger.info(f"[OTURUM] Yeni oturum olusturuldu: oturum:{token} (TTL={OTURUM_TTL}s)")
    alerjen_idler = db.kullanici_alerjenlerini_getir_db(k["id"])
    return {
        "token": token,
        "kullanici": {**oturum_verisi, "bakiye": float(k["bakiye"]), "alerjen_idler": alerjen_idler},
    }


@app.post("/cikis")
def cikis(kullanici=Depends(aktif_kullanici_getir)):
    rdb.delete(f"oturum:{kullanici['token']}")
    logger.info(f"[OTURUM] Oturum sonlandirildi: oturum:{kullanici['token']}")
    return {"mesaj": "Çıkış yapıldı."}


@app.get("/ben")
def ben(kullanici=Depends(aktif_kullanici_getir)):
    k = db.kullanici_getir_kaynak(kullanici["id"])
    if not k:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı.")
    k["alerjen_idler"] = db.kullanici_alerjenlerini_getir_db(kullanici["id"])
    return k


@app.get("/profil/alerjenler")
def profil_alerjenler_getir(kullanici=Depends(aktif_kullanici_getir)):
    return {"alerjen_idler": db.kullanici_alerjenlerini_getir_db(kullanici["id"])}


@app.put("/profil/alerjenler")
def profil_alerjenler_kaydet(istek: AlerjenGuncelleIstegi, kullanici=Depends(aktif_kullanici_getir)):
    guncel = db.kullanici_alerjenlerini_kaydet_db(kullanici["id"], istek.alerjen_idler)
    return {"mesaj": "Alerjen tercihleriniz güncellendi.", "alerjen_idler": guncel}


@app.get("/referanslar")
def referanslar(response: Response, kullanici=Depends(aktif_kullanici_getir)):
    cache_key = "ref:genel"
    onbellek = rdb.get(cache_key)
    if onbellek:
        data = json.loads(onbellek)
        if "alerjenler" in data:
            response.headers["X-Cache"] = "HIT"
            return data

    sonuc = db.referanslari_getir()
    rdb.setex(cache_key, gun_sonu_ttl_saniye(), json.dumps(sonuc, ensure_ascii=False, default=str))
    response.headers["X-Cache"] = "MISS"
    return sonuc


def _tek_ogun_menu_getir(yemekhane_id: int, tarih_str: str, ogun_id: int, response: Response):
    cache_key = f"menu:{yemekhane_id}:{tarih_str}:{ogun_id}"
    onbellek = rdb.get(cache_key)
    if onbellek:
        data = json.loads(onbellek)
        if "yarim_porsiyon_uygun" in data:
            logger.info(f"[CACHE HIT] Redis onbellekten okundu -> {cache_key}")
            response.headers["X-Cache"] = "HIT"
            return data

    logger.info(f"[CACHE MISS] Veritabanindan okundu -> {cache_key}")
    menu = db.tek_menu_getir_db(yemekhane_id, tarih_str, ogun_id)
    if menu:
        rdb.setex(cache_key, gun_sonu_ttl_saniye(), json.dumps(menu, ensure_ascii=False, default=str))
    response.headers["X-Cache"] = "MISS"
    return menu


@app.get("/menuler")
def menuler(
    response: Response,
    yemekhane_id: int,
    tarih: Optional[str] = None,
    baslangic: Optional[str] = None,
    bitis: Optional[str] = None,
    kullanici=Depends(aktif_kullanici_getir),
):
    ogun_idler = db.ogun_idler_getir()
    sonuc = []

    if tarih:
        for oid in ogun_idler:
            m = _tek_ogun_menu_getir(yemekhane_id, tarih, oid, response)
            if m:
                sonuc.append(m)
    elif baslangic and bitis:
        t1 = date.fromisoformat(baslangic)
        t2 = date.fromisoformat(bitis)
        gun = t1
        while gun <= t2:
            for oid in ogun_idler:
                m = _tek_ogun_menu_getir(yemekhane_id, gun.isoformat(), oid, response)
                if m:
                    sonuc.append(m)
            gun += timedelta(days=1)

    rez_harita = db.kullanici_rezervasyon_haritasi(kullanici["id"])
    kullanici_alerjen_set = set(db.kullanici_alerjenlerini_getir_db(kullanici["id"]))

    for m in sonuc:
        tam_fiyat = m["ogrenci_fiyati"] if kullanici["rol"] == "ogrenci" else m["personel_fiyati"]
        m["kullanici_fiyati"] = tam_fiyat
        m["yarim_fiyat"] = round(tam_fiyat * 0.60, 2) if m.get("yarim_porsiyon_uygun") else None
        m["mevcut_rezervasyon"] = rez_harita.get(m["menu_id"])

        for y in m["yemekler"]:
            y_alerjen_idler = set(y.get("alerjen_id_listesi") or [])
            y["alerjen_uyarisi"] = bool(kullanici_alerjen_set.intersection(y_alerjen_idler))

    return sonuc


@app.post("/rezervasyonlar")
def rezervasyon_yap(istek: RezervasyonIstegi, kullanici=Depends(aktif_kullanici_getir)):
    try:
        sonuc, meta = db.rezervasyon_olustur_db(kullanici["id"], istek.menu_id, istek.porsiyon)
    except Exception as e:
        raise HTTPException(status_code=400, detail=getattr(e, "args", ["", str(e)])[-1])

    if meta:
        menu_onbellegi_temizle(meta["yemekhane_id"], meta["tarih"], meta["ogun_id"])
    return {"mesaj": "Rezervasyon oluşturuldu.", **sonuc}


@app.get("/rezervasyonlarim")
def rezervasyonlarim(kullanici=Depends(aktif_kullanici_getir)):
    return db.rezervasyonlarim_listele_db(kullanici["id"])


@app.post("/rezervasyonlar/{rez_id}/iptal")
def rezervasyon_iptal(rez_id: int, kullanici=Depends(aktif_kullanici_getir)):
    try:
        sonuc, meta = db.rezervasyon_iptal_db(kullanici["id"], rez_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=getattr(e, "args", ["", str(e)])[-1])

    if meta:
        menu_onbellegi_temizle(meta["yemekhane_id"], meta["tarih"], meta["ogun_id"])
    return {"mesaj": "Rezervasyon iptal edildi ve ücret iade edildi.", **sonuc}


@app.post("/degerlendirmeler")
def degerlendirme_yap(istek: DegerlendirmeIstegi, kullanici=Depends(aktif_kullanici_getir)):
    try:
        meta = db.degerlendirme_kaydet_db(kullanici["id"], istek.menu_id, istek.puan, istek.yorum or "")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=400, detail="Bu menü için zaten değerlendirme yaptınız.")

    if meta:
        menu_onbellegi_temizle(meta["yemekhane_id"], meta["tarih"], meta["ogun_id"])
    return {"mesaj": "Değerlendirmeniz kaydedildi."}


@app.get("/bakiye")
def bakiye_getir(kullanici=Depends(aktif_kullanici_getir)):
    return db.bakiye_ve_hareketler_getir_db(kullanici["id"])


@app.post("/bakiye/yukle")
def bakiye_yukle(istek: BakiyeIstegi, kullanici=Depends(aktif_kullanici_getir)):
    try:
        sonuc = db.bakiye_yukle_db(kullanici["id"], istek.tutar)
        return {"mesaj": "Bakiye yüklendi.", **sonuc}
    except Exception as e:
        raise HTTPException(status_code=400, detail=getattr(e, "args", ["", str(e)])[-1])


@app.post("/yonetim/menuler")
def yonetim_menu_kaydet(istek: MenuOlusturIstegi, kullanici=Depends(aktif_kullanici_getir)):
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekiyor.")
    if not istek.yemek_idler:
        raise HTTPException(status_code=400, detail="En az bir yemek seçmelisiniz.")

    menu_id = db.yonetim_menu_kaydet_db(
        istek.yemekhane_id, istek.tarih, istek.ogun_id, istek.ogrenci_fiyati, istek.personel_fiyati, istek.yemek_idler
    )
    menu_onbellegi_temizle(istek.yemekhane_id, istek.tarih.isoformat(), istek.ogun_id)
    return {"mesaj": "Menü başarıyla kaydedildi.", "menu_id": menu_id}


@app.get("/yonetim/rapor")
def yonetim_rapor(kullanici=Depends(aktif_kullanici_getir)):
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekiyor.")
    return db.yonetim_rapor_getir_db()