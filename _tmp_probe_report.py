import os
import sys

sys.path.insert(0, r"c:\PMNP\personal\central-hub")
os.chdir(r"c:\PMNP\personal\central-hub")
from dotenv import load_dotenv

load_dotenv()

from hub.dhis2 import Dhis2Client, Dhis2Error
from hub.dhis2.instance_profiles import build_dhis2_settings_for_instance
from hub.dhis2_reports.security import period_to_dhis2_date
from hub.dhis2_reports.store import ReportsStore

uid = "zDH0OW4JKEi"
store = ReportsStore()
report = store.get_synced_report("stage", uid)
print("name=", report.name)
print("type=", report.report_type)
print("needs_period=", report.needs_period)
print("needs_org_unit=", report.needs_org_unit)
print("params=", report.report_params)
print("rel=", report.relative_periods)
print("unsupported=", report.unsupported_reason)

client = Dhis2Client(build_dhis2_settings_for_instance("stage"))
print("base=", client.settings.base_url)

for label, pe, ou in [
    ("empty", "", ""),
    ("period", "202601", ""),
    ("period_pe", "202601", ""),
]:
    params = {}
    date = period_to_dhis2_date(pe)
    if date:
        params["date"] = date
    if ou:
        params["ou"] = ou
    if label == "period_pe":
        params = {"pe": pe}
        if ou:
            params["ou"] = ou
    try:
        html = client.get_text(
            f"/api/reports/{uid}/data.html",
            params=params or None,
            accept="text/html, */*",
            timeout=45,
        )
        print(f"{label} OK len={len(html)} head={html[:120]!r}")
    except Dhis2Error as e:
        print(f"{label} Dhis2Error status={e.status_code} msg={e.message[:300]!r}")
    except Exception as e:
        print(f"{label} ERR {type(e).__name__}: {e}")
