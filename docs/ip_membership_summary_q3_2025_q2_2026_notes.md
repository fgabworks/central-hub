# Region-level IP membership summary: Q3 2025 through Q2 2026

Generated from the Live read-only PostgreSQL connection on 2026-08-12. The executable query and generated CSV are adjacent to this note.

## Source tables, joins, and row selection

- Final sources: `_pmnp_linelist_hh_member_2025` for `2025Q3` and `2025Q4`; `_pmnp_linelist_hh_member_2026` for `2026Q1` and `2026Q2`.
- Those linelists are built from `analytics_event_vvlirjoogbj_2025` / `analytics_event_vvlirjoogbj_2026` for Household Member program `VVLirjoOGbj`.
- The source SQL joins the Member Demographic Details event (`ps = 'LRJrFeDNEdT'`) to the member Scorecard event (`ps = 'QfXSvc9HtKN'`) by member TEI and the exact execution date. It maps `uidlevel2` through `uidlevel5` to `organisationunit` as Region, Province, Municipality, and Barangay, and excludes test region `zqTkGmyJZeh`.
- The final query selects one exact row per `quarterly + member TEI`, using latest `visit_date`, `created_date`, `last_updated_date`, PSI UID, and interview UID. All fields used for a member therefore come from one selected interview row.
- Required row filters: nonblank member TEI, region, and interview ID; `HHM_Approval status = 'Approved'`; `HHM_Interview result = 'Completed'`; resolved member status `000`.
- Member status precedence is quarter-specific DE `Rb0k4fOdysI`, falling back to current TEA `vcVNGyzdJ2l` only when the DE is blank. This is the current live-processing rule and supersedes older SQL that allowed `DE = 000 OR TEA = 000`.
- All final indicator cells use `COUNT(DISTINCT tei_uid)`.

## Indicator and counting rules

- Pregnant Women: sex code `1` (female) and pregnancy code `1`.
- WRA: sex code `1`, completed-calendar age `120–599` months (10–49 years). Pregnant and postpartum women remain included when they meet the WRA rule.
- Child bands: `0–5`, `6–11`, `12–23`, and `24–59` completed months. The boundaries are mutually exclusive.
- For all four requested quarters, age is recomputed from DOB to the selected demographic event/visit date. If that date is before DOB, the approved fallback is the same event's creation date. Missing/invalid age excludes the member from WRA and child bands, but not from pregnancy when its direct fields qualify.
- IP categories are exact codes `1`, `2`, and `3`. Values outside these codes are not silently assigned to a category.
- `Total = IP_Yes + IP_No + IP_Dont_Know`; it is the classified-IP total for that indicator row, not a count of members with missing/invalid IP membership.

## UID mapping

| Component | UID / code | Meaning |
|---|---|---|
| Household Member program | `VVLirjoOGbj` | Member analytics program |
| Member Demographic Details stage | `LRJrFeDNEdT` | Quarter-specific member demographics |
| Member Scorecard stage | `QfXSvc9HtKN` | Member scorecard survey |
| IP Membership TEA | `OiOvGqVEyY9` | Confirmed IP membership field |
| IP option set | `wOe8Cf4hFRx` | Yes / No / I don't know |
| IP Yes option | `NYVtsB5BMBt`, code `1` | Yes |
| IP No option | `Ca0XUk03UTx`, code `2` | No |
| IP Don't Know option | `Om9Rbn4TXAJ`, code `3` | I don't know |
| Current member-status TEA | `vcVNGyzdJ2l` | Fallback member status |
| Quarter member-status DE | `Rb0k4fOdysI` | Preferred status for selected interview |
| Present status | code `000` | Currently part of household |
| Pregnancy DE | `ycBIHr9bYyw` | `1` means pregnant |
| Sex TEA | `Qt4YSwPxw0X` | `1` means female |
| WRA logic | derived; no standalone UID | Female and 120–599 months |
| DOB TEA | `fJPZFs2yYJQ` | Date of Birth |
| Stored age-month DE | `RoSxLAB5cfo` | Reference/audit field; final query recomputes age |
| Stored age-year DE | `Hc9Vgt4LXjb` | Reference/audit field |
| Stored age-week DE | `Gds5wTiXoSK` | Reference/audit field |
| Stored age-day DE | `ICbJBQoOsVt` | Reference/audit field |
| Interview ID DE | `RND5auPDknz` | Exact interview-cycle identifier |
| Quarter DE | `I5nbD6rXhmn` | Interview year/quarter |
| Member interview-result DE | `EjW3gXX2zCd` | Must be Completed |
| Member approval-status DE | `CU2939WQAsN` | Must be Approved |
| Household UID TEA | `RDQQ3t9oXw5` | Household relationship reference |
| National root OU | `DcGhhRsspFX` | Philippines |
| Test region OU | `zqTkGmyJZeh` | Excluded by source linelist SQL |
| Event OU / region mapping | `orguid` / `uidlevel2` | Event OU UID; `uidlevel2` joins `organisationunit.uid` for Region |

The 12 exact production region UIDs are in `ip_membership_summary_q3_2025_q2_2026_region_uids.csv`.

## Conflicts reviewed and decisions

