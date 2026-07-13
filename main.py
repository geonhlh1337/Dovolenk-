import os
import re
import sys
import html
import json
import time
import hashlib
import datetime
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("CHYBA: Chybí TELEGRAM_BOT_TOKEN nebo TELEGRAM_CHAT_ID "
                     "v prostředí (secrets ve workflow).")

SEEN_FILE = "seen.json"
STATS_FILE = "stats.json"

# ============================================================
#                       NASTAVENÍ HLEDÁNÍ
# ============================================================

# Filtr odletových letišť. Bot pošle nabídku jen tehdy, když text karty
# obsahuje některé z těchto slov. Prázdný seznam ([]) = filtr vypnutý.
# POZOR: kvůli skloňování používáme KMENY slov - "Prah" chytí Praha,
# Prahy, Praze i "z Prahy". Nepiš sem celá slova jako "Praha".
LETISTE_FILTR = ["Prah", "Brn", "Ostrav"]

# Pojistka proti záplavě zpráv: maximum Telegram zpráv za jeden běh.
# Když web změní vzhled nebo se pokazí seen.json, bot by jinak mohl poslat
# stovky zpráv (a při 4 s/zprávu i spálit minuty GitHub Actions). Zprávy
# nad limit se jen zalogují a na konci přijde jedno upozornění s počtem.
MAX_ZPRAV_ZA_BEH = 25

# Diagnostika: když je True, u Exim Tours a Fischer se do logu vypíše
# ukázka skutečně nalezených odkazů na stránce. Slouží k jednorázovému
# doladění rozpoznávacího vzoru - po doladění vrať na False.
DIAGNOSTIKA_ODKAZU = False

# Filtr cílových destinací (whitelist). Vyplníš-li, projdou POUZE nabídky
# obsahující některé z těchto slov. Prázdný seznam ([]) = vypnuto.
# Příklady: "Egypt", "Řecko", "Turecko", "Kréta", "Rhodos", "Hurghada"...
# ------------------------------------------------------------
# FILTR CÍLOVÝCH DESTINACÍ (whitelist)
# ------------------------------------------------------------
# Projdou POUZE nabídky, jejichž text obsahuje některé z těchto slov
# (nezáleží na velikosti písmen, stačí část slova - "Egypt" chytí i "Egypta").
# Prázdný seznam ([]) = filtr vypnutý, chodí všechny destinace.
#
# Jak si přidat další zemi: prostě dopiš řádek, např.
#     "Řecko", "Kréta", "Rhodos",
#     "Turecko", "Antalya", "Side",
# Zemi stačí uvést jednou - konkrétní letoviska přidávej jen tehdy, když
# chceš mít jistotu i u zdrojů, které v textu neuvádějí název země.
DESTINACE_FILTR = [
    # Egypt jako pojistka (chytí karty, které uvádějí jen zemi):
    "Egypt",
    # Egyptská letoviska (hlavní filtr - karty často uvádějí rovnou letovisko):
    "Hurghada",
    "Marsa Alam",
    "Sharm",            # Sharm El Sheikh / Šarm
    "Marsa Matrouh",
    "Marsa Matruh",
    "Almaza",           # Almaza Bay - zde je Jaz Almaza Bay
    "Safaga",
    "Taba",
    "Dahab",
    "El Gouna",
    "Makadi",           # Makadi Bay
    "Soma Bay",
    "Sahl Hasheesh",
    "Nuweiba",
    "Nuwejba",
    "Naama Bay",
    "Ain Sokhna",
    "Ain Soukhna",
    "El Quseir",
    "Alexandria",
    "Káhira",
    "Luxor",            # Jaz má hotel i v Luxoru
]

# ------------------------------------------------------------
# FILTR HOTELOVÉHO ŘETĚZCE
# ------------------------------------------------------------
# Projdou POUZE nabídky, jejichž název/text obsahuje některé z těchto slov
# jako SAMOSTATNÉ SLOVO (aby "Jaz" nechytlo "jazyk", "jazz" apod.).
# Prázdný seznam ([]) = filtr vypnutý.
# Tento filtr platí VŽDY - i na vyhledávacích URL z DUVERYHODNE_EGYPT_URL,
# protože chceš striktně jen tyto hotely.
#
# Aktuálně: jen řetězec Jaz (Jaz Aquamarine, Jaz Mirabel, Jaz Grand Marsa...).
# Chceš přidat další řetězec? Dopiš např. "Steigenberger", "Rixos", "Pickalbatros".
HOTEL_FILTR = [
    "Jaz",
]

# Cenový strop v Kč za osobu. Nabídky s vyšší cenou se zahodí.
# None = bez omezení. Nabídky, u kterých se cenu nepodařilo přečíst,
# procházejí vždy (ať o ně nepřijdeš omylem).
MAX_CENA = None
# MAX_CENA = 40000

# Minimální počet nocí. Nabídky kratší se zahodí. None = bez omezení.
# Pojistka z textu karty - hlavní filtrování dělá URL parametr (nl_length_from
# apod.), tohle je záloha, kdyby URL nějakou kratší pustila. Nabídky, u
# kterých počet nocí nejde z textu přečíst, procházejí (ať o ně nepřijdeš).
MIN_NOCI = 7

# Oznamovat i ZDRAŽENÍ? True = přijde 🔺 zpráva, když nabídka zdraží.
# False = chodí jen zlevnění 🔻 (a nové nabídky).
OZNAMOVAT_ZDRAZENI = True

# URL, které už samy vrací jen požadovanou zemi (vyfiltrované parametry přímo
# na webu - tvoje vyhledávací URL). Na nabídky z těchto URL se filtr
# DESTINACE_FILTR NEAPLIKUJE - bereš vše, co vrátí. Porovnává se podle
# začátku adresy. Sem patří tvoje vyladěné vyhledávací URL na Egypt.
# POZOR: důvěřuje se URL, ne textu - proto sem dávej JEN adresy opravdu
# omezené na Egypt, jinak by prošly i jiné země.
DUVERYHODNE_EGYPT_URL = [
    # Invia vyhledávací URL sem ZÁMĚRNĚ nedávám - ověřeno, že vrací i jiné
    # země, takže na ně necháváme platit filtr Egyptu (spolehlivější).
    "https://www.eximtours.cz/vysledky-vyhledavani",
    "https://www.eximtours.cz/last-minute/egypt",
    "https://www.fischer.cz/vysledky-vyhledavani",
    "https://www.fischer.cz/last-minute/egypt",
    "https://www.blue-style.cz/vyhledavani/",
    "https://dovolenkovani.cz/vyhledavani-zajezdu",
]

