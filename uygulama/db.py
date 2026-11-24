import os
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