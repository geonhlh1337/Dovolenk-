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
MAX_ZPRAV_ZA_BEH = 100

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

# Minimální změna ceny (Kč), aby přišla zpráva o zlevnění/zdražení.
# Weby s cenami drobně "šumí" (přepočty kurzu, palivové příplatky) a bot
# by jinak posílal ping-pong zpráv o pár desetikorunách. Změny menší než
# tento práh se NEHLÁSÍ a referenční cena se NEMĚNÍ - drobné poklesy se
# tak sčítají a zpráva přijde, jakmile celkový rozdíl práh překročí.
# (Historické minimum se ale sleduje vždy, i u malých poklesů.)
# 0 = hlásit každou změnu jako dřív.
MIN_ZMENA_CENY = 300

# Hlídání ZMIZELÝCH nabídek: když nabídka, kterou bot zná, nepřijde
# v ZMIZENI_PO_BEZICH bězích po sobě, pošle se ⌛ upozornění (nejspíš
# vyprodáno / stažena). Počítá se jen u nabídek viděných v posledních
# 14 dnech a jen když běh přečetl dost karet (ochrana proti falešným
# poplachům při výpadku webu). Užitečný signál "příště neváhej".
OZNAMOVAT_ZMIZENI = True
ZMIZENI_PO_BEZICH = 3

# Denní digest: jedna ranní zpráva s TOP 5 nejlevnějšími Jaz nabídkami
# přepočtenými na cenu za NOC (z nabídek viděných tentýž den; každý hotel
# nejvýš jednou). Hodina je v UTC: 5 UTC = 7:00 letního / 6:00 zimního
# českého času. Vypnutí: DENNI_DIGEST = False.
DENNI_DIGEST = True
DIGEST_HODINA_UTC = 5

# Číst i strukturovaná data JSON-LD ze stránek (ceny, které weby vkládají
# pro Google)? Doplní cenu u karet, kde ji nejde přečíst z textu. Nic
# nepřidává navíc, jen zpřesňuje - a přežije i změnu vzhledu webu.
POUZIT_JSONLD = True

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
    "https://www.cedok.cz/dovolena/egypt",
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
# Repozitář je VEŘEJNÝ = minuty GitHub Actions se nepočítají, takže si
# můžeme dovolit 5 stránek (víc už obvykle nenosí nic - jsou to drahé
# nabídky na konci řazení podle ceny).
INVIA_MAX_STRANEK = 5

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
    # --- Sharm El Sheikh (ověřeno 07/2026) ---
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-mirabel-beach-resort/",
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-mirabel-park-club/",
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-mirabel-park/",
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-mirabel-club/",
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-fanara-resort-residence/",
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-fanara-resort/",
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-fanara-residence/",
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-sharm-dreams/",
    "https://www.invia.cz/hotel/egypt/sharm-el-sheikh/jaz-belvedere/",
    # --- Almaza Bay / Marsa Matrouh (ověřeno 07/2026) ---
    "https://www.invia.cz/hotel/egypt/marsa-matrouh/jaz-almaza-beach-resort/",
    "https://www.invia.cz/hotel/egypt/marsa-matrouh/jaz-almaza-blue/",
    "https://www.invia.cz/hotel/egypt/marsa-matrouh/jaz-viva-almaza-blue/",
    "https://www.invia.cz/hotel/egypt/marsa-matrouh/jaz-almazino/",
    "https://www.invia.cz/hotel/egypt/marsa-matrouh/jaz-sakhra/",
    "https://www.invia.cz/hotel/egypt/marsa-matrouh/jaz-tamerina/",
    "https://www.invia.cz/hotel/egypt/marsa-matrouh/jaz-oriental-resort/",
    "https://www.invia.cz/hotel/egypt/marsa-matrouh/jaz-crystal-resort/",
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

# --- Čedok ---
# Stránky letovisek s výpisem hotelů - vykreslené na SERVERU včetně cen
# (ověřeno 07/2026), takže parsování je spolehlivé. Karta hotelu ukazuje
# původní a aktuální cenu "/os." - bot sleduje tu aktuální. Filtr Jaz
# hotely vybere podle slugu v odkazu (hotel-jaz-...).
# POZOR: výpisy bydlí na /vysledky-vyhledavani/dovolena/<destinace>/
# (ověřeno 07/2026) - dřívější /dovolena/egypt/<letovisko>/ vrací 404.
CEDOK_SEARCH_URLS = [
    "https://www.cedok.cz/vysledky-vyhledavani/dovolena/egypt/",
    "https://www.cedok.cz/vysledky-vyhledavani/dovolena/marsa-matrouh/",
    "https://www.cedok.cz/vysledky-vyhledavani/dovolena/marsa-alam/",
    "https://www.cedok.cz/vysledky-vyhledavani/dovolena/hurghada/",
    "https://www.cedok.cz/vysledky-vyhledavani/dovolena/sharm-el-sheikh/",
]

