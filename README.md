# Net Talep Farkı Simülatörü — Aşama 1

## GitHub'a yükleme
Bu 3 dosyayı (`app.py`, `requirements.txt`, `README.md`) daha önce oluşturduğun
GitHub reposuna, repo sayfasındaki **Add file > Upload files** ile sürükle-bırak
şeklinde yükle. Zaten var olan `README.md`'nin üzerine yazmasına izin ver.

## Streamlit Cloud'da deploy
1. share.streamlit.io üzerinden **New app**
2. Reponu seç
3. Main file path: `app.py`
4. **Deploy**

## Kullanım
1. Tuvale fare ile bir çizgi çiz (yukarı = pozitif net talep, aşağı = negatif)
2. **Çizgiyi Ekle (ADD)** ile listeye ekle
3. İstediğin kadar çizgi ekle, listeden istediğini **Kaldır** ile sil
4. **GRAPH** ile sonucu gör

## Bilinen sınırlamalar (v1)
- Bir çizgi sadece o an görünen pencere aralığını kaplar; pencereyi kaydırıp
  aynı çizgiyi genişletmek şu an desteklenmiyor (her pencere için ayrı çizgi ekle).
- Fitiller tamamen kozmetiktir, gerçek bir intrabar simülasyonu değildir.
- Ortalama hacim, tüm serinin ortalamasıdır (hareketli ortalama değil).
