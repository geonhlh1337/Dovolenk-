"""Testy čistých funkcí bota. Spouštět: python3 test_bot.py"""
import os, sys, json, datetime, tempfile, shutil

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "y")

import importlib
MODUL = os.environ.get('BOT_MODUL', 'lastminute_bot')
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


def check_true(name, cond):
    check(name, bool(cond), True)


# ---------------- extract_price ----------------
check("cena od", B.extract_price("Jaz Aquamarine od 15 880 Kč"), 15880)
check("cena /os. (Čedok 2 ceny)",
      B.extract_price("31 756 Kč 15 190 Kč /os."), 15190)
check("cena bez od", B.extract_price("13 dní 28 790 Kč"), 28790)
check("cena tecka tisic", B.extract_price("od 15.880 Kč"), 15880)
check("cena mimo rozsah", B.extract_price("od 900 Kč"), None)
check("cena nenalezena", B.extract_price("Jaz Mirabel Beach"), None)
check("cena nbsp", B.extract_price("od 15\u00a0880 Kč"), 15880)
check("cena bez oddelovace", B.extract_price("od 15880 Kč"), 15880)

# ---------------- extract_nights ----------------
check("noci z URL", B.extract_nights("cokoliv", "/zajezd/?s_offer_id=1&nl_nights=11"), 11)
check("noci z textu", B.extract_nights("Praha 7 nocí all inclusive"), 7)
check("noci slepene", B.extract_nights("Brno4 dny19 190Kč"), 3)
check("noci z dnu", B.extract_nights("11 dní"), 10)
check("noci z terminu", B.extract_nights("od 15. 7. 2026 do 22. 7. 2026"), 7)
check("noci neznamé", B.extract_nights("Jaz Mirabel"), None)

# ---------------- extract_term ----------------
check("termin", B.extract_term("15. 7. 2026 – 22. 7. 2026"),
      (datetime.date(2026, 7, 15), datetime.date(2026, 7, 22)))
check("termin slepeny", B.extract_term("St04. 11. 2026 - St11. 11. 2026"),
      (datetime.date(2026, 11, 4), datetime.date(2026, 11, 11)))
check("termin neni", B.extract_term("Jaz Makadi"), None)

# ---------------- filtry ----------------
check_true("hotel Jaz", B.passes_hotel_filter("Jaz Aquamarine Resort"))
check("hotel jazyk", B.passes_hotel_filter("Jazyková škola"), False)
check("hotel jazz", B.passes_hotel_filter("Jazzový hotel"), False)
check_true("hotel v URL", B.passes_hotel_filter("/hotel/egypt/jaz elite riviera/"))
check_true("letiste Praha", B.passes_airport_filter("odlet z Prahy"))
check("letiste Katovice", B.passes_airport_filter("odlet z Katovic"), False)
check_true("letiste neuvedeno", B.passes_airport_filter("Jaz Mirabel all inclusive"))
check_true("destinace Hurghada", B.passes_destination_filter("Hurghada, Egypt"))
check("destinace Tunisko", B.passes_destination_filter("Djerba, Tunisko"), False)
check_true("min noci 7", B.passes_min_nights("7 nocí"))
check("min noci 4", B.passes_min_nights("4 noci"), False)
check_true("min noci neznamé", B.passes_min_nights("Jaz Mirabel"))

# ---------------- strava ----------------
check("strava ultra", B._strava_z_textu("Ultra all inclusive"), "Ultra all inclusive")
check("strava AI", B._strava_z_textu("All inclusive"), "All inclusive")

# ---------------- format ----------------
check("format ceny", B.format_price(15880), "15 880 Kč")

