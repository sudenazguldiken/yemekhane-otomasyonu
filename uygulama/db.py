import os
import mysql.connector
from mysql.connector import Error


def _baglanti(host: str):
    return mysql.connector.connect(
        host=host,
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_ADI", "yemekhane"),
        user=os.getenv("DB_KULLANICI", "yemekhane_app"),
        password=os.getenv("DB_SIFRE", "Uyg_2026"),
        charset="utf8mb4",
        use_unicode=True,
        autocommit=True,
    )


def okuma_baglantisi():
    """Listeleme ve rapor sorguları replikaya gider."""
    return _baglanti(os.getenv("DB_OKUMA_HOST", "mysql-replica"))


def yazma_baglantisi():
    """Yazma işlemleri ve stored procedure çağrıları kaynağa gider."""
    return _baglanti(os.getenv("DB_YAZMA_HOST", "mysql-primary"))


def sorgu_getir(sql: str, params: tuple = (), kaynaktan: bool = False):
    conn = yazma_baglantisi() if kaynaktan else okuma_baglantisi()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def tek_satir_getir(sql: str, params: tuple = (), kaynaktan: bool = False):
    sonuc = sorgu_getir(sql, params, kaynaktan=kaynaktan)
    return sonuc[0] if sonuc else None


def yazma_calistir(sql: str, params: tuple = ()):
    conn = yazma_baglantisi()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()


def prosedür_cagir(ad: str, params: tuple = ()):
    """Stored procedure'leri autocommit açıkken kaynak sunucuda çalıştırır."""
    conn = yazma_baglantisi()
    try:
        cur = conn.cursor(dictionary=True)
        cur.callproc(ad, params)
        sonuc = None
        for res in cur.stored_results():
            satirlar = res.fetchall()
            if satirlar:
                sonuc = satirlar[0]
        return sonuc
    except Error as e:
        if e.errno == 1644 or e.sqlstate == "45000":
            raise ValueError(e.msg)
        raise RuntimeError("Veritabanı işlemi sırasında beklenmeyen bir hata oluştu.")
    finally:
        cur.close()
        conn.close()