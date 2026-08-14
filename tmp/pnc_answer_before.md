## PNC logic

The PNC score is evaluated per eligible postpartum member, then rolled up to household level.

### Period policy

`lookup/convergence/quarter_policy.py` — `interview_scoring_policy`

- **2025 Q3 and Q4:** legacy rule — assess all four PNC checks; pass with **at least 2 Yes**.
- **All other periods:** due-window rule — assess only checks already due by the member’s interview date; **every due check must be Yes**.

### Four PNC checks and due dates

`lookup/convergence/derive_pnc_four.py` — `PNC_CHECKUP_DES`, `PNC_DUE_AFTER_DAYS`, `_due_checks`

| Check | Data element | Becomes due |
|---|---|---:|
| Within 24 hours | `jIAwnqn8GTU` | 1 day after delivery |
| Within 3 days | `AhH8CegcpvQ` | 3 days after delivery |
| Within 7–14 days | `sOsvy89ROmD` | 14 days after delivery |
| At 6 weeks | `EadgXIE9RbC` | 42 days after delivery |

Due status is calculated as `interview_date - delivery_date`. A check becomes due only after the upper end of its permitted window, so a still-completable check does not fail the score early.

### Standard-period evaluation

`lookup/convergence/derive_pnc_four.py` — `derive`, `_pnc_checkup_status`

For a non-legacy cycle:

- The system gets the delivery date from `DELIVERY_DATE_DE` and the member demographic/event date as the interview date (`_member_delivery_date`, `_member_interview_date`).
- It selects only the due checks.
- It counts affirmative values using `is_flag_true`.
- The effective threshold is `min(pnc_min_visits, number_of_due_checks)`. Since the standard policy supplies `pnc_min_visits = 4`, that means **all due checks must be Yes**.

Examples:

- 2 days after delivery: only the 24-hour check is due; it must be Yes.
- 10 days after delivery: the 24-hour and 3-day checks are due; both must be Yes.
- 20 days after delivery: the first three checks are due; all three must be Yes.
- 42+ days after delivery: all four checks are due; all four must be Yes.

If no check is due yet, that member is **N/A** and makes no household contribution.

If either delivery date or interview date is missing, the code cannot determine due-ness. It falls back to evaluating all four checks; under the standard policy, all four must then be Yes.

### Legacy 2025 Q3/Q4 evaluation

`lookup/convergence/derive_pnc_four.py` — `derive`, `_pnc_checkup_status`

The due-window is not used. All four fields are assessed, and the member passes with **2 or more Yes**.

### Eligibility and roll-up

`lookup/convergence/derive_pnc_four.py` — `derive`

- Only postpartum members identified by `is_postpartum_member_in_cycle` are evaluated; the breakdown labels these as eligible postpartum members with status `000`.
- No eligible postpartum member → household result is **N/A**.
- Eligible members exist, but none has a check due yet → household result is **N/A**.
- Otherwise, household PNC is **Pass if at least one evaluable postpartum member passes** (`passing > 0`); it is not an all-members-must-pass roll-up.
- A member result is `"1"` for Pass and `"0"` for Fail.

### Member scorecard preview

`lookup/convergence/member_anc_pnc_compliance.py` — `derive_member_pnc_compliance`, `PROCESS_CONFIG`, `build_member_compliance_preview`

`derive_member_pnc_compliance` delegates directly to `derive_pnc_four.derive`, so the member preview uses the same quarter-specific logic. The dedicated member PNC compliance data element is `iPA4CCa6tFd`.