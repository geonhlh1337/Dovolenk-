import os
import re
import json
import time
import hashlib
import datetime
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_FILE = "seen.json"
STATS_FILE = "stats.json"

# ============================================================
#                       NASTAVENÍ HLEDÁNÍ
# ============================================================

# Filtr odletových letišť. Bot pošle nabídku jen tehdy, když text karty
# obsahuje některé z těchto slov. Prázdný seznam ([]) = filtr vypnutý.
LETISTE_FILTR = ["Praha", "Brno", "Ostrava"]

# Diagnostika: když je True, u Exim Tours a Fischer se do logu vypíše
# ukázka skutečně nalezených odkazů na stránce. Slouží k jednorázovému
# doladění rozpoznávacího vzoru - po doladění vrať na False.
DIAGNOSTIKA_ODKAZU = True

# Filtr cílových destinací (whitelist). Vyplníš-li, projdou POUZE nabídky
# obsahující některé z těchto slov. Prázdný seznam ([]) = vypnuto.
# Příklady: "Egypt", "Řecko", "Turecko", "Kréta", "Rhodos", "Hurghada"...
DESTINACE_FILTR = [
    # "Egypt",
    # "Řecko",
]

# Cenový strop v Kč za osobu. Nabídky s vyšší cenou se zahodí.
# None = bez omezení. Nabídky, u kterých se cenu nepodařilo přečíst,
# procházejí vždy (ať o ně nepřijdeš omylem).
MAX_CENA = None
# MAX_CENA = 15000

# Filtr stravy. Vyplníš-li, projdou jen nabídky obsahující některé z těchto
# slov. Prázdný seznam ([]) = vypnuto.
# Obvyklé hodnoty: "All inclusive", "Polopenze", "Plná penze", "Snídaně"
STRAVA_FILTR = [
    # "All inclusive",
]

# --- Invia.cz --- (srovnávač 120+ CK: Exim, Fischer, Blue Style, Čedok...)
INVIA_SEARCH_URLS = [
    "https://www.invia.cz/dovolena/last-minute/",
    "https://www.invia.cz/dovolena/last-minute-z-brna/",
    "https://www.invia.cz/dovolena/last-minute-ostrava/",
]

# --- Blue Style ---
BLUESTYLE_SEARCH_URLS = [
    "https://www.blue-style.cz/last-minute/",
]

# --- Exim Tours a Fischer ---
# POZNÁMKA: Tyto weby (obě CK patří pod DER Touristik) nevykreslují nabídky
# jako běžné odkazy - dotahují je až dodatečně z interního API a zobrazují
# jako interaktivní prvky. Přes prohlížeč se do HTML nedostanou (ověřeno
# diagnostikou: na stránce jsou jen odkazy na kategorie, ne na konkrétní
# zájezdy). Zprovoznění by vyžadovalo křehké reverzní rozklíčování jejich
# API. Není to ale potřeba: nabídky OBOU těchto CK už obsahuje Invia.cz
# (jsou to její partnerské kanceláře), takže o ně nepřicházíš.
# Proto jsou zde vypnuté (prázdné seznamy). Kdybys je chtěl v budoucnu
# zkusit oživit, stačí doplnit URL a upravit check funkce.
EXIMTOURS_SEARCH_URLS = []

FISCHER_SEARCH_URLS = []

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


def send_telegram(text, link=None):
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
    m = re.search(r"od\s*([\d\s]{3,})\s*Kč", text)
    if not m:
        m = re.search(r"([\d\s]{4,})\s*Kč", text)
    if m:
        digits = re.sub(r"\s+", "", m.group(1))
        if digits.isdigit():
            return int(digits)
    return None


def format_price(value):
    return f"{value:,}".replace(",", " ") + " Kč"


def passes_airport_filter(text):
    if not LETISTE_FILTR:
        return True
    return any(l.lower() in text.lower() for l in LETISTE_FILTR)


def passes_destination_filter(text):
    if not DESTINACE_FILTR:
        return True
    return any(d.lower() in text.lower() for d in DESTINACE_FILTR)


