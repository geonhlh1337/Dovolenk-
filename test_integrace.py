"""
Integrační test: pustí CELÝ main() třikrát po sobě proti falešnému
prohlížeči, který vrací realisticky vypadající HTML. Ověřuje, že
1. běh nic neposílá, 2. běh hlásí zlevnění a 3. běh hlásí zmizení -
a že se seen.json/stats.json korektně ukládají.
"""
import os, sys, json, importlib, tempfile, shutil

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "y")
MODUL = os.environ.get("BOT_MODUL", "lastminute_bot")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
B = importlib.import_module(MODUL)

OK = FAIL = 0
FAILS = []


def check(name, got, want):
    global OK, FAIL
    if got == want:
        OK += 1
    else:
        FAIL += 1
        FAILS.append(f"{name}: dostal {got!r}, čekal {want!r}")


INVIA_TOKEN = ("eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0b3VyT3BlcmF0b3JJZCI6MTAy"
               "NTI1LCJ0b3VyT3BlcmF0b3JDb2RlIjoiVlRPSSIsImNoZWNrSW5EYXRlIjoiMjAy"
               "NjA5MjEiLCJjaGVja091dERhdGUiOiIyMDI2MDkyOCIsImRheXNDb3VudCI6OCwi"
               "aG90ZWxJZCI6MjI3MjYsIm1lYWxJZCI6NSwiYWlycG9ydElkIjoxfQ.sig")


def stranka(nabidky):
    """
    Karta s 'tlacitko': True má název hotelu jen v textu karty a odkaz
    je popisek tlačítka - přesně situace, kvůli které do zpráv chodilo
    "🏨 Zobrazit nabídku" místo jména hotelu.
    """
    kusy = []
    for i, n in enumerate(nabidky):
        if n.get("invia"):
            # realny format odkazu Invie: v tokenu je cestovka, termin i strava,
            # v query cena za osobu
            odkaz = (f"/zajezd/?s_offer_id={INVIA_TOKEN}"
                     f"&c_price_unit={n['cena'].replace(' ', '')}&nl_nights=7")
        else:
            odkaz = f"/zajezd/?s_offer_id={n.get('id', i)}&nl_nights=7"
        if n.get("tlacitko"):
            anchor = f"<h3>{n['nazev']}</h3><a href='{odkaz}'>Zobrazit nabídku</a>"
        else:
            anchor = f"<a href='{odkaz}'>{n['nazev']}</a>"
        kusy.append(f"""
      <article class="card">{anchor}
        <span>{n['od']} - {n['do']}</span>
        <span>All inclusive</span><span>Letecky z Prahy</span>
        <strong>od {n['cena']} Kč</strong>
      </article>""")
    return f"<html><body><div class='list'>{''.join(kusy)}</div></body></html>"


NABIDKY_1 = [
    {"nazev": "Jaz Aquamarine Resort, Hurghada", "od": "15. 8. 2026", "do": "22. 8. 2026", "cena": "20 000"},
    {"nazev": "Jaz Mirabel Beach, Sharm", "od": "20. 8. 2026", "do": "27. 8. 2026", "cena": "24 500"},
    {"nazev": "Jaz Makadi Star & Spa", "od": "25. 8. 2026", "do": "1. 9. 2026", "cena": "18 900", "tlacitko": True, "id": 2},
    {"nazev": "Djerba Palace, Tunisko", "od": "15. 8. 2026", "do": "22. 8. 2026", "cena": "19 000"},
    {"nazev": "Jaz Sharm Dreams", "od": "21. 9. 2026", "do": "28. 9. 2026", "cena": "9 695", "invia": True, "id": 9},
]
# 2. běh: Aquamarine výrazně zlevní, Mirabel drobně (pod prahem), Djerba pryč
NABIDKY_2 = [
    {"nazev": "Jaz Aquamarine Resort, Hurghada", "od": "15. 8. 2026", "do": "22. 8. 2026", "cena": "16 500"},
    {"nazev": "Jaz Mirabel Beach, Sharm", "od": "20. 8. 2026", "do": "27. 8. 2026", "cena": "24 400"},
    {"nazev": "Jaz Makadi Star & Spa", "od": "25. 8. 2026", "do": "1. 9. 2026", "cena": "18 900", "tlacitko": True, "id": 2},
    {"nazev": "Jaz Sharm Dreams", "od": "21. 9. 2026", "do": "28. 9. 2026", "cena": "9 695", "invia": True, "id": 9},
]
# 3.-5. běh: Mirabel zmizí úplně
NABIDKY_3 = [
    {"nazev": "Jaz Aquamarine Resort, Hurghada", "od": "15. 8. 2026", "do": "22. 8. 2026", "cena": "16 500"},
    {"nazev": "Jaz Makadi Star & Spa", "od": "25. 8. 2026", "do": "1. 9. 2026", "cena": "18 900", "tlacitko": True, "id": 2},
    {"nazev": "Jaz Sharm Dreams", "od": "21. 9. 2026", "do": "28. 9. 2026", "cena": "9 695", "invia": True, "id": 9},
]

AKTUALNI = {"data": NABIDKY_1}
ZPRAVY = []


class FakeBrowser:
    def close(self): pass
    def new_page(self, **kw): raise AssertionError("nemá se volat")


class FakePlaywright:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    @property
    def chromium(self):
        class C:
            def launch(self, *a, **kw): return FakeBrowser()
        return C()