# Přímé stránky Jaz hotelů na Čedoku - stejný princip jako INVIA_JAZ_HOTEL_URLS.
# Stránka hotelu obsahuje (vykreslené na serveru!) aktuální cenu za osobu
# a "Nejlepší možnost" s termínem a počtem nocí. Všechny URL OVĚŘENÉ 07/2026.
# Další Jaz hotel objevíš v logu z výpisů letovisek výše a přidáš sem.
CEDOK_JAZ_HOTEL_URLS = [
    "https://www.cedok.cz/dovolena/egypt/marsa-matrouh/hotel-jaz-almaza-beach-resort,MUH2JAB/",
    "https://www.cedok.cz/dovolena/egypt/marsa-matrouh/hotel-jaz-elite-crystal,MUH2CRY/",
    "https://www.cedok.cz/dovolena/egypt/marsa-matrouh/hotel-jaz-oriental-resort,MUH2JAO/",
    "https://www.cedok.cz/dovolena/egypt/marsa-matruh/hotel-jaz-almazino,MUH2JAZ/",
    "https://www.cedok.cz/dovolena/egypt/marsa-matrouh/hotel-jaz-sakhra,MUH2SAK/",
    "https://www.cedok.cz/dovolena/egypt/marsa-alam/hotel-jaz-riviera,RMF2RIV/",
    "https://www.cedok.cz/dovolena/egypt/sharm-el-sheikh/jaz-belvedere-hotel,AEGSSH11PU/",
]

# Přímé stránky Jaz hotelů na Eximu (tvar /egypt/<region>/<oblast>/<slug>).
# Termíny/ceny se dokreslují JavaScriptem - bot čte vyrenderovanou stránku
# a cenu bere z textu nebo z JSON-LD. Všechny URL OVĚŘENÉ 07/2026.
EXIM_JAZ_HOTEL_URLS = [
    "https://www.eximtours.cz/egypt/marsa-matruh/almaza-bay/jaz-oriental",
    "https://www.eximtours.cz/egypt/marsa-matruh/almaza-bay/jaz-sakhra",
    "https://www.eximtours.cz/egypt/marsa-matruh/almaza-bay/jaz-tamerina",
    "https://www.eximtours.cz/egypt/marsa-matruh/almaza-bay/jaz-almaza-blue",
    "https://www.eximtours.cz/egypt/marsa-matruh/almaza-bay/jaz-neo-casa-maza",
    "https://www.eximtours.cz/egypt/stredozemni-pobrezi/stredozemni-pobrezi/jaz-almaza-beach-resort",
    "https://www.eximtours.cz/egypt/marsa-alam/marsa-alam/jaz-amara",
    "https://www.eximtours.cz/egypt/marsa-alam/marsa-alam/jaz-samaya-resort",
    "https://www.eximtours.cz/egypt/marsa-alam/el-quseir/jaz-grand-resort",
]

# --- Exim Tours a Fischer ---
# VYPNUTO podle reálných logů (07/2026): Fischer vracel 0 karet na všech
# URL (nejspíš blokace/jiná struktura) a Exim jen 1-8 karet bez jediného
# úlovku. Nabídky OBOU CK přitom chodí přes Invii (agreguje 120+ CK včetně
# hotelových stránek Jaz), takže o nic nepřicházíš - jen se šetří ~1,5 min
# za běh. Kdyby ses chtěl vrátit, odkomentuj URL níže.
EXIMTOURS_SEARCH_URLS = [
    # ZNOVU ZAPNUTO (07/2026) na přání - sleduj v logu řádky "Exim Tours":
    # když budou dlouhodobě "0 karet", web bota blokuje a má smysl je zase
    # vypnout (nabídky Eximu stejně chodí přes Invii).
    "https://www.eximtours.cz/hledani-vysledky?q=Jaz",
    "https://www.eximtours.cz/last-minute/egypt",
    # Stránka destinace Egypt - obsahuje výpis hotelů s odkazy na detaily:
    "https://www.eximtours.cz/egypt",
    # "https://www.eximtours.cz/vysledky-vyhledavani?ac1=2&d=64419|64420|64423&dd=2026-07-11&m=5&nn=1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20|21&rd=2026-09-10&to=4312|4305|2682|4308|4392|4309&tt=1",
]

