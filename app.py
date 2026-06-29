"""Lichte lokale Streamlit-UI rond de bestaande pipeline.

Vul een stad, een doelgroep (zoekterm) en een aantal leads in, en bekijk de
resultaten zodra de volledige run klaar is. Roept enkel de bestaande nodes aan
(search/scrape/signals/analyze/write/export) - geen nieuwe scrape- of
pitch-logica, dit is puur een invoer-/weergavelaag erbovenop.

Starten: streamlit run app.py
"""

from __future__ import annotations

import asyncio

import pandas as pd
import streamlit as st

from src.config import DEFAULT_MAX_LEADS, DEFAULT_REGIO, MIN_BEHOEFTE_SCORE, require_api_keys
from src.nodes.analyze import analyze_leads
from src.nodes.export import export_leads
from src.nodes.scrape import scrape_leads
from src.nodes.search import search_leads
from src.nodes.signals import apply_signals
from src.nodes.write import write_mails
from src.state import LeadState

st.set_page_config(page_title="Lead-scraper", page_icon="🔍", layout="wide")
st.title("Lead-scraper")
st.caption(
    "Vul een stad en doelgroep in. De pipeline zoekt, scrapet, scoort en schrijft "
    "automatisch pitches - niets wordt verstuurd, je leest alles zelf na."
)

with st.form("run_form"):
    col1, col2, col3 = st.columns(3)
    with col1:
        stad = st.text_input("Stad / regio", value=DEFAULT_REGIO)
    with col2:
        doelgroep = st.text_input("Doelgroep (zoekterm)", placeholder="bv. loodgieter, schilder, fotograaf")
    with col3:
        aantal = st.number_input("Aantal leads", min_value=1, max_value=50, value=DEFAULT_MAX_LEADS)

    with st.expander("Geavanceerd"):
        min_score = st.slider(
            "Minimale behoefte-score",
            min_value=0,
            max_value=5,
            value=MIN_BEHOEFTE_SCORE,
            help="Leads onder deze score slaan analyze/write over (bespaart API-kosten).",
        )
    submitted = st.form_submit_button("Start scrape")


async def _run_pipeline(zoekterm: str, regio: str, max_leads: int, min_score: int) -> list[LeadState]:
    leads = await search_leads(zoekterm, regio, max_leads)
    leads = await scrape_leads(leads)
    leads = apply_signals(leads, min_score=min_score)
    leads = await analyze_leads(leads)
    leads = await write_mails(leads)
    return leads


if submitted:
    if not doelgroep.strip():
        st.error("Vul een doelgroep (zoekterm) in.")
    else:
        try:
            require_api_keys()
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            with st.spinner(f"Bedrijven zoeken en analyseren voor '{doelgroep}' in {stad}..."):
                leads = asyncio.run(
                    _run_pipeline(doelgroep.strip(), stad.strip(), int(aantal), min_score)
                )
                output_path = export_leads(leads, regio=stad.strip())

            approved = [lead for lead in leads if lead["status"] == "ok"]
            afgewezen = [lead for lead in leads if lead["status"] != "ok"]

            st.success(f"Klaar: {len(approved)} van {len(leads)} bedrijven leverden een pitch op.")

            if approved:
                rows = [
                    {
                        "Bedrijfsnaam": lead["bedrijfsnaam"],
                        "Website": lead["website"],
                        "Telefoon": lead["telefoon"] or "",
                        "Email": lead["email"] or "",
                        "Niche": lead["niche"],
                        "Behoefte-score": lead["behoefte_score"],
                        "Haakje": lead["haakje"],
                        "Onderwerpregel": lead["onderwerpregel"],
                        "Mail": lead["mail"],
                    }
                    for lead in approved
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.info("Geen enkele lead haalde de score-drempel of slaagde voor analyse.")

            if afgewezen:
                with st.expander(f"{len(afgewezen)} overgeslagen/foutieve bedrijven bekijken"):
                    skip_rows = [
                        {
                            "Bedrijfsnaam": lead["bedrijfsnaam"],
                            "Status": lead["status"],
                            "Reden": lead["error_reden"] or "",
                        }
                        for lead in afgewezen
                    ]
                    st.dataframe(pd.DataFrame(skip_rows), use_container_width=True)

            with open(output_path, "rb") as f:
                st.download_button(
                    "Download Excel",
                    data=f.read(),
                    file_name=output_path.rsplit("/", 1)[-1],
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
