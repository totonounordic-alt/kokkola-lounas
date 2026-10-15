"""Tekee index.html:stä Tampereen version tampere/index.html.

Sivu on molemmissa sama, mutta somepalvelut (WhatsApp, Facebook, Slack ym.)
eivät aja JavaScriptiä, vaan lukevat linkin esikatselun (otsikko, kuvaus, kuva)
suoraan HTML:stä. Cloudflaren URL Rewrite -sääntö ohjaa osoitteen
ratinanlounas.aihio.fyi/ tähän tiedostoon, joten esikatselussa näkyy Ratina.

Älä muokkaa tampere/index.html:ää käsin: se luodaan automaattisesti
GitHub Actionsissa aina, kun index.html muuttuu.
"""
import os

REPLACEMENTS = [
    ('<head>\n', '<head>\n  <!-- Luotu automaattisesti make_city_pages.py:llä index.html:stä. Älä muokkaa käsin. -->\n  <base href="/" />\n'),
    ('content="#163b6d" media="(prefers-color-scheme: light)"', 'content="#1b2228" media="(prefers-color-scheme: light)"'),
    ('<title>Rantakadun lounas – Kokkola</title>', '<title>Ratinan lounas – Tampere</title>'),
    (
        '<meta name="description" content="Kokkolan keskustan päivän lounaslistat: Harjun Rinkeli, Ravintola Hilda, '
        'Smokey Road Diner, Ravintola Kulma, Pancho Villa, Madez Lounge ja Food & Co Coccolita." />',
        '<meta name="description" content="Ratinan ja Hatanpään päivän lounaslistat: Juvenes Bistro Elisa, Saarni ja '
        'Dynamo, Caffitella, Momento, John Scott\'s, Via Tribunali, Robert\'s Coffee ja Neste Hatanpää." />',
    ),
    ('href="assets/favicon.svg" id="faviconSvg"', 'href="assets/favicon-ratina.svg" id="faviconSvg"'),
    ('href="assets/favicon-32.png" id="faviconPng"', 'href="assets/ratina-favicon-32.png" id="faviconPng"'),
    ('href="assets/apple-touch-icon.png" id="touchIcon"', 'href="assets/ratina-apple-touch-icon.png" id="touchIcon"'),
    ('<meta property="og:title" content="Rantakadun lounas – Kokkola" />', '<meta property="og:title" content="Ratinan lounas – Tampere" />'),
    (
        '<meta property="og:description" content="Rantakadun päivän lounaslistat yhdellä silmäyksellä." />',
        '<meta property="og:description" content="Ratinan päivän lounaslistat yhdellä silmäyksellä." />',
    ),
    (
        'content="https://rantakadunlounas.aihio.fyi/assets/og-image.jpg"',
        'content="https://ratinanlounas.aihio.fyi/assets/og-image-tampere.jpg"',
    ),
]


def main():
    with open("index.html", encoding="utf-8") as f:
        html = f.read()
    for old, new in REPLACEMENTS:
        if html.count(old) != 1:
            raise SystemExit(f"make_city_pages.py: tekstiä ei löytynyt tasan kerran: {old[:60]!r}")
        html = html.replace(old, new)
    os.makedirs("tampere", exist_ok=True)
    with open("tampere/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("tampere/index.html päivitetty")


if __name__ == "__main__":
    main()
