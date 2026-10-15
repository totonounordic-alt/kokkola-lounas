import io
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
    "VE": "Veg",  # John Scott's, Robert's Coffee
    "VEGAANINEN": "Veg",  # Via Tribunali
}
DIET_ORDER = ["G", "PG", "L", "VL", "M", "Veg"]

DIET_TOKEN = r"(?:g|l|m|vl|veg|ve|pg|gv!|v\*?)"
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


def fetch_json(url: str):
    try:
        resp = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Virhe haettaessa {url}: {e}")
        return None


def fetch_bytes(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.content
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
    r"^(meillä käy|maksutavat|lounaslista puuttuu|päivän lounaslista puuttuu|lounas kello|tervetuloa)", re.IGNORECASE
)


# Lisätietorivit, jotka näytetään kortissa annosten sijaan
LOUNAAT_INFO_NOTE = re.compile(
    r"^(sisältää\b|lounas on tarjolla|lounasbuffet sisältää|huomio|klo \d)", re.IGNORECASE
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
            if LOUNAAT_INFO_NOTE.search(text):
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
# ==========================================================================
# TAMPERE (Ratina)
# ==========================================================================
def split_diet_groups(text: str):
    """"Rapeaa seitiä (M, G), tartarkastiketta (L, G) ja perunamuusia (L, G)"
    -> ("Rapeaa seitiä, tartarkastiketta ja perunamuusia", ["G"])

    Kun rivillä on useita merkintäryhmiä, koko annokselle annetaan vain merkinnät,
    jotka ovat jokaisessa ryhmässä (esim. G yllä). Sulkeissa oleva "(G)" yhden
    merkinnän sisällä tarkoittaa "pyydettäessä".
    """
    groups = []

    def grab(m):
        tokens = [t for t in re.split(r"[\s,]+", m.group(1)) if t]
        groups.append(normalize_diets(tokens))
        return ""

    title = re.sub(rf"\(\s*({DIET_TOKEN}(?:[\s,]+{DIET_TOKEN})*)\s*\)", grab, text, flags=re.IGNORECASE)
    # Alkuperämerkinnät, esim. "(suomalaista nautaa ja possua)", pois otsikosta
    title = re.sub(r"\(\s*suomalaista[^)]*\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+([,.])", r"\1", title)
    title = re.sub(r",\s*(ja|&)\s", r" \1 ", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" ,-–")
    diets = sorted(set.intersection(*(set(g) for g in groups)), key=DIET_ORDER.index) if groups else []
    return title, diets


def package(title: str, parts: list, price=None) -> dict:
    """Annoskokonaisuus (buffet tms.), jonka osat näytetään omine merkintöineen."""
    common = set.intersection(*(set(p["diets"]) for p in parts)) if parts else set()
    return {
        "title": title,
        "diets": [d for d in DIET_ORDER if d in common],
        "price": price,
        "parts": parts,
    }


def parse_jamix(kitchen_id: int, today: date, customer: str = "93077") -> dict:
    """Juvenes-ravintolat (Jamix-ruokalistapalvelu)."""
    menus = empty_week()
    data = fetch_json(
        f"https://fi.jamix.cloud/apps/menuservice/rest/haku/menu/{customer}/{kitchen_id}?lang=fi&type=json"
    )
    if not data:
        return menus
    dates = {d.strftime("%Y%m%d"): i for i, d in enumerate(week_dates(today))}
    for kitchen in data:
        for menu_type in kitchen.get("menuTypes", []):
            for menu in menu_type.get("menus", []):
                for day in menu.get("days", []):
                    idx = dates.get(str(day.get("date")))
                    if idx is None:
                        continue
                    for option in day.get("mealoptions", []):
                        parts = []
                        for item in option.get("menuItems", []):
                            name = re.sub(r"\s+", " ", item.get("name") or "").strip()
                            if not name:
                                continue
                            diets = normalize_diets(re.split(r"[,\s]+", item.get("diets") or ""))
                            parts.append({"title": name, "diets": diets})
                        if parts:
                            title = (option.get("name") or "Lounas").strip()
                            menus[str(idx)].append(package(title[:1].upper() + title[1:].lower(), parts))
    return menus


def parse_huili(today: date) -> dict:
    """Neste Hatanpää / Huili: viikon lista PDF:nä, päivän lista sivulla."""
    menus = empty_week()
    html = fetch_html("https://huilipiste.fi/lounas/")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    price = "13,90 €"
    pm = re.search(r"Lounas[^.]{0,60}?(\d{1,2}[.,]\d{2})\s*€", soup.get_text(" "))
    if pm:
        price = format_price(PRICE_RE.search(pm.group(1) + " €"))

    def dish(text):
        title, diets = split_diet_groups(text)
        return {"title": title, "diets": diets} if title else None

    # Viikon lista PDF:stä
    pdf_link = next((a["href"] for a in soup.find_all("a", href=True) if a["href"].lower().endswith(".pdf")), None)
    if pdf_link:
        content = fetch_bytes(pdf_link)
        if content:
            try:
                from pypdf import PdfReader

                text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
                day_names = [d.upper() for d in WEEKDAYS_FI] + ["LAUANTAI", "SUNNUNTAI"]
                current = None
                buffer = []

                def flush():
                    if current is not None and current < 5 and buffer:
                        parts = [x for x in (dish(b) for b in buffer) if x]
                        if parts:
                            menus[str(current)] = [package("Lounasbuffet", parts, price)]

                for raw in text.splitlines():
                    line = raw.strip()
                    if line.upper() in day_names:
                        flush()
                        current, buffer = day_names.index(line.upper()), []
                        continue
                    if current is None or not line:
                        continue
                    if line.startswith("•"):
                        buffer.append(line.lstrip("• ").strip())
                    elif buffer and not re.match(r"^(hintaan|lounas|kaikki|l = )", line, re.IGNORECASE):
                        buffer[-1] += " " + line
                flush()
            except Exception as e:
                print(f"  Huilin PDF:n luku epäonnistui: {e}")

    # Päivän lista sivulta on aina ajantasaisin -> korvaa tämän päivän
    if today.weekday() < 5:
        lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
        # Sivun päivämäärä (esim. "perjantai 16.10.") on oltava tämä päivä
        i_today = lines.index("Päivän lounas") if "Päivän lounas" in lines else None
        date_ok = i_today is not None and any(
            f"{today.day}.{today.month}." in l for l in lines[max(0, i_today - 2): i_today]
        )
        if i_today is not None and not date_ok:
            print("  Huilin päivän lista ei ole tältä päivältä, käytetään PDF:ää")
        if date_ok:
            i = i_today + 1
            today_parts = []
            for line in lines[i:]:
                if line.lower().startswith("katso koko viikon"):
                    break
                if PRICE_RE.search(line) or re.match(r"^\d{1,2}[.,]\d{2}\s*€$", line):
                    continue
                # Sivulla merkinnät ilman sulkeita: "Herkkusienikeitto L, G"
                line = re.sub(rf"((?:\s|,)+{DIET_TOKEN}(?:\s*,\s*{DIET_TOKEN})*)(?=,|\s+ja\s|$)",
                              lambda m: f" ({m.group(1).strip(' ,')})", line, flags=re.IGNORECASE)
                d = dish(line)
                if d:
                    today_parts.append(d)
            if today_parts:
                menus[str(today.weekday())] = [package("Lounasbuffet", today_parts, price)]
    return menus


def parse_momento() -> dict:
    """Momento Ratina: viikkotarjoukset (ma-pe), samat koko viikon."""
    menus = empty_week()
    html = fetch_html("https://ristorantemomento.fi/momento/ratina/")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    anchor = soup.find(string=re.compile(r"Viikkotarjoukset", re.IGNORECASE))
    if not anchor:
        return menus
    items = []
    for h3 in anchor.find_all_next("h3"):
        price_el = h3.select_one(".hinta")
        if not price_el:
            if items:
                break
            continue
        pm = re.search(r"(\d{1,2})[.,](\d{2})", price_el.get_text())
        price = f"{pm.group(1)},{pm.group(2)} €" if pm else None
        price_el.decompose()
        desc_el = h3.find_next_sibling("p")
        desc = desc_el.get_text(" ", strip=True).lstrip("-– ").strip() if desc_el and "kuvaus" in (desc_el.get("class") or []) else None
        title, diets, _ = parse_dish_line(h3.get_text(" ", strip=True))
        items.append({"title": title, "diets": diets, "price": price, **({"desc": desc} if desc else {})})
    for d in range(5):
        menus[str(d)] = [dict(i) for i in items]
    return menus


def parse_johnscotts(today: date):
    """John Scott's Ratina: viikon lounaslista. Palauttaa (menus, staticNote)."""
    menus = empty_week()
    html = fetch_html("https://johnscotts.se/ratina/john-scotts-lunch/")
    if not html:
        return menus, None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    week_i = next((i for i, l in enumerate(lines) if re.match(r"^Viikko\s+\d+$", l)), None)
    if week_i is None:
        return menus, None
    if int(lines[week_i].split()[-1]) != today.isocalendar()[1]:
        print(f"  John Scott'sin lista on viikolta {lines[week_i]}, ohitetaan")
        return menus, None

    price, current, static = None, None, []
    for line in lines[week_i + 1:]:
        if price is None and re.match(r"^\d{1,2}[.,]\d{2}\s*€$", line):
            price = format_price(PRICE_RE.search(line))
            continue
        day_idx = match_weekday(line)
        if day_idx is not None and line.lower().strip(":") in WEEKDAYS_FI:
            current = day_idx
            continue
        if re.match(r"^viikon leike", line, re.IGNORECASE):
            current = "static"
            continue
        if line.startswith("John Scott's Ratina") or line.startswith("John Scott’s Ratina"):
            break
        if current == "static":
            if PRICE_RE.search(line) or not static:
                static.append(line)
            else:
                static[-1] += f" – {line}"
        elif current is not None:
            title, diets = split_diet_groups(line)
            if title and not title.endswith(":"):
                menus[str(current)].append({"title": title, "diets": diets})
    for d in range(5):
        if menus[str(d)]:
            menus[str(d)] = [package("Päivän lounas", menus[str(d)], price)]
    return menus, static or None


def parse_via_tribunali(today: date):
    """Via Tribunali: viikon lounasmenu (sama koko viikon). Palauttaa (menus, info)."""
    menus = empty_week()
    html = fetch_html("https://viatribunali.fi/lounasmenu/")
    if not html:
        return menus, []
    soup = BeautifulSoup(html, "html.parser")
    week_h = soup.find(lambda t: t.name in ("h1", "h2", "h3") and re.match(r"^\s*Viikko\s+\d+", t.get_text()))
    if not week_h:
        return menus, []
    if int(re.search(r"\d+", week_h.get_text()).group()) != today.isocalendar()[1]:
        print(f"  Via Tribunalin lista on {week_h.get_text(strip=True)}, ohitetaan")
        return menus, []

    items = []
    for h4 in week_h.find_all_next("h4"):
        box = h4.find_parent("div", class_="col-lg-8") or h4.parent
        name = h4.get_text(" ", strip=True)
        price_el = box.select_one("p.sdm-3xl--text")
        pm = re.search(r"\d{1,2}(?:[.,]\d{2})?", price_el.get_text()) if price_el else None
        price = format_price(PRICE_RE.search(pm.group() + " €")) if pm else None
        sub_el = box.select_one("p.sdm-xl--text")
        diet_els = box.select("p.sdm--uppercase")
        diet_text = diet_els[-1].get_text(" ", strip=True) if diet_els else ""
        # "L (G)" = laktoositon, gluteeniton pyydettäessä; "Vegaaninen" = vegaaninen
        base = re.sub(r"\(.*?\)", "", diet_text)
        optional = " ".join(re.findall(r"\((.*?)\)", diet_text))
        diets = normalize_diets(re.split(r"[\s,]+", base))
        if re.search(r"\bG\b", optional):
            diets = normalize_diets(diets + ["PG"])
        skip = {id(e) for e in [price_el, sub_el, *diet_els] if e}
        desc = " ".join(
            p.get_text(" ", strip=True) for p in box.find_all("p") if id(p) not in skip and p.get_text(strip=True)
        )
        title = f"{name} ({sub_el.get_text(strip=True).lower()})" if sub_el else name
        items.append({"title": title, "diets": diets, "price": price, **({"desc": desc} if desc else {})})
    info = []
    if soup.find(string=re.compile(r"Gluteeniton pizzataikina", re.IGNORECASE)):
        info.append("Gluteeniton pizzataikina +4 € (keittiö ei ole gluteeniton ympäristö).")
    for d in range(5):
        menus[str(d)] = [dict(i) for i in items]
    return menus, info


def parse_caffitella(today: date) -> dict:
    """Caffitella Ratina: viikon lounasbuffet (Ratinan lista omana osionaan)."""
    menus = empty_week()
    html = fetch_html("https://www.caffitella.fi/lounaslista/")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    starts = [i for i, l in enumerate(lines) if l.lower().startswith("tämä lounaslista tarjolla")]
    block = None
    for n, i in enumerate(starts):
        if i + 1 < len(lines) and "ratina" in lines[i + 1].lower():
            block = lines[i + 2: starts[n + 1] if n + 1 < len(starts) else len(lines)]
            break
    if not block:
        print("  Caffitellan Ratinan listaa ei löytynyt")
        return menus

    week_m = next((re.search(r"(\d+)", l) for l in block if l.lower().startswith("viikkonumero")), None)
    if week_m and int(week_m.group(1)) != today.isocalendar()[1]:
        print(f"  Caffitellan lista on viikolta {week_m.group(1)}, ohitetaan")
        return menus
    price_line = next((l for l in block if l.lower().startswith("lounasbuffet")), "")
    pm = LOOSE_PRICE_RE.search(price_line)
    price = format_price(pm) if pm else None

    day_names = [d.upper() for d in WEEKDAYS_FI]
    current, buffet, alacarte, after_salad = None, [], [], False

    def flush():
        if current is None:
            return
        items = []
        if buffet:
            items.append(package("Lounasbuffet", buffet, price))
        if alacarte:
            items.append(package("Annosruoka (ei sisälly buffettiin)", alacarte))
        menus[str(current)] = items

    for line in block:
        if line.upper() in day_names:
            flush()
            current, buffet, alacarte, after_salad = day_names.index(line.upper()), [], [], False
            continue
        if line.upper() == "LAUANTAI" or line.startswith("G = "):
            break
        if current is None:
            continue
        # Lähteessä edellisen rivin merkintä voi alkaa seuraavalta riviltä: "(L) Burger salaatti (G,L)"
        lead = re.match(rf"^(\(\s*{DIET_TOKEN}(?:[\s,]+{DIET_TOKEN})*\s*\))\s*(.*)$", line, re.IGNORECASE)
        target = alacarte if after_salad else buffet
        if lead and target:
            title, diets = split_diet_groups(f"{target[-1]['title']} {lead.group(1)}")
            target[-1] = {"title": title, "diets": diets}
            line = lead.group(2)
            if not line:
                continue
        line = re.sub(r"\(\s*annosruoka ei sis\.? buffettiin\s*\)", "", line, flags=re.IGNORECASE).strip()
        title, diets = split_diet_groups(line)
        if not title:
            continue
        if title.lower() == "salaattibuffet":
            buffet.append({"title": title, "diets": []})
            after_salad = True
            continue
        (alacarte if after_salad else buffet).append({"title": title, "diets": diets})
    flush()
    return menus


def build_city(path: str, today: date, now_fi: datetime, restaurants: list):
    """Hakee kaupungin ravintoloiden listat ja kirjoittaa ne tiedostoon `path`.

    restaurants: lista ravintoloiden tiedoista, joissa "fetch" palauttaa
    {"menus": {...}, ...lisäkentät}. Yhden ravintolan virhe ei kaada muita.
    """
    iso_year, iso_week, _ = today.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"

    # Edellisen ajon data käytetään vain, jos se on samalta viikolta
    previous_menus = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
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

    out = []
    for spec in restaurants:
        spec = dict(spec)
        fetch = spec.pop("fetch")
        print(f"Haetaan {spec['name']}...")
        try:
            result = fetch() or {}
        except Exception as e:
            print(f"  VIRHE ({spec['id']}): {e}")
            result = {}
        menus = result.pop("menus", None) or empty_week()
        for key, value in result.items():
            if value:
                spec[key] = value
        spec["menus"] = ensure_menus(menus, spec["id"])
        out.append(spec)

    data = {
        "updated": now_fi.strftime("%d.%m.%Y klo %H.%M"),
        "week": week_key,
        "restaurants": out,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Valmis! {path} päivitetty ({data['updated']}).")


def kokkola_restaurants(today: date) -> list:
    def lounaat(url, **kw):
        menus, info = parse_lounaat_info(url, today, **kw)
        return {"menus": menus, "info": info}

    def pancho():
        menus, static = parse_pancho()
        return {"menus": menus, "staticNote": static}

    compass = "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/"
    # Koordinaatit: OpenStreetMap / Nominatim
    return [
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
            "fetch": lambda: {"menus": parse_harjun_rinkeli(today)},
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
            "fetch": lambda: lounaat("https://www.lounaat.info/lounas/hilda/kokkola"),
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
            "fetch": lambda: lounaat("https://www.lounaat.info/lounas/smokey-road-diner-bar-oy/kokkola"),
        },
        {
            "id": "kulma",
            "name": "Ravintola Kulma",
            "address": "Rantakatu 10",
            "hours": "10:45–14:00",
            "price": "13,60 €",
            "lat": 63.83697,
            "lon": 23.13299,
            "url": compass + "kulma/",
            "source": {"label": "Compass Group", "url": compass + "kulma/"},
            "fetch": lambda: {"menus": parse_compass(compass + "kulma/", today)},
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
            "staticNote": PANCHO_STATIC_FALLBACK,
            "fetch": pancho,
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
            "fetch": lambda: lounaat(
                "https://www.lounaat.info/lounas/madez-lounge/kokkola", package_title="Lounasbuffet"
            ),
        },
        {
            "id": "coccolita",
            "name": "Food & Co Coccolita",
            "address": "Kauppatori 5",
            "hours": "10:30–13:30",
            "price": None,
            "lat": 63.84027,
            "lon": 23.13416,
            "url": compass + "coccolita/",
            "source": {"label": "Compass Group", "url": compass + "coccolita/"},
            "fetch": lambda: {"menus": parse_compass(compass + "coccolita/", today)},
        },
    ]


def tampere_restaurants(today: date) -> list:
    def johnscotts():
        menus, static = parse_johnscotts(today)
        return {"menus": menus, "staticNote": static}

    def tribunali():
        menus, info = parse_via_tribunali(today)
        return {"menus": menus, "info": info}

    def roberts():
        menus, info = parse_lounaat_info("https://www.lounaat.info/lounas/roberts-coffee-ratina/tampere", today)
        return {"menus": menus, "info": info}

    ratina = {"lat": 61.49327, "lon": 23.76734}  # Kauppakeskus Ratina, Vuolteenkatu 1
    # Koordinaatit: OpenStreetMap / Nominatim
    return [
        {
            "id": "bistro-elisa",
            "name": "Juvenes Bistro Elisa",
            "address": "Vuolteenkatu 2",
            "hours": "10:30–13:00",
            "price": None,
            "lat": 61.49396,
            "lon": 23.76857,
            "url": "https://juvenes.fi/bistroelisa/",
            "source": {"label": "Juvenes / Jamix", "url": "https://juvenes.fi/bistroelisa/"},
            "info": ["Lounas Elisan henkilöstölle ja heidän vierailleen."],
            "fetch": lambda: {"menus": parse_jamix(59, today)},
        },
        {
            "id": "caffitella-ratina",
            "name": "Caffitella Ratina",
            "address": "Vuolteenkatu 1 (Ratina, 1. krs)",
            "hours": "10:30–14:00",
            "price": "11,90 €",
            **ratina,
            "url": "https://www.caffitella.fi/toimipaikat/ratina-tampere/",
            "source": {"label": "caffitella.fi", "url": "https://www.caffitella.fi/lounaslista/"},
            "info": ["Lounaaseen sisältyy salaattipöytä, kahvi ja pieni herkku."],
            "fetch": lambda: {"menus": parse_caffitella(today)},
        },
        {
            "id": "momento-ratina",
            "name": "Momento Ratina",
            "address": "Vuolteenkatu 1 (Ratina, 3. krs)",
            "hours": "10:30–15:00",
            "price": "13,70–14,00 €",
            **ratina,
            "url": "https://ristorantemomento.fi/momento/ratina/",
            "source": {"label": "ristorantemomento.fi", "url": "https://ristorantemomento.fi/momento/ratina/"},
            "info": ["Viikkotarjoukset ovat samat koko viikon (ma–pe)."],
            "fetch": lambda: {"menus": parse_momento()},
        },
        {
            "id": "john-scotts-ratina",
            "name": "John Scott's Ratina",
            "address": "Vuolteenkatu 1 (Ratina)",
            "hours": "11:00–14:00",
            "price": "13,50 €",
            "info": ["Keittolounas 11,90 €."],
            **ratina,
            "url": "https://johnscotts.se/ratina/",
            "source": {"label": "johnscotts.se", "url": "https://johnscotts.se/ratina/john-scotts-lunch/"},
            "fetch": johnscotts,
        },
        {
            "id": "via-tribunali",
            "name": "Pizzeria Via Tribunali",
            "address": "Vuolteenkatu 1 (Ratina, 1. krs)",
            "hours": "11:00–14:00",
            "price": "14,00 €",
            **ratina,
            "url": "https://viatribunali.fi/tampere/",
            "source": {"label": "viatribunali.fi", "url": "https://viatribunali.fi/lounasmenu/"},
            "fetch": tribunali,
        },
        {
            "id": "roberts-ratina",
            "name": "Robert's Coffee Ratina",
            "address": "Vuolteenkatu 5",
            "hours": "11:00–14:00",
            "price": "14,50 €",
            "lat": 61.49329,
            "lon": 23.76673,
            "url": "https://www.lounaat.info/lounas/roberts-coffee-ratina/tampere",
            "source": {"label": "lounaat.info", "url": "https://www.lounaat.info/lounas/roberts-coffee-ratina/tampere"},
            "fetch": roberts,
        },
        {
            "id": "juvenes-saarni",
            "name": "Juvenes Saarni",
            "address": "Hatanpään valtatie 11",
            "hours": "10:30–13:00",
            "price": "13,50 €",
            "lat": 61.49138,
            "lon": 23.77070,
            "url": "https://juvenes.fi/saarni/",
            "source": {"label": "Juvenes / Jamix", "url": "https://juvenes.fi/saarni/"},
            "fetch": lambda: {"menus": parse_jamix(32, today)},
        },
        {
            "id": "neste-hatanpaa",
            "name": "Neste Hatanpää (Huili)",
            "address": "Hatanpään valtatie 20",
            "hours": "10:30–15:00",
            "price": "13,90 €",
            "lat": 61.49219,
            "lon": 23.77216,
            "url": "https://huilipiste.fi/lounas/",
            "source": {"label": "huilipiste.fi", "url": "https://huilipiste.fi/lounas/"},
            "info": ["Salaattibuffet 8,90 € tai keittolounas 9,90 €."],
            "fetch": lambda: {"menus": parse_huili(today)},
        },
        {
            "id": "juvenes-dynamo",
            "name": "Juvenes Dynamo",
            "address": "Hatanpään valtatie 24",
            "hours": "10:30–13:00",
            "price": "13,50 €",
            "lat": 61.49095,
            "lon": 23.77210,
            "url": "https://juvenes.fi/restaurant-dynamo/",
            "source": {"label": "Juvenes / Jamix", "url": "https://juvenes.fi/restaurant-dynamo/"},
            "fetch": lambda: {"menus": parse_jamix(54, today)},
        },
    ]


def main():
    import sys

    now_fi = datetime.now(ZoneInfo("Europe/Helsinki"))
    today = now_fi.date()
    cities = {
        "kokkola": ("lounasdata.json", kokkola_restaurants),
        "tampere": ("lounasdata-tampere.json", tampere_restaurants),
    }
    # Oletuksena kaikki kaupungit; esim. `python update_menus.py tampere` hakee vain yhden
    selected = sys.argv[1:] or list(cities)
    for city in selected:
        path, restaurants = cities[city]
        print(f"=== {city.capitalize()} ===")
        try:
            build_city(path, today, now_fi, restaurants(today))
        except Exception as e:
            print(f"VIRHE kaupungin {city} haussa: {e}")


if __name__ == "__main__":
    main()