# Filtr stravy. Vyplníš-li, projdou jen nabídky obsahující některé z těchto
# slov. Prázdný seznam ([]) = vypnuto.
# Obvyklé hodnoty: "All inclusive", "Polopenze", "Plná penze", "Snídaně"
STRAVA_FILTR = [
    # "All inclusive",
]

# --- Invia.cz --- (srovnávač 120+ CK: Exim, Fischer, Blue Style, Čedok...)
# Invia na svých last-minute stránkách agreguje nabídky od VŠECH partnerských
# CK dohromady (Exim Tours, Fischer, Blue Style, Čedok a dalších 120+), takže
# přes tyto stránky chodí i jejich zájezdy - není potřeba je řešit zvlášť.
# Kromě obecných stránek (Praha/Brno/Ostrava) je zde i cílená stránka na
# Egypt kvůli filtru DESTINACE_FILTR.
# Pozn.: Samostatné stránky /cestovni-kancelare/ck-.../ jsou jen rozcestníky
# bez konkrétních nabídek, proto je nepoužíváme (vracely by 0).
INVIA_SEARCH_URLS = [
    # ZEŠTÍHLENO podle reálných logů (07/2026): obecné last-minute stránky
    # dlouhodobě nosily 0 nových a egyptské last-minute stránky vracely
    # 0 karet (rozbité parsování/prázdné stránky). Hlavní úlovky nosí
    # hotelové stránky níže + tato dvě vyhledávání, která navíc hlídají
    # NOVÉ Jaz hotely mimo seznam INVIA_JAZ_HOTEL_URLS.
    # Kdyby bylo potřeba, odkomentuj:
    # "https://www.invia.cz/dovolena/last-minute/",
    # "https://www.invia.cz/dovolena/last-minute-z-brna/",
    # "https://www.invia.cz/dovolena/last-minute-ostrava/",
    # "https://www.invia.cz/dovolena/last-minute/egypt/",
    # "https://www.invia.cz/dovolena/last-minute/egypt/marsa-alam/",
    # CELÝ EGYPT - vyhledávání přes nl_country_id=11 (pokrývá VŠECHNA egyptská
    # letoviska najednou, kde Jaz má hotel), 7+ nocí, 2 dospělí, letecky z ČR,
    # řazeno od nejlevnějšího. Tohle je hlavní zdroj pro kompletní přehled Jaz
    # v Egyptě. Filtr Egypt+Jaz z toho vybere jen Jaz hotely.
    "https://www.invia.cz/dovolena/?nl_country_id=11&nl_length_from=7&nl_occupancy_adults=2&nl_transportation_id%5B%5D=3_CZ&sort=c_price&sort_order=asc&search_form=1",
    # Tvoje původní vyhledávací URL (ponechána; filtr Egypt+Jaz ji pročistí):
    "https://www.invia.cz/dovolena/?s_action=default&d_start_from=12.07.2026&nl_transportation_id%5B%5D=3_1&nl_transportation_id%5B%5D=3_2&nl_transportation_id%5B%5D=3_3&page=1&nl_occupancy_adults=2&nl_locality_parent_id%5B%5D=626&nl_length_from=7&sort=nl_sell&nl_locality_id%5B%5D=626",
]

# Kolik stránek výsledků projít u vyhledávacích URL (page=1..N). Týká se jen
# URL s vyhledávacími parametry (s_action / nl_country_id / search_form);
# statické last-minute stránky se čtou jen jednou. Bot přestane listovat
# dřív, jakmile stránka nevrátí žádné nabídky.
# POZOR na čas běhu: každá stránka = ~15-20 s v prohlížeči. Při hodinovém
# spouštění v SOUKROMÉM repozitáři hlídej měsíční limit GitHub Actions
# (2000 minut zdarma). Veřejný repozitář limit nemá. Hodnota 3 je rozumný
# kompromis; klidně zvyš na 5, pokud máš repozitář veřejný.
INVIA_MAX_STRANEK = 3

# Přímé stránky Jaz hotelů na Invii - nejspolehlivější "jen Jaz" zdroj.
# Každá stránka hotelu obsahuje jeho aktuální termíny a ceny od všech CK,
# které Invia prodává (včetně Exim Tours, Fischer, Blue Style, Čedok...) -
# proto NENÍ potřeba přidávat tytéž hotely na ostatních portálech zvlášť.
# Všechny níže uvedené URL jsou OVĚŘENÉ (z reálného logu bota nebo z
# vyhledávání). Další Jaz hotel přidáš vložením URL jeho Invia stránky
# (tvar: https://www.invia.cz/hotel/egypt/<letovisko>/<slug>/).
INVIA_JAZ_HOTEL_URLS = [
    # --- Marsa Alam / Madinat Coraya ---
    "https://www.invia.cz/hotel/egypt/marsa-alam/jaz-elite-riviera/",
    "https://www.invia.cz/hotel/egypt/marsa-alam/jaz-costa-mares/",
    "https://www.invia.cz/hotel/egypt/marsa-alam/jaz-costa-mares-adults-only/",
    "https://www.invia.cz/hotel/egypt/marsa-alam/jaz-elite-amara/",
    "https://www.invia.cz/hotel/egypt/marsa-alam/jaz-grand-marsa-ex-grand-resta/",
    "https://www.invia.cz/hotel/egypt/marsa-alam/jaz-solaya-resort/",
    "https://www.invia.cz/hotel/egypt/marsa-alam/jaz-maraya/",
    # --- Hurghada / Makadi Bay ---
    "https://www.invia.cz/hotel/egypt/hurghada/jaz-aquamarine-resort/",
    "https://www.invia.cz/hotel/egypt/hurghada/jaz-aquaviva/",
    "https://www.invia.cz/hotel/egypt/hurghada/jaz-makadi-saraya-resort/",
    "https://www.invia.cz/hotel/egypt/hurghada/jaz-makadi-star-spa/",
    "https://www.invia.cz/hotel/egypt/hurghada/jaz-makadina-ex-sol-y-mar-club-makadi/",
]

