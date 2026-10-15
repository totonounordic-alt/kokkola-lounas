import json
import os
import re
from datetime import datetime
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

# Turvaverkko: jos ravintolan verkkosivu estää haun tai on tilapäisesti alhaalla,
# kortti ei koskaan katoa sivulta vaan näyttää nämä perustiedot.
FALLBACK_MENUS = {
    "harjun-rinkeli": {
        str(i): [
            {"title": "Päivän kotiruokalounas noutopöydästä (2 lämmintä pääruokaa)", "diets": ["L", "G"], "price": "13,40 €"},
            {"title": "Päivän keittolounas & leipäpöytä", "diets": ["L", "G"], "price": "11,00 €"},
        ]
        for i in range(5)
    },
    "hilda": {
        str(i): [
            {"title": "Hildan kotiruokalounas, salaattipöytä & jälkiruoka", "diets": ["L", "G"], "price": "13,40 €"},
        ]
        for i in range(5)
    },
    "kulma": {
        str(i): [
            {"title": "Food & Co Buffet-lounas & runsas salaattipöytä", "diets": ["L", "G"], "price": "13,60 €"},
            {"title": "Päivän kasvislounas buffasta", "diets": ["Veg", "M", "G"], "price": "13,60 €"},
            {"title": "Päivän keittolounas", "diets": ["L", "G"], "price": "11,70 €"},
        ]
        for i in range(5)
    },
    "pancho-villa": {
        "0": [
            {"title": "Päivän Meksikolounas (vaihtuva meksikolainen annos)", "diets": ["L"], "price": "13,00 €"},
            {"title": "Viikon Special Burger & ranskalaiset", "diets": ["L", "G"], "price": "15,00 €"},
        ],
        "1": [
            {"title": "Päivän Meksikolounas (vaihtuva meksikolainen annos)", "diets": ["L"], "price": "13,00 €"},
            {"title": "Viikon Special Burger & ranskalaiset", "diets": ["L", "G"], "price": "15,00 €"},
        ],
        "2": [
            {"title": "Päivän Meksikolounas (vaihtuva meksikolainen annos)", "diets": ["L"], "price": "13,00 €"},
            {"title": "Viikon Special Burger & ranskalaiset", "diets": ["L", "G"], "price": "15,00 €"},
        ],
        "3": [
            {"title": "Päivän Meksikolounas (vaihtuva meksikolainen annos)", "diets": ["L"], "price": "13,00 €"},
            {"title": "Viikon Special Burger & ranskalaiset", "diets": ["L", "G"], "price": "15,00 €"},
        ],
        "4": [
            {"title": "Päivän Meksikolounas (vaihtuva meksikolainen annos)", "diets": ["L"], "price": "13,00 €"},
            {"title": "Viikon Special Burger & ranskalaiset", "diets": ["L", "G"], "price": "15,00 €"},
        ],
    },
}


