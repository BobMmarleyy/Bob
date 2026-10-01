#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bob CloudStream — Günlük Kanal Kontrolü ve Otomatik Onarım
==========================================================
GitHub Actions tarafından her gün çalıştırılır (ayrıca elle tetiklenebilir).

1) Her eklentinin mainUrl adresini redirect takibiyle kontrol eder.
2) Domain değişmişse -> .kt dosyasını günceller, versiyonu artırır.
3) Site tamamen ölmüşse -> aynı markaya ait olası yeni domainleri dener;
   uygun bir sonuç bulursa onarır, bulamazsa DOWN olarak raporlar.
4) Değişiklik varsa main dalına commit + push yapar (Derleyici workflow'ü
   otomatik tetiklenir ve yeni eklenti dosyaları yayınlanır).
5) Raporu "📡 Günlük Durum Raporu" issue'una yazar.

Yerel test için:  python KONTROL.py --dry-run
"""

import base64
import datetime
import html as htmllib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    HAS_CRYPTO = True
except ImportError:          # pycryptodome yoksa RecTV/GolgeTV özel durumları atlanır
    HAS_CRYPTO = False

# ---------------------------------------------------------------- ayarlar --
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS    = {"gradle", "__Temel"}
TIMEOUT      = 20           # ana kontrol zaman aşımı (sn)
CAND_TIMEOUT = 12           # aday domain denemeleri için daha kısa
DRY_RUN      = "--dry-run" in sys.argv
TOKEN        = os.environ.get("GITHUB_TOKEN", "")
REPO         = f"{os.environ.get('REPO_OWNER', 'BobMmarleyy')}/{os.environ.get('REPO_NAME', 'Bob')}"
ISSUE_TITLE  = "📡 Günlük Durum Raporu"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

session = requests.Session()
session.headers.update({"User-Agent": UA})


def log(msg: str) -> None:
    print(f"[KONTROL] {msg}", flush=True)


# --------------------------------------------- özel durumlar (upstream'den) --
def rectv_api():
    """RecTV'nin güncel API adresini Firebase Remote Config'ten okur."""
    r = session.post(
        "https://firebaseremoteconfig.googleapis.com/v1/projects/791583031279/namespaces/firebase:fetch",
        headers={
            "X-Goog-Api-Key":    "AIzaSyBbhpzG8Ecohu9yArfCO5tF13BQLhjLahc",
            "X-Android-Package": "com.rectv.shot",
            "User-Agent":        "Dalvik/2.1.0 (Linux; U; Android 12)",
        },
        json={
            "appBuild":      "81",
            "appInstanceId": "evON8ZdeSr-0wUYxf0qs68",
            "appId":         "1:791583031279:android:1",
        },
        timeout=TIMEOUT,
    )
    return r.json().get("entries", {}).get("api_url", "").replace("/api/", "")


def golgetv_api():
    """GolgeTV'nin güncel API adresini şifreli anahtar dosyasından okur."""
    if not HAS_CRYPTO:
        return None
    raw = session.get(
        "https://raw.githubusercontent.com/sevdaliyim/sevdaliyim/main/ssl2.key",
        timeout=TIMEOUT,
    ).text
    cipher = AES.new(b"trskmrskslmzbzcnfstkcshpfstkcshp", AES.MODE_CBC, b"trskmrskslmzbzcn")
    data = unpad(cipher.decrypt(base64.b64decode(raw)), AES.block_size).decode("utf-8")
    return json.loads(data, strict=False)["apiUrl"]


# ------------------------------------------------------- eklenti keşfi --
def plugins():
    out = []
    for name in sorted(os.listdir(BASE_DIR)):
        d = os.path.join(BASE_DIR, name)
        if not os.path.isdir(d) or name.startswith(".") or name in SKIP_DIRS:
            continue
        if os.path.exists(os.path.join(d, "build.gradle.kts")):
            out.append(name)
    return out


def find_kt(plugin):
    target = f"{plugin}.kt"
    for root, _, files in os.walk(os.path.join(BASE_DIR, plugin)):
        if target in files:
            return os.path.join(root, target)
    return None


MAINURL_RES = (
    re.compile(r'(?:override\s+var|val|var)\s+mainUrl\s*=\s*"([^"]+)"'),
    # InatBox gibi mainUrl yerine contentUrl kullanan özel yapılar:
    re.compile(r'(?:private\s+)?(?:val|var)\s+contentUrl\s*=\s*"([^"]+)"'),
)