# --- Blue Style ---
BLUESTYLE_SEARCH_URLS = [
    # ZEŠTÍHLENO: obecná last-minute a vyhledávací stránka nosily dlouhodobě
    # 0 nových (Jaz nabídky Blue Stylu navíc chodí i přes Invii). Fulltext
    # "Hotel jaz" je jediný, který reálně nosil úlovky. Odkomentuj v případě potřeby:
    # "https://www.blue-style.cz/last-minute/",
    # "https://www.blue-style.cz/vyhledavani/?depCity=2%2C10%2C11&arrCity=8&dateFrom=2026-07-12&dateTo=2026-08-11&room1=2&priceType=per-person",
    # Fulltext hledání "Hotel jaz" - první výsledky jsou Jaz hotely, zbytek
    # (jiné hotely) spolehlivě odfiltruje HOTEL_FILTR.
    "https://www.blue-style.cz/fulltext/?q=Hotel+jaz",
]

# --- Exim Tours a Fischer ---
# VYPNUTO podle reálných logů (07/2026): Fischer vracel 0 karet na všech
# URL (nejspíš blokace/jiná struktura) a Exim jen 1-8 karet bez jediného
# úlovku. Nabídky OBOU CK přitom chodí přes Invii (agreguje 120+ CK včetně
# hotelových stránek Jaz), takže o nic nepřicházíš - jen se šetří ~1,5 min
# za běh. Kdyby ses chtěl vrátit, odkomentuj URL níže.
EXIMTOURS_SEARCH_URLS = [
    # "https://www.eximtours.cz/hledani-vysledky?q=Jaz",
    # "https://www.eximtours.cz/vysledky-vyhledavani?ac1=2&d=64419|64420|64423&dd=2026-07-11&m=5&nn=1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20|21&rd=2026-09-10&to=4312|4305|2682|4308|4392|4309&tt=1",
    # "https://www.eximtours.cz/last-minute/egypt",
]

FISCHER_SEARCH_URLS = [
    # "https://www.fischer.cz/hledani-vysledky?q=Jaz",
    # "https://www.fischer.cz/vysledky-vyhledavani?ac1=2&d=64419|64420|64423&dd=2026-07-11&nn=1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20|21&rd=2026-09-10&to=4312|4305|2682&tt=1",
    # "https://www.fischer.cz/last-minute/egypt",
]

# --- Dovolenkovani.cz ---
# VYPNUTO: web opakovaně neodpovídá z GitHub Actions (timeout i s plným
# Chrome user-agentem a 2 pokusy) - nejspíš blokuje IP adresy datacenter.
# Každý běh tak jen pálil ~2,5 minuty. Je to srovnávač, jehož nabídky
# stejně pokrývá Invia. Kdyby ses k němu chtěl vrátit, odkomentuj URL níže.
DOVOLENKOVANI_SEARCH_URLS = [
    # "https://dovolenkovani.cz/vyhledavani-zajezdu/1?di%5B0%5D=2460&di%5B1%5D=146&di%5B2%5D=758&di%5B3%5D=761&di%5B4%5D=762&di%5B5%5D=2433&di%5B6%5D=1007&di%5B7%5D=147&di%5B8%5D=148&di%5B9%5D=149&di%5B10%5D=145&di%5B11%5D=2416&di%5B12%5D=2461&di%5B13%5D=150&di%5B14%5D=144&di%5B15%5D=1010&di%5B16%5D=1011&di%5B17%5D=1012&di%5B18%5D=1013&di%5B19%5D=1014&df=2026-07-11&dt=2027-07-11&uf=1&ut=25&ac=2&cc=0&rooms%5B0%5D=18%2C18&ti=1&ai%5B0%5D=1&ai%5B1%5D=2&ai%5B2%5D=3&ar=5&pf=5000&pt=1000000",
]

# ============================================================


# seen.json: { "klic": {"ref": posledni_referencni_cena, "min": historicke_minimum} }
def load_seen():
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if isinstance(data, list):  # nejstarší formát (seznam klíčů)
        return {k: {"ref": 0, "min": 0} for k in data}
    if not isinstance(data, dict):
        return {}
    # Ochrana: kdyby se do seen.json omylem dostal obsah stats.json
    # (klíče week/novych/zlevneni/...), bereme ho jako prázdný a začneme znovu.
    STATS_KLICE = {"week", "novych", "zlevneni", "nejvetsi_sleva", "nejlevnejsi"}
    if STATS_KLICE & set(data.keys()):
        print("VAROVÁNÍ: seen.json obsahoval data statistik - resetuji na prázdný.")
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = {"ref": v.get("ref", 0), "min": v.get("min", 0)}
            if "d" in v:
                out[k]["d"] = v["d"]
        else:  # starší formát (klic -> cena)
            out[k] = {"ref": v, "min": v}
    return out


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=0, sort_keys=True)


# stats.json: průběžné počítadlo pro týdenní přehled
def default_stats(week):
    return {
        "week": week,
        "novych": 0,
        "zlevneni": 0,
        "nejvetsi_sleva": None,   # {"castka": int, "titulek": str}
        "nejlevnejsi": None,      # {"cena": int, "titulek": str}
    }


def load_stats(current_week):
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "week" in data:
                return data
        except Exception:
            pass
    return default_stats(current_week)


def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=0)


# Počítadla zpráv za běh (pojistka MAX_ZPRAV_ZA_BEH).
_poslano_zprav = 0
_potlaceno_zprav = 0


def send_telegram(text, link=None):
    """Pošle zprávu, ale nejvýš MAX_ZPRAV_ZA_BEH za jeden běh (pojistka)."""
    global _poslano_zprav, _potlaceno_zprav
    if _poslano_zprav >= MAX_ZPRAV_ZA_BEH:
        _potlaceno_zprav += 1
        print(f"Zpráva POTLAČENA (limit {MAX_ZPRAV_ZA_BEH}/běh): {text[:80]!r}")
        return
    _poslano_zprav += 1
    _telegram_post(text, link)


def _telegram_post(text, link=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if link:
        payload["reply_markup"] = json.dumps({
            "inline_keyboard": [[{"text": "🔗 Otevřít nabídku", "url": link}]]
        })
    resp = requests.post(url, data=payload, timeout=20)
    if resp.status_code == 429:
        try:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 3)
        except Exception:
            retry_after = 3
        time.sleep(retry_after + 1)
        resp = requests.post(url, data=payload, timeout=20)
    if not resp.ok:
        print("Chyba při odesílání na Telegram:", resp.text)
    time.sleep(4)  # limit ~20 zpráv/min do jednoho chatu


