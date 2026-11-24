import os
from datetime import date
from typing import List
import mysql.connector


class SozlukBaglantisi:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        kwargs.setdefault("dictionary", True)
        return self._conn.cursor(*args, **kwargs)

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _baglan(host: str):
    conn = mysql.connector.connect(
        host=host,
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_ADI", "yemekhane"),
        user=os.getenv("DB_KULLANICI", "yemekhane_app"),
        password=os.getenv("DB_SIFRE", "Uyg_2026"),
        charset="utf8mb4",
        autocommit=True,
    )
    return SozlukBaglantisi(conn)


def yazma_baglantisi():
    return _baglan(os.getenv("DB_YAZMA_HOST", "mysql-primary"))


def okuma_baglantisi():
    return _baglan(os.getenv("DB_OKUMA_HOST", "mysql-replica"))


# --- VERİ ERİŞİM KATMANI (DAL) FONKSİYONLARI ---

def kullanici_getir_eposta(eposta: str):
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, sifre_hash, rol, bakiye FROM kullanicilar WHERE eposta = %s",
                (eposta.strip().lower(),),
            )
            return cur.fetchone()
    finally:
        conn.close()


def kullanici_getir_kaynak(kullanici_id: int):
    """Replikasyon gecikmesi olmaması için güncel bakiye doğrudan kaynak sunucudan okunur."""
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, rol, bakiye FROM kullanicilar WHERE id = %s",
                (kullanici_id,),
            )
            k = cur.fetchone()
            if k:
                k["bakiye"] = float(k["bakiye"])
            return k
    finally:
        conn.close()


def referanslari_getir():
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, ad, kapasite FROM yemekhaneler ORDER BY id")
            yemekhaneler = cur.fetchall()
            cur.execute("SELECT id, ad, TIME_FORMAT(son_saat, '%H:%i') AS son_saat FROM ogunler ORDER BY id")
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
            cur.execute("SELECT id, ad FROM alerjenler ORDER BY id")
            alerjenler = cur.fetchall()
            return {
                "yemekhaneler": yemekhaneler,
                "ogunler": ogunler,
                "yemekler": yemekler,
                "alerjenler": alerjenler,
            }
    finally:
        conn.close()


def kullanici_alerjenlerini_getir_db(kullanici_id: int) -> List[int]:
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT alerjen_id FROM kullanici_alerjenleri WHERE kullanici_id = %s ORDER BY alerjen_id",
                (kullanici_id,),
            )
            return [int(r["alerjen_id"]) for r in cur.fetchall()]
    finally:
        conn.close()


def kullanici_alerjenlerini_kaydet_db(kullanici_id: int, alerjen_idler: List[int]) -> List[int]:
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kullanici_alerjenleri WHERE kullanici_id = %s", (kullanici_id,))
            for aid in sorted(set(alerjen_idler)):
                cur.execute(
                    "INSERT INTO kullanici_alerjenleri (kullanici_id, alerjen_id) VALUES (%s, %s)",
                    (kullanici_id, aid),
                )
            return sorted(set(alerjen_idler))
    finally:
        conn.close()


def ogun_idler_getir() -> List[int]:
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM ogunler ORDER BY id")
            return [r["id"] for r in cur.fetchall()]
    finally:
        conn.close()