# ---------------- klic nabidky ----------------
k1 = B.make_offer_key("invia", "/zajezd/", "15. 7. 2026 – 22. 7. 2026 All inclusive", "Jaz Aquamarine")
k2a = B.make_offer_key("invia", "/zajezd/", "15. 7. 2026 – 22. 7. 2026 All inclusive od 15 880 Kč", "Jaz Aquamarine od 15 880 Kč")
k2b = B.make_offer_key("invia", "/zajezd/", "15. 7. 2026 – 22. 7. 2026 All inclusive od 13 200 Kč", "Jaz Aquamarine od 13 200 Kč")
check("klic stabilni pri zmene ceny", k2a, k2b)
k3 = B.make_offer_key("invia", "/zajezd/", "22. 7. 2026 – 29. 7. 2026 All inclusive", "Jaz Aquamarine")
check_true("klic se lisi pri jinem terminu", k1 != k3)

# ---------------- JSON-LD ----------------
from bs4 import BeautifulSoup
soup = BeautifulSoup("""
<script type="application/ld+json">
{"@type":"Hotel","url":"/zajezd/?s_offer_id=1","offers":{"price":"18990"}}
</script>""", "html.parser")
check("jsonld cena", B.extract_jsonld_prices(soup), {"/zajezd/?s_offer_id=1": 18990})

# ---------------- parse_offers_from_soup ----------------
import re
html_karta = """
<div class="list">
  <article><a href="/zajezd/?s_offer_id=123">Jaz Aquamarine</a>
     <span>15. 7. 2026 - 22. 7. 2026</span><span>All inclusive</span>
     <strong>od 15 880 Kč</strong></article>
</div>"""
off = B.parse_offers_from_soup(BeautifulSoup(html_karta, "html.parser"),
                              re.compile(r"/zajezd/\?s_offer_id="))
check("parse pocet karet", len(off), 1)
check_true("parse card_text ma cenu", "15 880 Kč" in off[0][2])
check_true("parse card_text ma termin", "15. 7. 2026" in off[0][2])

# ---------------- load_seen migrace ----------------
tmp = tempfile.mkdtemp()
old_cwd = os.getcwd()
os.chdir(tmp)
try:
    json.dump(["a", "b"], open("seen.json", "w"))
    check("load_seen ze seznamu", B.load_seen(), {"a": {"ref": 0, "min": 0}, "b": {"ref": 0, "min": 0}})
    json.dump({"a": 100}, open("seen.json", "w"))
    check("load_seen stary format", B.load_seen(), {"a": {"ref": 100, "min": 100}})
    json.dump({"week": "2026-W31", "novych": 5}, open("seen.json", "w"))
    check("load_seen odmitne stats", B.load_seen(), {})
    open("seen.json", "w").write("{rozbity")
    check("load_seen rozbity json", B.load_seen(), {})
    # stats s chybejicimi klici
    json.dump({"week": "2026-W31"}, open("stats.json", "w"))
    st = B.load_stats("2026-W31")
    try:
        B.stats_note_new(st, 10000, "Jaz")
        check("stats necelý soubor nespadne", True, True)
    except Exception as e:
        check("stats necelý soubor nespadne", f"{type(e).__name__}: {e}", True)
    try:
        B.send_telegram_orig = B.send_telegram
        B.send_telegram = lambda *a, **kw: None
        B.send_weekly_summary(st)
        check("weekly summary nespadne", True, True)
    except Exception as e:
        check("weekly summary nespadne", f"{type(e).__name__}: {e}", True)
    finally:
        B.send_telegram = B.send_telegram_orig
finally:
    os.chdir(old_cwd)
    shutil.rmtree(tmp)

# ---------------- process_offer ----------------
poslano = []
B._orig_send = B.send_telegram
B.send_telegram = lambda text, link=None, tise=False: poslano.append(text)

seen, updates, stats = {}, {}, B.default_stats("2026-W31")
card = "Jaz Aquamarine Resort Hurghada 15. 7. 2026 - 22. 7. 2026 All inclusive odlet z Prahy od 20 000 Kč"
B.process_offer("invia", "Invia", "https://www.invia.cz", seen, updates, stats, True,
                "/zajezd/?s_offer_id=1", "Jaz Aquamarine Resort", card)
