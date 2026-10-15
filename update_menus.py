import json
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
}

WEEKDAYS_FI = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai"]

# Näytetään, jos ravintolan päivän listaa ei saada haettua. Ei keksittyjä annoksia.
PLACEHOLDER_ITEM = {
    "title": "Päivän lista ei ole saatavilla – katso ravintolan omilta sivuilta",
    "diets": [],
    "price": None,
}

PANCHO_STATIC_FALLBACK = [
    "Panchon burgerit (juusto / pekoni / kana / valkosipuli / chili / kasvis) 14,00 €, tupla 17,50 € (gluteeniton pyydettäessä)",
    "Lounassalaatti (kana / halloumi / lohi) 14,50 €",
    "Lounaspihvi (paneroitu kana / paneroitu porsaan pihvi) 14,50 €",
]

# Merkinnät, jotka näytetään sivulla. Muut (A, ILM, VS, * ...) jätetään pois.
DIET_MAP = {
    "G": "G",
    "L": "L",
    "M": "M",
    "VL": "VL",
    "VEG": "Veg",
    "PG": "PG",
    "GV!": "PG",  # Harjun Rinkeli: "toteutettavissa gluteenittomana tilauksesta"
    "V": "Veg",
    "V*": "Veg",  # Smokey Road Diner
}
DIET_ORDER = ["G", "PG", "L", "VL", "M", "Veg"]

DIET_TOKEN = r"(?:g|l|m|vl|veg|pg|gv!|v\*?)"
# Rivin lopussa olevat merkinnät, esim. "g,l", "l gv!", "(L, PG)"
TRAILING_DIETS_RE = re.compile(
    rf"[\s,]*\(?\s*({DIET_TOKEN}(?:[\s,]+{DIET_TOKEN})*)\s*\)?\s*$", re.IGNORECASE
)
PAREN_DIETS_RE = re.compile(rf"\(\s*({DIET_TOKEN}(?:[\s,]+{DIET_TOKEN})*)\s*\)", re.IGNORECASE)
PRICE_RE = re.compile(r"(\d{1,2})(?:[.,](\d{1,2}))?\s*€")
# lounaat.infon hintakenttä ja vapaamuotoiset hinnat: "13,90e", "13 euro"
LOOSE_PRICE_RE = re.compile(r"(\d{1,2})(?:[.,](\d{1,2}))?\s*(?:€|e\b|eur\b|euroa?\b)", re.IGNORECASE)


def week_dates(today: date):
    monday = today - timedelta(days=today.weekday())
    return [monday + timedelta(days=i) for i in range(5)]


def empty_week() -> dict:
    return {str(i): [] for i in range(5)}


def has_any_items(menus: dict) -> bool:
    return bool(menus) and any(len(items) > 0 for items in menus.values())


