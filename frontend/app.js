const durum = {
  token: localStorage.getItem("yemekhane_token") || null,
  kullanici: null,
  referanslar: null,
  porsiyonSecimleri: {},
};

function bugunStr(ekGun = 0) {
  const d = new Date();
  d.setDate(d.getDate() + ekGun);
  return d.toISOString().slice(0, 10);
}

function bildirimGoster(mesaj, tip = "basari") {
  const kutu = document.getElementById("bildirim-kutusu");
  kutu.textContent = mesaj;
  kutu.className = `bildirim ${tip}`;
  setTimeout(() => {
    kutu.className = "bildirim gizli";
  }, 4500);
}

async function apiIstek(yol, secenekler = {}) {
  const headers = { "Content-Type": "application/json", ...(secenekler.headers || {}) };
  if (durum.token) {
    headers["Authorization"] = `Bearer ${durum.token}`;
  }
  const yanit = await fetch(`/api${yol}`, { ...secenekler, headers });
  const veri = await yanit.json().catch(() => ({}));
  if (!yanit.ok) {
    throw new Error(veri.detail || "İşlem sırasında bir hata oluştu.");
  }
  return veri;
}

function ekranAc(ekranId) {
  document.querySelectorAll(".ekran").forEach((el) => el.classList.remove("aktif"));
  document.getElementById(ekranId).classList.add("aktif");

  document.querySelectorAll(".sekme-btn").forEach((btn) => {
    btn.classList.toggle("aktif", btn.dataset.ekran === ekranId);
  });

  if (ekranId === "ekran-gunluk") gunlukMenuleriYukle();
  if (ekranId === "ekran-haftalik") haftalikMenuleriYukle();
  if (ekranId === "ekran-rezervasyonlar") rezervasyonlariYukle();
  if (ekranId === "ekran-bakiye") bakiyeYukle();
  if (ekranId === "ekran-alerjenler") alerjenFormunuDoldur();
  if (ekranId === "ekran-yonetim-rapor") yonetimRaporuYukle();
}

function ustBilgiGuncelle() {
  const ozet = document.getElementById("kullanici-ozet");
  const menu = document.getElementById("ana-menu");
  if (!durum.kullanici) {
    ozet.classList.add("gizli");
    menu.classList.add("gizli");
    document.getElementById("ust-rol-bilgi").textContent = "Otomasyon Sistemi";
    return;
  }

  ozet.classList.remove("gizli");
  menu.classList.remove("gizli");
  const rolEtiket = { ogrenci: "Öğrenci", personel: "Personel", yonetici: "Yönetici" }[durum.kullanici.rol] || durum.kullanici.rol;
  document.getElementById("ust-rol-bilgi").textContent = `${rolEtiket} Paneli`;
  document.getElementById("ust-ad").textContent = `${durum.kullanici.ad} ${durum.kullanici.soyad}`;
  document.getElementById("ust-bakiye").textContent = `${Number(durum.kullanici.bakiye).toFixed(2)} ₺`;

  const yoneticiMi = durum.kullanici.rol === "yonetici";
  document.querySelectorAll(".yonetici-ozel").forEach((el) => {
    el.classList.toggle("gizli", !yoneticiMi);
  });
}

async function oturumBaslat() {
  try {
    durum.kullanici = await apiIstek("/ben");
    durum.referanslar = await apiIstek("/referanslar");
    referanslariDoldur();
    ustBilgiGuncelle();
    ekranAc("ekran-gunluk");
  } catch {
    localStorage.removeItem("yemekhane_token");
    durum.token = null;
    durum.kullanici = null;
    ustBilgiGuncelle();
    ekranAc("ekran-giris");
  }
}