FISCHER_SEARCH_URLS = [
    # ZNOVU ZAPNUTO (07/2026) na přání - sleduj v logu řádky "Fischer":
    # dřív vracel 0 karet (blokace/jiná struktura webu). Když se to bude
    # opakovat, zase zakomentuj (nabídky Fischeru chodí i přes Invii).
    "https://www.fischer.cz/hledani-vysledky?q=Jaz",
    "https://www.fischer.cz/last-minute/egypt",
    # "https://www.fischer.cz/vysledky-vyhledavani?ac1=2&d=64419|64420|64423&dd=2026-07-11&nn=1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20|21&rd=2026-09-10&to=4312|4305|2682&tt=1",
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
    STATS_KLICE = {"week", "novych", "zlevneni", "zdrazeni",
                   "nejvetsi_sleva", "nejlevnejsi"}
    if STATS_KLICE & set(data.keys()):
        print("VAROVÁNÍ: seen.json obsahoval data statistik - resetuji na prázdný.")
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(v, dict):
            zaznam = {"ref": v.get("ref", 0), "min": v.get("min", 0)}
            # Metadata: d=datum posledního spatření, t=titulek, n=noci,
            # u=odkaz, miss=počet běhů bez spatření, gone=ohlášeno zmizení.
            for extra in ("d", "t", "n", "u", "miss", "gone"):
                if extra in v:
                    zaznam[extra] = v[extra]
            out[k] = zaznam
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
        "zdrazeni": 0,
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
    m = re.search(r"\bod\s*([\d\s.]{3,9})\s*Kč", text)
    if m:
        digits = re.sub(r"[\s.]+", "", m.group(1))
        # Rozumné rozpětí ceny zájezdu na osobu (3 000 - 500 000 Kč).
        if digits.isdigit() and 3000 <= int(digits) <= 500000:
            return int(digits)
    # Cena "za osobu" - Čedok píše "31 756 Kč 15 190 Kč /os." (přeškrtnutá
    # původní + aktuální). Bereme číslo TĚSNĚ PŘED "/os." = aktuální cena;
    # obecný fallback níže by vzal první číslo, tedy tu starou přeškrtnutou.
    m = re.search(r"(?<!\d)(\d{1,3}(?:[\s.]\d{3})+)\s*Kč\s*/\s*os", text)
    if m:
        digits = re.sub(r"[\s.]+", "", m.group(1))
        if digits.isdigit() and 3000 <= int(digits) <= 500000:
            return int(digits)
    # Fallback pro weby, které "od" nepíšou (Blue Style: "13 dní 28 790 Kč").
    # Vyžadujeme ČÍSLO S ODDĚLENÝMI TISÍCI těsně před "Kč" - to nesplní
    # slepence nesouvisejících čísel (obava původního kódu, např. "40294"),
    # ale reálná cena "28 790 Kč" ano. Bereme první výskyt na kartě.
    m = re.search(r"(?<!\d)(\d{1,3}(?:[\s.]\d{3})+)\s*Kč", text)
    if m:
        digits = re.sub(r"[\s.]+", "", m.group(1))
        if digits.isdigit() and 3000 <= int(digits) <= 500000:
            return int(digits)
    return None


def format_price(value):
    return f"{value:,}".replace(",", " ") + " Kč"


# Kolik karet nabídek běh celkem přečetl - ochrana hlídání zmizelých
# nabídek: když weby nevrátí skoro nic (výpadek), zmizení se nepočítá.
_karet_parsovano = 0


def _pridej_meta(entry, title, card_text, link):
    """
    Doplní do záznamu v seen.json metadata pro denní digest a hlídání
    zmizelých nabídek: t=titulek, n=počet nocí, u=odkaz.
    """
    t = (title or "").strip()
    if t:
        entry["t"] = t[:60]
    n = extract_nights(card_text, link)
    if n:
        entry["n"] = n
    if link:
        entry["u"] = link
    return entry


def _za_noc(price, card_text, url=""):
    """
    Vrátí ' · 2 268 Kč/noc' - přepočet ceny na noc, když známe cenu i délku
    pobytu. Skvělé pro rychlé porovnání nabídek s různým počtem nocí.
    """
    noci = extract_nights(card_text, url)
    if price and noci:
        return f" · {format_price(round(price / noci))}/noc"
    return ""


def zprava_detail(title, card_text, source_label, url=""):
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

    # Termín · počet nocí na JEDNOM řádku - kompaktnější a přehlednější
    term = extract_term(card_text)
    noci = extract_nights(card_text, url)
    radek_termin = []
    if term:
        radek_termin.append(f"📅 {format_term(term)}")
    if noci is not None:
        radek_termin.append(f"🌙 <b>{noci} nocí</b>")
    if radek_termin:
        radky.append(" · ".join(radek_termin))
    if noci is None:
        radky.append("⚠️ <i>délka pobytu neuvedena – zkontroluj v odkazu</i>")

    # Strava · odlet na JEDNOM řádku (jen ty údaje, které karta uvádí).
    # Helper stravy zkouší delší názvy dřív, takže "Ultra all inclusive"
    # se správně rozliší od "All inclusive"; letiště hledáme kmenem,
    # aby se chytly i skloňované tvary ("z Prahy", "odlet z Brna").
    radek_info = []
    strava = _strava_z_textu(card_text)
    if strava:
        radek_info.append(f"🍽 {strava}")
    for kmen, nazev in [("prah", "Praha"), ("brn", "Brno"), ("ostrav", "Ostrava")]:
        if kmen in card_text.lower():
            radek_info.append(f"✈️ {nazev}")
            break
    if radek_info:
        radky.append(" · ".join(radek_info))

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
    # (?<!\d) místo \b: text bývá slepený ("St04. 11. 2026" - mezi "t" a "0"
    # není hranice slova, takže \b by datum nenašlo).
    for m in re.finditer(r"(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b", text):
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
        for m in re.finditer(r"(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.(?!\s*\d{4})", text):
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


def extract_nights(text, url=""):
    """
    Přečte počet nocí. Priorita:
      0) URL parametr nl_nights= (Invia ho má v každém odkazu - NEJPŘESNĚJŠÍ,
         nezávisí na tom, co je zrovna vidět v textu karty)
      1) ROZSAH DAT - spočítá noci z rozdílu datumů
      2) 'X nocí'
      3) 'X dní/dnů' (Y dní = Y-1 nocí)
    Vrací int, nebo None když se délku nepodaří zjistit.
    """
    if url:
        m = re.search(r"[?&]nl_nights=(\d{1,2})\b", url)
        if m:
            return int(m.group(1))

    term = extract_term(text)
    if term:
        return (term[1] - term[0]).days

    # (?<!\d) místo \b: weby často slepí text bez mezer ("Brno4 dny",
    # "Praha7 nocí") a \b tam mezi písmenem a číslicí NENÍ - regex by selhal.
    # Koncové \b nefunguje, když hned za slovem následuje číslice ceny
    # ("4 dny19 190Kč") - mezi "y" a "1" hranice slova není. Proto lookahead
    # na "nenásleduje písmeno".
    _pis = "a-záčďéěíňóřšťúůýžA-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
    m = re.search(rf"(?<!\d)(\d{{1,2}})\s*noc[íie]*(?![{_pis}])", text,
                  re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Všechny české tvary: den, dny, dní, dnů, dnu, dni, dnech
    # (původní vzor neuměl "4 dny" - nejčastější tvar u Blue Style!)
    m = re.search(rf"(?<!\d)(\d{{1,2}})\s*(?:dnech|dny|dní|dnů|dnu|dni|den)(?![{_pis}])",
                  text, re.IGNORECASE)
    if m:
        dni = int(m.group(1))
        return dni - 1 if dni > 1 else dni
    return None


def passes_min_nights(text, url=""):
    """
    Zahodí jen nabídky, u kterých PROKAZATELNĚ víme, že jsou kratší než
    MIN_NOCI. Když délku nejde zjistit, nabídku PUSTÍME (a ve zprávě ji
    označíme ⚠️) - aby ti nic neuniklo a mohl sis to sám posoudit.
    """
    if MIN_NOCI is None:
        return True
    nights = extract_nights(text, url)
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
    lines = ["📊 <b>TÝDENNÍ PŘEHLED</b>"]
    lines.append(f"🆕 Nové nabídky: <b>{stats['novych']}</b>"
                 f" · 🟢 Zlevnění: <b>{stats['zlevneni']}</b>"
                 f" · 🔴 Zdražení: <b>{stats.get('zdrazeni', 0)}</b>")
    if stats["nejvetsi_sleva"]:
        s = stats["nejvetsi_sleva"]
        lines.append(f"🏅 Největší sleva: <b>{format_price(s['castka'])}</b>\n"
                     f"     {html.escape(s['titulek'])}")
    if stats["nejlevnejsi"]:
        n = stats["nejlevnejsi"]
        lines.append(f"💸 Nejlevnější nabídka: <b>{format_price(n['cena'])}</b>\n"
                     f"     {html.escape(n['titulek'])}")
    if stats["novych"] == 0 and stats["zlevneni"] == 0:
        lines.append("<i>Tento týden se neobjevilo nic nového.</i>")
    send_telegram("\n".join(lines))


def extract_jsonld_prices(soup):
    """
    Vytáhne ceny ze strukturovaných dat <script type="application/ld+json">
    (data, která weby vkládají pro Google). Vrací slovník {url: cena}.
    Odolnější než čtení textu karet - přežije i změnu vzhledu webu.
    """
    ceny = {}

    def projdi(obj):
        if isinstance(obj, dict):
            url = obj.get("url") or obj.get("@id")
            cena = obj.get("price") or obj.get("lowPrice")
            offers_pole = obj.get("offers")
            if cena is None and isinstance(offers_pole, dict):
                cena = offers_pole.get("price") or offers_pole.get("lowPrice")
            if isinstance(url, str) and cena is not None:
                try:
                    c = int(float(str(cena).replace(" ", "").replace(",", ".")))
                    if 3000 <= c <= 500000:
                        ceny[url] = c
                except (ValueError, TypeError):
                    pass
            for v in obj.values():
                projdi(v)
        elif isinstance(obj, list):
            for v in obj:
                projdi(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            projdi(json.loads(tag.string or ""))
        except Exception:
            continue
    return ceny


def doplnit_ceny_z_jsonld(soup, offers, base_url):
    """
    U karet BEZ čitelné ceny zkusí doplnit cenu ze strukturovaných dat
    JSON-LD (párování podle URL nabídky). Nové nabídky nevytváří, jen
    zpřesňuje existující karty.
    """
    if not POUZIT_JSONLD or not offers:
        return offers
    ld = extract_jsonld_prices(soup)
    if not ld:
        return offers
    out = []
    doplneno = 0
    for href, title, card_text in offers:
        if extract_price(card_text) is None:
            plna = href if href.startswith("http") else base_url + href
            cena = ld.get(href) or ld.get(plna)
            if cena:
                card_text = f"{card_text} od {cena} Kč"
                doplneno += 1
        out.append((href, title, card_text))
    if doplneno:
        print(f"  JSON-LD doplnil cenu u {doplneno} karet.")
    return out


def hlidej_zmizele(seen, updates, today_str):
    """
    Nabídkám, které v tomto běhu nepřišly, zvýší počítadlo 'miss'.
    Po ZMIZENI_PO_BEZICH bězích bez spatření pošle ⌛ upozornění
    (jednorázově - záznam se označí 'gone'). Když se nabídka znovu
    objeví, počítadlo se automaticky vynuluje (záznam přepíší updates).
    """
    if not OZNAMOVAT_ZMIZENI:
        return
    if _karet_parsovano < 10:
        # Weby skoro nic nevrátily (výpadek/blokace) - nepočítat zmizení,
        # jinak by po jednom rozbitém běhu přišla vlna falešných ⌛ zpráv.
        print(f"Jen {_karet_parsovano} karet za běh - hlídání zmizelých přeskočeno.")
        return
    dnes = datetime.date.fromisoformat(today_str)
    for k, v in seen.items():
        if k in updates or v.get("gone"):
            continue
        d = v.get("d")
        try:
            stari = (dnes - datetime.date.fromisoformat(d)).days if d else 999
        except ValueError:
            stari = 999
        if stari > 14:
            continue  # stará nabídka - zmizení už není zajímavé
        v["miss"] = v.get("miss", 0) + 1
        if v["miss"] >= ZMIZENI_PO_BEZICH:
            v["gone"] = True
            titulek = html.escape(v.get("t") or "Nabídka")
            cena = (f"\n💰 naposledy {format_price(v['ref'])}"
                    if v.get("ref") else "")
            send_telegram(
                f"⌛ <b>NABÍDKA ZMIZELA</b>\n"
                f"🏨 {titulek}"
                f"{cena}\n"
                f"<i>Neobjevila se {ZMIZENI_PO_BEZICH}× po sobě – "
                f"nejspíš vyprodáno nebo stažena.</i>",
                link=v.get("u"),
            )


def send_daily_digest(seen, today_str):
    """
    TOP 5 nejlevnějších Jaz nabídek přepočtených na cenu za NOC
    z nabídek viděných dnes. Každý hotel/titulek nejvýš jednou
    (bere se jeho nejlevnější termín).
    """
    nejlepsi = {}  # titulek -> (kc_za_noc, cena, noci, odkaz)
    for v in seen.values():
        if v.get("d") != today_str or v.get("gone"):
            continue
        ref, noci, t = v.get("ref"), v.get("n"), v.get("t")
        if not ref or not noci or not t:
            continue
        za_noc = ref / noci
        if t not in nejlepsi or za_noc < nejlepsi[t][0]:
            nejlepsi[t] = (za_noc, ref, noci, v.get("u"))
    if not nejlepsi:
        return
    lines = ["🌅 <b>Ranní přehled – nejlevnější Jaz za noc</b>"]
    serazeno = sorted(nejlepsi.items(), key=lambda kv: kv[1][0])
    for t, (za_noc, ref, noci, link) in serazeno[:5]:
        nazev = html.escape(t)
        if link:
            nazev = f'<a href="{html.escape(link)}">{nazev}</a>'
        lines.append(f"• {nazev}: <b>{format_price(int(round(za_noc)))}/noc</b>"
                     f" ({format_price(ref)} / {noci} nocí)")
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
    if not passes_min_nights(card_text, href):
        return 0

    price = extract_price(card_text)
    if not passes_price_cap(price):
        return 0

    base_path = href.split("?")[0]
    key = make_offer_key(source, base_path, card_text, title)
    link = href if href.startswith("http") else base_url + href
    price_to_store = price if price is not None else 0

    if key not in seen and key not in updates:
        updates[key] = _pridej_meta(
            {"ref": price_to_store, "min": price_to_store}, title, card_text, link)
        stats_note_new(stats, price, title)
        if notify:
            if price:
                cena_radek = (f"\n💰 <b>{format_price(price)}</b>"
                              f"{_za_noc(price, card_text, link)}")
            else:
                cena_radek = "\n💰 <i>cena neuvedena</i>"
            send_telegram(
                f"🆕 <b>NOVÁ NABÍDKA</b>\n"
                f"{zprava_detail(title, card_text, source_label, link)}"
                f"{cena_radek}\n"
                f"🌐 {source_label}",
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
        if sleva < MIN_ZMENA_CENY:
            # Drobný pokles (cenový šum): nehlásíme a referenční cenu
            # NEMĚNÍME - malé poklesy se tak sčítají a zpráva přijde,
            # jakmile celkový rozdíl překročí MIN_ZMENA_CENY. Historické
            # minimum si ale zapamatujeme i tak.
            updates[key] = _pridej_meta(
                {"ref": old_ref, "min": new_min}, title, card_text, link)
            return 0
        updates[key] = _pridej_meta(
            {"ref": price, "min": new_min}, title, card_text, link)
        stats_note_discount(stats, sleva, price, title)
        if notify:
            badge = "\n🏆 <b>Rekordně nízká cena!</b>" if is_record else ""
            # Když to není rekord, ukážeme pro kontext dosavadní minimum -
            # hned vidíš, jestli má smysl čekat na další pokles.
            min_radek = (f"\n📉 Dosavadní minimum: {format_price(new_min)}"
                         if old_min and not is_record else "")
            send_telegram(
                f"🟢 <b>ZLEVNĚNÍ −{format_price(sleva)}</b>{badge}\n"
                f"{zprava_detail(title, card_text, source_label, link)}\n"
                f"💰 <s>{format_price(old_ref)}</s> → <b>{format_price(price)}</b>"
                f"{_za_noc(price, card_text, link)}{min_radek}\n"
                f"🌐 {source_label}",
                link=link,
            )
        return 1

    # ZDRAŽENÍ 🔺
    if OZNAMOVAT_ZDRAZENI and price and old_ref and price > old_ref:
        zdrazeni = price - old_ref
        if zdrazeni < MIN_ZMENA_CENY:
            # Drobný nárůst: nehlásíme, ref necháváme (nárůsty se sčítají).
            updates[key] = _pridej_meta(
                {"ref": old_ref, "min": old_min if old_min else price},
                title, card_text, link)
            return 0
        updates[key] = _pridej_meta(
            {"ref": price, "min": old_min if old_min else price},
            title, card_text, link)
        stats["zdrazeni"] = stats.get("zdrazeni", 0) + 1
        if notify:
            send_telegram(
                f"🔴 <b>ZDRAŽENÍ +{format_price(zdrazeni)}</b>\n"
                f"{zprava_detail(title, card_text, source_label, link)}\n"
                f"💰 <s>{format_price(old_ref)}</s> → <b>{format_price(price)}</b>"
                f"{_za_noc(price, card_text, link)}\n"
                f"🌐 {source_label}",
                link=link,
            )
        return 1

    # Beze změny: minimum držíme a záznam VŽDY "orazítkujeme" jako viděný
    # v tomto běhu - i bez ceny. Bez toho by hlídání zmizelých nabídek
    # falešně hlásilo zmizení u karet, které cenu zrovna neuvádějí.
    if price:
        new_min = min(price, old_min) if old_min else price
        updates[key] = _pridej_meta(
            {"ref": price, "min": new_min}, title, card_text, link)
    else:
        updates[key] = _pridej_meta(
            {"ref": old_ref, "min": old_min}, title, card_text, link)
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
        # Odrolovat stránku dolů: karty pod ohybem obrazovky se na řadě
        # webů (Exim, Fischer) donačítají líně až při scrollu - bez tohohle
        # mají spodní karty prázdné ceny ("Načítám...").
        try:
            for _ in range(6):
                page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                page.wait_for_timeout(400)
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
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
    global _karet_parsovano
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not detail_pattern.search(href):
            continue
        # get_text S mezerou jako oddělovačem i u titulku - bez ní se text
        # slepí ("St04. 11. 2026Brno4 dny") a regexy pak termín nenajdou.
        title = a.get_text(" ", strip=True) or "Nabídka last minute"

        # Text karty: nejbližší rodič často obsahuje jen název hotelu nebo
        # JEN CENU (na hotelových stránkách Invie je anchor karty přímo
        # "od 15 880 Kč"!). Proto NESTAČÍ zastavit u prvního rodiče s "Kč" -
        # tak se k termínu/počtu nocí nikdy nedostaneme a zpráva by hlásila
        # "délka pobytu neuvedena". Lezeme nahoru, dokud text neobsahuje
        # TERMÍN (datum) nebo počet nocí/dní; cena samotná nestačí.
        _ma_termin = re.compile(
            r"\d{1,2}\.\s?\d{1,2}\.|\d{4}-\d{2}-\d{2}"      # datum
            r"|\d{1,2}\s*noc|\d{1,2}\s*(?:dnech|dny|dní|dnů|dnu|dni|den)\b",
            re.IGNORECASE)
        card_text = ""
        node = a
        for _ in range(6):  # max 6 úrovní nahoru
            parent = node.find_parent(["article", "li", "div", "section"])
            if parent is None:
                break
            text = parent.get_text(" ", strip=True)
            node = parent
            # Pojistka: rodič delší než ~1500 znaků už je skoro jistě
            # kontejner s VÍCE kartami - dál nelezeme, jinak by se do
            # card_text dostala data sousední nabídky. Necháme si poslední
            # menší blok.
            if card_text and len(text) > 1500:
                break
            card_text = text[:600]  # zapamatuj poslední nalezený blok
            # Zastavíme, až když blok obsahuje termín/noci A cenu (typická
            # kompletní karta). Samotná cena bez termínu nestačí.
            if ("Kč" in text) and _ma_termin.search(text):
                break

        if not card_text:
            card_text = (a.get_text(" ", strip=True) or "")[:600]
        if len(card_text) < min_text_len:
            continue
        results.append((href, title, card_text))
    _karet_parsovano += len(results)
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
            offers = doplnit_ceny_z_jsonld(soup, offers, "https://www.invia.cz")
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
        offers = doplnit_ceny_z_jsonld(soup, offers, "https://www.invia.cz")
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
        offers = doplnit_ceny_z_jsonld(soup, offers, "https://www.blue-style.cz")
        for href, title, card_text in offers:
            found += process_offer("bluestyle", "Blue Style", "https://www.blue-style.cz",
                                   seen, updates, stats, notify, href, title, card_text,
                                   trusted=is_trusted_url(url))
        print(f"Blue Style ({url}): {len(offers)} karet, {found} nových/zlevněných.")


def _hotel_z_cesty(url):
    """
    Z URL hotelu vytáhne čitelné jméno (poslední segment cesty):
    .../hotel-jaz-almaza-beach-resort,MUH2JAB/ -> "Jaz Almaza Beach Resort"
    .../almaza-bay/jaz-oriental -> "Jaz Oriental"
    """
    cesta = url.split("?")[0].rstrip("/")
    slug = cesta.rsplit("/", 1)[-1]
    slug = slug.split(",")[0]                 # odříznout kód nabídky Čedoku
    slug = re.sub(r"^hotel-", "", slug)       # odříznout prefix "hotel-"
    return slug.replace("-", " ").strip().title()


def zkontroluj_hotelove_stranky(source, source_label, base_url, urls,
                                seen, updates, stats, notify, browser,
                                fetcher=None):
    if fetcher is None:
        fetcher = fetch_rendered_html
    """
    Přímé stránky hotelů (model jako Invia Jaz hotely): z každé stránky
    se vytáhne hotel (ze slugu URL), termín "nejlepší nabídky", počet
    nocí a cena za osobu, a založí se jedna nabídka na hotel. Cena se
    hledá postupně: JSON-LD -> "od X Kč" -> dvojice přeškrtnutá+aktuální
    (Čedok) -> "X Kč /os.". Bez nalezené ceny se nabídka nezakládá
    (ať nechodí prázdné zprávy) - jen se to zapíše do logu.
    """
    for url in urls:
        try:
            page_html = fetcher(browser, url)
        except Exception as e:
            print(f"{source_label} hotel chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        hotel = _hotel_z_cesty(url)
        full_text = soup.get_text(" ", strip=True)
        # Okno: začátek stránky PŘED blokem podobných hotelů - tam jsou
        # ceny JINÝCH hotelů a nesmí se přimíchat.
        # Bez pevného limitu délky: ceny (hlavně u Eximu) bývají v textu
        # až daleko za popisem hotelu - jen odřízneme blok podobných hotelů.
        okno = re.split(r"Podobné hotely|Doporučené hotely", full_text)[0]
        # POJISTKA: když ořez sebral většinu stránky, seklo to nejspíš už
        # v horním MENU (Exim má "Doporučené hotely" i v navigaci!) a ne
        # v patičce - v tom případě ořez zahodíme a bereme celý text.
        if len(okno) < 0.3 * len(full_text):
            okno = full_text

        # Termín + noci: Čedok píše "24.09 - 28.09.2026 (5 dní, 4 noci)".
        term_txt, noci = "", None
        m = re.search(r"(\d{1,2}\.\d{1,2})\.?\s*[-–]\s*(\d{1,2}\.\d{1,2}\.\d{4})"
                      r"[^()]{0,20}\(\s*\d+\s*(?:dní|dny|den)\s*,\s*(\d+)\s*noc",
                      okno)
        if m:
            term_txt = f"{m.group(1)}. – {m.group(2)}"
            noci = int(m.group(3))
        else:
            term = extract_term(okno)
            if term:
                term_txt = format_term(term)
                noci = (term[1] - term[0]).days

        # Cena za osobu
        cena = None
        ld = extract_jsonld_prices(soup)
        if ld:
            cena = ld.get(url) or ld.get(url.rstrip("/"))
            if cena is None and len(ld) == 1:
                cena = next(iter(ld.values()))
        if cena is None:
            m = re.search(r"\bod\s*(\d{1,3}(?:[\s.]\d{3})+)\s*Kč", okno)
            if m:
                cena = int(re.sub(r"[\s.]+", "", m.group(1)))
        if cena is None:
            # Čedok hlavička: "35 247 Kč 20 047 Kč" (přeškrtnutá + aktuální)
            m = re.search(r"(\d{1,3}(?:[\s.]\d{3})+)\s*Kč\s*(\d{1,3}(?:[\s.]\d{3})+)\s*Kč",
                          okno)
            if m:
                cena = int(re.sub(r"[\s.]+", "", m.group(2)))
        if cena is None:
            m = re.search(r"(?<!\d)(\d{1,3}(?:[\s.]\d{3})+)\s*Kč\s*/\s*os", okno)
            if m:
                cena = int(re.sub(r"[\s.]+", "", m.group(1)))
        if cena is not None and not (3000 <= cena <= 500000):
            cena = None
        if cena is None:
            print(f"{source_label} hotel ({url}): cena nenalezena, přeskakuji.")
            print(f"  [DIAG] text: {len(full_text)} znaků, okno: {len(okno)} znaků, "
                  f"'Kč' na stránce: {full_text.count('Kč')}x, "
                  f"'Kč' v okně: {okno.count('Kč')}x, "
                  f"JSON-LD cen: {len(ld)}, začátek: {okno[:160]!r}")
            continue

        # Odletové letiště, když ho stránka uvádí ("Odlet: Praha")
        odlet = ""
        m = re.search(r"Odlet:\s*([A-ZÁ-Ž][a-zá-ž]+)", okno)
        if m:
            odlet = f" odlet z {m.group(1)}"

        noci_txt = f" {noci} nocí" if noci else ""
        card_text = f"{hotel} {term_txt}{noci_txt}{odlet} od {format_price(cena)}"
        found = process_offer(source, source_label, base_url,
                              seen, updates, stats, notify, url, hotel,
                              card_text, trusted=True)
        print(f"{source_label} hotel ({url}): 1 karta"
              f" ({cena} Kč{noci_txt}), {found} nových/zlevněných.")


def fetch_cedok_html(browser, url):
    """
    Čedok má citlivější ochranu proti botům než ostatní weby, proto pro něj
    máme opatrnější načítání:
      1. Playwright s maskováním headless znaků (navigator.webdriver, jazyk,
         časové pásmo) a BEZ blokování požadavků - ochrany si všímají, když
         stránka neodesílá obrázky/skripty.
      2. Čeká se přímo na výskyt ceny ("Kč") - ochranná mezistránka se často
         po pár sekundách sama přesměruje na obsah.
      3. Když prohlížeč dostane prázdnou/ochrannou stránku, zkusí se obyčejné
         HTTP stažení (stránky Čedoku jsou vykreslené na serveru, takže
         obsah je i bez JavaScriptu).
    Vrací HTML; když všechno selže, vrátí to nejlepší, co má.

    Z běhů 07/2026 víme, že prohlížeč na Čedoku narazí na ochrannou stránku
    VŽDY, zatímco přímé HTTP stažení funguje spolehlivě (obsah je vykreslený
    na serveru). Proto jdeme rovnou na přímé stažení a prohlížeč necháváme
    jen jako zálohu, kdyby Čedok někdy začal vyžadovat JavaScript.
    """
    try:
        r = requests.get(url, timeout=30, headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.7",
            "Referer": "https://www.cedok.cz/",
        })
        if r.ok and "Kč" in r.text and len(r.text) > 20000:
            return r.text
        print(f"  Čedok: přímé stažení nestačí (HTTP {r.status_code}, "
              f"{len(r.text)} znaků), zkouším prohlížeč.")
    except Exception as e:
        print(f"  Čedok: přímé stažení selhalo ({e}), zkouším prohlížeč.")

    html = ""
    ctx = None
    try:
        ctx = browser.new_context(
            user_agent=_USER_AGENT,
            locale="cs-CZ",
            timezone_id="Europe/Prague",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.7"},
        )
        # Skrýt nejčastější znaky automatizace, podle kterých ochrany poznají
        # headless Chromium.
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['cs-CZ','cs','en']});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});"
        )
        page = ctx.new_page()
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        # Ochranná mezistránka se typicky do pár sekund přesměruje - čekáme
        # rovnou na cenu, ta na obsahové stránce Čedoku je vždy.
        try:
            page.wait_for_selector("text=Kč", timeout=15000)
        except Exception:
            pass
        try:
            for _ in range(4):
                page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                page.wait_for_timeout(350)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        html = page.content()
    except Exception as e:
        print(f"  Čedok: prohlížeč selhal ({e}), zkouším přímé stažení.")
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass

    text_ok = "Kč" in html and len(html) > 20000
    ochrana = any(z in html for z in (
        "Just a moment", "Access denied", "Attention Required",
        "captcha", "Ověřujeme", "cf-challenge"))
    if text_ok and not ochrana:
        return html
    if ochrana:
        print("  Čedok: prohlížeč narazil na ochrannou stránku, "
              "zkouším přímé stažení bez prohlížeče.")

    # Fallback: obyčejné HTTP - obsah Čedoku je vykreslený na serveru.
    try:
        r = requests.get(url, timeout=30, headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.7",
            "Referer": "https://www.cedok.cz/",
        })
        if r.ok and "Kč" in r.text:
            print(f"  Čedok: přímé stažení uspělo ({len(r.text)} znaků).")
            return r.text
        print(f"  Čedok: přímé stažení nepomohlo (HTTP {r.status_code}, "
              f"'Kč' v odpovědi: {'Kč' in r.text}).")
    except Exception as e:
        print(f"  Čedok: přímé stažení selhalo ({e}).")
    return html