def fetch_html(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding if not resp.encoding else resp.encoding
        return resp.text
    except Exception as e:
        print(f"  Virhe haettaessa {url}: {e}")
        return None


def normalize_diets(tokens) -> list:
    found = set()
    for t in tokens:
        key = t.strip().upper()
        if key in DIET_MAP:
            found.add(DIET_MAP[key])
    if "G" in found:
        found.discard("PG")
    return [d for d in DIET_ORDER if d in found]


def format_price(match) -> str:
    euros, cents = match.group(1), match.group(2) or "00"
    return f"{euros},{cents.ljust(2, '0')} €"


def parse_dish_line(text: str):
    """Erottaa annosrivistä nimen, ruokavaliomerkit ja hinnan.

    Esim. "Rapea kana ja mangomajoneesi 13.40 € g,l" -> ("Rapea kana ja mangomajoneesi", ["G", "L"], "13,40 €")
    """
    text = re.sub(r"\s+", " ", text).strip()
    diets = []

    price = None
    pm = PRICE_RE.search(text)
    if pm:
        price = format_price(pm)
        text = (text[: pm.start()] + " " + text[pm.end():]).strip()

    # Merkinnät voivat olla hinnan jälkeen tai ennen sitä, joten poistetaan toistuvasti lopusta
    while True:
        m = TRAILING_DIETS_RE.search(text)
        if not m or m.start() == 0:
            break
        diets += re.split(r"[\s,]+", m.group(1))
        text = text[: m.start()].strip()

    for m in PAREN_DIETS_RE.finditer(text):
        diets += re.split(r"[\s,]+", m.group(1))
    text = PAREN_DIETS_RE.sub("", text)

    text = re.sub(r"\s{2,}", " ", text).strip(" -–,.")
    return text, normalize_diets(diets), price


def match_weekday(line: str):
    """Palauttaa viikonpäivän indeksin (0-4), jos rivi on pelkkä viikonpäivän otsikko."""
    lower = line.lower().strip(" .:-–")
    for idx, day_name in enumerate(WEEKDAYS_FI):
        if lower.startswith(day_name) and len(lower) < 25:
            return idx
    return None


# --------------------------------------------------------------------------
# Harjun Rinkeli (harjunleipomo.fi/lounas)
# --------------------------------------------------------------------------
def parse_harjun_rinkeli(today: date) -> dict:
    menus = empty_week()
    html = fetch_html("https://harjunleipomo.fi/lounas")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")

    heading = None
    for h in soup.find_all(["h2", "h3"]):
        if re.search(r"Harjun\s+Rinkeli\s*-\s*lounasravintola", h.get_text(" "), re.IGNORECASE):
            heading = h
            break
    if heading is None:
        print("  Harjun Rinkelin osiota ei löytynyt")
        return menus

    parts = []
    for sib in heading.find_next_siblings():
        if sib.name in ("h2", "h3"):
            break
        parts.append(sib.get_text("\n"))
    lines = [l.strip() for l in "\n".join(parts).splitlines() if l.strip()]

    week_m = re.search(r"\(vk\.?\s*(\d{1,2})\)", " ".join(lines), re.IGNORECASE)
    if week_m and int(week_m.group(1)) != today.isocalendar()[1]:
        print(f"  Harjun Rinkelin lista on viikolta {week_m.group(1)}, ohitetaan")
        return menus

    current = None
    for line in lines:
        day_idx = match_weekday(line)
        if day_idx is not None:
            current = day_idx
            continue
        if current is None:
            continue
        lower = line.lower()
        if lower.startswith(("lounas tarjolla", "lounas sisältää", "lauantai", "sunnuntai")):
            break
        title, diets, price = parse_dish_line(line)
        if len(title) > 2:
            menus[str(current)].append({"title": title, "diets": diets, "price": price})
    return menus


# --------------------------------------------------------------------------
# lounaat.info (Ravintola Hilda)
# --------------------------------------------------------------------------
# Rivit, jotka eivät ole annoksia (maksutavat, puuttuva lista ym.)
LOUNAAT_INFO_SKIP = re.compile(
    r"^(meillä käy|maksutavat|lounaslista puuttuu|päivän lounaslista puuttuu|lounas kello)", re.IGNORECASE
)


def parse_lounaat_info(url: str, today: date, package_title: str = None):
    """lounaat.info-sivun viikkolista.

    package_title: jos annettu, päivän rivit kootaan yhdeksi kokonaisuudeksi (esim. buffet),
    jonka hinta poimitaan riviltä kuten "Koko setti 13 euro".

    Palauttaa (menus, info), jossa info on lista lisätietoriveistä
    (esim. "Sisältää: salaattipöytä, leipä, kahvi").
    """
    menus = empty_week()
    info = []
    html = fetch_html(url)
    if not html:
        return menus, info
    soup = BeautifulSoup(html, "html.parser")
    dates = week_dates(today)

    for item in soup.select("div.item"):
        header = item.select_one(".item-header h3")
        if not header:
            continue
        header_text = header.get_text(" ", strip=True)
        day_idx = match_weekday(header_text)
        if day_idx is None:
            continue
        # Otsikko on muotoa "Maanantaina 12.10." – varmistetaan, että päivä on tältä viikolta
        dm = re.search(r"(\d{1,2})\.(\d{1,2})\.", header_text)
        if dm:
            d = dates[day_idx]
            if (int(dm.group(1)), int(dm.group(2))) != (d.day, d.month):
                continue

        day_items = []
        package_price = None
        for li in item.select("li.menu-item"):
            price_el = li.select_one(".price")
            field_price = None
            if price_el:
                pm = LOOSE_PRICE_RE.search(price_el.get_text(" ", strip=True))
                field_price = format_price(pm) if pm else None
                price_el.decompose()
            dish = li.select_one(".dish") or li
            diets = normalize_diets(a.get_text(strip=True) for a in dish.select("a.diet"))
            for a in dish.select("a.diet"):
                a.decompose()
            text = re.sub(r"\s+", " ", dish.get_text(" ")).strip()
            if not text or LOUNAAT_INFO_SKIP.search(text):
                continue
            if re.match(r"^sisältää\b", text, re.IGNORECASE):
                if text not in info:
                    info.append(text)
                continue

            if package_title:
                # "Kaikki 13 euro" / "Koko setti 13 euro!" -> kokonaisuuden hinta
                pm = LOOSE_PRICE_RE.search(text)
                if pm and re.search(r"(kaikki|koko|setti|hinta|lounas)", text, re.IGNORECASE):
                    package_price = format_price(pm)
                    continue
                title, extra_diets, _ = parse_dish_line(text)
                if title:
                    day_items.append({"title": title, "diets": normalize_diets(diets + extra_diets)})
                continue

            title, extra_diets, price = parse_dish_line(text)
            diets = normalize_diets(diets + extra_diets)
            price = field_price or price
            if not title:
                continue

            # lounaat.infoon syötetyt jatkorivit yhdistetään edelliseen annokseen:
            # "Porsaanleike l" + "Pyydettäessä g"  ->  "Porsaanleike" (L, G pyydettäessä)
            # "Jauhelihamureke" + "Bolognesekastikkeessa" -> "Jauhelihamureke bolognesekastikkeessa"
            if day_items and title.lower() == "pyydettäessä":
                prev = day_items[-1]
                prev["diets"] = normalize_diets(prev["diets"] + ["PG" if d == "G" else d for d in diets])
                continue
            if day_items and " " not in title and re.search(r"(ssa|ssä)$", title.lower()):
                prev = day_items[-1]
                prev["title"] = f"{prev['title']} {title.lower()}"
                continue

            day_items.append({"title": title, "diets": diets, "price": price})

        if package_title and day_items:
            common = set.intersection(*(set(p["diets"]) for p in day_items))
            day_items = [{
                "title": package_title,
                "diets": [d for d in DIET_ORDER if d in common],
                "price": package_price,
                "parts": day_items,
            }]
        menus[str(day_idx)] = day_items
    return menus, info


# --------------------------------------------------------------------------
# Compass Group / Food & Co (Kulma, Coccolita)
# --------------------------------------------------------------------------
def parse_compass(url: str, today: date) -> dict:
    menus = empty_week()
    html = fetch_html(url)
    if not html:
        return menus

    m = re.search(r"window\.__INITIAL_MENU__\s*=\s*", html)
    if not m:
        print(f"  __INITIAL_MENU__-dataa ei löytynyt: {url}")
        return menus
    try:
        data, _ = json.JSONDecoder().raw_decode(html, m.end())
    except ValueError as e:
        print(f"  Menu-JSONin jäsennys epäonnistui ({url}): {e}")
        return menus

    dates = [d.isoformat() for d in week_dates(today)]
    for day in (data.get("weekMenu") or {}).get("menus", []):
        day_date = (day.get("date") or "")[:10]
        if day_date not in dates:
            continue
        day_idx = dates.index(day_date)

        items = []
        for pkg in sorted(day.get("menuPackages", []), key=lambda p: p.get("sortOrder", 0)):
            parts = []
            for meal in pkg.get("meals", []):
                raw = meal.get("name", "")
                diets = list(meal.get("diets") or [])
                # "(pyyd. Glut)" = saatavilla gluteenittomana pyydettäessä
                if re.search(r"\(\s*pyyd\.?\s*glut\.?\s*\)", raw, re.IGNORECASE):
                    raw = re.sub(r"\(\s*pyyd\.?\s*glut\.?\s*\)", "", raw, flags=re.IGNORECASE)
                    diets.append("PG")
                name, inline_diets, _ = parse_dish_line(raw)
                if name:
                    parts.append({"title": name, "diets": normalize_diets(diets + inline_diets)})
            if not parts:
                continue

            pkg_name = (pkg.get("name") or "").strip()
            pkg_name = pkg_name[:1].upper() + pkg_name[1:].lower()
            price_m = PRICE_RE.search(pkg.get("price") or "")
            # Kokonaisuuden jokainen osa näytetään omine merkintöineen (parts).
            # Kokonaisuuden "diets" = merkinnät, jotka sopivat kaikkiin osiin.
            common = set.intersection(*(set(p["diets"]) for p in parts))
            items.append({
                "title": pkg_name or "Lounas",
                "diets": [d for d in DIET_ORDER if d in common],
                "price": format_price(price_m) if price_m else None,
                "parts": parts,
            })
        menus[str(day_idx)] = items
    return menus


# --------------------------------------------------------------------------
# Pancho Villa
# --------------------------------------------------------------------------
def parse_pancho():
    """Palauttaa (menus, staticNote)."""
    menus = empty_week()
    html = fetch_html("https://panchovilla.fi/ravintolat/kokkola/")
    if not html:
        return menus, None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]

    mex_price = "13,00 €"
    for line in lines:
        pm = re.match(r"MEKSIKOLOUNAS\s+(.*)", line, re.IGNORECASE)
        if pm and PRICE_RE.search(pm.group(1)):
            mex_price = format_price(PRICE_RE.search(pm.group(1)))
            break

    # Lista toistuu sivulla (mobiili/työpöytä), joten otetaan kunkin rivin ensimmäinen esiintymä
    for idx, day_name in enumerate(WEEKDAYS_FI):
        for line in lines:
            dm = re.match(rf"{day_name}\s*[–\-:]\s*(.+)", line, re.IGNORECASE)
            if dm:
                title, diets, _ = parse_dish_line(dm.group(1))
                if title:
                    menus[str(idx)].append({
                        "title": f"Meksikolounas: {title}",
                        "diets": diets,
                        "price": mex_price,
                    })
                break

    special = None
    static = []
    for line in lines:
        upper = line.upper()
        if special is None and upper.startswith("SPECIAL BURGER"):
            rest = re.sub(r"^SPECIAL\s+BURGER\s*[–\-:]?\s*", "", line, flags=re.IGNORECASE)
            title, diets, price = parse_dish_line(rest)
            special = {"title": f"Viikon Special Burger: {title}", "diets": diets, "price": price}
        elif upper.startswith(("PANCHON BURGERIT", "LOUNASSALAATTI", "LOUNASPIHVI")):
            if not any(s.split()[0].upper() == line.split()[0].upper() for s in static):
                first, _, rest = re.sub(r"\s+", " ", line).partition(" ")
                line_text = f"{first.capitalize()} {rest}".strip()
                static.append(re.sub(r"\(PG\)", "(gluteeniton pyydettäessä)", line_text))

    if special and has_any_items(menus):
        for idx in range(5):
            menus[str(idx)].append(dict(special))
    return menus, static or None


