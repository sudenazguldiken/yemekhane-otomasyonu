import secrets
from typing import List, Optional
import bcrypt
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import db

app = FastAPI(title="Yemekhane Otomasyonu API")

# Aşama 2'de bellek içi oturum deposu (Aşama 3'te Redis'e taşınacak)
OTURUMLAR = {}


class GirisIstegi(BaseModel):
    eposta: str
    sifre: str


class RezervasyonIstegi(BaseModel):
    menu_id: int


class BakiyeIstegi(BaseModel):
    tutar: float


class DegerlendirmeIstegi(BaseModel):
    menu_id: int
    puan: int
    yorum: Optional[str] = None


class MenuKayitIstegi(BaseModel):
    yemekhane_id: int
    tarih: str
    ogun_id: int
    ogrenci_fiyati: float
    personel_fiyati: float
    yemek_idler: List[int]


def aktif_kullanici(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Oturum açmanız gerekiyor.")
    token = authorization.replace("Bearer ", "").strip()
    kullanici_id = OTURUMLAR.get(token)
    if not kullanici_id:
        raise HTTPException(status_code=401, detail="Oturum geçersiz veya süresi dolmuş.")
    kullanici = db.tek_satir_getir(
        "SELECT id, ad, soyad, eposta, rol, ogrenci_no, bakiye FROM kullanicilar WHERE id = %s AND aktif = 1",
        (kullanici_id,),
        kaynaktan=True,
    )
    if not kullanici:
        raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı.")
    return kullanici


@app.get("/api/")
def saglik_kontrol():
    return {"durum": "ok", "mesaj": "Yemekhane API aktif"}


@app.post("/api/giris")
def giris(veri: GirisIstegi):
    kullanici = db.tek_satir_getir(
        "SELECT id, ad, soyad, eposta, sifre_hash, rol, ogrenci_no, bakiye, aktif FROM kullanicilar WHERE eposta = %s",
        (veri.eposta.strip(),),
    )
    if not kullanici or kullanici["aktif"] == 0:
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")

    sifre_dogru = bcrypt.checkpw(
        veri.sifre.encode("utf-8"),
        kullanici["sifre_hash"].encode("utf-8"),
    )
    if not sifre_dogru:
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")

    token = secrets.token_hex(24)
    OTURUMLAR[token] = kullanici["id"]
    del kullanici["sifre_hash"]
    return {"token": token, "kullanici": kullanici}


@app.post("/api/cikis")
def cikis(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "").strip()
        OTURUMLAR.pop(token, None)
    return {"mesaj": "Çıkış yapıldı."}


@app.get("/api/ben")
def ben(authorization: Optional[str] = Header(None)):
    return aktif_kullanici(authorization)


@app.get("/api/referanslar")
def referanslar(authorization: Optional[str] = Header(None)):
    aktif_kullanici(authorization)
    yemekhaneler = db.sorgu_getir("SELECT id, ad, konum, kapasite FROM yemekhaneler ORDER BY id")
    ogunler = db.sorgu_getir("SELECT id, ad, sira, TIME_FORMAT(son_saat, '%H:%i') AS son_saat FROM ogunler ORDER BY sira")
    yemekler = db.sorgu_getir(
        """SELECT y.id, y.ad, y.kalori, y.vejetaryen, k.ad AS kategori, k.sira AS kategori_sira
             FROM yemekler y JOIN yemek_kategorileri k ON k.id = y.kategori_id
            ORDER BY k.sira, y.ad"""
    )
    return {"yemekhaneler": yemekhaneler, "ogunler": ogunler, "yemekler": yemekler}


@app.get("/api/menuler")
def menuler(yemekhane_id: int, tarih: Optional[str] = None, baslangic: Optional[str] = None, bitis: Optional[str] = None, authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    kosullar = ["d.yemekhane_id = %s"]
    params = [yemekhane_id]

    if tarih:
        kosullar.append("d.tarih = %s")
        params.append(tarih)
    elif baslangic and bitis:
        kosullar.append("d.tarih BETWEEN %s AND %s")
        params.extend([baslangic, bitis])

    where_sql = " AND ".join(kosullar)

    basliklar = db.sorgu_getir(
        f"""SELECT d.menu_id, DATE_FORMAT(d.tarih, '%Y-%m-%d') AS tarih, d.yemekhane_id, d.yemekhane,
                   d.ogun_id, d.ogun, d.kapasite, d.rezerve, d.bos_yer, d.doluluk_yuzde,
                   DATE_FORMAT(d.rezervasyon_son, '%Y-%m-%d %H:%i') AS rezervasyon_son,
                   (NOW() >= d.rezervasyon_son) AS sure_doldu,
                   m.ogrenci_fiyati, m.personel_fiyati,
                   COALESCE(p.oy_sayisi, 0) AS oy_sayisi,
                   p.ortalama_puan
              FROM v_menu_doluluk d
              JOIN menuler m ON m.id = d.menu_id
              JOIN ogunler o ON o.id = d.ogun_id
              LEFT JOIN v_menu_puan p ON p.menu_id = d.menu_id
             WHERE {where_sql}
             ORDER BY d.tarih, o.sira""",
        tuple(params),
    )

    if not basliklar:
        return []

    menu_idler = [b["menu_id"] for b in basliklar]
    yer_tutucu = ",".join(["%s"] * len(menu_idler))

    detaylar = db.sorgu_getir(
        f"""SELECT menu_id, yemek_id, yemek, kategori, kategori_sira, kalori, vejetaryen, alerjenler, alerjen_idler
              FROM v_menu_detay
             WHERE menu_id IN ({yer_tutucu})
             ORDER BY kategori_sira, yemek""",
        tuple(menu_idler),
    )

    aktif_rezler = db.sorgu_getir(
        f"""SELECT menu_id, id AS rezervasyon_id, durum
              FROM rezervasyonlar
             WHERE kullanici_id = %s AND menu_id IN ({yer_tutucu}) AND durum IN ('aktif', 'kullanildi')""",
        tuple([kullanici["id"]] + menu_idler),
        kaynaktan=True,
    )
    rez_map = {r["menu_id"]: r for r in aktif_rezler}

    yemek_map = {}
    for sat in detaylar:
        yemek_map.setdefault(sat["menu_id"], []).append(sat)

    for b in basliklar:
        yemek_listesi = yemek_map.get(b["menu_id"], [])
        b["yemekler"] = yemek_listesi
        b["toplam_kalori"] = sum(y["kalori"] for y in yemek_listesi)
        b["kullanici_fiyati"] = float(b["ogrenci_fiyati"] if kullanici["rol"] == "ogrenci" else b["personel_fiyati"])
        b["ogrenci_fiyati"] = float(b["ogrenci_fiyati"])
        b["personel_fiyati"] = float(b["personel_fiyati"])
        b["sure_doldu"] = bool(b["sure_doldu"])
        b["mevcut_rezervasyon"] = rez_map.get(b["menu_id"])

    return basliklar


@app.post("/api/rezervasyonlar")
def rezervasyon_yap(veri: RezervasyonIstegi, authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    try:
        sonuc = db.prosedür_cagir("sp_rezervasyon_yap", (kullanici["id"], veri.menu_id))
        return {"mesaj": "Rezervasyon oluşturuldu.", "sonuc": sonuc}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/rezervasyonlarim")
def rezervasyonlarim(authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    return db.sorgu_getir(
        """SELECT r.id, r.menu_id, r.durum, r.ucret,
                  DATE_FORMAT(r.olusturma, '%Y-%m-%d %H:%i') AS olusturma,
                  DATE_FORMAT(r.kullanma_zamani, '%Y-%m-%d %H:%i') AS kullanma_zamani,
                  DATE_FORMAT(r.iptal_zamani, '%Y-%m-%d %H:%i') AS iptal_zamani,
                  DATE_FORMAT(m.tarih, '%Y-%m-%d') AS tarih,
                  yh.ad AS yemekhane, o.ad AS ogun,
                  (NOW() < TIMESTAMP(m.tarih, o.son_saat)) AS iptal_edilebilir,
                  d.puan AS verilen_puan, d.yorum AS verilen_yorum
             FROM rezervasyonlar r
             JOIN menuler m ON m.id = r.menu_id
             JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
             JOIN ogunler o ON o.id = m.ogun_id
             LEFT JOIN degerlendirmeler d ON d.kullanici_id = r.kullanici_id AND d.menu_id = r.menu_id
            WHERE r.kullanici_id = %s
            ORDER BY m.tarih DESC, o.sira DESC""",
        (kullanici["id"],),
        kaynaktan=True,
    )


@app.post("/api/rezervasyonlar/{rezervasyon_id}/iptal")
def rezervasyon_iptal(rezervasyon_id: int, authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    try:
        sonuc = db.prosedür_cagir("sp_rezervasyon_iptal", (kullanici["id"], rezervasyon_id))
        return {"mesaj": "Rezervasyon iptal edildi ve ücret iade edildi.", "sonuc": sonuc}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/bakiye")
def bakiye_bilgisi(authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    # Güncel bakiye replikasyon gecikmesine takılmamak için kaynaktan okunur
    guncel = db.tek_satir_getir("SELECT bakiye FROM kullanicilar WHERE id = %s", (kullanici["id"],), kaynaktan=True)
    hareketler = db.sorgu_getir(
        """SELECT id, tur, tutar, rezervasyon_id, aciklama,
                  DATE_FORMAT(olusturma, '%Y-%m-%d %H:%i') AS olusturma
             FROM bakiye_hareketleri
            WHERE kullanici_id = %s
            ORDER BY olusturma DESC, id DESC""",
        (kullanici["id"],),
    )
    return {"bakiye": float(guncel["bakiye"]), "hareketler": hareketler}


@app.post("/api/bakiye/yukle")
def bakiye_yukle(veri: BakiyeIstegi, authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    try:
        sonuc = db.prosedür_cagir("sp_bakiye_yukle", (kullanici["id"], veri.tutar))
        return {"mesaj": "Bakiye yüklendi.", "sonuc": sonuc}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/degerlendirmeler")
def degerlendirme_yap(veri: DegerlendirmeIstegi, authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    if veri.puan < 1 or veri.puan > 5:
        raise HTTPException(status_code=400, detail="Puan 1 ile 5 arasında olmalıdır.")

    rez = db.tek_satir_getir(
        "SELECT durum FROM rezervasyonlar WHERE kullanici_id = %s AND menu_id = %s",
        (kullanici["id"], veri.menu_id),
        kaynaktan=True,
    )
    if not rez or rez["durum"] != "kullanildi":
        raise HTTPException(status_code=400, detail="Yalnızca kullanılmış bir menüyü değerlendirebilirsiniz.")

    mevcut = db.tek_satir_getir(
        "SELECT id FROM degerlendirmeler WHERE kullanici_id = %s AND menu_id = %s",
        (kullanici["id"], veri.menu_id),
        kaynaktan=True,
    )
    if mevcut:
        raise HTTPException(status_code=400, detail="Bu menüyü daha önce değerlendirdiniz.")

    db.yazma_calistir(
        "INSERT INTO degerlendirmeler (kullanici_id, menu_id, puan, yorum) VALUES (%s, %s, %s, %s)",
        (kullanici["id"], veri.menu_id, veri.puan, veri.yorum.strip() if veri.yorum else None),
    )
    return {"mesaj": "Değerlendirmeniz kaydedildi."}


@app.get("/api/yonetim/rapor")
def yonetim_raporu(authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekir.")

    satirlar = db.sorgu_getir(
        """SELECT d.menu_id, DATE_FORMAT(d.tarih, '%Y-%m-%d') AS tarih, d.yemekhane_id, d.yemekhane,
                  d.ogun_id, d.ogun, d.kapasite, d.rezerve, d.bos_yer, d.doluluk_yuzde,
                  m.ogrenci_fiyati, m.personel_fiyati,
                  COALESCE(p.oy_sayisi, 0) AS oy_sayisi, p.ortalama_puan
             FROM v_menu_doluluk d
             JOIN menuler m ON m.id = d.menu_id
             JOIN ogunler o ON o.id = d.ogun_id
             LEFT JOIN v_menu_puan p ON p.menu_id = d.menu_id
            ORDER BY d.tarih DESC, d.yemekhane_id, o.sira"""
    )
    for s in satirlar:
        yemekler = db.sorgu_getir(
            "SELECT yemek_id, yemek, kategori FROM v_menu_detay WHERE menu_id = %s ORDER BY kategori_sira, yemek",
            (s["menu_id"],),
        )
        s["yemekler"] = yemekler
    return satirlar


@app.post("/api/yonetim/menuler")
def yonetim_menu_kaydet(veri: MenuKayitIstegi, authorization: Optional[str] = Header(None)):
    kullanici = aktif_kullanici(authorization)
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekir.")
    if not veri.yemek_idler:
        raise HTTPException(status_code=400, detail="Menüye en az bir yemek seçmelisiniz.")

    mevcut = db.tek_satir_getir(
        "SELECT id FROM menuler WHERE yemekhane_id = %s AND tarih = %s AND ogun_id = %s",
        (veri.yemekhane_id, veri.tarih, veri.ogun_id),
        kaynaktan=True,
    )

    if mevcut:
        menu_id = mevcut["id"]
        db.yazma_calistir(
            "UPDATE menuler SET ogrenci_fiyati = %s, personel_fiyati = %s WHERE id = %s",
            (veri.ogrenci_fiyati, veri.personel_fiyati, menu_id),
        )
        db.yazma_calistir("DELETE FROM menu_yemekleri WHERE menu_id = %s", (menu_id,))
    else:
        menu_id = db.yazma_calistir(
            """INSERT INTO menuler (yemekhane_id, tarih, ogun_id, ogrenci_fiyati, personel_fiyati)
               VALUES (%s, %s, %s, %s, %s)""",
            (veri.yemekhane_id, veri.tarih, veri.ogun_id, veri.ogrenci_fiyati, veri.personel_fiyati),
        )

    for yid in set(veri.yemek_idler):
        db.yazma_calistir("INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)", (menu_id, yid))

    return {"mesaj": "Menü başarıyla kaydedildi.", "menu_id": menu_id}