check("nova nabidka ohlasena", len(poslano), 1)
check_true("nova nabidka text", "NOVÁ NABÍDKA" in poslano[0])

# zlevneni pod prahem -> zadna zprava, ref se nemeni
seen.update(updates); updates = {}
poslano.clear()
card2 = card.replace("20 000", "19 900")
B.process_offer("invia", "Invia", "https://www.invia.cz", seen, updates, stats, True,
                "/zajezd/?s_offer_id=1", "Jaz Aquamarine Resort", card2)
check("male zlevneni nehlasi", len(poslano), 0)
key = list(seen)[0]
check("male zlevneni nemeni ref", updates[key]["ref"], 20000)
check("male zlevneni uklada min", updates[key]["min"], 19900)

# zlevneni nad prahem
seen.update(updates); updates = {}
poslano.clear()
card3 = card.replace("20 000", "18 000")
B.process_offer("invia", "Invia", "https://www.invia.cz", seen, updates, stats, True,
                "/zajezd/?s_offer_id=1", "Jaz Aquamarine Resort", card3)
check("velke zlevneni hlasi", len(poslano), 1)
check_true("zlevneni text", "ZLEVNĚNÍ" in poslano[0])
check_true("rekord badge", "Rekordně" in poslano[0])

# zdrazeni
seen.update(updates); updates = {}
poslano.clear()
card4 = card.replace("20 000", "25 000")
B.process_offer("invia", "Invia", "https://www.invia.cz", seen, updates, stats, True,
                "/zajezd/?s_offer_id=1", "Jaz Aquamarine Resort", card4)
check("zdrazeni hlasi", len(poslano), 1)
check_true("zdrazeni text", "ZDRAŽENÍ" in poslano[0])

# nabidka bez ceny neprepise znamou cenu
seen.update(updates); updates = {}
poslano.clear()
card5 = "Jaz Aquamarine Resort Hurghada 15. 7. 2026 - 22. 7. 2026 All inclusive odlet z Prahy"
B.process_offer("invia", "Invia", "https://www.invia.cz", seen, updates, stats, True,
                "/zajezd/?s_offer_id=1", "Jaz Aquamarine Resort", card5)
check("bez ceny nehlasi", len(poslano), 0)

# ---------------- hlidej_zmizele ----------------
poslano.clear()
B._karet_parsovano = 100
seen_z = {"invia:abc": {"ref": 20000, "min": 18000, "d": "2026-07-28",
                        "t": "Jaz Aquamarine", "u": "https://x", "miss": 2}}
B.hlidej_zmizele(seen_z, {}, "2026-07-29")
check("zmizeni ohlaseno po 3. minusu", len(poslano), 1)
check_true("zmizeni text", "ZMIZELA" in poslano[0])
check_true("gone flag", seen_z["invia:abc"].get("gone"))
poslano.clear()
B.hlidej_zmizele(seen_z, {}, "2026-07-29")
check("zmizeni jen jednou", len(poslano), 0)

# ochrana pri vypadku webu
poslano.clear()
B._karet_parsovano = 3
seen_z2 = {"invia:xyz": {"ref": 20000, "min": 1, "d": "2026-07-28", "t": "X", "miss": 2}}
B.hlidej_zmizele(seen_z2, {}, "2026-07-29")
check("vypadek webu nehlasi zmizeni", len(poslano), 0)

# ---------------- digest ----------------
poslano.clear()
seen_d = {
    "a": {"ref": 14000, "n": 7, "t": "Jaz A", "d": "2026-07-29", "u": "https://a"},
    "b": {"ref": 12000, "n": 10, "t": "Jaz B", "d": "2026-07-29", "u": "https://b"},
    "c": {"ref": 9000, "n": 7, "t": "Jaz C", "d": "2026-07-28", "u": "https://c"},
}
B.send_daily_digest(seen_d, "2026-07-29")
check("digest odeslan", len(poslano), 1)
check_true("digest ma B pred A (levnejsi za noc)",
           poslano[0].index("Jaz B") < poslano[0].index("Jaz A"))
