# Lead-generatie pipeline — Antwerpen B2B

Lokaal draaiende pipeline die zelfstandigen/KMO's in regio Antwerpen opzoekt,
hun website scrapet, hun niche bepaalt en een korte gepersonaliseerde
verkoopmail schrijft. De output is een Excel-bestand dat je **manueel**
naleest — er wordt nooit automatisch iets verstuurd.

## Architectuur

Vier samenwerkende rollen, elk een eigen module:

| Rol | Verantwoordelijkheid | Bestanden |
|---|---|---|
| **Architect** | systeemontwerp, State, graph-bedrading | `src/state.py`, `src/graph.py` |
| **Engineer** | databronnen: Google Places + Playwright-scraper | `src/nodes/search.py`, `src/nodes/scrape.py` |
| **Reviewer** | niche bepalen, bad-data weigeren, ijsbreker | `src/nodes/analyze.py` |
| **Optimizer** | mail-copywriting, Pydantic-export, compliance | `src/nodes/write.py`, `src/nodes/export.py`, `src/compliance.py` |

De pipeline is een LangGraph `StateGraph`:
`search → scrape → analyze → write → export`, met conditionele edges die
rechtstreeks naar `export` springen zodra er geen actieve leads meer
overblijven (alles al `error`/`skipped`). Eén kapotte lead stopt de run dus
nooit.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# vul nu ANTHROPIC_API_KEY en GOOGLE_PLACES_API_KEY in .env in
```

## Gebruik

```bash
python main.py --zoekterm "loodgieter" --regio Antwerpen --max-leads 20
```

Argumenten:
- `--zoekterm` (verplicht) — bv. `loodgieter`, `fotograaf`, `boekhouder`
- `--max-leads` (optioneel, standaard `20`)
- `--regio` (optioneel, standaard `Antwerpen`)

Resultaat: `data/output/leads_<regio>_<timestamp>.xlsx` met twee tabbladen:
- **Leads** — de goedgekeurde leads, klaar voor manuele review (Bedrijfsnaam,
  Website, Telefoon, Email, Niche, Openingszin, Gepersonaliseerde Mail). Het
  e-mailadres wordt best-effort van de website gehaald (mailto-links, dan een
  contactpagina) — niet elke site vermeldt het, dus deze kolom kan leeg zijn.
- **bron_log** — elke verwerkte lead (ook geweigerde/foutieve), met bron,
  zoekterm, ophaal-timestamp, status en reden — voor traceerbaarheid.

## Suppression-list

`data/suppression.csv` bevat domeinen die nooit gemaild mogen worden (kolom
`domein`). Elke lead waarvan het websitedomein hierin voorkomt wordt
automatisch overgeslagen, ongeacht hoe goed de rest van de pipeline scoorde.

## Robots.txt-beleid

Vóór elke scrape wordt `robots.txt` van het domein opgehaald. Bij een
expliciete `Disallow` voor de homepage wordt die lead **niet** gescraped
(status `error`, reden vermeld in `bron_log`). Is `robots.txt` niet bereikbaar
of niet parseerbaar, dan scrapen we wél door — een onbereikbaar bestand is
geen disallow. We scrapen daarnaast sequentieel met een korte pauze tussen
leads (geen parallelle requests naar kleine KMO-sites).

## Tests draaien

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio respx
python -m pytest tests/ -v
```

Alle tests mocken Google Places (httpx/respx), Playwright (gemockte browser)
en Claude (fake LLM-objecten) — er wordt geen enkele API-credit verbruikt
tijdens het testen.

## GDPR / compliance disclaimer

Dit is een hulpmiddel om **B2B cold-outreach** voor te bereiden, niet om ze
automatisch te versturen:

- De pipeline genereert enkel een Excel-bestand. Verzending gebeurt altijd
  manueel, door een mens, na controle van elke individuele lead.
- Elke gegenereerde mail bevat verplicht een opt-out-zin; de schrijf-node
  valideert dit in code.
- Respecteer de suppression-list (`data/suppression.csv`) en vul ze aan
  zodra iemand aangeeft niet meer gecontacteerd te willen worden.
- B2B cold mail valt in België doorgaans onder het legitiem-belang-criterium
  van de GDPR, maar **jij** bent als verzender verantwoordelijk voor correcte
  toepassing (juiste rechtsgrond, opt-out effectief verwerken, geen
  persoonsgegevens van natuurlijke personen zonder rechtsgrond verwerken).
  Deze tool geeft geen juridisch advies.