def passes_meal_filter(text):
    if not STRAVA_FILTR:
        return True
    return any(s.lower() in text.lower() for s in STRAVA_FILTR)


def passes_price_cap(price):
    if MAX_CENA is None or price is None:
        return True
    return price <= MAX_CENA


def make_offer_key(source, base_path, card_text):
    date_match = re.search(r"\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}", card_text)
    date_part = date_match.group(0) if date_match else ""
    return f"{source}:{short_hash(f'{base_path}|{date_part}')}"


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
        lines.append(f"🏅 Největší sleva: {format_price(s['castka'])} – {s['titulek']}")
    if stats["nejlevnejsi"]:
        n = stats["nejlevnejsi"]
        lines.append(f"💸 Nejlevnější nabídka: {format_price(n['cena'])} – {n['titulek']}")
    if stats["novych"] == 0 and stats["zlevneni"] == 0:
        lines.append("Tento týden se neobjevilo nic nového.")
    send_telegram("\n".join(lines))


def process_offer(source, source_label, base_url, seen, updates, stats, notify,
                  href, title, card_text):
    if not passes_airport_filter(card_text):
        return 0
    if not passes_destination_filter(card_text):
        return 0
    if not passes_meal_filter(card_text):
        return 0

    price = extract_price(card_text)
    if not passes_price_cap(price):
        return 0

    base_path = href.split("?")[0]
    key = make_offer_key(source, base_path, card_text)
    link = href if href.startswith("http") else base_url + href
    price_to_store = price if price is not None else 0

    if key not in seen and key not in updates:
        updates[key] = {"ref": price_to_store, "min": price_to_store}
        stats_note_new(stats, price, title)
        if notify:
            price_line = f"\n💰 {format_price(price)}" if price else ""
            send_telegram(
                f"✈️ <b>{source_label}</b> · 🆕 NOVÉ{price_line}\n{title}\n{card_text}",
                link=link,
            )
        return 1

    entry = updates.get(key) or seen.get(key)
    old_ref = entry.get("ref", 0)
    old_min = entry.get("min", 0)

    if price and old_ref and price < old_ref:
        sleva = old_ref - price
        is_record = bool(old_min) and price < old_min
        new_min = min(price, old_min) if old_min else price
        updates[key] = {"ref": price, "min": new_min}
        stats_note_discount(stats, sleva, price, title)
        if notify:
            badge = "\n🏆 <b>Nejnižší cena, jakou jsem u této nabídky kdy viděl!</b>" if is_record else ""
            send_telegram(
                f"✈️ <b>{source_label}</b> · 🔻 ZLEVNĚNÍ o {format_price(sleva)}{badge}\n"
                f"{title}\n"
                f"Původně {format_price(old_ref)} → nyní <b>{format_price(price)}</b>\n"
                f"{card_text}",
                link=link,
            )
        return 1

    # Beze změny nebo zdražení: aktualizujeme referenci nahoru, minimum držíme.
    if price:
        new_ref = max(price, old_ref) if old_ref else price
        new_min = min(price, old_min) if old_min else price
        updates[key] = {"ref": new_ref, "min": new_min}
    return 0


def fetch_rendered_html(browser, url):
    page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    try:
        page.goto(url, timeout=45000, wait_until="networkidle")
        consent_selectors = [
            "button:has-text('Souhlasím')",
            "button:has-text('Rozumím')",
            "button:has-text('Přijmout')",
            "button:has-text('Přijmout vše')",
            "button:has-text('Povolit')",
            "button:has-text('Accept')",
            "button:has-text('Accept all')",
            "#didomi-notice-agree-button",
        ]
        for selector in consent_selectors:
            try:
                page.click(selector, timeout=2000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)
                break
            except Exception:
                continue
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
        print(f"  [DIAG {zdroj}] nabídek přes vzor: {len(vzorek)}")
        for href, title, card_text in vzorek[:3]:
            print(f"  [DIAG {zdroj}] --- karta: {title}")
            print(f"  [DIAG {zdroj}]     text: {card_text[:220]}")
            print(f"  [DIAG {zdroj}]     letiště={passes_airport_filter(card_text)} "
                  f"dest={passes_destination_filter(card_text)} "
                  f"strava={passes_meal_filter(card_text)} "
                  f"cena={extract_price(card_text)}")