check_true("digest nema vcerejsi", "Jaz C" not in poslano[0])

# ---------------- prune_seen ----------------
seen_p = {"stary": {"ref": 1, "min": 1, "d": "2026-01-01"},
          "novy": {"ref": 1, "min": 1, "d": "2026-07-29"},
          "bez_data": {"ref": 1, "min": 1}}
out = B.prune_seen(seen_p, {}, "2026-07-29")
check("prune smazal stary", "stary" in out, False)
check_true("prune nechal novy", "novy" in out)
check_true("prune doplnil datum", out["bez_data"]["d"] == "2026-07-29")

# ---------------- strankovani ----------------
check("page 1", B._url_se_strankou("https://x.cz/?a=1", 1), "https://x.cz/?a=1")
check("page 2 nova", B._url_se_strankou("https://x.cz/?a=1", 2), "https://x.cz/?a=1&page=2")
check("page 2 prepis", B._url_se_strankou("https://x.cz/?page=1&a=1", 2), "https://x.cz/?page=2&a=1")

# ---------------- helpery jmen ----------------
check("hotel ze slugu invia",
      B._hotel_ze_slugu("https://www.invia.cz/hotel/egypt/marsa-alam/jaz-solaya-resort/"),
      "Jaz Solaya Resort")
check("hotel z cesty cedok",
      B._hotel_z_cesty("https://www.cedok.cz/dovolena/egypt/marsa-matrouh/hotel-jaz-almaza-beach-resort,MUH2JAB/"),
      "Jaz Almaza Beach Resort")


# ============ TESTY OPRAV ============
if hasattr(B, "nazev_hotelu"):
    check("nazev: tlacitko -> slug z URL",
          B.nazev_hotelu("Zobrazit nabídku", "nejaky text",
                         "https://www.invia.cz/hotel/egypt/hurghada/jaz-aquaviva/"),
          "Jaz Aquaviva")
    check("nazev: prazdny titulek -> slug",
          B.nazev_hotelu("", "text", "https://www.cedok.cz/dovolena/egypt/x/hotel-jaz-sakhra,MUH2SAK/"),
          "Jaz Sakhra")
    check("nazev: jen cena -> slug",
          B.nazev_hotelu("od 15 880 Kč", "text",
                         "https://www.invia.cz/hotel/egypt/hurghada/jaz-aquaviva/"),
          "Jaz Aquaviva")
    check("nazev: normalni titulek zustava",
          B.nazev_hotelu("Jaz Mirabel Beach Resort", "text", "https://x"),
          "Jaz Mirabel Beach Resort")
    check("nazev: /zajezd/ URL nedela nesmysl",
          B.nazev_hotelu("Zobrazit nabídku", "Jaz Makadi 7 nocí",
                         "https://www.invia.cz/zajezd/?s_offer_id=1").startswith("Zajezd"),
          False)

if hasattr(B, "_nove_minimum"):
    check("minimum ignoruje nuly", B._nove_minimum(0, 20000, 25000), 20000)
    check("minimum bere nejnizsi", B._nove_minimum(18000, 20000, 25000), 18000)
    check("minimum ze samych nul", B._nove_minimum(0, 0, 0), 0)

# doplnena cena po zaznamu bez ceny
poslano.clear()
seen2, updates2 = {}, {}
st2 = B.default_stats("2026-W31")
bez = "Jaz Aquaviva Hurghada 15. 7. 2026 - 22. 7. 2026 All inclusive z Prahy"
B.process_offer("invia", "Invia", "https://www.invia.cz", seen2, updates2, st2, True,
                "/zajezd/?s_offer_id=9", "Jaz Aquaviva", bez)