def mainurl_of(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()
    for rx in MAINURL_RES:
        if m := rx.search(content):
            return m.group(1)
    return None


# ---------------------------------------------------------- ağ kontrolü --
def norm(u):
    return u[:-1] if u and u.endswith("/") else u


def probe(url, timeout=TIMEOUT):
    """url'yi redirect takibiyle dener -> (ok, final_url, hata, gövde_örneği)"""
    for attempt in range(2):
        try:
            r = session.get(url, allow_redirects=True, timeout=timeout)
            if r.status_code >= 400:
                return False, None, f"HTTP {r.status_code}", ""
            body = (r.text or "")[:15000]
            return True, norm(r.url), None, body
        except Exception as e:
            err = type(e).__name__
            if attempt == 0 and ("Connect" in err or "Connection" in err):
                time.sleep(3)      # geçici kesinti olabilir -> bir kez daha dene
                continue
            return False, None, err, ""


PARKING_MARKERS = (
    "hugedomains", "sedoparking", "parkingcrew", "domainmarket", "godaddy/whois",
    "cgi-sys/suspendedpage", "plarclck", "click-v4", "atpanel", "bodis.com",
    "teaminternet", "above.com", "registrar", "dan.com", "40reg",
)


def is_parking(url, body):
    low = (url + " " + htmllib.unescape(body or "")).lower()
    return any(m in low for m in PARKING_MARKERS)


def plausible(url, body):
    """Yanıtın gerçekten bir izleme sitesi olup olmadığını kabaca doğrular."""
    if not body or len(body) < 400:
        return False
    if is_parking(url, body):
        return False
    low = htmllib.unescape(body).lower()
    if "<html" not in low and "<!doctype" not in low:
        return False
    # park subdomain deseni (ww16.turkanime.com vb.)
    first = urlparse(url).netloc.lower().split(".")[0]
    if re.fullmatch(r"ww\d+", first):
        return False
    return any(k in low for k in ("izle", "film", "dizi", "anime", "video", "player", "watch"))


def brand_match(old_host, new_host):
    """Yeni adresin aynı markaya ait olup olmadığını kontrol eder."""
    old_host = old_host.lower().removeprefix("www.")
    new_host = new_host.lower().removeprefix("www.")
    if old_host == new_host:
        return True

    def stem(h):
        lab = h.split(".")[0]
        s = re.sub(r"\d+$", "", lab)
        return s or lab

    so, sn = stem(old_host), stem(new_host)
    if len(so) >= 4 and (so in sn or sn in so):
        return True
    # alt domain durumu: ww16.turkanime.com -> 'turkanime' etiketi eski kökle eşleşir
    for lab in new_host.split("."):
        ls = re.sub(r"\d+$", "", lab) or lab
        if len(ls) >= 5 and (ls == so or so in ls):
            return True
    return False


def candidates(url):
    """Ölü domain için olası yeni adresler (aynı marka, farklı TLD/numara)."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    m = re.match(r"^(?P<base>[a-z][a-z0-9]*(?:-[a-z0-9]+)*?)(?P<num>\d*)\.(?P<tld>[a-z]{2,}(?:\.[a-z]{2})?)$", host)
    if not m:
        return []
    base, num, tld = m.group("base"), m.group("num"), m.group("tld")
    stem = re.sub(r"\d+$", "", base) or base

    out, seen = [], set()

    def add(u):
        u = norm(u)
        if u and u != norm(url) and u not in seen:
            seen.add(u)
            out.append(u)

    # 1) aynı marka + sıfır varyantları, önce mevcut TLD ile
    for b in (stem, f"{stem}{num}" if num else None):
        if b:
            add(f"https://{b}.{tld}")
    # 2) kök ad + yaygın TLD'ler
    common = ["com", "net", "tv", "co", "pw", "site", "online", "live",
              "vip", "club", "xyz", "me", "org", "io", "app", "cc"]
    for t in common:
        add(f"https://{stem}.{t}")
    return out[:14]


# ------------------------------------------------------------- onarım --
def set_mainurl(kt_path, old, new):
    with open(kt_path, encoding="utf-8") as f:
        content = f.read()
    if old not in content:
        return False
    with open(kt_path, "w", encoding="utf-8") as f:
        f.write(content.replace(old, new))
    return True


def bump_version(plugin):
    p = os.path.join(BASE_DIR, plugin, "build.gradle.kts")
    try:
        with open(p, encoding="utf-8") as f:
            content = f.read()
        m = re.search(r"version\s*=\s*(\d+)", content)
        if not m:
            return None
        new_v = int(m.group(1)) + 1
        with open(p, "w", encoding="utf-8") as f:
            f.write(content.replace(f"version = {m.group(1)}", f"version = {new_v}", 1))
        return new_v
    except Exception as e:
        log(f"{plugin}: versiyon artırma hatası -> {e}")
        return None


# -------------------------------------------------------- tek kanal --
def check_plugin(plugin):
    res = {"plugin": plugin}
    kt = find_kt(plugin)
    if not kt:
        res["status"] = "no-file"          # .kt dosyası yok (özel yapı), dokunma
        return res

    cur = mainurl_of(kt)
    if not cur:
        res["status"] = "no-mainurl"       # mainUrl bulunamadı, dokunma
        return res

    special = plugin in ("RecTV", "GolgeTV")
    _body = ""
    try:
        if plugin == "RecTV":
            final = rectv_api()
            ok, err = bool(final), None if final else "Firebase API yanıt vermedi"
        elif plugin == "GolgeTV":
            final = golgetv_api()
            ok, err = bool(final), None if final else "Anahtar dosyası okunamadı"
        else:
            ok, final, err, _body = probe(cur)
    except Exception as e:
        ok, final, err = False, None, str(e)[:120]

    # 1) site yaşıyor ve adres değişmiş -> doğrula, uygunsa düzelt
    if ok and norm(final) != norm(cur):
        good = special or (
            brand_match(urlparse(cur).netloc, urlparse(norm(final)).netloc)
            and plausible(norm(final), _body))
        if good:
            applied = (not DRY_RUN) and set_mainurl(kt, cur, norm(final))
            v = bump_version(plugin) if applied else None
            res.update(status="fixed", old=cur, new=norm(final), version=v)
        else:
            # adres başka bir siteye / parking sayfasına gidiyor -> şüpheli
            res.update(status="down", old=cur,
                       error=f"adres {norm(final)} adresine yönlendiriyor (marka uyuşmazlığı)")
        return res

    # 2) site yaşıyor ve adres aynı -> tamam
    if ok:
        res["status"] = "ok"
        return res

    # 3) HTTP hatası -> site ayakta ama hata veriyor (bot engeli / geçici olabilir)
    if err and err.startswith("HTTP"):
        res.update(status="warn", old=cur, error=err)
        return res

    # 4) bağlantı tamamen ölü -> olası yeni domainleri dene
    fixed_url = None
    if not special:
        for cand in candidates(cur):
            cok, cfinal, _, cbody = probe(cand, timeout=CAND_TIMEOUT)
            # aday hem izleme sitesi gibi görünmeli hem de aynı markaya ait olmalı
            if cok and plausible(cfinal, cbody) \
                    and brand_match(urlparse(cand).netloc, urlparse(norm(cfinal)).netloc):
                fixed_url = norm(cfinal)
                break

    if fixed_url:
        applied = (not DRY_RUN) and set_mainurl(kt, cur, fixed_url)
        v = bump_version(plugin) if applied else None
        res.update(status="recovered", old=cur, new=fixed_url, version=v)
    else:
        res.update(status="down", old=cur, error=err or "bilinmeyen hata")
    return res


# ------------------------------------------------------------- rapor --
def build_issue_body(report):
    ts = report["date"]
    lines = [
        f"_Son kontrol: **{ts} UTC_ · Toplam {report['total']} kanal",
        "",
        "| Durum | Sayı |",
        "|---|---|",
        f"| ✅ Çalışıyor | {report['ok']} |",
        f"| 🔧 Onarıldı | {report['fixed']} |",
        f"| ⚠️ Uyarı | {report.get('warn', 0)} |",
        f"| ❌ Ölü | {report['down']} |",
    ]

    fixed = [r for r in report["details"] if r["status"] in ("fixed", "recovered")]
    warn  = [r for r in report["details"] if r["status"] == "warn"]
    down  = [r for r in report["details"] if r["status"] == "down"]
    skip  = [r for r in report["details"] if r["status"] not in ("ok", "fixed", "recovered", "warn", "down")]

    if fixed:
        lines += ["", "## 🔧 Bugün Onarılanlar"]
        for r in fixed:
            icon = "🔁" if r["status"] == "fixed" else "♻️"
            vtxt = f" · v{r.get('version')}" if r.get("version") else ""
            lines.append(f"- {icon} **{r['plugin']}**: `{r['old']}` → `{r['new']}`{vtxt}")

    if warn:
        lines += ["", "## ⚠️ Uyarı (site ayakta ama hata veriyor, uygulama tarafında çalışıyor olabilir)"]
        for r in warn:
            lines.append(f"- {r['plugin']} — `{r.get('old', '?')}` → {r.get('error', '?')}")

    if down:
        lines += ["", "## ❌ Ölü Kanallar (manuel inceleme gerekli)"]
        for r in down:
            lines.append(f"- ☠️ **{r['plugin']}** — `{r.get('old', '?')}` → {r.get('error', '?')}")

    if skip:
        names = ", ".join(r["plugin"] for r in skip)
        lines += ["", f"<sub>⚪ Kontrol edilemedi (yapı farklı): {names}</sub>"]

    # geçmiş satırı (son 20 gün)
    old_hist = ""
    if TOKEN and not DRY_RUN:
        try:
            r = session.get(
                f"https://api.github.com/repos/{REPO}/issues",
                headers={"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github+json"},
                params={"state": "open", "per_page": 100}, timeout=TIMEOUT)
            for it in r.json():
                if it.get("title") == ISSUE_TITLE:
                    m = re.search(r"<!-- TARIH_BASLA -->(.*?)<!-- TARIH_BIT -->", it["body"] or "", re.S)
                    old_hist = m.group(1).strip() if m else ""
                    break
        except Exception as e:
            log(f"issue okuma hatası: {e}")

    today = (f"- {ts[:10]} — ✅{report['ok']} · 🔧{report['fixed']} "
             f"· ⚠️{report.get('warn', 0)} · ❌{report['down']}")
    hist_lines = [today] + old_hist.splitlines()[:19]
    lines += ["", "---", "<details><summary>📅 Son 20 gün</summary>", "",
              "<!-- TARIH_BASLA -->"] + hist_lines + ["<!-- TARIH_BIT -->", "</details>", ""]

    return "\n".join(lines)


def publish(report):
    """Git commit/push + issue raporu. Yalnızca CI ortamında çalışır."""
    if not TOKEN or DRY_RUN:
        return

    # --- git ---
    def git(*args):
        return subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True)

    st = git("status", "--porcelain")
    if st.stdout.strip():
        git("config", "user.name", "github-actions[bot]")
        git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        n = report["fixed"]
        msg = f"🔧 Günlük kontrol: {n} kanal onarıldı, {report['down']} ölü" if n \
              else "🩺 Günlük kontrol (kod değişikliği yok)"
        git("add", "-A")
        git("commit", "-m", msg)
        p = git("push", "origin", "HEAD:main")
        log(f"git push -> {p.returncode} {p.stderr.strip()[:200]}")

    # --- issue ---
    body = build_issue_body(report)
    headers = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{REPO}"
    try:
        r = session.get(f"{api}/issues", headers=headers, params={"state": "open", "per_page": 100}, timeout=TIMEOUT)
        issue_id = next((it["number"] for it in r.json() if it.get("title") == ISSUE_TITLE), None)
        if issue_id:
            session.patch(f"{api}/issues/{issue_id}", headers=headers, json={"body": body}, timeout=TIMEOUT)
        else:
            session.post(f"{api}/issues", headers=headers,
                         json={"title": ISSUE_TITLE, "body": body}, timeout=TIMEOUT)
        log("issue raporu güncellendi")
    except Exception as e:
        log(f"issue raporu hatası: {e}")


# ---------------------------------------------------------------- main --
def main():
    t0 = datetime.datetime.now(datetime.timezone.utc)
    plist = plugins()
    order = {p: i for i, p in enumerate(plist)}

    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(check_plugin, p) for p in plist]
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            extra = ""
            if r["status"] == "fixed":       extra = f"  {r['old']} -> {r['new']}"
            elif r["status"] == "recovered": extra = f"  {r['old']} -> {r['new']}"
            elif r["status"] in ("down", "warn"): extra = f"  ({r.get('error')})"
            log(f"{r['plugin']}: {r['status'].upper()}{extra}")

    results.sort(key=lambda r: order.get(r["plugin"], 99))
    report = {
        "date":   t0.strftime("%Y-%m-%d %H:%M"),
        "total":  len(results),
        "ok":     sum(1 for r in results if r["status"] == "ok"),
        "fixed":  sum(1 for r in results if r["status"] in ("fixed", "recovered")),
        "warn":   sum(1 for r in results if r["status"] == "warn"),
        "down":   sum(1 for r in results if r["status"] == "down"),
        "details": results,
    }

    with open(os.path.join(BASE_DIR, "kontrol_rapor.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\nREPORT_JSON:" + json.dumps(
        {k: report[k] for k in ("date", "total", "ok", "fixed", "warn", "down")},
        ensure_ascii=False))
    publish(report)


if __name__ == "__main__":
    main()
