"""OPTIMIZER: export-node — Pydantic-validatie + Excel-export.

Past de suppression-list toe (compliance.py), bouwt het Pydantic `Lead`-model
voor elke goedgekeurde lead, en schrijft een Excel-bestand met twee tabs:
"Leads" (de export voor manuele review/verzending) en "bron_log" (volledige
traceerbaarheid: elke lead, ook geweigerde/foutieve, met bron en timestamp).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, ConfigDict, Field

from src.compliance import apply_suppression, load_suppression_domains
from src.config import OUTPUT_DIR
from src.state import LeadState

logger = logging.getLogger(__name__)

_COLUMN_WIDTHS = {
    "Bedrijfsnaam": 28,
    "Website": 34,
    "Telefoon": 16,
    "Email": 30,
    "Contactformulier": 34,
    "Niche": 24,
    "Openingszin": 50,
    "Gepersonaliseerde Mail": 70,
}

_LOG_HEADERS = ["Bedrijfsnaam", "Zoekterm", "Bron", "Opgehaald op", "Status", "Reden"]


class Lead(BaseModel):
    """Eén goedgekeurde lead, exact de kolommen/volgorde die de gebruiker manueel naleest."""

    model_config = ConfigDict(populate_by_name=True)

    bedrijfsnaam: str = Field(alias="Bedrijfsnaam")
    website: str = Field(alias="Website")
    telefoon: str = Field(default="", alias="Telefoon")
    email: str = Field(default="", alias="Email")
    contactformulier: str = Field(default="", alias="Contactformulier")
    niche: str = Field(alias="Niche")
    openingszin: str = Field(alias="Openingszin")
    mail: str = Field(alias="Gepersonaliseerde Mail")

    @classmethod
    def from_lead_state(cls, lead: LeadState) -> "Lead":
        return cls(
            bedrijfsnaam=lead["bedrijfsnaam"],
            website=lead["website"] or "",
            telefoon=lead["telefoon"] or "",
            email=lead["email"] or "",
            contactformulier=lead["contact_url"] or "",
            niche=lead["niche"],
            openingszin=lead["ijsbreker"],
            mail=lead["mail"],
        )


COLUMN_HEADERS: list[str] = [field.alias for field in Lead.model_fields.values()]


def export_leads(leads: list[LeadState], regio: str, output_dir: str = OUTPUT_DIR) -> str:
    """Past compliance toe, schrijft de Excel en geeft het bestandspad terug."""
    suppression_domains = load_suppression_domains()
    apply_suppression(leads, suppression_domains)

    approved = [Lead.from_lead_state(lead) for lead in leads if lead["status"] == "ok"]

    workbook = Workbook()
    leads_sheet = workbook.active
    leads_sheet.title = "Leads"
    _write_leads_sheet(leads_sheet, approved)

    log_sheet = workbook.create_sheet("bron_log")
    _write_log_sheet(log_sheet, leads)

    os.makedirs(output_dir, exist_ok=True)
    filename = f"leads_{regio.lower()}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    path = os.path.join(output_dir, filename)
    workbook.save(path)

    logger.info(
        "Excel geëxporteerd naar %s (%d goedgekeurde leads van %d totaal)",
        path,
        len(approved),
        len(leads),
    )
    return path


def _write_leads_sheet(sheet, approved_leads: list[Lead]) -> None:
    sheet.append(COLUMN_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for idx, header in enumerate(COLUMN_HEADERS, start=1):
        sheet.column_dimensions[get_column_letter(idx)].width = _COLUMN_WIDTHS.get(header, 20)

    for lead in approved_leads:
        values = lead.model_dump(by_alias=True)
        sheet.append([values[header] for header in COLUMN_HEADERS])

    mail_col = COLUMN_HEADERS.index("Gepersonaliseerde Mail") + 1
    for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row, min_col=mail_col, max_col=mail_col):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    sheet.freeze_panes = "A2"


def _write_log_sheet(sheet, all_leads: list[LeadState]) -> None:
    sheet.append(_LOG_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for idx, width in enumerate([28, 18, 16, 24, 12, 45], start=1):
        sheet.column_dimensions[get_column_letter(idx)].width = width

    for lead in all_leads:
        sheet.append(
            [
                lead["bedrijfsnaam"],
                lead["zoekterm"],
                lead["bron"],
                lead["opgehaald_op"],
                lead["status"],
                lead.get("error_reden") or "",
            ]
        )

    sheet.freeze_panes = "A2"