seen2.update(updates2); updates2 = {}; poslano.clear()
B.process_offer("invia", "Invia", "https://www.invia.cz", seen2, updates2, st2, True,
                "/zajezd/?s_offer_id=9", "Jaz Aquaviva", bez + " od 18 500 Kč")
check("doplnena cena se ohlasi", len(poslano), 1)
check_true("doplnena cena obsahuje castku", poslano and "18 500 Kč" in poslano[0])
check("doplnena cena ulozena", updates2[list(updates2)[0]]["ref"], 18500)

# load_stats doplni chybejici klice
tmp2 = tempfile.mkdtemp(); os.chdir(tmp2)
try:
    json.dump({"week": "2026-W31"}, open("stats.json", "w"))
    st3 = B.load_stats("2026-W31")
    check("load_stats doplni novych", st3.get("novych"), 0)
    try:
        B.stats_note_new(st3, 10000, "Jaz")
        check("stats_note_new funguje", st3["novych"], 1)
    except Exception as e:
        check("stats_note_new funguje", f"{type(e).__name__}: {e}", 1)
    _o = B.send_telegram; B.send_telegram = lambda *a, **k: None
    try:
        B.send_weekly_summary(st3)
        check("weekly summary nespadne", True, True)
    except Exception as e:
        check("weekly summary nespadne", f"{type(e).__name__}: {e}", True)
    B.send_telegram = _o
    json.dump({"week": "2026-W30", "novych": "spatne", "nejlevnejsi": "taky"}, open("stats.json", "w"))
    st4 = B.load_stats("2026-W31")
    check("load_stats prezije spatne typy", (st4["novych"], st4["nejlevnejsi"]), (0, None))
finally:
    os.chdir(old_cwd); shutil.rmtree(tmp2)

# telegram vypadek nesmi shodit beh
B.send_telegram = B._orig_send
import requests as _rq
_orig_post = _rq.post
_rq.post = lambda *a, **k: (_ for _ in ()).throw(Exception("sit spadla"))
try:
    B._telegram_post("test")
    check("vypadek Telegramu nespadne", True, True)
except Exception as e:
    check("vypadek Telegramu nespadne", f"{type(e).__name__}: {e}", True)
finally:
    _rq.post = _orig_post

# urljoin
if "urljoin" in open(MODUL + ".py", encoding="utf-8").read():
    poslano2 = []
    B.send_telegram = lambda text, link=None, tise=False: poslano2.append(link)
    s3, u3 = {}, {}
    B.process_offer("invia", "Invia", "https://www.invia.cz", s3, u3,
                    B.default_stats("2026-W31"), True,
                    "//www.invia.cz/zajezd/?s_offer_id=5&nl_nights=7",
                    "Jaz Test", "Jaz Test Hurghada 7 nocí z Prahy od 20 000 Kč")
    check("protokolove relativni odkaz", poslano2 and poslano2[0],
          "https://www.invia.cz/zajezd/?s_offer_id=5&nl_nights=7")

B.send_telegram = B._orig_send