def parse_offers_from_soup(soup, detail_pattern, min_text_len=0):
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not detail_pattern.search(href):
            continue
        title = a.get_text(strip=True) or "Nabídka last minute"
        card = a.find_parent(["article", "li", "div"]) or a
        card_text = card.get_text(" ", strip=True)[:400]
        if len(card_text) < min_text_len:
            continue
        results.append((href, title, card_text))
    return results


def check_invia(seen, updates, stats, notify, browser):
    detail_pattern = re.compile(r"/zajezd/\?s_offer_id=", re.IGNORECASE)
    for url in INVIA_SEARCH_URLS:
        try:
            html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Invia chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        diagnostika_vypis(soup, "Invia")
        found = 0
        for href, title, card_text in parse_offers_from_soup(soup, detail_pattern):
            found += process_offer("invia", "Invia.cz", "https://www.invia.cz",
                                   seen, updates, stats, notify, href, title, card_text)
        print(f"Invia ({url}): {found} nových/zlevněných nabídek.")


def check_bluestyle(seen, updates, stats, notify, browser):
    detail_pattern = re.compile(r"/zajezd", re.IGNORECASE)
    for url in BLUESTYLE_SEARCH_URLS:
        try:
            html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Blue Style chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        found = 0
        for href, title, card_text in parse_offers_from_soup(soup, detail_pattern):
            found += process_offer("bluestyle", "Blue Style", "https://www.blue-style.cz",
                                   seen, updates, stats, notify, href, title, card_text)
        print(f"Blue Style ({url}): {found} nových/zlevněných nabídek.")


def check_eximtours(seen, updates, stats, notify, browser):
    detail_pattern = re.compile(r"/(zajezd|hotel)[-/]", re.IGNORECASE)
    for url in EXIMTOURS_SEARCH_URLS:
        try:
            html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Exim Tours chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        diagnostika_vypis(soup, "Exim Tours")
        found = 0
        for href, title, card_text in parse_offers_from_soup(soup, detail_pattern, min_text_len=15):
            found += process_offer("eximtours", "Exim Tours", "https://www.eximtours.cz",
                                   seen, updates, stats, notify, href, title, card_text)
        print(f"Exim Tours ({url}): {found} nových/zlevněných nabídek.")


def check_fischer(seen, updates, stats, notify, browser):
    detail_pattern = re.compile(r"/(zajezd|hotel)[-/]", re.IGNORECASE)
    for url in FISCHER_SEARCH_URLS:
        try:
            html = fetch_rendered_html(browser, url)
        except Exception as e:
            print(f"Fischer chyba ({url}): {e}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        diagnostika_vypis(soup, "Fischer")
        found = 0
        for href, title, card_text in parse_offers_from_soup(soup, detail_pattern, min_text_len=15):
            found += process_offer("fischer", "Fischer", "https://www.fischer.cz",
                                   seen, updates, stats, notify, href, title, card_text)
        print(f"Fischer ({url}): {found} nových/zlevněných nabídek.")


def main():
    first_run = not os.path.exists(SEEN_FILE)
    seen = load_seen()
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
            check_invia(seen, updates, stats, notify=not first_run, browser=browser)
            check_bluestyle(seen, updates, stats, notify=not first_run, browser=browser)
            check_eximtours(seen, updates, stats, notify=not first_run, browser=browser)
            check_fischer(seen, updates, stats, notify=not first_run, browser=browser)
        finally:
            browser.close()

    seen.update(updates)
    save_seen(seen)
    save_stats(stats)
    if updates:
        print(f"Zpracováno {len(updates)} nových/aktualizovaných nabídek.")
    else:
        print("Žádné nové nabídky.")


if __name__ == "__main__":
    main()