function referanslariDoldur() {
  const { yemekhaneler, ogunler, yemekler } = durum.referanslar;
  ["gunluk-yemekhane", "haftalik-yemekhane", "ym-yemekhane"].forEach((id) => {
    const sel = document.getElementById(id);
    sel.innerHTML = yemekhaneler.map((y) => `<option value="${y.id}">${y.ad}</option>`).join("");
  });

  document.getElementById("gunluk-tarih").value = bugunStr(0);
  document.getElementById("haftalik-baslangic").value = bugunStr(0);
  document.getElementById("ym-tarih").value = bugunStr(1);

  document.getElementById("ym-ogun").innerHTML = ogunler
    .map((o) => `<option value="${o.id}">${o.ad} (Son: ${o.son_saat})</option>`)
    .join("");

  document.getElementById("ym-yemek-secim").innerHTML = yemekler
    .map(
      (y) => `
      <label class="yemek-secim-item">
        <input type="checkbox" name="ym_yemek" value="${y.id}" />
        <span><strong>${y.ad}</strong> <small>(${y.kategori}, ${y.kalori} kcal)</small></span>
      </label>`
    )
    .join("");

  alerjenFormunuDoldur();
}

function alerjenFormunuDoldur() {
  const kapsayici = document.getElementById("alerjen-secim-listesi");
  if (!kapsayici || !durum.referanslar) return;
  const seciliSet = new Set(durum.kullanici?.alerjen_idler || []);
  const alerjenler = durum.referanslar.alerjenler || [];

  kapsayici.innerHTML = alerjenler
    .map(
      (a) => `
      <label class="yemek-secim-item ${seciliSet.has(a.id) ? "alerjen-secili" : ""}">
        <input type="checkbox" name="kullanici_alerjen" value="${a.id}" ${seciliSet.has(a.id) ? "checked" : ""} />
        <span><strong>${a.ad}</strong></span>
      </label>`
    )
    .join("");
}

function porsiyonDegistir(menuId, porsiyon, tamFiyat, yarimFiyat) {
  durum.porsiyonSecimleri[menuId] = porsiyon;
  const gosterilenFiyat = porsiyon === "yarim" ? yarimFiyat : tamFiyat;
  const ucretEl = document.getElementById(`menu-ucret-${menuId}`);
  const btnEl = document.getElementById(`rez-btn-${menuId}`);
  if (ucretEl) ucretEl.textContent = `Ücret: ${Number(gosterilenFiyat).toFixed(2)} ₺`;
  if (btnEl) {
    const etiket = porsiyon === "yarim" ? "Yarım Porsiyon" : "Tam Porsiyon";
    btnEl.textContent = `Rezervasyon Yap (${etiket} • ${Number(gosterilenFiyat).toFixed(2)} ₺)`;
  }
}