# ============ TESTY DAT Z ODKAZŮ INVIE ============
if hasattr(B, "invia_detaily"):
    # znovu odchytit zpravy - predchozi blok vratil originalni send_telegram
    B.send_telegram = lambda text, link=None, tise=False: poslano.append(text)
    REAL = ("https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-sharm-dreams/"
            "zajezd/?s_offer_id=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
            "eyJwcm92aWRlclByZWZpeCI6IlRUIiwib2ZmZXJJZCI6IlgiLCJ0b3VyT3BlcmF0"
            "b3JJZCI6MTAyNTI1LCJ0b3VyT3BlcmF0b3JDb2RlIjoiVlRPSSIsImRhdGVGcm9t"
            "IjoiMjAyNjA5MjEiLCJkYXRlVG8iOiIyMDI2MDkyOCIsImRheXNDb3VudCI6OCwi"
            "aG90ZWxJZCI6MjI3MjYsImNvdW50cnlJZCI6MTEsInRyYW5zcG9ydGF0aW9uSWQi"
            "OjQsIm1lYWxJZCI6NiwiZXh0ZXJuYWxTb3VyY2VOaWdodHNPZlN0YXkiOjcsImFp"
            "cnBvcnRJZCI6MSwib3JpZ2luYWxDdXJyZW5jeUlkIjozfQ.sig"
            "&c_price_unit=9695&c_price_group=19390&nl_nights=7")
    inv = B.invia_detaily(REAL)
    check("invia: ck_id", inv["ck_id"], 102525)
    check("invia: ck_kod", inv["ck_kod"], "VTOI")
    check("invia: termin od", inv["od"], datetime.date(2026, 9, 21))
    check("invia: termin do", inv["do"], datetime.date(2026, 9, 28))
    check("invia: noci", inv["noci"], 7)
    check("invia: strava z mealId", inv["strava"], "Ultra all inclusive")
    check("invia: letiste z airportId", inv["letiste"], "Praha")
    check("invia: cena z c_price_unit", inv["cena"], 9695)
    check("invia: neplatny odkaz", B.invia_detaily("https://x.cz/?a=1"), None)
    check("invia: rozbity token", B.invia_detaily("https://x.cz/?s_offer_id=aaa.bbb"), None)

    # daysCount -> noci, kdyz chybi explicitni pocet
    import base64 as _b64
    def _url(payload, extra=""):
        p = _b64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        return f"https://www.invia.cz/zajezd/?s_offer_id=hdr.{p}.sig{extra}"
    inv2 = B.invia_detaily(_url({"tourOperatorId": 39, "daysCount": 8,
                                 "checkInDate": "20260723", "mealId": 5}))
    check("invia: noci z daysCount", inv2["noci"], 7)
    check("invia: do dopocitano", inv2["do"], datetime.date(2026, 7, 30))

    # jmeno CK: z kodu, z naucene mapy, neznama
    B._ck_mapa = {}
    check("CK z kodu", B.jmeno_ck({"ck_id": 1, "ck_kod": "SLR"}), "Schauinsland Reisen")
    B._ck_mapa = {"39": "Blue Style"}
    check("CK z naucene mapy", B.jmeno_ck({"ck_id": 39, "ck_kod": None}), "Blue Style")
    B._ck_mapa = {}
    B.DOHLEDAVAT_CK = False
    check("CK neznama -> ID", B.jmeno_ck({"ck_id": 777, "ck_kod": None}), "CK #777")
    check("CK bez ID", B.jmeno_ck(None), "")
    B.DOHLEDAVAT_CK = True

    # obohaceni karty
    ob = B._obohat_kartu("Jaz Sharm Dreams", inv)
    check_true("obohaceni: termin", "21. 9." in ob or "21. 9" in ob)
    check_true("obohaceni: noci", "7 nocí" in ob)
    check_true("obohaceni: strava", "Ultra all inclusive" in ob)
    check("obohaceni: bez dat nemeni text", B._obohat_kartu("abc", None), "abc")

    # KLÍČ se nesmí změnit tím, že odkaz nese data
    B._ck_mapa = {"102525": "V Tours International"}
    poslano.clear()
    s5, u5 = {}, {}
    st5 = B.default_stats("2026-W31")
    B._hodina_behu = 14
    B.process_offer("invia", "Invia", "https://www.invia.cz", s5, u5, st5, True,
                    REAL, "Jaz Sharm Dreams", "Jaz Sharm Dreams")
    klic_inv = list(u5)[0]
    klic_ocekavany = B.make_offer_key("invia", REAL.split("?")[0],
                                      "Jaz Sharm Dreams", "Jaz Sharm Dreams")
    check("klic se NEmeni kvuli datum z odkazu", klic_inv, klic_ocekavany)
    check("invia: nova nabidka ohlasena", len(poslano), 1)
    check_true("zprava obsahuje cestovku", "V Tours International" in poslano[0])
    check_true("zprava obsahuje cenu z odkazu", "9 695 Kč" in poslano[0])
    check_true("zprava obsahuje noci", "7 nocí" in poslano[0])
    check("cestovka ulozena do zaznamu", u5[klic_inv].get("ck"), "V Tours International")
    check("noci ulozeny", u5[klic_inv].get("n"), 7)

    # ne-invia zdroj se nesmi zmenit
    poslano.clear()
    s6, u6 = {}, {}
    B.process_offer("cedok", "Čedok", "https://www.cedok.cz", s6, u6,
                    B.default_stats("2026-W31"), True,
                    "/dovolena/egypt/hotel-jaz-x,MUH2X/",
                    "Jaz X", "Jaz X 15. 7. 2026 - 22. 7. 2026 z Prahy od 20 000 Kč")
    check("cedok: nabidka projde", len(poslano), 1)
    check("cedok: zadna cestovka v textu", "🏢" in poslano[0], False)

    # statistika hodin
    poslano.clear()
    s7 = dict(u5); u7 = {}
    st7 = B.default_stats("2026-W31")
    B._hodina_behu = 9
    levnejsi = REAL.replace("c_price_unit=9695", "c_price_unit=8000")
    B.process_offer("invia", "Invia", "https://www.invia.cz", s7, u7, st7, True,
                    levnejsi, "Jaz Sharm Dreams", "Jaz Sharm Dreams")
    check("zlevneni z odkazu ohlaseno", len(poslano), 1)
    check("hodina zapsana do statistiky", st7["zmeny_po_hodinach"], {"09": 1})
    _o = B.send_telegram; zpr7 = []
    B.send_telegram = lambda t, link=None, tise=False: zpr7.append(t)
    B.send_weekly_summary(st7)
    B.send_telegram = _o
    check_true("souhrn ukazuje hodiny zmen", zpr7 and "09:00 UTC" in zpr7[0])

    # digest a zmizeni ukazuji cestovku
    zpr8 = []
    B.send_telegram = lambda t, link=None, tise=False: zpr8.append(t)
    B.send_daily_digest({"a": {"ref": 14000, "n": 7, "t": "Jaz A",
                              "d": "2026-07-29", "u": "https://a",
                              "ck": "Alltours"}}, "2026-07-29")
    check_true("digest ukazuje cestovku", zpr8 and "Alltours" in zpr8[0])
    zpr8.clear()
    B._karet_parsovano = 100
    sz = {"invia:q": {"ref": 20000, "min": 1, "d": "2026-07-28", "t": "Jaz Q",
                      "ck": "Čedok", "miss": B.ZMIZENI_PO_BEZICH - 1}}
    B.hlidej_zmizele(sz, {}, "2026-07-29")
    check_true("zmizeni ukazuje cestovku", zpr8 and "Čedok" in zpr8[0])
    B.send_telegram = _o



