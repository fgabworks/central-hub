"""One-shot: add HCSC–RF classification + display_group to registry YAML."""
from __future__ import annotations

from pathlib import Path

import yaml

p = Path("config/hcsc_indicators.yaml")
data = yaml.safe_load(p.read_text(encoding="utf-8"))

# Verified classifications only (NPMO HCSC Overview vs RF domain rates).
# Do not invent HCSC + RF dual membership without a verified source.
HCSC_KEYS = {
    "eligible_households",
    "approved_eligible_households",
    "convergent_households",
    "convergence_rate",
    "completion_validated_eligible_rate",
    "pregnant_women",
    "postpartum_women",
    "children_0_59_months",
    "children_less_than_6_months",
    "children_6_23_months",
    "children_6_59_months",
    "indigenous_peoples",
}
RF_KEYS = {
    "anc_prenatal_checkup_rate",
    "hh_pw_prenatal_rate",
    "ifa_mms_intake_rate",
    "tetanus_vaccine_pw_rate",
    "facility_based_delivery_rate",
    "postnatal_care_rate",
    "deworming_pw_rate",
    "exclusive_breastfeeding_rate",
    "mdd_children_6_23_rate",
    "vitamin_a_6_59_rate",
    "age_appropriate_immunization_rate",
    "growth_monitoring_rate",
    "safely_managed_drinking_water_rate",
    "handwashing_water_source_rate",
    "improved_toilet_rate",
    "sbc_session_hh_child_rate",
    "sbc_session_ip_hh_rate",
    "hunger_experience_rate",
    "no_hunger_experience_count",
}
UNRESOLVED_CLASS = {
    "convergent_units",
    "pct_convergence_mun_client",
    "overview_ip_non_ip_disaggregation",
    "nutritious_balanced_food_frequency",
    "hcsc_rf_approved_sql_lineage",
}

# display_group: organize table (Overview for NPMO overview set; HCSC for scorecard rates;
# Eligible for beneficiary denominators; RF domains for Results Framework rates).
DISPLAY_GROUP = {
    "eligible_households": "overview",
    "approved_eligible_households": "overview",
    "convergent_households": "overview",
    "convergence_rate": "overview",
    "completion_validated_eligible_rate": "overview",
    "pregnant_women": "eligible_beneficiaries",
    "postpartum_women": "eligible_beneficiaries",
    "children_0_59_months": "eligible_beneficiaries",
    "children_less_than_6_months": "eligible_beneficiaries",
    "children_6_23_months": "eligible_beneficiaries",
    "children_6_59_months": "eligible_beneficiaries",
    "indigenous_peoples": "eligible_beneficiaries",
    # Scorecard metrics also listed under HCSC (overview cards keep Overview).
    # Use hcsc for convergent_* beyond the overview card set — keep overview group for the five cards.
    "convergent_units": "unresolved",
    "pct_convergence_mun_client": "unresolved",
    "overview_ip_non_ip_disaggregation": "unresolved",
    "nutritious_balanced_food_frequency": "unresolved",
    "hcsc_rf_approved_sql_lineage": "unresolved",
}

for ind in data["indicators"]:
    key = ind["key"]
    if key in UNRESOLVED_CLASS or ind.get("unresolved"):
        ind["classification"] = "unresolved"
        ind["classification_unresolved"] = True
    elif key in HCSC_KEYS:
        ind["classification"] = "HCSC"
        ind["classification_unresolved"] = False
    elif key in RF_KEYS:
        ind["classification"] = "RF"
        ind["classification_unresolved"] = False
    else:
        ind["classification"] = "unresolved"
        ind["classification_unresolved"] = True

    if key in DISPLAY_GROUP:
        ind["display_group"] = DISPLAY_GROUP[key]
    elif ind.get("section") == "convergence" and not ind.get("unresolved"):
        ind["display_group"] = "hcsc"
    elif ind.get("section") in {
        "maternal_health",
        "child_nutrition_health",
        "household_wash_sbc",
        "food_security",
    }:
        ind["display_group"] = ind["section"]
    elif ind.get("unresolved"):
        ind["display_group"] = "unresolved"
    else:
        ind["display_group"] = ind.get("section") or "unresolved"

    # Keep section for category API; align convergence → hcsc label via registry SECTION_LABELS.
    if ind.get("section") == "convergence" and not ind.get("unresolved"):
        ind["section"] = "hcsc"
    if ind.get("section") in {"data_mapping", "validation"} or (
        ind.get("unresolved") and key in UNRESOLVED_CLASS
    ):
        # Keep unresolved items addressable under unresolved display group;
        # leave section as-is for category routes except force unresolved group.
        pass

header = """# Central Hub HCSC–RF registry
# Household Convergence Scorecard and Results Framework
# Hub stores references only — no formula reimplementation.
# Verified seeds: AI_UID_INDEX.csv + NPMO design qTQD08sNuzZ.
# classification: HCSC | RF | HCSC + RF | unresolved (do not invent dual membership).

"""
p.write_text(
    header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120),
    encoding="utf-8",
)
print("indicators", len(data["indicators"]))
from collections import Counter

print("class", Counter(i.get("classification") for i in data["indicators"]))
print("group", Counter(i.get("display_group") for i in data["indicators"]))