function menuKartiHtml(m) {
  const alerjenliMenuMu = m.yemekler.some((y) => y.alerjen_uyarisi);
  const seciliPorsiyon = m.yarim_porsiyon_uygun ? (durum.porsiyonSecimleri[m.menu_id] || "tam") : "tam";
  const aktifFiyat = seciliPorsiyon === "yarim" && m.yarim_fiyat !== null ? m.yarim_fiyat : m.kullanici_fiyati;

  const yemeklerHtml = m.yemekler
    .map(
      (y) => `
      <li class="${y.alerjen_uyarisi ? "yemek-alerjen-uyari" : ""}">
        <div>
          <strong>${y.yemek}</strong> ${y.vejetaryen ? "🌱" : ""}
          ${y.alerjen_uyarisi ? `<span class="alerjen-uyari-rozet">⚠️ Alerjen Uyarısı!</span>` : ""}
          ${y.alerjenler ? `<span class="alerjen-metin">Alerjen: ${y.alerjenler}</span>` : ""}
        </div>
        <span>${y.kalori} kcal</span>
      </li>`
    )
    .join("");

  let porsiyonSeciciHtml = "";
  if (!m.mevcut_rezervasyon && !m.sure_doldu && m.bos_yer > 0) {
    if (m.yarim_porsiyon_uygun && m.yarim_fiyat !== null) {
      porsiyonSeciciHtml = `
        <div class="porsiyon-secici">
          <label>
            <input type="radio" name="porsiyon-${m.menu_id}" value="tam" ${seciliPorsiyon === "tam" ? "checked" : ""}
              onchange="porsiyonDegistir(${m.menu_id}, 'tam', ${m.kullanici_fiyati}, ${m.yarim_fiyat})" />
            <span>Tam (${m.kullanici_fiyati.toFixed(2)} ₺)</span>
          </label>
          <label>
            <input type="radio" name="porsiyon-${m.menu_id}" value="yarim" ${seciliPorsiyon === "yarim" ? "checked" : ""}
              onchange="porsiyonDegistir(${m.menu_id}, 'yarim', ${m.kullanici_fiyati}, ${m.yarim_fiyat})" />
            <span>Yarım %60 (${m.yarim_fiyat.toFixed(2)} ₺)</span>
          </label>
        </div>`;
    } else {
      porsiyonSeciciHtml = `<small style="color:#6b7280;display:block;margin-top:4px;">Kahvaltı öğününde yalnızca tam porsiyon sunulur.</small>`;
    }
  }

  let aksiyonHtml = "";
  if (m.mevcut_rezervasyon) {
    const porsiyonBilgi = m.mevcut_rezervasyon.porsiyon === "yarim" ? "Yarım" : "Tam";
    aksiyonHtml = `<span class="etiket">Rezerve Edildi (${m.mevcut_rezervasyon.durum} • ${porsiyonBilgi})</span>`;
  } else if (m.sure_doldu) {
    aksiyonHtml = `<button class="btn btn-ikincil" disabled>Süre Doldu</button>`;
  } else if (m.bos_yer <= 0) {
    aksiyonHtml = `<button class="btn btn-ikincil" disabled>Kapasite Dolu</button>`;
  } else {
    const etiket = seciliPorsiyon === "yarim" ? "Yarım Porsiyon" : "Tam Porsiyon";
    aksiyonHtml = `<button id="rez-btn-${m.menu_id}" class="btn btn-birincil" onclick="rezervasyonYap(${m.menu_id})">Rezervasyon Yap (${etiket} • ${aktifFiyat.toFixed(2)} ₺)</button>`;
  }

  const puanMetni = m.oy_sayisi > 0 ? `⭐ ${Number(m.ortalama_puan).toFixed(1)} (${m.oy_sayisi} oy)` : "Henüz puanlanmadı";

  return `
    <article class="kart ${alerjenliMenuMu ? "kart-alerjen-riskli" : ""}">
      <div class="menu-baslik">
        <div>
          <h3>${m.ogun} ${alerjenliMenuMu ? "⚠️" : ""}</h3>
          <small>${m.tarih} • Son Rezervasyon: ${m.rezervasyon_son.slice(11)}</small>
        </div>
        <span class="etiket">${m.toplam_kalori} kcal</span>
      </div>
      ${alerjenliMenuMu ? `<div class="alerjen-banner">⚠️ Bu menüde seçtiğiniz alerjenleri içeren yemek bulunuyor!</div>` : ""}
      <ul class="yemek-listesi">${yemeklerHtml}</ul>
      <div>
        <small>Doluluk: <strong>${m.rezerve} / ${m.kapasite}</strong> (%${m.doluluk_yuzde}) • ${puanMetni}</small>
        <div class="doluluk-cubuk"><div class="doluluk-dolu" style="width:${Math.min(100, m.doluluk_yuzde)}%"></div></div>
      </div>
      ${porsiyonSeciciHtml}
      <div class="menu-alt">
        <strong id="menu-ucret-${m.menu_id}">Ücret: ${aktifFiyat.toFixed(2)} ₺</strong>
        ${aksiyonHtml}
      </div>
    </article>`;
}

