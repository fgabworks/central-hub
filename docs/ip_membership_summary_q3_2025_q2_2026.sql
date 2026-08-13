/*
Region-level IP membership summary for 2025Q3, 2025Q4, 2026Q1, 2026Q2.

Approved source seams:
  - _pmnp_linelist_hh_member_2025 / _pmnp_linelist_hh_member_2026
  - Latest exact member row per quarter, matching the SQL report pattern
  - Quarter-specific member status DE first; current TEA only when DE is blank
  - Completed-calendar-month age from DOB to visit/event date for Q2 2026 and earlier
  - IP Membership codes: 1 Yes, 2 No, 3 I don't know

The result has exactly these columns:
Region,Quarter,Indicator,IP_Yes,IP_No,IP_Dont_Know,Total
*/
WITH source_rows AS (
    SELECT
        '2025'::text AS source_year,
        BTRIM(tei_uid::text) AS tei_uid,
        BTRIM(psi_uid::text) AS psi_uid,
        BTRIM(region::text) AS region,
        BTRIM(quarterly::text) AS quarterly,
        BTRIM(interview_id::text) AS interview_id,
        visit_date,
        created_date,
        last_updated_date,
        BTRIM(hhm_interview_result::text) AS hhm_interview_result,
        BTRIM(hhm_approval_status::text) AS hhm_approval_status,
        NULLIF(BTRIM(household_member_status_de::text), '') AS member_status_de,
        NULLIF(BTRIM(hh_member_status::text), '') AS member_status_tea,
        BTRIM(sex::text) AS sex,
        date_of_birth,
        BTRIM(hhm_pregnancy_status::text) AS hhm_pregnancy_status,
        BTRIM(ip_membership::text) AS ip_membership
    FROM _pmnp_linelist_hh_member_2025
    WHERE quarterly IN ('2025Q3', '2025Q4')

    UNION ALL

    SELECT
        '2026'::text AS source_year,
        BTRIM(tei_uid::text) AS tei_uid,
        BTRIM(psi_uid::text) AS psi_uid,
        BTRIM(region::text) AS region,
        BTRIM(quarterly::text) AS quarterly,
        BTRIM(interview_id::text) AS interview_id,
        visit_date,
        created_date,
        last_updated_date,
        BTRIM(hhm_interview_result::text) AS hhm_interview_result,
        BTRIM(hhm_approval_status::text) AS hhm_approval_status,
        NULLIF(BTRIM(household_member_status_de::text), '') AS member_status_de,
        NULLIF(BTRIM(hh_member_status::text), '') AS member_status_tea,
        BTRIM(sex::text) AS sex,
        date_of_birth,
        BTRIM(hhm_pregnancy_status::text) AS hhm_pregnancy_status,
        BTRIM(ip_membership::text) AS ip_membership
    FROM _pmnp_linelist_hh_member_2026
    WHERE quarterly IN ('2026Q1', '2026Q2')
),
ranked_member_quarters AS (
    SELECT
        s.*,
        ROW_NUMBER() OVER (
            PARTITION BY s.quarterly, s.tei_uid
            ORDER BY
                s.visit_date DESC NULLS LAST,
                s.created_date DESC NULLS LAST,
                s.last_updated_date DESC NULLS LAST,
                s.psi_uid DESC NULLS LAST,
                s.interview_id DESC NULLS LAST
        ) AS member_quarter_rank
    FROM source_rows s
    WHERE s.tei_uid <> ''
),
selected_members AS (
    SELECT
        r.*,
        COALESCE(r.member_status_de, r.member_status_tea) AS resolved_member_status,
        CASE
            /* Q2 2026 and earlier: event/visit date is the approved age basis. */
            WHEN r.date_of_birth IS NOT NULL
             AND r.visit_date IS NOT NULL
             AND r.visit_date::date >= r.date_of_birth
            THEN (
                EXTRACT(YEAR FROM AGE(r.visit_date::date, r.date_of_birth)) * 12
              + EXTRACT(MONTH FROM AGE(r.visit_date::date, r.date_of_birth))
            )::integer
            /* Approved fallback only when the event date is invalid (before DOB). */
            WHEN r.date_of_birth IS NOT NULL
             AND r.visit_date IS NOT NULL
             AND r.visit_date::date < r.date_of_birth
             AND r.created_date IS NOT NULL
             AND r.created_date::date >= r.date_of_birth
            THEN (
                EXTRACT(YEAR FROM AGE(r.created_date::date, r.date_of_birth)) * 12
              + EXTRACT(MONTH FROM AGE(r.created_date::date, r.date_of_birth))
            )::integer
            ELSE NULL
        END AS age_months_at_interview
    FROM ranked_member_quarters r
    WHERE r.member_quarter_rank = 1
      AND r.region <> ''
      AND r.interview_id <> ''
      AND r.hhm_approval_status = 'Approved'
      AND r.hhm_interview_result = 'Completed'
      AND COALESCE(r.member_status_de, r.member_status_tea) = '000'
),
classified_members AS (
    SELECT
        s.*,
        CASE s.ip_membership
            WHEN '1' THEN 'Yes'
            WHEN '2' THEN 'No'
            WHEN '3' THEN 'I Don''t Know'
        END AS ip_category
    FROM selected_members s
    WHERE s.ip_membership IN ('1', '2', '3')
),
indicator_members AS (
    SELECT
        c.region,
        c.quarterly,
        c.tei_uid,
        c.ip_category,
        i.indicator,
        i.indicator_order
    FROM classified_members c
    CROSS JOIN LATERAL (
        VALUES
            ('Pregnant Women'::text, 1, c.sex = '1' AND c.hhm_pregnancy_status = '1'),
            ('Women of Reproductive Age (WRA)'::text, 2,
                c.sex = '1' AND c.age_months_at_interview BETWEEN 120 AND 599),
            ('Children less than 6 months'::text, 3,
                c.age_months_at_interview BETWEEN 0 AND 5),
            ('Children 6–11 months'::text, 4,
                c.age_months_at_interview BETWEEN 6 AND 11),
            ('Children 12–23 months'::text, 5,
                c.age_months_at_interview BETWEEN 12 AND 23),
            ('Children 24–59 months'::text, 6,
                c.age_months_at_interview BETWEEN 24 AND 59)
    ) AS i(indicator, indicator_order, qualifies)
    WHERE i.qualifies
),
regions AS (
    SELECT DISTINCT region
    FROM selected_members
),
quarters(quarterly, quarter_order) AS (
    VALUES
        ('2025Q3'::text, 1),
        ('2025Q4'::text, 2),
        ('2026Q1'::text, 3),
        ('2026Q2'::text, 4)
),
indicators(indicator, indicator_order) AS (
    VALUES
        ('Pregnant Women'::text, 1),
        ('Women of Reproductive Age (WRA)'::text, 2),
        ('Children less than 6 months'::text, 3),
        ('Children 6–11 months'::text, 4),
        ('Children 12–23 months'::text, 5),
        ('Children 24–59 months'::text, 6)
),
summary AS (
    SELECT
        region,
        quarterly,
        indicator,
        COUNT(DISTINCT tei_uid) FILTER (WHERE ip_category = 'Yes') AS ip_yes,
        COUNT(DISTINCT tei_uid) FILTER (WHERE ip_category = 'No') AS ip_no,
        COUNT(DISTINCT tei_uid) FILTER (WHERE ip_category = 'I Don''t Know') AS ip_dont_know
    FROM indicator_members
    GROUP BY region, quarterly, indicator
)
SELECT
    r.region AS "Region",
    CASE q.quarterly
        WHEN '2025Q3' THEN 'Q3 2025'
        WHEN '2025Q4' THEN 'Q4 2025'
        WHEN '2026Q1' THEN 'Q1 2026'
        WHEN '2026Q2' THEN 'Q2 2026'
    END AS "Quarter",
    i.indicator AS "Indicator",
    COALESCE(s.ip_yes, 0) AS "IP_Yes",
    COALESCE(s.ip_no, 0) AS "IP_No",
    COALESCE(s.ip_dont_know, 0) AS "IP_Dont_Know",
    COALESCE(s.ip_yes, 0)
      + COALESCE(s.ip_no, 0)
      + COALESCE(s.ip_dont_know, 0) AS "Total"
FROM regions r
CROSS JOIN quarters q
CROSS JOIN indicators i
LEFT JOIN summary s
  ON s.region = r.region
 AND s.quarterly = q.quarterly
 AND s.indicator = i.indicator
ORDER BY r.region, q.quarter_order, i.indicator_order;
