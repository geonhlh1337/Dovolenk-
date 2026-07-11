Telegram bot – hlídač last minute zájezdů
Bot každou hodinu zkontroluje nabídky last minute zájezdů (Invia.cz, Blue
Style, Exim Tours, Fischer) s odletem z Prahy, Brna nebo Ostravy a nové
nabídky ti pošle zprávou na Telegram. Běží zdarma na GitHub Actions.
Je to stejný princip jako u realitního bota – pokud jsi ho už nastavoval/a,
postup nasazení bude povědomý.
1–5. Nasazení
Postup je identický jako u realitního bota:
Vytvoř Telegram bota přes @BotFather, ulož si token.
Zjisti své chat ID (napiš botovi zprávu, pak otevři
https://api.telegram.org/bot<TOKEN>/getUpdates).
Založ nový GitHub repozitář a nahraj do něj všechny soubory z této
složky – zachovej přesně cestu .github/workflows/hodinova_kontrola.yml.
V Settings → Secrets and variables → Actions přidej:
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
V záložce Actions spusť workflow ručně (Run workflow), ověř, že
proběhne bez chyb, pak už poběží automaticky každou hodinu.
První spuštění si jen uloží aktuální nabídky bez odesílání zpráv (jinak
by přišly desítky zpráv najednou).
Co bot umí
Nové nabídky – 🆕 NOVÉ, s cenou a tlačítkem „Otevřít nabídku" přímo
pod zprávou.
Hlídač slev – u známých zájezdů si pamatuje referenční cenu. Když
klesne, pošle 🔻 ZLEVNĚNÍ s původní i novou cenou a výší slevy.
Historické minimum 🏆 – bot si u každého zájezdu pamatuje nejnižší
cenu, jakou kdy viděl. Když zlevnění spadne i pod toto minimum, zpráva
dostane štítek 🏆 – tak poznáš skutečnou peckovou slevu od běžného
kolísání cen.
Týdenní přehled 📊 – na začátku každého týdne přijde souhrn: kolik
nových nabídek se objevilo, kolik bylo zlevnění, největší sleva týdne a
nejlevnější nabídka týdne. (Průběžný stav se ukládá do stats.json.)
Filtry (vše v main.py nahoře, prázdný seznam/None = vypnuto):
LETISTE_FILTR – odletová letiště, výchozí ["Praha", "Brno", "Ostrava"]
DESTINACE_FILTR – cílové země/oblasti (whitelist). Aktuálně nastaven
na Egypt a všechna jeho letoviska (Hurghada, Marsa Alam, Sharm, Safaga,
Marsa Matrouh, Taba, El Gouna, Sahl Hasheesh, Nuweiba, Dahab) – chodí
tedy jen egyptské nabídky. Další země přidáš dopsáním řádku (např.
"Řecko", "Kréta",). Prázdný seznam [] = všechny destinace.
MAX_CENA – cenový strop v Kč/os., např. MAX_CENA = 15000. Nabídky,
u kterých se cenu nepodařilo přečíst, procházejí vždy.
STRAVA_FILTR – např. ["All inclusive"] nebo ["Polopenze", "All inclusive"]
Všechny filtry platí zároveň – nabídka musí vyhovět každému zapnutému.
Zdroje a jejich spolehlivost
Invia.cz – hlavní a nejspolehlivější zdroj. Server-rendered (žádný
prohlížeč není potřeba), agreguje nabídky od více než 120 CK najednou –
včetně Exim Tours, Fischer, Blue Style, Čedok. Uvádí i odletové letiště,
takže funguje s filtrem letišť.
Blue Style – rovněž server-rendered.
Exim Tours a Fischer – dotahují nabídky přes JavaScript (obě patří
pod DER Touristik), proto bot používá headless prohlížeč (Playwright).
Důležité upozornění: U Exim Tours a Fischer jsem nemohl naživo ověřit
přesný formát odkazu na detail konkrétní nabídky (weby jsou dynamické a
nešlo je otestovat mimo běžící bota). Rozpoznávání je proto nastavené
obecněji (hledá odkazy s klíčovými slovy v URL) a je pravděpodobné, že po
prvním ostrém běhu bude potřeba doladit podle skutečného výstupu v logu
GitHub Actions – přesně jako jsme to dělali u realitního bota. Stačí mi
poslat log a upravíme to.
Přizpůsobení filtrů
V main.py, sekce NASTAVENÍ HLEDÁNÍ:
Invia.cz – jdi na invia.cz, nastav filtry (destinace, cena, strava,
délka pobytu) a zkopírovanou URL vlož do INVIA_SEARCH_URLS. Invia má i
samostatné last-minute stránky pro jednotlivé destinace (Egypt, Turecko,
Řecko...), které můžeš přidat jako další položky seznamu.
Blue Style, Exim Tours, Fischer – obdobně, uprav
BLUESTYLE_SEARCH_URLS, EXIMTOURS_SEARCH_URLS, FISCHER_SEARCH_URLS.
Údržba
Odolnost: každý zdroj běží samostatně – když jeden web spadne nebo
se změní, ostatní jedou dál a chyba se jen vypíše do logu.
Automatický úklid paměti: záznamy o nabídkách, které bot neviděl déle
než 60 dní, se samy mažou ze seen.json – soubor tedy neroste donekonečna.
Weby čas od času mění strukturu stránek. Pokud bot přestane posílat nové
nabídky, zkontroluj v záložce Actions log posledního běhu – vypíše
počet nalezených odkazů u každého zdroje, podle kterého se dá poznat, co
je potřeba v kódu upravit.