# ============ TESTY ČASOVÉHO ROZPOČTU A ODOLNOSTI ZDROJŮ ============
if hasattr(B, "dosel_cas"):
    B.send_telegram = lambda text, link=None, tise=False: poslano.append(text)

    # rozpočet
    B._zacatek_behu = B.time.monotonic()
    B._rozpocet_hlaseno = False
    check("rozpocet: na zacatku je cas", B.dosel_cas(), False)
    B._zacatek_behu = B.time.monotonic() - (B.CASOVY_ROZPOCET_MINUT * 60 + 1)
    check("rozpocet: po vyprseni dosel", B.dosel_cas(), True)
    B._zacatek_behu = B.time.monotonic()
    B._rozpocet_hlaseno = False

    # pojistka proti mrtvemu zdroji
    B._chyby_zdroje = 0
    try:
        B._zaznamenej_chybu_zdroje("Test", "https://x", "timeout")
        prvni_ok = True
    except B.ZdrojSeVzdal:
        prvni_ok = False
    check("pojistka: prvni chyba jeste nevzdava", prvni_ok, True)
    try:
        B._zaznamenej_chybu_zdroje("Test", "https://x", "timeout")
        check("pojistka: druha chyba vzda zdroj", False, True)
    except B.ZdrojSeVzdal:
        check("pojistka: druha chyba vzda zdroj", True, True)
    B._chyby_zdroje = 0
    B._uspech_zdroje()
    check("pojistka: uspech nuluje citac", B._chyby_zdroje, 0)

    # ZMIZENÍ jen u zdrojů, které v běhu odpověděly
    poslano.clear()
    B._karet_parsovano = 100
    sz = {
        "invia:a": {"ref": 1, "min": 1, "d": "2026-08-03", "t": "Jaz A",
                    "miss": B.ZMIZENI_PO_BEZICH - 1},
        "eximtours:b": {"ref": 1, "min": 1, "d": "2026-08-03", "t": "Jaz B",
                        "miss": B.ZMIZENI_PO_BEZICH - 1},
    }
    B._zdroje_ok = {"invia"}          # Exim tento běh spadl na timeout
    B.hlidej_zmizele(sz, {}, "2026-08-04")
    check("zmizeni: ohlaseno u funkcniho zdroje", len(poslano), 1)
    check_true("zmizeni: jde o Invii", poslano and "Jaz A" in poslano[0])
    check("zmizeni: NEohlaseno u spadleho zdroje",
          sz["eximtours:b"].get("gone"), None)
    check("zmizeni: spadlemu zdroji se nezvysil citac",
          sz["eximtours:b"]["miss"], B.ZMIZENI_PO_BEZICH - 1)

    poslano.clear()
    B._zdroje_ok = set()               # nic neproslo
    sz2 = {"invia:c": {"ref": 1, "min": 1, "d": "2026-08-03", "t": "Jaz C",
                       "miss": B.ZMIZENI_PO_BEZICH - 1}}
    B.hlidej_zmizele(sz2, {}, "2026-08-04")
    check("zmizeni: zadny zdroj neprosel = ticho", len(poslano), 0)

    # process_offer registruje zdroj jako funkcni
    B._zdroje_ok = set()
    B.process_offer("cedok", "Čedok", "https://www.cedok.cz", {}, {},
                    B.default_stats("2026-W31"), False,
                    "/dovolena/x/hotel-jaz-y,M1/", "Jaz Y",
                    "Jaz Y 15. 7. 2026 - 22. 7. 2026 z Prahy od 20 000 Kč")
    check("zdroj se zaregistroval jako funkcni", B._zdroje_ok, {"cedok"})

    # rotace zdroju
    st_r = B.default_stats("2026-W31")
    check("rotace: vychozi start", st_r.get("zdroj_start"), 0)
    poradi = []
    zdroje = [("A", None), ("B", None), ("C", None)]
    for _ in range(4):
        start = st_r.get("zdroj_start", 0) % len(zdroje)
        poradi.append([z[0] for z in (zdroje[start:] + zdroje[:start])][0])
        st_r["zdroj_start"] = (start + 1) % len(zdroje)
    check("rotace: kazdy beh zacina jinym zdrojem", poradi, ["A", "B", "C", "A"])

    # tiche zpravy
    zachyt = {}
    _rq = B.requests.post
    B.requests.post = lambda url, data=None, timeout=None: (
        zachyt.update(data or {}) or type("R", (), {"ok": True, "status_code": 200})())
    B._telegram_post("test", tise=True)
    check("ticha zprava nastavi disable_notification",
          zachyt.get("disable_notification"), True)
    B.requests.post = _rq

print(f"\n{'='*60}\nCELKEM PROŠLO: {OK}   NEPROŠLO: {FAIL}\n{'='*60}")
for f in FAILS:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