async function gunlukMenuleriYukle() {
  const yemekhaneId = document.getElementById("gunluk-yemekhane").value;
  const tarih = document.getElementById("gunluk-tarih").value;
  const liste = document.getElementById("gunluk-liste");
  liste.innerHTML = "<p>Yükleniyor...</p>";
  try {
    const menuler = await apiIstek(`/menuler?yemekhane_id=${yemekhaneId}&tarih=${tarih}`);
    liste.innerHTML = menuler.length
      ? menuler.map(menuKartiHtml).join("")
      : `<div class="kart"><p>Seçilen tarih ve yemekhane için tanımlı menü bulunamadı.</p></div>`;
  } catch (e) {
    liste.innerHTML = `<div class="kart"><p>${e.message}</p></div>`;
  }
}

async function haftalikMenuleriYukle() {
  const yemekhaneId = document.getElementById("haftalik-yemekhane").value;
  const baslangic = document.getElementById("haftalik-baslangic").value;
  const bitisTarih = new Date(baslangic);
  bitisTarih.setDate(bitisTarih.getDate() + 6);
  const bitis = bitisTarih.toISOString().slice(0, 10);

  const liste = document.getElementById("haftalik-liste");
  liste.innerHTML = "<p>Yükleniyor...</p>";
  try {
    const menuler = await apiIstek(`/menuler?yemekhane_id=${yemekhaneId}&baslangic=${baslangic}&bitis=${bitis}`);
    if (!menuler.length) {
      liste.innerHTML = `<div class="kart"><p>Bu 7 günlük aralıkta tanımlı menü bulunmuyor.</p></div>`;
      return;
    }
    liste.innerHTML = menuler
      .map((m) => {
        const alerjenVar = m.yemekler.some((y) => y.alerjen_uyarisi);
        return `
        <div class="kart ${alerjenVar ? "kart-alerjen-riskli" : ""}">
          <div class="menu-baslik">
            <strong>${m.tarih} — ${m.ogun} ${alerjenVar ? "⚠️ Alerjen İçerir" : ""}</strong>
            <span>${m.kullanici_fiyati.toFixed(2)} ₺ • ${m.toplam_kalori} kcal</span>
          </div>
          <p>${m.yemekler.map((y) => (y.alerjen_uyarisi ? `⚠️ ${y.yemek}` : y.yemek)).join(", ")}</p>
        </div>`;
      })
      .join("");
  } catch (e) {
    liste.innerHTML = `<div class="kart"><p>${e.message}</p></div>`;
  }
}