def fetch_soup(url: str):
    """Hakee verkkosivun ja palauttaa BeautifulSoup-olion sekä siistityn tekstin."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        lines = [line.strip() for line in soup.get_text("\n").splitlines()]
        clean_text = "\n".join(line for line in lines if line)
        return soup, clean_text
    except Exception as e:
        print(f"Virhe haettaessa {url}: {e}")
        return None, ""


def extract_diets_and_clean(text: str):
    """Poimii ruokavaliomerkit (L, G, M, Veg, VL) ja siistii annoksen nimen."""
    diets = []
    tokens = re.findall(r"\b(G|L|VL|M|VEG|PG|GV!)\b", text, flags=re.IGNORECASE)
    for t in tokens:
        up = t.upper().replace("!", "")
        if up in ("G", "GV", "PG") and "G" not in diets:
            diets.append("G")
        elif up == "L" and "L" not in diets:
            diets.append("L")
        elif up == "VL" and "VL" not in diets:
            diets.append("VL")
        elif up == "M" and "M" not in diets:
            diets.append("M")
        elif up == "VEG" and "Veg" not in diets:
            diets.append("Veg")

    price_match = re.search(r"(\d{1,2}[.,]\d{2})\s*€", text)
    price = f"{price_match.group(1).replace('.', ',')} €" if price_match else None

    cleaned = re.sub(r"\(\s*[\*,A-Za-z,\s]+\)", "", text)
    cleaned = re.sub(r"\d{1,2}[.,]\d{2}\s*€", "", cleaned)
    cleaned = re.sub(
        r"(\s+|[.,])(g|l|vl|m|veg|gv!|vs|a|ilm)([,\s]+(g|l|vl|m|veg|gv!|vs|a|ilm))*\.?$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–*,.")
    return cleaned, diets, price


def match_weekday_index(line: str):
    """Palauttaa viikonpäivän indeksin (0-4), jos rivi alkaa viikonpäivän nimellä."""
    lower = line.lower().strip(" .:-–")
    for idx, day_name in enumerate(WEEKDAYS_FI):
        if lower.startswith(day_name):
            # Varmistetaan ettei kyseessä ole pitkä leipätekstilause
            if len(lower) < 35:
                return str(idx)
    return None


def parse_lounaat_info(url: str) -> dict:
    """Yleiskäyttöinen jäsennin lounaat.info-sivuille."""
    menus = {str(i): [] for i in range(5)}
    _, raw_text = fetch_soup(url)
    if not raw_text:
        return menus

    current_day = None
    skip_words = [
        "lounaat.info", "arvioi", "rantakatu", "tehtaankatu", "kokkola",
        "lounas kello", "lounas ma-", "lounas arkisin", "lisää suosikiksi",
        "muokkaa", "ilmoita virheestä", "www.", "http"
    ]

    for line in raw_text.splitlines():
        lower = line.lower().strip()
        day_idx = match_weekday_index(lower)
        if day_idx is not None:
            current_day = day_idx
            continue

        if current_day is not None:
            if lower.startswith(("lauantai", "sunnuntai", "aukioloajat", "kartta")):
                current_day = None
                continue
            if len(line) < 3 or any(w in lower for w in skip_words):
                continue
            title, diets, price = extract_diets_and_clean(line)
            if title and len(title) > 2 and len(menus[current_day]) < 6:
                menus[current_day].append({"title": title, "diets": diets, "price": price})

    return menus


def parse_harjun_rinkeli() -> dict:
    """Poimii Harjun Rinkelin viikkolistan Harjun Leipomon sivulta."""
    menus = {str(i): [] for i in range(5)}
    _, raw_text = fetch_soup("https://harjunleipomo.fi/lounas")
    if not raw_text:
        return menus

    # Etsitään se "Harjun Rinkeli" -kohta, jonka jälkeen tekstissä esiintyy viikonpäiviä
    matches = [m.start() for m in re.finditer(r"Harjun\s+Rinkeli", raw_text, flags=re.IGNORECASE)]
    section = raw_text
    for pos in matches:
        window = raw_text[pos : pos + 3500]
        if "maanantai" in window.lower() and "tiistai" in window.lower():
            end_match = re.search(r"Harjun\s+Puffetti", window[50:], flags=re.IGNORECASE)
            section = window[: 50 + end_match.start()] if end_match else window
            break

    current_day = None
    for line in section.splitlines():
        lower = line.lower().strip()
        day_idx = match_weekday_index(lower)
        if day_idx is not None:
            current_day = day_idx
            continue

        if current_day is not None:
            if any(stop in lower for stop in ["kanta-asiakas", "harjun puffetti", "kahvilasta"]):
                break
            if len(line) < 4 or lower.startswith(("lounas tarjolla", "lounas sisältää", "avoinna")):
                continue
            title, diets, price = extract_diets_and_clean(line)
            if title and len(title) > 3 and len(menus[current_day]) < 6:
                menus[current_day].append({"title": title, "diets": diets, "price": price})

    return menus


def parse_kulma() -> dict:
    """Hakee Ravintola Kulman listan ensisijaisesti lounaat.infosta ja toissijaisesti Compass Groupilta."""
    for url in [
        "https://www.lounaat.info/lounas/food-co-kulma/kokkola",
        "https://www.lounaat.info/lounas/kulma/kokkola",
    ]:
        menus = parse_lounaat_info(url)
        if any(len(v) > 0 for v in menus.values()):
            return menus

    return {str(i): [] for i in range(5)}


def parse_pancho() -> dict:
    """Hakee Pancho Villan vaihtuvan Meksikolounaan ja Special Burgerin."""
    # 1. Kokeillaan ensin lounaat.info-sivua, jos lista on siellä
    menus = parse_lounaat_info("https://www.lounaat.info/lounas/pancho-villa/kokkola")
    if any(len(v) > 0 for v in menus.values()):
        return menus

    # 2. Haetaan suoraan Pancho Villan Kokkolan sivulta
    menus = {str(i): [] for i in range(5)}
    _, raw_text = fetch_soup("https://panchovilla.fi/ravintolat/kokkola/")
    if not raw_text:
        return menus

    special_burger = "Viikon Special Burger & ranskalaiset"
    sb_match = re.search(r"SPECIAL\s+BURGER\s*[–\-:]?\s*([^\n]+)", raw_text, flags=re.IGNORECASE)
    if sb_match:
        sb_clean, _, _ = extract_diets_and_clean(sb_match.group(1))
        if sb_clean and len(sb_clean) > 3:
            special_burger = f"Viikon Special Burger: {sb_clean}"

    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    for idx, day_name in enumerate(WEEKDAYS_FI):
        day_key = str(idx)
        # Tapaus A: "Maanantai – Pollo Burrito" samalla rivillä
        inline_match = re.search(rf"{day_name}\s*[–\-:]\s*([^\n]+)", raw_text, flags=re.IGNORECASE)
        if inline_match:
            title, diets, _ = extract_diets_and_clean(inline_match.group(1))
            if title:
                menus[day_key].append({
                    "title": f"Päivän Meksikolounas: {title}",
                    "diets": diets or ["L"],
                    "price": "13,00 €",
                })
        else:
            # Tapaus B: Viikonpäivä omalla rivillään ja annos seuraavalla rivillä
            for i, line in enumerate(lines[:-1]):
                if line.lower().strip(" .:-–") == day_name:
                    title, diets, _ = extract_diets_and_clean(lines[i + 1])
                    if title and len(title) > 3:
                        menus[day_key].append({
                            "title": f"Päivän Meksikolounas: {title}",
                            "diets": diets or ["L"],
                            "price": "13,00 €",
                        })
                    break

        menus[day_key].append({
            "title": special_burger,
            "diets": ["L", "G"],
            "price": "15,00 €",
        })

    return menus


def has_any_items(menus_dict: dict) -> bool:
    return bool(menus_dict) and any(len(items) > 0 for items in menus_dict.values())


def main():
    now_fi = datetime.now(ZoneInfo("Europe/Helsinki"))
    updated_str = now_fi.strftime("%d.%m.%Y klo %H.%M")

    previous_menus = {}
    if os.path.exists("lounasdata.json"):
        try:
            with open("lounasdata.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
                for r in old_data.get("restaurants", []):
                    if has_any_items(r.get("menus", {})):
                        previous_menus[r["id"]] = r["menus"]
        except Exception:
            pass

    print("Haetaan Harjun Rinkeli...")
    rinkeli_menus = parse_harjun_rinkeli()

    print("Haetaan Ravintola Hilda...")
    hilda_menus = parse_lounaat_info("https://www.lounaat.info/lounas/hilda/kokkola")

    print("Haetaan Ravintola Kulma...")
    kulma_menus = parse_kulma()

    print("Haetaan Pancho Villa...")
    pancho_menus = parse_pancho()

    def ensure_menus(new_m: dict, rest_id: str) -> dict:
        if has_any_items(new_m):
            # Jos jokin yksittäinen arkipäivä jäi tyhjäksi, täydennetään se varalistasta
            fallback = previous_menus.get(rest_id, FALLBACK_MENUS[rest_id])
            for d in range(5):
                key = str(d)
                if not new_m.get(key):
                    new_m[key] = fallback.get(key, [])
            return new_m
        if rest_id in previous_menus:
            return previous_menus[rest_id]
        return FALLBACK_MENUS[rest_id]

    data = {
        "updated": updated_str,
        "city": "Kokkola",
        "restaurants": [
            {
                "id": "harjun-rinkeli",
                "name": "Harjun Rinkeli",
                "address": "Rantakatu 5",
                "hours": "10:30–14:00",
                "price": "11,00–14,00 €",
                "lat": 63.8388,
                "lon": 23.1304,
                "url": "https://harjunleipomo.fi/lounas",
                "menus": ensure_menus(rinkeli_menus, "harjun-rinkeli"),
            },
            {
                "id": "hilda",
                "name": "Ravintola Hilda",
                "address": "Rantakatu 7",
                "hours": "10:30–14:00",
                "price": "13,40 €",
                "lat": 63.8386,
                "lon": 23.1308,
                "url": "https://www.ravintolahilda.fi",
                "menus": ensure_menus(hilda_menus, "hilda"),
            },
            {
                "id": "kulma",
                "name": "Ravintola Kulma",
                "address": "Rantakatu 10",
                "hours": "10:45–14:00",
                "price": "13,60 € (Keitto 11,70 €)",
                "lat": 63.8383,
                "lon": 23.1312,
                "url": "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/kulma/",
                "menus": ensure_menus(kulma_menus, "kulma"),
            },
            {
                "id": "pancho-villa",
                "name": "Pancho Villa",
                "address": "Tehtaankatu 3–5",
                "hours": "11:00–14:00",
                "price": "13,00–15,00 €",
                "lat": 63.8378,
                "lon": 23.1322,
                "url": "https://panchovilla.fi/ravintolat/kokkola/",
                "staticNote": [
                    "Lounasburgerit (Pekoni / Juusto / Kana / Kasvis) + ranut 14,00 € (L, saatavilla G)",
                    "Lounaspihvi (Porsaan ulkofilee tai pariloitu kana) 14,50 € (L, G)",
                    "Lounassalaatti (Kana / Halloumi / Lohi) 14,50 € (L, G)",
                ],
                "menus": ensure_menus(pancho_menus, "pancho-villa"),
            },
        ],
    }

    with open("lounasdata.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Valmis! lounasdata.json päivitetty ({updated_str}).")


if __name__ == "__main__":
    main()
