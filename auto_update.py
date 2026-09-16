# ! Bu araç @onursiyhan tarafından otomatikleştirilmiştir.

import os
import re
import base64
import json
import logging
import subprocess
from cloudscraper import CloudScraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# Log ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MainUrlUpdater:
    def __init__(self, base_dir="."):
        self.base_dir = base_dir
        self.oturum = CloudScraper()

    @property
    def eklentiler(self):
        return sorted([
            dosya for dosya in os.listdir(self.base_dir)
                if os.path.isdir(os.path.join(self.base_dir, dosya))
                    and not dosya.startswith(".")
                        and dosya not in {"gradle", "CanliTV", "OxAx", "__Temel", "SineWix", "YouTube", "NetflixMirror", "HQPorner", "WebteIzle", "TrDiziIzle"}
        ])

    def _kt_dosyasini_bul(self, dizin, dosya_adi):
        for kok, alt_dizinler, dosyalar in os.walk(dizin):
            if dosya_adi in dosyalar:
                return os.path.join(kok, dosya_adi)
        return None

    @property
    def kt_dosyalari(self):
        kt_list = []
        for eklenti in self.eklentiler:
            yol = self._kt_dosyasini_bul(eklenti, f"{eklenti}.kt")
            if yol:
                kt_list.append(yol)
        return kt_list

    def _mainurl_bul(self, kt_dosya_yolu):
        try:
            with open(kt_dosya_yolu, "r", encoding="utf-8") as file:
                icerik = file.read()
                if mainurl := re.search(r'override\s+var\s+mainUrl\s*=\s*"([^"]+)"', icerik):
                    return mainurl[1]
        except Exception as e:
            logger.error(f"Dosya okuma hatası ({kt_dosya_yolu}): {e}")
        return None

    def _mainurl_guncelle(self, kt_dosya_yolu, eski_url, yeni_url):
        try:
            with open(kt_dosya_yolu, "r+", encoding="utf-8") as file:
                icerik = file.read()
                yeni_icerik = icerik.replace(eski_url, yeni_url)
                file.seek(0)
                file.write(yeni_icerik)
                file.truncate()
            return True
        except Exception as e:
            logger.error(f"Güncelleme hatası ({kt_dosya_yolu}): {e}")
            return False

    def _versiyonu_artir(self, build_gradle_yolu):
        try:
            with open(build_gradle_yolu, "r+", encoding="utf-8") as file:
                icerik = file.read()
                if version_match := re.search(r'version\s*=\s*(\d+)', icerik):
                    eski_versiyon = int(version_match[1])
                    yeni_versiyon = eski_versiyon + 1
                    yeni_icerik = icerik.replace(f"version = {eski_versiyon}", f"version = {yeni_versiyon}")
                    file.seek(0)
                    file.write(yeni_icerik)
                    file.truncate()
                    return yeni_versiyon
        except Exception as e:
            logger.error(f"Versiyon artırma hatası ({build_gradle_yolu}): {e}")
        return None

    def _rectv_ver(self):
        istek = self.oturum.post(
            url     = "https://firebaseremoteconfig.googleapis.com/v1/projects/791583031279/namespaces/firebase:fetch",
            headers = {
                "X-Goog-Api-Key"    : "AIzaSyBbhpzG8Ecohu9yArfCO5tF13BQLhjLahc",
                "X-Android-Package" : "com.rectv.shot",
                "User-Agent"        : "Dalvik/2.1.0 (Linux; U; Android 12)",
            },
            json    = {
                "appBuild"      : "81",
                "appInstanceId" : "evON8ZdeSr-0wUYxf0qs68",
                "appId"         : "1:791583031279:android:1",
            }
        )
        return istek.json().get("entries", {}).get("api_url", "").replace("/api/", "")

    def _golgetv_ver(self):
        istek = self.oturum.get("https://raw.githubusercontent.com/sevdaliyim/sevdaliyim/main/ssl2.key").text
        cipher = AES.new(b"trskmrskslmzbzcnfstkcshpfstkcshp", AES.MODE_CBC, b"trskmrskslmzbzcn")
        encrypted_data = base64.b64decode(istek)
        decrypted_data = unpad(cipher.decrypt(encrypted_data), AES.block_size).decode("utf-8")
        return json.loads(decrypted_data, strict=False)["apiUrl"]

    @property
    def mainurl_listesi(self):
        return {
            dosya: self._mainurl_bul(dosya) for dosya in self.kt_dosyalari
        }

    def guncelle(self):
        değişiklik_var = False
        for dosya, mainurl in self.mainurl_listesi.items():
            if not mainurl:
                continue
            
            eklenti_adi = dosya.split("/")[0]
            logger.info(f"Kontrol Ediliyor: {eklenti_adi}")

            try:
                if eklenti_adi == "RecTV":
                    final_url = self._rectv_ver()
                elif eklenti_adi == "GolgeTV":
                    final_url = self._golgetv_ver()
                else:
                    istek = self.oturum.get(mainurl, allow_redirects=True)
                    final_url = istek.url[:-1] if istek.url.endswith("/") else istek.url

                logger.info(f"Kontrol Edildi: {mainurl}")

                if mainurl == final_url:
                    continue

                if self._mainurl_guncelle(dosya, mainurl, final_url):
                    logger.info(f"GÜNCELLENDİ: {mainurl} -> {final_url}")
                    self._versiyonu_artir(f"{eklenti_adi}/build.gradle.kts")
                    değişiklik_var = True

            except Exception as e:
                logger.error(f"Hata oluştu ({eklenti_adi}): {e}")

        return değişiklik_var

    def git_push(self):
        try:
            logger.info("Değişiklikler Git'e gönderiliyor...")
            subprocess.run(["git", "add", "."], check=True)
            subprocess.run(["git", "commit", "-m", "Auto-update: Linkler ve versiyonlar güncellendi"], check=True)
            subprocess.run(["git", "push"], check=True)
            logger.info("Başarıyla gönderildi.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git push hatası: {e}")

if __name__ == "__main__":
    updater = MainUrlUpdater()
    changed = updater.guncelle()
    if changed:
        updater.git_push()
    else:
        logger.info("Her şey güncel, işlem tamamlandı.")