async function rezervasyonYap(menuId) {
  const porsiyon = durum.porsiyonSecimleri[menuId] || "tam";
  try {
    const yanit = await apiIstek("/rezervasyonlar", {
      method: "POST",
      body: JSON.stringify({ menu_id: menuId, porsiyon }),
    });
    bildirimGoster(yanit.mesaj, "basari");
    durum.kullanici = await apiIstek("/ben");
    ustBilgiGuncelle();
    gunlukMenuleriYukle();
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
}

async function rezervasyonlariYukle() {
  const liste = document.getElementById("rezervasyon-liste");
  liste.innerHTML = "<p>Yükleniyor...</p>";
  try {
    const kayitlar = await apiIstek("/rezervasyonlarim");
    if (!kayitlar.length) {
      liste.innerHTML = `<div class="kart"><p>Henüz rezervasyonunuz bulunmuyor.</p></div>`;
      return;
    }
    liste.innerHTML = kayitlar
      .map((r) => {
        const porsiyonEtiketi = r.porsiyon === "yarim" ? "YARIM PORSİYON" : "TAM PORSİYON";
        let ekstraHtml = "";
        if (r.durum === "aktif" && r.iptal_edilebilir) {
          ekstraHtml = `<button class="btn btn-tehlike btn-kucuk" onclick="rezervasyonIptal(${r.id})">İptal Et (Ücret İadesi)</button>`;
        } else if (r.durum === "kullanildi" && !r.verilen_puan) {
          ekstraHtml = `
            <div class="degerlendirme-form">
              <select id="puan-${r.menu_id}">
                <option value="5">5 Yıldız</option>
                <option value="4">4 Yıldız</option>
                <option value="3">3 Yıldız</option>
                <option value="2">2 Yıldız</option>
                <option value="1">1 Yıldız</option>
              </select>
              <input type="text" id="yorum-${r.menu_id}" placeholder="Yorumunuz (isteğe bağlı)" />
              <button class="btn btn-birincil btn-kucuk" onclick="degerlendirmeGonder(${r.menu_id})">Puanla</button>
            </div>`;
        } else if (r.verilen_puan) {
          ekstraHtml = `<small>Verdiğiniz Puan: ⭐ ${r.verilen_puan}/5 ${r.verilen_yorum ? `— "${r.verilen_yorum}"` : ""}</small>`;
        }

        return `
          <div class="kart">
            <div class="menu-baslik">
              <div>
                <strong>${r.tarih} • ${r.ogun}</strong>
                <small style="display:block">${r.yemekhane} • ${porsiyonEtiketi}</small>
              </div>
              <span class="etiket">${r.durum.toUpperCase()} • ${Number(r.ucret).toFixed(2)} ₺</span>
            </div>
            ${ekstraHtml}
          </div>`;
      })
      .join("");
  } catch (e) {
    liste.innerHTML = `<div class="kart"><p>${e.message}</p></div>`;
  }
}

async function rezervasyonIptal(rezId) {
  try {
    const yanit = await apiIstek(`/rezervasyonlar/${rezId}/iptal`, { method: "POST" });
    bildirimGoster(yanit.mesaj, "basari");
    durum.kullanici = await apiIstek("/ben");
    ustBilgiGuncelle();
    rezervasyonlariYukle();
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
}

async function degerlendirmeGonder(menuId) {
  const puan = Number(document.getElementById(`puan-${menuId}`).value);
  const yorum = document.getElementById(`yorum-${menuId}`).value;
  try {
    const yanit = await apiIstek("/degerlendirmeler", {
      method: "POST",
      body: JSON.stringify({ menu_id: menuId, puan, yorum }),
    });
    bildirimGoster(yanit.mesaj, "basari");
    rezervasyonlariYukle();
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
}

async function bakiyeYukle() {
  try {
    const veri = await apiIstek("/bakiye");
    document.getElementById("bakiye-buyuk").textContent = `${veri.bakiye.toFixed(2)} ₺`;
    document.getElementById("ust-bakiye").textContent = `${veri.bakiye.toFixed(2)} ₺`;
    const liste = document.getElementById("hareket-liste");
    liste.innerHTML = veri.hareketler.length
      ? veri.hareketler
          .map(
            (h) => `
            <div class="hareket-satir">
              <div>
                <strong>${h.aciklama || h.tur}</strong>
                <small style="display:block;color:#6b7280">${h.olusturma}</small>
              </div>
              <span class="${Number(h.tutar) >= 0 ? "tutar-arti" : "tutar-eksi"}">
                ${Number(h.tutar) >= 0 ? "+" : ""}${Number(h.tutar).toFixed(2)} ₺
              </span>
            </div>`
          )
          .join("")
      : "<p>Henüz bakiye hareketi yok.</p>";
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
}

async function yonetimRaporuYukle() {
  const liste = document.getElementById("rapor-liste");
  liste.innerHTML = "<p>Yükleniyor...</p>";
  try {
    const satirlar = await apiIstek("/yonetim/rapor");
    liste.innerHTML = satirlar
      .map(
        (s) => `
        <div class="kart">
          <div class="menu-baslik">
            <strong>${s.tarih} • ${s.yemekhane} — ${s.ogun}</strong>
            <span class="etiket">Doluluk: ${s.rezerve}/${s.kapasite} (%${s.doluluk_yuzde})</span>
          </div>
          <p><small>Yemekler: ${s.yemekler.map((y) => y.yemek).join(", ")}</small></p>
          <p><small>Ortalama Puan: ${s.oy_sayisi > 0 ? `⭐ ${Number(s.ortalama_puan).toFixed(1)} (${s.oy_sayisi} oy)` : "Oy yok"}</small></p>
        </div>`
      )
      .join("");
  } catch (e) {
    liste.innerHTML = `<div class="kart"><p>${e.message}</p></div>`;
  }
}

document.getElementById("giris-formu").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const eposta = document.getElementById("giris-eposta").value;
    const sifre = document.getElementById("giris-sifre").value;
    const yanit = await apiIstek("/giris", {
      method: "POST",
      body: JSON.stringify({ eposta, sifre }),
    });
    durum.token = yanit.token;
    localStorage.setItem("yemekhane_token", yanit.token);
    await oturumBaslat();
  } catch (err) {
    bildirimGoster(err.message, "hata");
  }
});

document.querySelectorAll(".hizli-butonlar button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById("giris-eposta").value = btn.dataset.eposta;
    document.getElementById("giris-sifre").value = btn.dataset.sifre;
  });
});

