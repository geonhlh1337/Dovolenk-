# Last minute bot – hlídač zájezdů Jaz v Egyptě

Bot běží na GitHub Actions každých 30 minut, prochází české cestovky, hledá
zájezdy do hotelů řetězce **Jaz** v Egyptě a posílá zprávy na Telegram.

## Co posílá

| Zpráva | Kdy přijde |
|---|---|
| 🆕 **NOVÁ NABÍDKA** | nabídka, kterou bot ještě neviděl |
| 💰 **DOPLNĚNA CENA** | u nabídky, která dřív cenu neuváděla, se cena objevila |
| 🟢 **ZLEVNĚNÍ** | pokles ceny aspoň o `MIN_ZMENA_CENY` (+ 🏆 když je to historické minimum) |
| 🔴 **ZDRAŽENÍ** | nárůst ceny aspoň o `MIN_ZMENA_CENY` (vypnutelné) |
| ⌛ **NABÍDKA ZMIZELA** | nabídka se neobjevila `ZMIZENI_PO_BEZICH` běhů po sobě (tiše) |
| ⚠️ **Zdroj nevrátil nabídky** | web nereagoval nebo se vzdal po chybách (tiše) |
| 🌅 **Ranní přehled** | jednou denně TOP 5 nejlevnějších Jaz podle ceny za noc |
| 📊 **Týdenní přehled** | při přelomu kalendářního týdne |

U nabídek z **Invie** je ve zprávě navíc řádek 🏢 s **pořádající cestovní
kanceláří**. Bere se z parametru `s_offer_id` v odkazu, což je JWT s popisem
zájezdu (`tourOperatorId`, termín, `mealId`, letiště) – cena za osobu je
v parametru `c_price_unit`. Mapa ID → jméno se učí do `ck.json`; co bot
nedohledá, ukáže jako `CK #39` a můžeš si to do `ck.json` dopsat ručně.

Ostatní zdroje řádek s cestovkou nemají – tam je pořadatelem sama CK.

## Zdroje

Invia (vyhledávání + přímé stránky Jaz hotelů), Blue Style, Čedok
(výpisy letovisek + hotelové stránky), Exim Tours, Fischer.
Dovolenkovani.cz je připravené, ale vypnuté (blokuje IP datacenter).

Invia agreguje 120+ cestovek, takže nabídky Eximu, Fischeru, Blue Stylu
i Čedoku chodí i přes ni – ostatní zdroje jsou pojistka.

## Soubory

```
lastminute_bot.py    hlavní skript (nastavení je nahoře v souboru)
seen.json            paměť bota: co už viděl a za kolik
stats.json           průběžné počítadlo pro týdenní přehled
ck.json              naučená mapa "ID cestovky -> jméno" (jen pro Invii)
test_bot.py          testy jednotlivých funkcí (parsování, filtry, klíče)
test_integrace.py    testy celého běhu proti falešnému prohlížeči
.github/workflows/lastminute.yml
```

### Formát `seen.json`

```json
"invia:52da91f70f": {
  "ref":  20000,          // referenční cena pro porovnání
  "min":  18000,          // historické minimum
  "d":    "2026-07-29",   // kdy naposledy viděno
  "t":    "Jaz Aquamarine Resort",
  "n":    7,              // počet nocí
  "u":    "https://...",  // odkaz
  "miss": 0,              // kolik běhů po sobě nepřišla
  "gone": true            // už bylo ohlášeno zmizení
}
```

Klíč je `zdroj:hash(cesta|termín|strava|titulek)`. **Když změníš způsob
tvorby klíče, všechny staré záznamy přestanou platit a bot pošle celý
seznam znovu jako „nové".**

## Nastavení

Vše je nahoře v `lastminute_bot.py`:

- `HOTEL_FILTR` – hledaný řetězec hotelů (výchozí `["Jaz"]`)
- `DESTINACE_FILTR` – whitelist destinací; `[]` = vypnuto
- `LETISTE_FILTR` – odletová letiště, kmeny slov (`"Prah"`, ne `"Praha"`)
- `MIN_NOCI`, `MAX_CENA`, `STRAVA_FILTR`
- `MIN_ZMENA_CENY` – práh, pod kterým se změna ceny nehlásí (cenový šum)
- `MAX_ZPRAV_ZA_BEH` – pojistka proti záplavě zpráv
- `DENNI_DIGEST`, `DIGEST_HODINA_UTC` – ranní přehled
- `OZNAMOVAT_ZMIZENI`, `ZMIZENI_PO_BEZICH`, `MIN_KARET_PRO_ZMIZENI`
- `CASOVY_ROZPOCET_MINUT` – po vyčerpání bot přestane načítat další
  stránky a korektně doběhne. **Musí být nižší než `timeout-minutes`
  ve workflow**, jinak runner běh zabije a stav se ztratí.
- `MAX_CHYB_ZDROJE` – po kolika chybách za sebou se zdroj v běhu vzdá
- `NACTENI_TIMEOUT_MS`, `NACTENI_POKUSU` – načítání jedné stránky
- `INVIA_MAX_STRANEK` – kolik stránek výsledků projít
- `DIAGNOSTIKA_ODKAZU` – vypíše do logu nalezené odkazy (na doladění vzorů)
- `DOHLEDAVAT_CK`, `MAX_DOHLEDANI_ZA_BEH` – dohledávání jména neznámé
  cestovky otevřením detailu nabídky (výsledek se uloží do `ck.json`)
- `CK_PODLE_ID` – ruční doplnění jmen cestovek podle ID

## Spuštění

**Na GitHubu:** nastav v repu *Settings → Secrets and variables → Actions*
tajné hodnoty `TELEGRAM_BOT_TOKEN` a `TELEGRAM_CHAT_ID`. Dál to jede samo,
ruční spuštění je přes tlačítko *Run workflow*.

**Lokálně:**

```bash
pip install -r requirements.txt
playwright install chromium
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python lastminute_bot.py
```

**Testy** (nepotřebují síť ani prohlížeč):

```bash
TELEGRAM_BOT_TOKEN=test TELEGRAM_CHAT_ID=test python test_bot.py
TELEGRAM_BOT_TOKEN=test TELEGRAM_CHAT_ID=test python test_integrace.py
```

## První spuštění

Když `seen.json` neexistuje nebo je poškozený, bot si nabídky jen uloží
a **nic neposílá** – jinak by první zpráva byla lavina několika stovek
nabídek. Zprávy začnou chodit od druhého běhu.

## Kdy se mění ceny

Pevný rozvrh neexistuje – velké cestovky mění ceny dynamicky podle
obsazenosti a last minute nabídky se mění i několikrát denně. Proto si bot
sám měří, ve kterou hodinu (UTC) změny zachytil, a v týdenním přehledu
ukáže tři nejaktivnější hodiny (`zmeny_po_hodinach` v `stats.json`).
Po dvou třech týdnech z toho poznáš, jestli má běh každou půlhodinu smysl.

## Když se běh nestíhá

Jeden úplný běh trvá zhruba 20–25 minut (nejdražší jsou hotelové stránky
Invie, ~30 s každá). Když bot vyčerpá `CASOVY_ROZPOCET_MINUT`, zbytek
přeskočí – ale **pořadí zdrojů se každý běh posune o jedna**, takže se
nikdo trvale nevynechá. Zdroje, které v daném běhu neodpověděly, se
navíc nezapočítávají do hlídání zmizelých nabídek.

Když chceš stihnout víc, jde snížit `INVIA_MAX_STRANEK` nebo ubrat
vyhledávací URL a spolehnout se na hotelové stránky – ty vracejí
prakticky všechny změny.

## Když přestanou chodit zprávy

Podívej se do logu běhu v záložce *Actions*. Klíčový je řádek u každého zdroje:

- `X karet, Y nových` – funguje, jen se nic nezměnilo
- **`0 karet`** – web změnil strukturu nebo bota blokuje; zapni
  `DIAGNOSTIKA_ODKAZU = True` a z vypsaných odkazů uprav `detail_pattern`
- `cena nenalezena, přeskakuji` – u hotelové stránky se nepodařilo přečíst
  cenu; log rovnou vypíše, kolikrát je na stránce „Kč" a jak vypadá začátek textu