def short_hash(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:10]


def extract_price(text):
    # Bereme jen jasnou cenu ve tvaru "od X Kč" (spolehlivý údaj u zájezdů).
    # Fallback na jakékoliv číslo + Kč je záměrně vynechán, protože u
    # hotelových přehledových karet slepoval nesouvisející čísla (např. 40000).
    m = re.search(r"\bod\s*([\d\s]{3,9})\s*Kč", text)
    if m:
        digits = re.sub(r"\s+", "", m.group(1))
        # Rozumné rozpětí ceny zájezdu na osobu (3 000 - 500 000 Kč).
        if digits.isdigit() and 3000 <= int(digits) <= 500000:
            return int(digits)
    return None


def format_price(value):
    return f"{value:,}".replace(",", " ") + " Kč"


def zprava_detail(title, card_text, source_label):
    """
    Sestaví přehledné tělo zprávy: hotel, termín, noci, strava, odlet, popis.
    """
    radky = []
    # Název hotelu - z title, doplněný z textu karty, kdyby byl title prázdný
    hotel = (title or "").strip()
    if not hotel or hotel.lower() in ("nabídka last minute", "hotel"):
        hotel = clean_card_text(card_text)[:60]
    # html.escape: názvy z webu mohou obsahovat &, < nebo > - bez escapování
    # by Telegram (parse_mode=HTML) zprávu odmítl a vůbec by nedorazila.
    radky.append(f"🏨 <b>{html.escape(hotel)}</b>")

    # Termín + počet nocí
    term = extract_term(card_text)
    noci = extract_nights(card_text)
    if term:
        radky.append(f"📅 {format_term(term)}")
    if noci is not None:
        radky.append(f"🌙 <b>{noci} nocí</b>")
    else:
        radky.append("🌙 ⚠️ <i>délka pobytu neuvedena – zkontroluj v odkazu</i>")

    # Strava (když ji karta uvádí) - helper zkouší delší názvy dřív,
    # takže "Ultra all inclusive" se správně rozliší od "All inclusive".
    strava = _strava_z_textu(card_text)
    if strava:
        radky.append(f"🍽 {strava}")

    # Odletové letiště (když ho karta uvádí) - hledáme kmenem, aby se
    # chytly i skloňované tvary ("z Prahy", "odlet z Brna").
    for kmen, nazev in [("prah", "Praha"), ("brn", "Brno"), ("ostrav", "Ostrava")]:
        if kmen in card_text.lower():
            radky.append(f"✈️ Odlet: {nazev}")
            break

    return "\n".join(radky)


def clean_card_text(text):
    """Pročistí text karty pro hezčí zprávu - odstraní balast a zdvojené mezery."""
    for junk in ["Informace", "Přidat do oblíbených", "Zobrazit detail zájezdu",
                 "Další Předchozí", "Více"]:
        text = text.replace(junk, " ")
    return re.sub(r"\s+", " ", text).strip()


def passes_airport_filter(text):
    if not LETISTE_FILTR:
        return True
    t = text.lower()
    # Když text obsahuje NĚKTERÝ z našich kmenů (Prah/Brn/Ostrav), sedí.
    # Kmeny chytí i skloňované tvary: "z Prahy", "odlet z Brna", "v Ostravě".
    if any(l.lower() in t for l in LETISTE_FILTR):
        return True
    # Když karta neuvádí žádné odletové letiště vůbec (typicky přehledové
    # hotelové karty bez termínu), nezahazujeme ji - odletiště stejně řeší
    # filtr přímo v URL (nl_transportation / vyhledávání z ČR).
    # I zde kmeny, aby "z Katovic" / "z Vídně" spolehlivě znamenalo "karta
    # letiště uvádí" a cizí odlet se správně zahodil.
    znama_letiste = ["prah", "brn", "ostrav", "katovic", "pardubic",
                     "katowic", "wien", "vídeň", "vídn", "bratislav",
                     "letiště", "odlet"]
    if not any(z in t for z in znama_letiste):
        return True
    return False


def passes_destination_filter(text):
    if not DESTINACE_FILTR:
        return True
    return any(d.lower() in text.lower() for d in DESTINACE_FILTR)


def passes_hotel_filter(text):
    """
    True, pokud text obsahuje některý z HOTEL_FILTR jako samostatné slovo.
    Word-boundary hledání zabrání tomu, aby krátké "Jaz" chytlo "jazyk"/"jazz".
    """
    if not HOTEL_FILTR:
        return True
    for h in HOTEL_FILTR:
        if re.search(r"\b" + re.escape(h) + r"\b", text, re.IGNORECASE):
            return True
    return False


def passes_meal_filter(text):
    if not STRAVA_FILTR:
        return True
    return any(s.lower() in text.lower() for s in STRAVA_FILTR)


def passes_price_cap(price):
    if MAX_CENA is None or price is None:
        return True
    return price <= MAX_CENA