document.getElementById("cikis-btn").addEventListener("click", async () => {
  await apiIstek("/cikis", { method: "POST" }).catch(() => {});
  localStorage.removeItem("yemekhane_token");
  durum.token = null;
  durum.kullanici = null;
  ustBilgiGuncelle();
  ekranAc("ekran-giris");
});

document.querySelectorAll(".sekme-btn").forEach((btn) => {
  btn.addEventListener("click", () => ekranAc(btn.dataset.ekran));
});

document.getElementById("gunluk-yenile").addEventListener("click", gunlukMenuleriYukle);
document.getElementById("haftalik-yenile").addEventListener("click", haftalikMenuleriYukle);
document.getElementById("rez-yenile").addEventListener("click", rezervasyonlariYukle);
document.getElementById("rapor-yenile").addEventListener("click", yonetimRaporuYukle);

document.querySelectorAll(".tutar-sec").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById("bakiye-tutar").value = btn.dataset.tutar;
  });
});

document.getElementById("bakiye-formu").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const tutar = Number(document.getElementById("bakiye-tutar").value);
    const yanit = await apiIstek("/bakiye/yukle", {
      method: "POST",
      body: JSON.stringify({ tutar }),
    });
    bildirimGoster(yanit.mesaj, "basari");
    await bakiyeYukle();
  } catch (err) {
    bildirimGoster(err.message, "hata");
  }
});

document.getElementById("alerjen-formu").addEventListener("submit", async (e) => {
  e.preventDefault();
  const seciliAlerjenler = Array.from(document.querySelectorAll('input[name="kullanici_alerjen"]:checked')).map((el) => Number(el.value));
  try {
    const yanit = await apiIstek("/profil/alerjenler", {
      method: "PUT",
      body: JSON.stringify({ alerjen_idler: seciliAlerjenler }),
    });
    durum.kullanici.alerjen_idler = yanit.alerjen_idler;
    bildirimGoster(yanit.mesaj, "basari");
    alerjenFormunuDoldur();
  } catch (err) {
    bildirimGoster(err.message, "hata");
  }
});

document.getElementById("yonetim-menu-formu").addEventListener("submit", async (e) => {
  e.preventDefault();
  const seciliYemekler = Array.from(document.querySelectorAll('input[name="ym_yemek"]:checked')).map((el) => Number(el.value));
  try {
    const yanit = await apiIstek("/yonetim/menuler", {
      method: "POST",
      body: JSON.stringify({
        yemekhane_id: Number(document.getElementById("ym-yemekhane").value),
        tarih: document.getElementById("ym-tarih").value,
        ogun_id: Number(document.getElementById("ym-ogun").value),
        ogrenci_fiyati: Number(document.getElementById("ym-ogrenci-fiyat").value),
        personel_fiyati: Number(document.getElementById("ym-personel-fiyat").value),
        yemek_idler: seciliYemekler,
      }),
    });
    bildirimGoster(yanit.mesaj, "basari");
  } catch (err) {
    bildirimGoster(err.message, "hata");
  }
});

if (durum.token) {
  oturumBaslat();
}