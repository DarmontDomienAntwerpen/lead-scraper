"""Lightweight local Streamlit UI around the pipeline.

Enter a city, a type of business and a number of leads, then review the results
once the run has finished. Only calls the existing nodes
(search/scrape/signals/analyze/write/export); it is purely an input/display layer.

Run: streamlit run app.py
"""

from __future__ import annotations

import asyncio

import pandas as pd
import streamlit as st

from src.config import DEFAULT_MAX_LEADS, DEFAULT_REGION, MIN_NEED_SCORE, require_api_keys
from src.nodes.analyze import analyze_leads
from src.nodes.export import export_leads
from src.nodes.scrape import scrape_leads
from src.nodes.search import search_leads
from src.nodes.signals import apply_signals
from src.nodes.write import write_mails
from src.state import LeadState

st.set_page_config(page_title="Lead Scraper", page_icon="🔍", layout="wide")
st.title("Lead Scraper")
st.caption(
    "Enter a city and a type of business. The pipeline searches, scrapes, scores and "
    "drafts personalised emails. Nothing is sent: you review everything yourself."
)

with st.form("run_form"):
    col1, col2, col3 = st.columns(3)
    with col1:
        city = st.text_input("City / region", value=DEFAULT_REGION)
    with col2:
        audience = st.text_input("Type of business", placeholder="e.g. loodgieter, schilder, fotograaf")
    with col3:
        num_leads = st.number_input("Number of leads", min_value=1, max_value=50, value=DEFAULT_MAX_LEADS)

    with st.expander("Advanced"):
        min_score = st.slider(
            "Minimum need score",
            min_value=0,
            max_value=5,
            value=MIN_NEED_SCORE,
            help="Leads below this score skip the analyze/write steps (saves API costs).",
        )
    submitted = st.form_submit_button("Run pipeline")


async def _run_pipeline(search_term: str, region: str, max_leads: int, min_score: int) -> list[LeadState]:
    leads = await search_leads(search_term, region, max_leads)
    leads = await scrape_leads(leads)
    leads = apply_signals(leads, min_score=min_score)
    leads = await analyze_leads(leads)
    leads = await write_mails(leads)
    return leads


if submitted:
    if not audience.strip():
        st.error("Enter a type of business.")
    else:
        try:
            require_api_keys()
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            with st.spinner(f"Searching and analysing '{audience}' businesses in {city}..."):
                leads = asyncio.run(
                    _run_pipeline(audience.strip(), city.strip(), int(num_leads), min_score)
                )
                output_path = export_leads(leads, region=city.strip())

            approved = [lead for lead in leads if lead["status"] == "ok"]
            rejected = [lead for lead in leads if lead["status"] != "ok"]

            st.success(f"Done: {len(approved)} of {len(leads)} businesses produced a draft email.")

            if approved:
                rows = [
                    {
                        "Company": lead["company_name"],
                        "Website": lead["website"],
                        "Phone": lead["phone"] or "",
                        "Email": lead["email"] or "",
                        "Niche": lead["niche"],
                        "Need score": lead["need_score"],
                        "Hook": lead["hook"],
                        "Subject line": lead["subject_line"],
                        "Mail": lead["mail"],
                    }
                    for lead in approved
                ]
                st.dataframe(pd.DataFrame(rows), width="stretch")
            else:
                st.info("No lead reached the score threshold or passed analysis.")

            if rejected:
                with st.expander(f"Show {len(rejected)} skipped or failed businesses"):
                    skip_rows = [
                        {
                            "Company": lead["company_name"],
                            "Status": lead["status"],
                            "Reason": lead["error_reason"] or "",
                        }
                        for lead in rejected
                    ]
                    st.dataframe(pd.DataFrame(skip_rows), width="stretch")

            with open(output_path, "rb") as f:
                st.download_button(
                    "Download Excel",
                    data=f.read(),
                    file_name=output_path.rsplit("/", 1)[-1],
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
