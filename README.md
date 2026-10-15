# Lounassivut: Kokkola ja Tampere

Kaksi lounaslistasivua samasta koodista:

- **Rantakadun lounas** (Kokkola): https://rantakadunlounas.aihio.fyi
- **Ratinan lounas** (Tampere): https://ratinanlounas.aihio.fyi

Sivulla on Kokkola | Tampere -kytkin. Valinta muistetaan selaimessa. Ensimmäisellä
käynnillä kaupunki päätellään osoitteesta, ja `?kaupunki=tampere` pakottaa valinnan.

## Miten se toimii

1. **GitHub Actions** (`.github/workflows/update-menus.yml`) ajaa `update_menus.py`:n arkiaamuisin.
   Skripti hakee listat ravintoloiden sivuilta ja kirjoittaa ne tiedostoihin
   `lounasdata.json` (Kokkola) ja `lounasdata-tampere.json` (Tampere).
2. **Cloudflare Worker** `kokkola-lounas` julkaisee repon automaattisesti jokaisen
   muutoksen jälkeen (`wrangler.jsonc`, `worker.js`). `.assetsignore` jättää skriptit
   ja työnkulut julkaisun ulkopuolelle.
3. **`index.html`** näyttää datan. Kaupunkikohtaiset asetukset (nimet, teema, kuvat)
   ovat sen `CITIES`-määrittelyssä ja CSS:n `:root[data-city="tampere"]`-säännöissä.
4. **`tampere/index.html`** luodaan automaattisesti `make_city_pages.py`:llä aina, kun
   `index.html` muuttuu. Siinä on Ratinan otsikko ja esikatselukuva, ja Worker
   tarjoilee sen osoitteen ratinanlounas.aihio.fyi etusivuna. Näin somepalvelujen
   linkkiesikatselu näyttää oikean kaupungin. Älä muokkaa tiedostoa käsin.

## Ravintolan lisääminen

Lisää ravintola `update_menus.py`:n funktioon `kokkola_restaurants()` tai
`tampere_restaurants()`. Kenttä `fetch` palauttaa `{"menus": {...}}` ja halutessaan
lisäkenttiä (`info`, `staticNote`). Valmiit jäsentimet:

| Lähde | Funktio |
|---|---|
| lounaat.info | `parse_lounaat_info(url, today, package_title=None)` |
| Compass Group / Food & Co | `parse_compass(url, today)` |
| Juvenes (Jamix) | `parse_jamix(kitchen_id, today)` |
| Ravintolakohtaiset | `parse_harjun_rinkeli`, `parse_pancho`, `parse_huili`, `parse_momento`, `parse_johnscotts`, `parse_via_tribunali`, `parse_caffitella` |

Yhden ravintolan virhe ei kaada muita. Puuttuvalle päivälle näytetään saman viikon
aiempi lista tai ohje katsoa ravintolan omilta sivuilta. Keksittyjä annoksia ei näytetä.

Testaus paikallisesti: `pip install requests beautifulsoup4 pypdf` ja
`python update_menus.py` (molemmat kaupungit) tai `python update_menus.py tampere`.

## Datan muoto

```json
{
  "updated": "16.10.2026 klo 10.02",
  "week": "2026-W42",
  "restaurants": [
    {
      "id": "kulma", "name": "Ravintola Kulma", "address": "Rantakatu 10",
      "hours": "10:45–14:00", "price": "13,60 €", "lat": 63.83697, "lon": 23.13299,
      "url": "…", "source": {"label": "Compass Group", "url": "…"},
      "info": ["lisätietorivi"], "staticNote": ["vakiolounas"],
      "menus": {
        "0": [
          {"title": "Annos", "diets": ["G", "L"], "price": "13,60 €", "desc": "kuvaus"},
          {"title": "Buffasta", "diets": ["L"], "price": "13,60 €",
           "parts": [{"title": "Osa", "diets": ["G", "L"]}]}
        ]
      }
    }
  ]
}
```

`menus`-avaimet `"0"`–`"4"` ovat maanantai–perjantai. Ruokavaliomerkinnät: `G`, `PG`
(näytetään muodossa "(G)" eli gluteeniton pyydettäessä), `L`, `VL`, `M` ja `Veg`.