def _vsechna_data(text):
    """Najde v textu všechna data a vrátí je jako seřazený seznam datetime.date."""
    nalezena = []
    # dd.mm.yyyy / dd.m.yyyy / dd. mm. yyyy
    for m in re.finditer(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b", text):
        try:
            nalezena.append(datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except ValueError:
            pass
    # yyyy-mm-dd
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            nalezena.append(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    # dd.mm. bez roku (doplníme rok podle jiného nalezeného data nebo dneška)
    if len(nalezena) < 2:
        rok = nalezena[0].year if nalezena else datetime.date.today().year
        for m in re.finditer(r"\b(\d{1,2})\.\s*(\d{1,2})\.(?!\s*\d{4})", text):
            try:
                nalezena.append(datetime.date(rok, int(m.group(2)), int(m.group(1))))
            except ValueError:
                pass
    return sorted(set(nalezena))


def extract_term(text):
    """
    Vrátí termín zájezdu jako (datum_od, datum_do) nebo None.
    Bere první dvě data nalezená v kartě (odlet a návrat).
    """
    data = _vsechna_data(text)
    if len(data) >= 2:
        od, do = data[0], data[1]
        if 0 < (do - od).days <= 60:
            return (od, do)
    return None


def format_term(term):
    """Naformátuje termín: '15. 7. – 22. 7. 2026'."""
    if not term:
        return None
    od, do = term
    return f"{od.day}. {od.month}. – {do.day}. {do.month}. {do.year}"


def extract_nights(text):
    """
    Přečte počet nocí. Priorita:
      1) ROZSAH DAT (nejspolehlivější) - spočítá noci z rozdílu datumů
      2) 'X nocí'
      3) 'X dní/dnů' (Y dní = Y-1 nocí)
    Vrací int, nebo None když se délku nepodaří zjistit.
    """
    term = extract_term(text)
    if term:
        return (term[1] - term[0]).days

    m = re.search(r"\b(\d{1,2})\s*noc[íieí]*\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,2})\s*(dn[íi]|dn[ůu]|dní)\b", text, re.IGNORECASE)
    if m:
        dni = int(m.group(1))
        return dni - 1 if dni > 1 else dni
    return None


def passes_min_nights(text):
    """
    Zahodí jen nabídky, u kterých PROKAZATELNĚ víme, že jsou kratší než
    MIN_NOCI. Když délku nejde zjistit, nabídku PUSTÍME (a ve zprávě ji
    označíme ⚠️) - aby ti nic neuniklo a mohl sis to sám posoudit.
    """
    if MIN_NOCI is None:
        return True
    nights = extract_nights(text)
    if nights is None:
        return True  # neznámá délka -> raději pošli, ať nic neunikne
    return nights >= MIN_NOCI


def is_trusted_url(url):
    """True, pokud URL už sama vrací jen požadovanou zemi (filtr se přeskočí)."""
    return any(url.startswith(prefix) for prefix in DUVERYHODNE_EGYPT_URL)


# Pořadí je důležité: delší/specifičtější názvy dřív ("Ultra all inclusive"
# se musí zkusit před "All inclusive", jinak by se nikdy nenašel).
STRAVY = ["Ultra all inclusive", "All inclusive", "Plná penze",
          "Polopenze", "Snídaně", "Bez stravy"]


def _strava_z_textu(text):
    """Vrátí typ stravy nalezený v textu karty, nebo prázdný řetězec."""
    t = text.lower()
    for s in STRAVY:
        if s.lower() in t:
            return s
    return ""


def _normalizuj_titulek(title):
    """
    Stabilní otisk titulku pro klíč nabídky: malá písmena, bez číslic a
    mezer. Z názvu hotelu zbyde stabilní řetězec ("jazaquamarineresort"),
    z ceny v titulku ("od 15 880 Kč") zbyde jen neškodná konstanta.
    """
    t = re.sub(r"[\d\s\xa0]+", "", (title or "").lower())
    return t[:60]


def _hotel_ze_slugu(url):
    """
    Z Invia URL hotelu vytáhne čitelné jméno:
    .../hotel/egypt/marsa-alam/jaz-solaya-resort/ -> "Jaz Solaya Resort"
    """
    m = re.search(r"/hotel/[^/]+/[^/]+/([^/?#]+)", url)
    if not m:
        return ""
    return m.group(1).replace("-", " ").strip().title()


def make_offer_key(source, base_path, card_text, title=""):
    """
    Klíč nabídky. Na Invii vedou VŠECHNY karty na /zajezd/?s_offer_id=...,
    takže base_path je pro všechny stejný - klíč proto musí obsahovat i
    celý termín (od-do), stravu a titulek. Jinak se různé termíny téhož
    hotelu přepisují navzájem a bot hlásí falešná zlevnění/zdražení
    (ping-pong stejné částky tam a zpět).
    """
    term = extract_term(card_text)
    if term:
        date_part = f"{term[0].isoformat()}_{term[1].isoformat()}"
    else:
        date_match = re.search(r"\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}", card_text)
        date_part = date_match.group(0) if date_match else ""
    strava = _strava_z_textu(card_text)
    titulek = _normalizuj_titulek(title)
    return f"{source}:{short_hash(f'{base_path}|{date_part}|{strava}|{titulek}')}"


def stats_note_new(stats, price, title):
    stats["novych"] += 1
    if price and (stats["nejlevnejsi"] is None or price < stats["nejlevnejsi"]["cena"]):
        stats["nejlevnejsi"] = {"cena": price, "titulek": title}


def stats_note_discount(stats, sleva, price, title):
    stats["zlevneni"] += 1
    if stats["nejvetsi_sleva"] is None or sleva > stats["nejvetsi_sleva"]["castka"]:
        stats["nejvetsi_sleva"] = {"castka": sleva, "titulek": title}
    if price and (stats["nejlevnejsi"] is None or price < stats["nejlevnejsi"]["cena"]):
        stats["nejlevnejsi"] = {"cena": price, "titulek": title}


def send_weekly_summary(stats):
    lines = ["📊 <b>Týdenní přehled last minute bota</b>"]
    lines.append(f"🆕 Nových nabídek: {stats['novych']}")
    lines.append(f"🔻 Zaznamenaných zlevnění: {stats['zlevneni']}")
    if stats["nejvetsi_sleva"]:
        s = stats["nejvetsi_sleva"]
        lines.append(f"🏅 Největší sleva: {format_price(s['castka'])} – {html.escape(s['titulek'])}")
    if stats["nejlevnejsi"]:
        n = stats["nejlevnejsi"]
        lines.append(f"💸 Nejlevnější nabídka: {format_price(n['cena'])} – {html.escape(n['titulek'])}")
    if stats["novych"] == 0 and stats["zlevneni"] == 0:
        lines.append("Tento týden se neobjevilo nic nového.")
    send_telegram("\n".join(lines))


def prune_seen(seen, updates, today_str, max_age_days=60):
    """
    Úklid paměti: nabídkám viděným v tomto běhu nastaví dnešní datum,
    záznamy neviděné déle než max_age_days smaže (last minute nabídky
    dávno zmizely, není důvod je držet - seen.json by jinak rostl navěky).
    """
    for v in updates.values():
        v["d"] = today_str
    cutoff = datetime.date.fromisoformat(today_str) - datetime.timedelta(days=max_age_days)
    out = {}
    for k, v in seen.items():
        d = v.get("d")
        if d is None:
            v["d"] = today_str  # starší záznamy bez data dostanou dnešek
            out[k] = v
            continue
        try:
            if datetime.date.fromisoformat(d) >= cutoff:
                out[k] = v
        except ValueError:
            v["d"] = today_str
            out[k] = v
    return out


def process_offer(source, source_label, base_url, seen, updates, stats, notify,
                  href, title, card_text, trusted=False):
    # Filtr hotelového řetězce (Jaz) platí VŽDY - i na důvěryhodných URL.
    # Kontrolujeme text karty, NÁZEV (title) i URL odkazu, protože název
    # hotelu (např. "jaz-elite-riviera") bývá jen v odkazu, ne v textu karty.
    hotel_haystack = f"{card_text} {title} {href.replace('-', ' ')}"
    if not passes_hotel_filter(hotel_haystack):
        return 0
    if not passes_airport_filter(card_text):
        return 0
    # U důvěryhodných URL (už vyfiltrované na zemi) filtr destinací přeskočíme.
    # Destinaci hledáme i v URL (název letoviska bývá v cestě odkazu).
    dest_haystack = f"{card_text} {href.replace('-', ' ')}"
    if not trusted and not passes_destination_filter(dest_haystack):
        return 0
    if not passes_meal_filter(card_text):
        return 0
    if not passes_min_nights(card_text):
        return 0

    price = extract_price(card_text)
    if not passes_price_cap(price):
        return 0

    base_path = href.split("?")[0]
    key = make_offer_key(source, base_path, card_text, title)
    link = href if href.startswith("http") else base_url + href
    price_to_store = price if price is not None else 0

    if key not in seen and key not in updates:
        updates[key] = {"ref": price_to_store, "min": price_to_store}
        stats_note_new(stats, price, title)
        if notify:
            cena_radek = f"\n💰 <b>{format_price(price)}</b>" if price else "\n💰 <i>cena neuvedena</i>"
            send_telegram(
                f"🆕 <b>NOVÁ NABÍDKA</b> · {source_label}\n"
                f"{zprava_detail(title, card_text, source_label)}"
                f"{cena_radek}",
                link=link,
            )
        return 1

    entry = updates.get(key) or seen.get(key)
    old_ref = entry.get("ref", 0)
    old_min = entry.get("min", 0)

    # ZLEVNĚNÍ 🔻
    if price and old_ref and price < old_ref:
        sleva = old_ref - price
        is_record = bool(old_min) and price < old_min
        new_min = min(price, old_min) if old_min else price
        updates[key] = {"ref": price, "min": new_min}
        stats_note_discount(stats, sleva, price, title)
        if notify:
            badge = "\n🏆 <b>NEJNIŽŠÍ CENA, JAKOU JSEM U TÉTO NABÍDKY VIDĚL!</b>" if is_record else ""
            send_telegram(
                f"🔻🟥 <b>ZLEVNĚNÍ o {format_price(sleva)}</b> · {source_label}{badge}\n"
                f"{zprava_detail(title, card_text, source_label)}\n"
                f"💰 <s>{format_price(old_ref)}</s> → <b>{format_price(price)}</b>",
                link=link,
            )
        return 1

    # ZDRAŽENÍ 🔺
    if OZNAMOVAT_ZDRAZENI and price and old_ref and price > old_ref:
        zdrazeni = price - old_ref
        updates[key] = {"ref": price, "min": old_min if old_min else price}
        if notify:
            send_telegram(
                f"🔺🟩 <b>ZDRAŽENÍ o {format_price(zdrazeni)}</b> · {source_label}\n"
                f"{zprava_detail(title, card_text, source_label)}\n"
                f"💰 <s>{format_price(old_ref)}</s> → <b>{format_price(price)}</b>",
                link=link,
            )
        return 1

    # Beze změny: minimum držíme.
    if price:
        new_min = min(price, old_min) if old_min else price
        updates[key] = {"ref": price, "min": new_min}
    return 0


# Plný UA řetězec reálného Chromu - holé "Mozilla/5.0 (Windows...)" vypadá
# botovsky a některé weby (Dovolenkovani) na něj odmítaly odpovědět.
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Obrázky, fonty a videa bot nepotřebuje (čte jen text/odkazy) - jejich
# blokování výrazně zrychlí načítání a šetří minuty GitHub Actions.
_BLOKOVANE_ZDROJE = {"image", "font", "media"}


def _blokuj_zbytecne(route):
    if route.request.resource_type in _BLOKOVANE_ZDROJE:
        route.abort()
    else:
        route.continue_()


def fetch_rendered_html(browser, url):
    page = browser.new_page(user_agent=_USER_AGENT)
    try:
        page.route("**/*", _blokuj_zbytecne)
        # Nečekáme na "networkidle" - weby s reklamami/trackingem mají trvalou
        # aktivitu na pozadí a síť nikdy neztichne (Exim, Fischer, Dovolenkovani
        # kvůli tomu padaly na timeout). Počkáme na načtení dokumentu a pak
        # dáme JS čas nabídky dopočítat.
        # goto zkoušíme 2x - pomalé weby občas první pokus nestihnou.
        posledni = None
        for pokus in range(2):
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                posledni = None
                break
            except Exception as e:
                posledni = e
                print(f"  goto pokus {pokus + 1} selhal ({url[:80]}...), zkouším znovu")
        if posledni is not None:
            raise posledni
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass  # síť neztichla - nevadí, pokračujeme
        # Cookie/consent dialog: jeden kombinovaný dotaz místo smyčky přes
        # 8 selektorů po 2 s (ta na stránkách bez dialogu pálila až 16 s).
        consent_selector = (
            "button:has-text('Souhlasím'), button:has-text('Rozumím'), "
            "button:has-text('Přijmout'), button:has-text('Povolit'), "
            "button:has-text('Accept'), #didomi-notice-agree-button"
        )
        try:
            page.locator(consent_selector).first.click(timeout=2500)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(1000)
        except Exception:
            pass  # žádný dialog - jedeme dál
        page.wait_for_timeout(2000)
        html = None
        last_error = None
        for attempt in range(3):
            try:
                html = page.content()
                break
            except Exception as e:
                last_error = e
                page.wait_for_timeout(1500)
        if html is None:
            raise last_error
    finally:
        page.close()
    return html


def diagnostika_vypis(soup, zdroj):
    """Vypíše do logu ukázku odkazů na stránce - pomůcka pro doladění vzoru."""
    if not DIAGNOSTIKA_ODKAZU:
        return
    html_text = str(soup)
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    pocet_zajezd = html_text.lower().count("s_offer_id")
    pocet_cookie = html_text.lower().count("souhlas") + html_text.lower().count("cookie")
    print(f"  [DIAG {zdroj}] délka HTML: {len(html_text)} znaků, "
          f"odkazů: {len(hrefs)}, výskytů 's_offer_id': {pocet_zajezd}, "
          f"cookie/souhlas: {pocet_cookie}")
    zajimave = []
    videno = set()
    for h in hrefs:
        if h in videno:
            continue
        videno.add(h)
        if any(k in h.lower() for k in ["zajezd", "hotel", "detail", "nabidka", "s_offer_id"]):
            zajimave.append(h)
    print(f"  [DIAG {zdroj}] kandidátů na nabídku: {len(zajimave)}")
    for h in zajimave[:15]:
        print(f"  [DIAG {zdroj}] {h[:150]}")

    # Navíc: u nabídek, které projdou vzorem, vypíšeme card_text a proč se
    # (ne)pošlou - tím poznáme, jestli je zahazuje filtr nebo špatný text.
    if zdroj == "Invia":
        detail = re.compile(r"/zajezd/\?s_offer_id=", re.IGNORECASE)
        vzorek = parse_offers_from_soup(soup, detail)
        # Kolik z nabídek obsahuje "Jaz"?
        jaz_pocet = sum(1 for _, _, ct in vzorek if passes_hotel_filter(ct))
        print(f"  [DIAG {zdroj}] nabídek přes vzor: {len(vzorek)}, z toho JAZ: {jaz_pocet}")
        # Ukážeme prvních pár Jaz nabídek (nebo když žádná, tak první 3 vůbec)
        jaz_offers = [(h, t, ct) for h, t, ct in vzorek if passes_hotel_filter(ct)]
        ukazat = jaz_offers[:3] if jaz_offers else vzorek[:3]
        for href, title, card_text in ukazat:
            print(f"  [DIAG {zdroj}] --- karta: {title}")
            print(f"  [DIAG {zdroj}]     text: {card_text[:200]}")
            print(f"  [DIAG {zdroj}]     hotel(Jaz)={passes_hotel_filter(card_text)} "
                  f"letiště={passes_airport_filter(card_text)} "
                  f"dest={passes_destination_filter(card_text)} "
                  f"noci={passes_min_nights(card_text)} "
                  f"cena={extract_price(card_text)}")


def parse_offers_from_soup(soup, detail_pattern, min_text_len=0):
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not detail_pattern.search(href):
            continue
        title = a.get_text(strip=True) or "Nabídka last minute"

        # Text karty: nejbližší rodič často obsahuje jen název hotelu.
        # Informace o termínu/letišti/ceně jsou ve větším nadřazeném bloku,
        # proto lezeme po rodičích nahoru, dokud text nezačne obsahovat cenu
        # (Kč) nebo datum, nebo dokud nedosáhneme rozumné velikosti.
        card_text = ""
        node = a
        for _ in range(6):  # max 6 úrovní nahoru
            parent = node.find_parent(["article", "li", "div", "section"])
            if parent is None:
                break
            text = parent.get_text(" ", strip=True)
            node = parent
            if ("Kč" in text) or re.search(r"\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}", text):
                card_text = text[:400]
                break
            card_text = text[:400]  # zapamatuj poslední (kdyby cena nikde nebyla)

        if not card_text:
            card_text = (a.get_text(" ", strip=True) or "")[:400]
        if len(card_text) < min_text_len:
            continue
        results.append((href, title, card_text))
    return results


def _je_vyhledavaci_url(url):
    """Vyhledávací URL podporují stránkování (page=N), statické stránky ne."""
    return any(k in url for k in ("s_action=", "nl_country_id=", "search_form"))


def _url_se_strankou(url, page):
    """Vrátí URL pro danou stránku výsledků (page=1 vrací původní URL)."""
    if page == 1:
        return url
    if "page=" in url:
        return re.sub(r"([?&])page=\d+", rf"\g<1>page={page}", url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}page={page}"


def check_invia(seen, updates, stats, notify, browser):
    detail_pattern = re.compile(r"/zajezd/\?s_offer_id=", re.IGNORECASE)

    # 1) Vyhledávací a last-minute stránky (se stránkováním u vyhledávacích)
    for url in INVIA_SEARCH_URLS:
        max_stranek = INVIA_MAX_STRANEK if _je_vyhledavaci_url(url) else 1
        found_celkem = 0
        karet_celkem = 0
        for page in range(1, max_stranek + 1):
            page_url = _url_se_strankou(url, page)
            try:
                page_html = fetch_rendered_html(browser, page_url)
            except Exception as e:
                print(f"Invia chyba ({page_url}): {e}")
                break
            soup = BeautifulSoup(page_html, "html.parser")
            if page == 1:
                diagnostika_vypis(soup, "Invia")
            offers = parse_offers_from_soup(soup, detail_pattern)
            if not offers:
                # Stránka bez nabídek = konec výsledků, dál nelistujeme.
                break
            karet_celkem += len(offers)
            for href, title, card_text in offers:
                found_celkem += process_offer(
                    "invia", "Invia.cz", "https://www.invia.cz",
                    seen, updates, stats, notify, href, title, card_text,
                    trusted=is_trusted_url(url))
        strany = f" (prošel až {max_stranek} stránek)" if max_stranek > 1 else ""
        # "0 karet" = parsování/web nefunguje; "X karet, 0 nových" = jen nic nového.
        print(f"Invia ({url}){strany}: {karet_celkem} karet, "
              f"{found_celkem} nových/zlevněných.")

    # 2) Přímé stránky Jaz hotelů - jen Jaz, termíny konkrétního hotelu.
    #    Jsou to egyptské Jaz stránky, takže trusted=True (destinaci neřešíme;
    #    filtr Jaz stejně platí vždy a projde díky slugu v URL).
    for url in INVIA_JAZ_HOTEL_URLS:
        try:
            page_html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Invia Jaz hotel chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        found = 0
        # Jméno hotelu ze slugu URL: anchor text karet na hotelové stránce
        # je totiž CENA ("od 15 880 Kč"), ne název - bez náhrady by cena
        # skončila ve zprávě jako 🏨 název i v klíči nabídky.
        hotel_ze_slugu = _hotel_ze_slugu(url)
        offers = parse_offers_from_soup(soup, detail_pattern)
        for href, title, card_text in offers:
            if hotel_ze_slugu:
                title = hotel_ze_slugu
            found += process_offer("invia", "Invia.cz (Jaz hotel)", "https://www.invia.cz",
                                   seen, updates, stats, notify, href, title, card_text,
                                   trusted=True)
        print(f"Invia Jaz hotel ({url}): {len(offers)} karet, {found} nových/zlevněných.")


def check_bluestyle(seen, updates, stats, notify, browser):
    # /zajezd = konkrétní zájezdy; hotel[-/] = hotelové stránky z fulltextu
    detail_pattern = re.compile(r"/(zajezd|hotel[-/])", re.IGNORECASE)
    for url in BLUESTYLE_SEARCH_URLS:
        try:
            page_html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Blue Style chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        found = 0
        offers = parse_offers_from_soup(soup, detail_pattern)
        for href, title, card_text in offers:
            found += process_offer("bluestyle", "Blue Style", "https://www.blue-style.cz",
                                   seen, updates, stats, notify, href, title, card_text,
                                   trusted=is_trusted_url(url))
        print(f"Blue Style ({url}): {len(offers)} karet, {found} nových/zlevněných.")


def check_eximtours(seen, updates, stats, notify, browser):
    detail_pattern = re.compile(r"/(zajezd|hotel)[-/]", re.IGNORECASE)
    for url in EXIMTOURS_SEARCH_URLS:
        try:
            page_html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Exim Tours chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        diagnostika_vypis(soup, "Exim Tours")
        found = 0
        offers = parse_offers_from_soup(soup, detail_pattern, min_text_len=15)
        for href, title, card_text in offers:
            found += process_offer("eximtours", "Exim Tours", "https://www.eximtours.cz",
                                   seen, updates, stats, notify, href, title, card_text,
                                   trusted=is_trusted_url(url))
        print(f"Exim Tours ({url}): {len(offers)} karet, {found} nových/zlevněných.")


def check_fischer(seen, updates, stats, notify, browser):
    detail_pattern = re.compile(r"/(zajezd|hotel)[-/]", re.IGNORECASE)
    for url in FISCHER_SEARCH_URLS:
        try:
            page_html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Fischer chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        diagnostika_vypis(soup, "Fischer")
        found = 0
        offers = parse_offers_from_soup(soup, detail_pattern, min_text_len=15)
        for href, title, card_text in offers:
            found += process_offer("fischer", "Fischer", "https://www.fischer.cz",
                                   seen, updates, stats, notify, href, title, card_text,
                                   trusted=is_trusted_url(url))
        print(f"Fischer ({url}): {len(offers)} karet, {found} nových/zlevněných.")


def check_dovolenkovani(seen, updates, stats, notify, browser):
    # Detail zájezdu/hotelu poznáme podle "zajezd" nebo "hotel" v cestě
    # odkazu; nerelevantní odkazy (navigace) spolehlivě odfiltruje HOTEL_FILTR.
    detail_pattern = re.compile(r"/(zajezd|hotel)[-/]?", re.IGNORECASE)
    for url in DOVOLENKOVANI_SEARCH_URLS:
        try:
            page_html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Dovolenkovani chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        found = 0
        offers = parse_offers_from_soup(soup, detail_pattern, min_text_len=15)
        for href, title, card_text in offers:
            found += process_offer("dovolenkovani", "Dovolenkovani.cz", "https://dovolenkovani.cz",
                                   seen, updates, stats, notify, href, title, card_text,
                                   trusted=is_trusted_url(url))
        print(f"Dovolenkovani ({url}): {len(offers)} karet, {found} nových/zlevněných.")


def main():
    # Řádkové flushování stdout - v GitHub Actions je jinak výstup vidět až
    # na konci běhu a nejde sledovat průběh ani poznat, kde běh visí.
    sys.stdout.reconfigure(line_buffering=True)

    seen = load_seen()
    # První běh = soubor neexistuje NEBO se nepodařilo nic načíst (poškozený
    # seen.json). Jinak by se po poškození souboru poslala záplava "nových"
    # nabídek (limit MAX_ZPRAV_ZA_BEH je až druhá pojistka).
    first_run = not os.path.exists(SEEN_FILE) or not seen
    updates = {}

    now = datetime.datetime.now(datetime.timezone.utc)
    iso = now.isocalendar()
    current_week = f"{iso[0]}-W{iso[1]:02d}"
    stats = load_stats(current_week)

    # Přelom týdne: pošleme souhrn za minulý týden a začneme počítat znovu.
    if stats.get("week") != current_week:
        if not first_run:
            send_weekly_summary(stats)
        stats = default_stats(current_week)

    if first_run:
        print("První spuštění – ukládám aktuální nabídky, ale zprávy zatím neposílám.")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            # Každý zdroj běží samostatně - pád jednoho nezastaví ostatní.
            zdroje = [
                ("Invia", check_invia),
                ("Blue Style", check_bluestyle),
                ("Exim Tours", check_eximtours),
                ("Fischer", check_fischer),
                ("Dovolenkovani", check_dovolenkovani),
            ]
            for nazev, fn in zdroje:
                try:
                    fn(seen, updates, stats, notify=not first_run, browser=browser)
                except Exception as e:
                    print(f"CHYBA zdroje {nazev} (pokračuji dalšími): {e}")
        finally:
            browser.close()

    today_str = now.date().isoformat()
    seen.update(updates)
    seen = prune_seen(seen, updates, today_str)
    save_seen(seen)
    save_stats(stats)
    if updates:
        print(f"Zpracováno {len(updates)} nových/aktualizovaných nabídek.")
    else:
        print("Žádné nové nabídky.")

    # Pokud pojistka potlačila zprávy, pošli o tom JEDNO upozornění
    # (mimo limit, přes _telegram_post) - ať víš, že se máš podívat do logu.
    if _potlaceno_zprav:
        _telegram_post(
            f"⚠️ Dosažen limit {MAX_ZPRAV_ZA_BEH} zpráv za běh - "
            f"{_potlaceno_zprav} dalších zpráv bylo potlačeno. "
            f"Podrobnosti najdeš v logu GitHub Actions."
        )


if __name__ == "__main__":
    main()