def check_cedok(seen, updates, stats, notify, browser):
    # Odkaz na hotel Čedoku obsahuje kód nabídky za čárkou:
    # /dovolena/egypt/marsa-matrouh/hotel-jaz-almaza-beach-resort,MUH2JAB/
    detail_pattern = re.compile(r"/dovolena/egypt/[^/?#]+/[^/?#,]+,[A-Za-z0-9]{4,}",
                                re.IGNORECASE)
    for url in CEDOK_SEARCH_URLS:
        try:
            page_html = fetch_cedok_html(browser, url)
        except Exception as e:
            print(f"Čedok chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        found = 0
        offers = parse_offers_from_soup(soup, detail_pattern, min_text_len=10)
        offers = doplnit_ceny_z_jsonld(soup, offers, "https://www.cedok.cz")
        for href, title, card_text in offers:
            found += process_offer("cedok", "Čedok", "https://www.cedok.cz",
                                   seen, updates, stats, notify, href, title, card_text,
                                   trusted=is_trusted_url(url))
        print(f"Čedok ({url}): {len(offers)} karet, {found} nových/zlevněných.")
        if not offers:
            hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
            vzorek = [h for h in hrefs if "/dovolena/" in h][:8]
            txt = soup.get_text(" ", strip=True)
            print(f"  [DIAG Čedok] HTML odkazů: {len(hrefs)}, text: {len(txt)} znaků, "
                  f"'Kč': {txt.count('Kč')}x, začátek: {txt[:120]!r}")
            for h in vzorek:
                print(f"  [DIAG Čedok] odkaz: {h[:130]}")

    # 2) Přímé stránky Jaz hotelů - stejný model jako u Invie.
    zkontroluj_hotelove_stranky("cedok", "Čedok (Jaz hotel)", "https://www.cedok.cz",
                                CEDOK_JAZ_HOTEL_URLS,
                                seen, updates, stats, notify, browser,
                                fetcher=fetch_cedok_html)


def check_eximtours(seen, updates, stats, notify, browser):
    # Hotelové stránky Eximu mají tvar /egypt/<region>/<oblast>/<slug>
    # (např. /egypt/marsa-matruh/almaza-bay/jaz-oriental) - v cestě NENÍ
    # slovo "hotel" ani "zajezd", proto původní vzor nechytal nic.
    # Chytáme odkazy s aspoň 3 segmenty za /egypt/ (příp. /hotely/egypt/).
    detail_pattern = re.compile(
        r"/(?:hotely/)?egypt/[^/?#]+/[^/?#]+/[^/?#]+/?(?:$|\?)", re.IGNORECASE)
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

    # 2) Přímé stránky Jaz hotelů - stejný model jako u Invie.
    zkontroluj_hotelove_stranky("eximtours", "Exim Tours (Jaz hotel)",
                                "https://www.eximtours.cz", EXIM_JAZ_HOTEL_URLS,
                                seen, updates, stats, notify, browser)


def check_fischer(seen, updates, stats, notify, browser):
    # Fischer jede na stejné platformě jako Exim (DER Touristik) -
    # hotelové odkazy mají stejný tvar /egypt/<region>/<oblast>/<slug>.
    detail_pattern = re.compile(
        r"/(?:hotely/)?egypt/[^/?#]+/[^/?#]+/[^/?#]+/?(?:$|\?)", re.IGNORECASE)
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
                ("Čedok", check_cedok),
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

    # Hlídání zmizelých nabídek - MUSÍ běžet PŘED seen.update(updates),
    # aby šlo rozlišit "viděno v tomto běhu" (updates) od "nepřišlo" (seen).
    if not first_run:
        hlidej_zmizele(seen, updates, today_str)

    seen.update(updates)
    seen = prune_seen(seen, updates, today_str)
    save_seen(seen)

    # Denní digest: jednou denně v nastavenou hodinu TOP 5 za noc.
    if (DENNI_DIGEST and not first_run
            and now.hour == DIGEST_HODINA_UTC
            and stats.get("digest_date") != today_str):
        send_daily_digest(seen, today_str)
        stats["digest_date"] = today_str

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