# --------------------------------------------------------------------------
def main():
    now_fi = datetime.now(ZoneInfo("Europe/Helsinki"))
    today = now_fi.date()
    iso_year, iso_week, _ = today.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"
    updated_str = now_fi.strftime("%d.%m.%Y klo %H.%M")

    # Edellisen ajon data käytetään vain, jos se on samalta viikolta
    previous_menus = {}
    if os.path.exists("lounasdata.json"):
        try:
            with open("lounasdata.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
            if old_data.get("week") == week_key:
                for r in old_data.get("restaurants", []):
                    if has_any_items(r.get("menus", {})):
                        previous_menus[r["id"]] = r["menus"]
        except Exception:
            pass

    def ensure_menus(new_m: dict, rest_id: str) -> dict:
        prev = previous_menus.get(rest_id, {})
        result = {}
        for d in range(5):
            key = str(d)
            items = new_m.get(key) or []
            if not items:
                prev_items = prev.get(key) or []
                if prev_items and prev_items != [PLACEHOLDER_ITEM]:
                    items = prev_items
                else:
                    items = [dict(PLACEHOLDER_ITEM)]
            result[key] = items
        found = sum(1 for d in range(5) if new_m.get(str(d)))
        print(f"  {rest_id}: {found}/5 päivää haettu")
        return result

    print("Haetaan Harjun Rinkeli...")
    rinkeli_menus = parse_harjun_rinkeli(today)

    print("Haetaan Ravintola Hilda...")
    hilda_menus, _ = parse_lounaat_info("https://www.lounaat.info/lounas/hilda/kokkola", today)

    print("Haetaan Smokey Road Diner & Bar...")
    smokey_menus, smokey_info = parse_lounaat_info(
        "https://www.lounaat.info/lounas/smokey-road-diner-bar-oy/kokkola", today
    )

    print("Haetaan Ravintola Kulma...")
    kulma_menus = parse_compass(
        "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/kulma/", today
    )

    print("Haetaan Pancho Villa...")
    pancho_menus, pancho_static = parse_pancho()

    print("Haetaan Madez Lounge...")
    madez_menus, _ = parse_lounaat_info(
        "https://www.lounaat.info/lounas/madez-lounge/kokkola", today, package_title="Lounasbuffet"
    )

    print("Haetaan Food & Co Coccolita...")
    coccolita_menus = parse_compass(
        "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/coccolita/", today
    )

    # Koordinaatit: OpenStreetMap / Nominatim
    data = {
        "updated": updated_str,
        "week": week_key,
        "city": "Kokkola",
        "restaurants": [
            {
                "id": "harjun-rinkeli",
                "name": "Harjun Rinkeli",
                "address": "Rantakatu 5",
                "hours": "10:30–14:00",
                "price": "10,00–14,00 €",
                "lat": 63.83664,
                "lon": 23.13242,
                "url": "https://harjunleipomo.fi/lounas",
                "source": {"label": "Harjun Leipomo", "url": "https://harjunleipomo.fi/lounas"},
                "menus": ensure_menus(rinkeli_menus, "harjun-rinkeli"),
            },
            {
                "id": "hilda",
                "name": "Ravintola Hilda",
                "address": "Rantakatu 7",
                "hours": "10:30–14:00",
                "price": "13,40 €",
                "lat": 63.83692,
                "lon": 23.13222,
                "url": "https://www.ravintolahilda.fi",
                "source": {"label": "lounaat.info", "url": "https://www.lounaat.info/lounas/hilda/kokkola"},
                "menus": ensure_menus(hilda_menus, "hilda"),
            },
            {
                "id": "smokey-road",
                "name": "Smokey Road Diner & Bar",
                "address": "Rantakatu 9",
                "hours": "10:30–14:00",
                "price": "13,90–15,90 €",
                "lat": 63.83710,
                "lon": 23.13218,
                "url": "https://smokeyroaddiner.fi/",
                "source": {
                    "label": "lounaat.info",
                    "url": "https://www.lounaat.info/lounas/smokey-road-diner-bar-oy/kokkola",
                },
                "info": smokey_info,
                "menus": ensure_menus(smokey_menus, "smokey-road"),
            },
            {
                "id": "kulma",
                "name": "Ravintola Kulma",
                "address": "Rantakatu 10",
                "hours": "10:45–14:00",
                "price": "13,60 €",
                "lat": 63.83697,
                "lon": 23.13299,
                "url": "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/kulma/",
                "source": {
                    "label": "Compass Group",
                    "url": "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/kulma/",
                },
                "menus": ensure_menus(kulma_menus, "kulma"),
            },
            {
                "id": "pancho-villa",
                "name": "Pancho Villa",
                "address": "Tehtaankatu 3–5",
                "hours": "11:00–14:00",
                "price": "13,00–15,00 €",
                "lat": 63.83617,
                "lon": 23.13454,
                "url": "https://panchovilla.fi/ravintolat/kokkola/",
                "source": {"label": "panchovilla.fi", "url": "https://panchovilla.fi/ravintolat/kokkola/"},
                "staticNote": pancho_static or PANCHO_STATIC_FALLBACK,
                "menus": ensure_menus(pancho_menus, "pancho-villa"),
            },
            {
                "id": "madez",
                "name": "Madez Lounge",
                "address": "Isokatu 15",
                "hours": "11:00–14:00",
                "price": "13,00 €",
                "lat": 63.83824,
                "lon": 23.13037,
                "url": "https://www.madez.fi/",
                "source": {"label": "lounaat.info", "url": "https://www.lounaat.info/lounas/madez-lounge/kokkola"},
                "menus": ensure_menus(madez_menus, "madez"),
            },
            {
                "id": "coccolita",
                "name": "Food & Co Coccolita",
                "address": "Kauppatori 5",
                "hours": "10:30–13:30",
                "price": None,
                "lat": 63.84027,
                "lon": 23.13416,
                "url": "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/coccolita/",
                "source": {
                    "label": "Compass Group",
                    "url": "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/coccolita/",
                },
                "menus": ensure_menus(coccolita_menus, "coccolita"),
            },
        ],
    }

    with open("lounasdata.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Valmis! lounasdata.json päivitetty ({updated_str}).")


if __name__ == "__main__":
    main()