def tek_menu_getir_db(yemekhane_id: int, tarih_str: str, ogun_id: int):
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id AS menu_id, m.yemekhane_id, yh.ad AS yemekhane,
                       DATE_FORMAT(m.tarih, '%Y-%m-%d') AS tarih,
                       o.id AS ogun_id, o.ad AS ogun,
                       m.ogrenci_fiyati, m.personel_fiyati,
                       d.kapasite, d.rezerve, d.bos_yer, d.doluluk_yuzde,
                       DATE_FORMAT(d.rezervasyon_son, '%Y-%m-%d %H:%i') AS rezervasyon_son,
                       (NOW() > d.rezervasyon_son) AS sure_doldu,
                       COALESCE(p.oy_sayisi, 0) AS oy_sayisi,
                       p.ortalama_puan
                FROM menuler m
                JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
                JOIN ogunler o ON o.id = m.ogun_id
                JOIN v_menu_doluluk d ON d.menu_id = m.id
                LEFT JOIN v_menu_puan p ON p.menu_id = m.id
                WHERE m.yemekhane_id = %s AND m.tarih = %s AND m.ogun_id = %s
                """,
                (yemekhane_id, tarih_str, ogun_id),
            )
            m = cur.fetchone()
            if not m:
                return None

            cur.execute(
                """
                SELECT yemek, kategori, kalori, vejetaryen, alerjenler, alerjen_idler
                FROM v_menu_detay
                WHERE menu_id = %s
                ORDER BY kategori_sira, yemek
                """,
                (m["menu_id"],),
            )
            yemekler = cur.fetchall()
            for y in yemekler:
                ham_idler = y.get("alerjen_idler")
                y["alerjen_id_listesi"] = [int(x) for x in str(ham_idler).split(",") if x] if ham_idler else []

            m["yemekler"] = yemekler
            m["toplam_kalori"] = sum(y["kalori"] for y in yemekler)
            m["ogrenci_fiyati"] = float(m["ogrenci_fiyati"])
            m["personel_fiyati"] = float(m["personel_fiyati"])
            m["doluluk_yuzde"] = float(m["doluluk_yuzde"])
            m["ortalama_puan"] = float(m["ortalama_puan"]) if m["ortalama_puan"] is not None else None
            m["sure_doldu"] = bool(m["sure_doldu"])
            m["yarim_porsiyon_uygun"] = int(m["ogun_id"]) != 1
            return m
    finally:
        conn.close()


def kullanici_rezervasyon_haritasi(kullanici_id: int):
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT menu_id, durum, porsiyon FROM rezervasyonlar WHERE kullanici_id = %s AND durum IN ('aktif', 'kullanildi')",
                (kullanici_id,),
            )
            return {r["menu_id"]: r for r in cur.fetchall()}
    finally:
        conn.close()


def rezervasyon_olustur_db(kullanici_id: int, menu_id: int, porsiyon: str = "tam"):
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("CALL sp_rezervasyon_yap(%s, %s, %s)", (kullanici_id, menu_id, porsiyon))
            sonuc = cur.fetchone()
            cur.execute(
                "SELECT yemekhane_id, DATE_FORMAT(tarih, '%Y-%m-%d') AS tarih, ogun_id FROM menuler WHERE id = %s",
                (menu_id,),
            )
            meta = cur.fetchone()
            return sonuc, meta
    finally:
        conn.close()


def rezervasyonlarim_listele_db(kullanici_id: int):
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.menu_id, r.durum, r.ucret, r.porsiyon,
                       DATE_FORMAT(m.tarih, '%Y-%m-%d') AS tarih,
                       yh.ad AS yemekhane, o.ad AS ogun,
                       (r.durum = 'aktif' AND NOW() <= d.rezervasyon_son) AS iptal_edilebilir,
                       dg.puan AS verilen_puan, dg.yorum AS verilen_yorum
                FROM rezervasyonlar r
                JOIN menuler m ON m.id = r.menu_id
                JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
                JOIN ogunler o ON o.id = m.ogun_id
                JOIN v_menu_doluluk d ON d.menu_id = r.menu_id
                LEFT JOIN degerlendirmeler dg ON dg.menu_id = r.menu_id AND dg.kullanici_id = r.kullanici_id
                WHERE r.kullanici_id = %s
                ORDER BY m.tarih DESC, o.id
                """,
                (kullanici_id,),
            )
            kayitlar = cur.fetchall()
            for k in kayitlar:
                k["ucret"] = float(k["ucret"])
                k["iptal_edilebilir"] = bool(k["iptal_edilebilir"])
            return kayitlar
    finally:
        conn.close()


