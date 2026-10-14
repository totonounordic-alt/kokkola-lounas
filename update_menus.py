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
        "Chrome/128.0.0.0 Safari/537.36"
    )
}

WEEKDAYS_FI = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai"]


def fetch_text(url: str) -> str:
    """Hakee verkkosivun ja palauttaa sen tekstisisällön riveittäin."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        lines = [line.strip() for line in soup.get_text("\n").splitlines()]
        return "\n".join(line for line in lines if line)
    except Exception as e:
        print(f"Virhe haettaessa {url}: {e}")
        return ""


def extract_diets_and_clean(text: str):
    """Poimii ruokavaliomerkit (L, G, M, Veg, VL) ja siistii annoksen nimen."""
    diets = []
    # Poimitaan sulkeissa tai rivin lopussa olevat merkinnät
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

    # Poimitaan mahdollinen hinta (esim. 13.40 € tai 13,40€)
    price_match = re.search(r"(\d{1,2}[.,]\d{2})\s*€", text)
    price = f"{price_match.group(1).replace('.', ',')} €" if price_match else None

    # Siistitään hinta ja allergeeniluettelot pois otsikosta
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


def parse_harjun_rinkeli(raw_text: str) -> dict:
    """Poimii Harjun Rinkelin viikkolistan Harjun Leipomon yhteissivulta."""
    menus = {str(i): [] for i in range(5)}
    if not raw_text:
        return menus

    # Rajataan vain Harjun Rinkelin osio ennen Harjun Puffettia
    start = raw_text.find("Harjun Rinkeli")
    end = raw_text.find("Harjun Puffetti", start if start != -1 else 0)
    section = raw_text[start:end] if start != -1 else raw_text

    current_day = None
    for line in section.splitlines():
        lower = line.lower().strip(" .:")
        if lower in WEEKDAYS_FI:
            current_day = str(WEEKDAYS_FI.index(lower))
            continue
        if current_day is not None:
            if any(stop in lower for stop in ["lounas tarjolla", "kanta-asiakas", "harjun puffetti"]):
                break
            if len(line) < 4 or line.lower().startswith("lounas "):
                continue
            title, diets, price = extract_diets_and_clean(line)
            if title and len(title) > 3:
                menus[current_day].append({"title": title, "diets": diets, "price": price})

    return menus


def parse_hilda(raw_text: str) -> dict:
    """Poimii Ravintola Hildan viikkolistan."""
    menus = {str(i): [] for i in range(5)}
    if not raw_text:
        return menus

    day_prefixes = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai"]
    current_day = None

    for line in raw_text.splitlines():
        lower = line.lower().strip()
        matched_day = None
        for idx, prefix in enumerate(day_prefixes):
            if lower.startswith(prefix):
                matched_day = str(idx)
                break
        if matched_day is not None:
            current_day = matched_day
            continue

        if current_day is not None:
            if lower.startswith("lounas kello") or lower.startswith("lauantai"):
                current_day = None
                continue
            if len(line) < 3 or any(skip in lower for skip in ["lounaat.info", "arvioi", "rantakatu"]):
                continue
            title, diets, price = extract_diets_and_clean(line)
            if title and len(title) > 2:
                menus[current_day].append({"title": title, "diets": diets, "price": price})

    return menus


def parse_kulma(raw_text: str) -> dict:
    """Poimii Compass Groupin Ravintola Kulman viikkolistan."""
    menus = {str(i): [] for i in range(5)}
    if not raw_text:
        return menus

    # Jaetaan sivu päiväkohtaisiin lohkoihin KASVISLOUNAS BUFFASTA -otsikon kohdalta
    blocks = re.split(r"(?:VEGAANINEN\s+)?KASVISLOUNAS\s+BUFFASTA", raw_text, flags=re.IGNORECASE)
    for day_idx, block in enumerate(blocks[1:6]):
        day_key = str(day_idx)
        for line in block.splitlines():
            line_s = line.strip()
            if not line_s or len(line_s) < 4:
                continue
            if any(
                skip in line_s.upper()
                for skip in ["BUFFASTA", "PÄIVÄN SALAATTI", "13,60", "14,90", "AUKIOLOAJAT", "RANTAKATU"]
            ):
                continue
            # Poimitaan vain varsinaiset pääruokarivit
            if "(" in line_s or any(k in line_s.lower() for k in ["keitto", "kana", "broileri", "liha", "kala", "lohi", "kasvis", "pihvi", "mureke", "pyöry", "vuoka", "kiusaus", "pasta", "pizza"]):
                title, diets, _ = extract_diets_and_clean(line_s)
                if title and len(title) > 3 and len(menus[day_key]) < 4:
                    price = "11,70 €" if "keitto" in title.lower() else "13,60 €"
                    menus[day_key].append({"title": title, "diets": diets, "price": price})

    return menus


def parse_pancho(raw_text: str) -> dict:
    """Poimii Pancho Villan vaihtuvan Meksikolounaan ja viikon Special Burgerin."""
    menus = {str(i): [] for i in range(5)}
    if not raw_text:
        return menus

    # Etsitään viikon Special Burger
    special_burger = "Special Burger (kysy viikon maku)"
    sb_match = re.search(r"SPECIAL BURGER\s*[–-]\s*([^\n]+)", raw_text, flags=re.IGNORECASE)
    if sb_match:
        sb_clean, _, _ = extract_diets_and_clean(sb_match.group(1))
        if sb_clean:
            special_burger = f"Viikon Special Burger: {sb_clean}"

    # Etsitään viikonpäiväkohtaiset Meksikolounaat
    for idx, day_name in enumerate(WEEKDAYS_FI):
        pattern = rf"{day_name}\s*[–-]\s*([^\n]+)"
        m = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if m:
            title, diets, _ = extract_diets_and_clean(m.group(1))
            menus[str(idx)].append(
                {
                    "title": f"Päivän Meksikolounas: {title}",
                    "diets": diets or ["L"],
                    "price": "13,00 €",
                }
            )
        menus[str(idx)].append(
            {
                "title": special_burger,
                "diets": ["L", "G"],
                "price": "15,00 €",
            }
        )

    return menus


def main():
    now_fi = datetime.now(ZoneInfo("Europe/Helsinki"))
    updated_str = now_fi.strftime("%d.%m.%Y klo %H.%M")

    # Ladataan vanha lounasdata.json pohjaksi, jotta mahdollisen yhteysvirheen
    # sattuessa ravintolan aiempi lista säilyy ehjänä
    previous_menus = {}
    if os.path.exists("lounasdata.json"):
        try:
            with open("lounasdata.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
                for r in old_data.get("restaurants", []):
                    previous_menus[r["id"]] = r.get("menus", {})
        except Exception:
            pass

    print("Haetaan Harjun Rinkeli...")
    rinkeli_menus = parse_harjun_rinkeli(fetch_text("https://harjunleipomo.fi/lounas"))

    print("Haetaan Ravintola Hilda...")
    hilda_menus = parse_hilda(fetch_text("https://www.lounaat.info/lounas/hilda/kokkola"))

    print("Haetaan Ravintola Kulma...")
    kulma_menus = parse_kulma(
        fetch_text("https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/kokkola/kulma/")
    )

    print("Haetaan Pancho Villa...")
    pancho_menus = parse_pancho(fetch_text("https://panchovilla.fi/ravintolat/kokkola/"))

    def ensure_menus(new_m, rest_id):
        has_items = any(len(v) > 0 for v in new_m.values())
        return new_m if has_items else previous_menus.get(rest_id, new_m)

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