def fake_fetch(browser, url):
    # jen Invia vrací karty, ostatní zdroje simulují prázdno
    if "invia.cz" in url and "page=" not in url:
        return stranka(AKTUALNI["data"])
    return "<html><body></body></html>"


def spust_beh():
    ZPRAVY.clear()
    importlib.reload(B)
    B.send_telegram = lambda text, link=None, tise=False: ZPRAVY.append(text)
    B._telegram_post = lambda text, link=None, tise=False: ZPRAVY.append(text)
    B.fetch_rendered_html = fake_fetch
    B.fetch_cedok_html = fake_fetch
    B.sync_playwright = lambda: FakePlaywright()
    # zúžit zdroje na Invii, ať test běží rychle a deterministicky
    B.INVIA_SEARCH_URLS = ["https://www.invia.cz/dovolena/last-minute/"]
    B.INVIA_JAZ_HOTEL_URLS = []
    B.BLUESTYLE_SEARCH_URLS = []
    B.CEDOK_SEARCH_URLS = []
    B.CEDOK_JAZ_HOTEL_URLS = []
    B.EXIMTOURS_SEARCH_URLS = []
    B.EXIM_JAZ_HOTEL_URLS = []
    B.FISCHER_SEARCH_URLS = []
    B.DOVOLENKOVANI_SEARCH_URLS = []
    B.ZMIZENI_PO_BEZICH = 2
    if hasattr(B, 'MIN_KARET_PRO_ZMIZENI'):
        B.MIN_KARET_PRO_ZMIZENI = 1
    B._karet_parsovano = 0
    B.time.sleep = lambda s: None
    import io
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        B.main()
    finally:
        sys.stdout = old
    return list(ZPRAVY), buf.getvalue()


tmp = tempfile.mkdtemp()
old_cwd = os.getcwd()
os.chdir(tmp)
try:
    # ---- BĚH 1: první spuštění, nic se neposílá ----
    zpravy, log = spust_beh()
    check("beh1: zadne zpravy", len(zpravy), 0)
    seen = json.load(open("seen.json"))
    check("beh1: ulozeny 4 Jaz nabidky (Tunisko odfiltrovano)", len(seen), 4)
    check("beh1: existuje stats.json", os.path.exists("stats.json"), True)
    nazvy = sorted(v.get("t", "") for v in seen.values())
    check("beh1: zadny titulek 'Zobrazit nabídku'",
          any(n == "Zobrazit nabídku" for n in nazvy), False)

    zpravy_beh1 = zpravy
    ck = [v.get("ck") for v in seen.values() if v.get("ck")]
    check("beh1: cestovka prectena z odkazu Invie", ck, ["V Tours International"])
    inv = [v for v in seen.values() if v.get("ck")][0]
    check("beh1: noci z tokenu", inv.get("n"), 7)
    check("beh1: cena z c_price_unit", inv.get("ref"), 9695)

    # ---- BĚH 2: zlevnění ----
    AKTUALNI["data"] = NABIDKY_2
    zpravy, log = spust_beh()
    zlevneni = [z for z in zpravy if "ZLEVNĚNÍ" in z]
    check("beh2: jedno zlevneni (male pod prahem se nehlasi)", len(zlevneni), 1)
    check("beh2: zlevneni o 3 500", "3 500 Kč" in zlevneni[0], True)
    check("beh2: rekordni cena", "Rekordně" in zlevneni[0], True)
    st = json.load(open("stats.json"))
    check("beh2: statistika zlevneni", st["zlevneni"], 1)

    # ---- BĚH 3 a 4: Mirabel mizí ----
    AKTUALNI["data"] = NABIDKY_3
    zpravy, _ = spust_beh()
    check("beh3: jeste nehlasi zmizeni", [z for z in zpravy if "ZMIZELA" in z], [])
    zpravy, _ = spust_beh()
    zmizela = [z for z in zpravy if "ZMIZELA" in z]
    check("beh4: hlasi zmizeni po 2 bezich", len(zmizela), 1)
    check("beh4: zmizela spravna nabidka", "Mirabel" in zmizela[0], True)
    zpravy, _ = spust_beh()
    check("beh5: zmizeni se neopakuje", [z for z in zpravy if "ZMIZELA" in z], [])

    # ---- BĚH 6: poškozený stats.json nesmí shodit běh ----
    open("stats.json", "w").write('{"week": "2026-W01"}')
    zpravy, log = spust_beh()
    check("beh6: poskozeny stats nespadne", "Traceback" in log, False)
    check("beh6: seen.json porad existuje", os.path.exists("seen.json"), True)

    # ---- BĚH 7: poškozený seen.json = první běh, žádná záplava ----
    open("seen.json", "w").write("{tohle neni json")
    zpravy, log = spust_beh()
    check("beh7: poskozeny seen = zadna zaplava zprav", len(zpravy), 0)
    # v tomto bode uz bezi NABIDKY_3 = 2 nabidky (Mirabel je pryc)
    check("beh7: seen.json obnoven", len(json.load(open("seen.json"))), 3)
finally:
    os.chdir(old_cwd)
    shutil.rmtree(tmp)

print(f"\n{'='*60}\nINTEGRACE – PROŠLO: {OK}   NEPROŠLO: {FAIL}\n{'='*60}")
for f in FAILS:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