def rezervasyon_iptal_db(kullanici_id: int, rez_id: int):
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.yemekhane_id, DATE_FORMAT(m.tarih, '%Y-%m-%d') AS tarih, m.ogun_id
                FROM rezervasyonlar r JOIN menuler m ON m.id = r.menu_id
                WHERE r.id = %s
                """,
                (rez_id,),
            )
            meta = cur.fetchone()
            cur.execute("CALL sp_rezervasyon_iptal(%s, %s)", (kullanici_id, rez_id))
            sonuc = cur.fetchone()
            return sonuc, meta
    finally:
        conn.close()


def degerlendirme_kaydet_db(kullanici_id: int, menu_id: int, puan: int, yorum: str):
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM rezervasyonlar WHERE kullanici_id = %s AND menu_id = %s AND durum = 'kullanildi'",
                (kullanici_id, menu_id),
            )
            if not cur.fetchone():
                raise ValueError("Yalnızca kullandığınız menüleri değerlendirebilirsiniz.")
            cur.execute(
                "INSERT INTO degerlendirmeler (kullanici_id, menu_id, puan, yorum) VALUES (%s, %s, %s, %s)",
                (kullanici_id, menu_id, puan, yorum),
            )
            cur.execute(
                "SELECT yemekhane_id, DATE_FORMAT(tarih, '%Y-%m-%d') AS tarih, ogun_id FROM menuler WHERE id = %s",
                (menu_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def bakiye_ve_hareketler_getir_db(kullanici_id: int):
    """Güncel bakiye kaynaktan, hareket geçmişi replikadan okunur."""
    kaynak = yazma_baglantisi()
    try:
        with kaynak.cursor() as cur:
            cur.execute("SELECT bakiye FROM kullanicilar WHERE id = %s", (kullanici_id,))
            k = cur.fetchone()
    finally:
        kaynak.close()

    replika = okuma_baglantisi()
    try:
        with replika.cursor() as cur:
            cur.execute(
                """
                SELECT id, tur, tutar, aciklama,
                       DATE_FORMAT(olusturma, '%Y-%m-%d %H:%i') AS olusturma
                FROM bakiye_hareketleri
                WHERE kullanici_id = %s
                ORDER BY id DESC LIMIT 30
                """,
                (kullanici_id,),
            )
            hareketler = cur.fetchall()
            for h in hareketler:
                h["tutar"] = float(h["tutar"])
            return {"bakiye": float(k["bakiye"]), "hareketler": hareketler}
    finally:
        replika.close()


def bakiye_yukle_db(kullanici_id: int, tutar: float):
    conn = yazma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute("CALL sp_bakiye_yukle(%s, %s)", (kullanici_id, tutar))
            return cur.fetchone()
    finally:
        conn.close()


def yonetim_menu_kaydet_db(yemekhane_id: int, tarih: date, ogun_id: int, ogrenci_fiyati: float, personel_fiyati: float, yemek_idler: List[int]):
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
                (yemekhane_id, tarih, ogun_id, ogrenci_fiyati, personel_fiyati),
            )
            cur.execute(
                "SELECT id FROM menuler WHERE yemekhane_id = %s AND tarih = %s AND ogun_id = %s",
                (yemekhane_id, tarih, ogun_id),
            )
            menu_id = cur.fetchone()["id"]
            cur.execute("DELETE FROM menu_yemekleri WHERE menu_id = %s", (menu_id,))
            for yid in yemek_idler:
                cur.execute("INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)", (menu_id, yid))
            return menu_id
    finally:
        conn.close()


def yonetim_rapor_getir_db():
    conn = okuma_baglantisi()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id AS menu_id, DATE_FORMAT(m.tarih, '%Y-%m-%d') AS tarih,
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