- **WRA age:** the report-template tutorial says 15–49, while the current SQL summaries and live-processing household-identification logic say 10–49. Repository priority and live code both favor 10–49, so the query uses 120–599 months.
- **Member status:** older summary SQL uses `(quarter DE = 000 OR current TEA = 000)`. Current live processing explicitly gives the quarter DE precedence and uses the TEA only when the DE is absent. The final query uses that precedence because it preserves historical-quarter status.
- **Age:** older linelist construction may coalesce stored age values or a created-date calculation. Current live-processing age policy recomputes historical quarters at the exact event date, so the query recomputes from DOB and visit date.
- **Row deduplication:** existing HCSC SQL uses latest row per member for a selected quarter. The final query preserves that rule and adds deterministic tie-breakers while keeping all fields on the same selected interview row.

## Validation results

| Quarter | Raw rows | Raw distinct TEIs | Duplicate extra rows removed | Final eligible rows | Final distinct TEIs | Missing recomputed age | Unclassified IP |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q3 2025 | 2,196,353 | 2,158,407 | 37,946 | 1,709,057 | 1,709,057 | 48 | 37 (`0`: 36; blank: 1) |
| Q4 2025 | 1,819,630 | 1,781,275 | 38,355 | 1,472,063 | 1,472,063 | 18 | 1 blank |
| Q1 2026 | 2,414,784 | 2,318,452 | 96,332 | 2,067,770 | 2,067,770 | 12 | 3 blank |
| Q2 2026 | 2,106,613 | 2,094,242 | 12,371 | 1,946,788 | 1,946,788 | 1 | 24 (code `0`) |

- Duplicate-member validation: final eligible row count equals final distinct TEI count in all four quarters; every output cell also uses `COUNT(DISTINCT tei_uid)`.
- Age-band overlap: `0`; the four integer-month predicates are disjoint by construction.
- IP totals: `0` mismatches across all 288 CSV rows.
- Quarter consistency: `0` selected blank interview IDs and `0` selected visit-date/quarter mismatches in every quarter.
- Status conflicts caught by DE precedence: Q3 2025 `170,325`; Q4 2025 `56,795`; Q1 2026 `11,415`; Q2 2026 `6,618` chosen member-quarter rows had differing nonblank DE and TEA statuses.
- Output completeness: 12 regions x 4 quarters x 6 indicators = 288 rows; no blank CSV cells.

## Major region changes

The sequences below are the sum of the six indicator-row totals in Q3 2025 → Q4 2025 → Q1 2026 → Q2 2026. Because Pregnant Women can also be WRA, these are indicator memberships, not unique people across indicators.

| Region | Six-row total sequence | Largest single-indicator swing |
|---|---:|---|
| Region III | 21,124 → 21,143 → 21,569 → 21,470 | WRA -394, Q3→Q4 |
| Region IV-A | 92,592 → 80,994 → 118,510 → 127,928 | WRA +28,733, Q4→Q1 |
| Region IV-B | 77,995 → 59,590 → 94,847 → 93,436 | WRA +27,696, Q4→Q1 |
| Region IX | 76,880 → 79,674 → 79,324 → 82,859 | WRA +2,556, Q1→Q2 |
| Region V | 596,118 → 384,133 → 714,835 → 667,774 | WRA +255,808, Q4→Q1 |
| Region VI | 67,823 → 54,865 → 74,570 → 73,526 | WRA +14,585, Q4→Q1 |
| Region VII | 232,962 → 195,243 → 259,534 → 250,039 | WRA +51,410, Q4→Q1 |
| Region VIII | 268,526 → 364,814 → 436,749 → 384,950 | WRA +76,662, Q3→Q4 |
| Region X | 85,091 → 59,559 → 74,336 → 58,428 | WRA -23,570, Q3→Q4 |
| Region XI | 99,213 → 83,715 → 108,493 → 102,596 | WRA +19,639, Q4→Q1 |
| Region XII | 59,058 → 59,001 → 58,950 → 59,998 | WRA +705, Q1→Q2 |
| Region XIII | 50,706 → 49,787 → 56,465 → 58,289 | WRA +4,372, Q4→Q1 |

The broadest pattern is a Q4-to-Q1 rebound led by WRA in Regions IV-A, IV-B, V, VI, VII, XI, and XIII. Region VIII rose most in Q3-to-Q4 before declining in Q2; Region X declined substantially in Q3-to-Q4 and again in Q2; Regions III and XII were comparatively stable.

## Repository provenance

- `sql-queries`: linelist tables, program/stage seams, exact event-date joins, latest-row selection, approval/completion filters, pregnancy/WRA definitions, child bands, distinct-TEI counts, and OU hierarchy joins.
- `reports template`: confirmed the required beneficiary categories, Present-member intent, distinct-member counting, and mutually exclusive child bands; its older 15–49 WRA note was not used because current SQL/live logic supersedes it.
- `live processing`: authoritative quarter-status precedence, current 10–49 WRA inclusion rule, exact event-date age policy, and UID registry confirmation.
- Central Hub: environment-isolated Live read-only connection, metadata verification, execution, CSV export, and validation only; no business logic was invented or copied into the application.
