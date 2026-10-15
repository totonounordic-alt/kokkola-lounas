// Ohjaa ratinanlounas.aihio.fyi:n etusivun Tampereen versioon (tampere/index.html).
// Sivu on sama kuin Kokkolan, mutta somepalvelut (WhatsApp, Facebook, Slack ym.)
// lukevat linkin esikatselun suoraan HTML:stä ilman JavaScriptiä, joten
// Tampereen versiossa on Ratinan otsikko, kuvaus ja esikatselukuva.
// Kaikki muut pyynnöt palvellaan suoraan staattisina tiedostoina.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname.startsWith("ratinanlounas.") && (url.pathname === "/" || url.pathname === "/index.html")) {
      url.pathname = "/tampere/";
      return env.ASSETS.fetch(new Request(url, request));
    }
    return env.ASSETS.fetch(request);
  },
};
