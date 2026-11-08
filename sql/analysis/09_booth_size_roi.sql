-- Question: Do bigger booths pay off? Cost per unique visitor (lead) by booth size, metro vs regional.
-- Skills: LEFT JOIN across facts at different grains (booking vs scan), NULLIF to avoid divide-by-zero
WITH booth AS (
  SELECT
    p.event_id,
    p.exhibitor_id,
    p.booth_sqm,
    p.package_amount_inr,
    COUNT(DISTINCT s.visitor_id) AS unique_visitors
  FROM event_analytics.fact_participation AS p
  LEFT JOIN event_analytics.fact_scans AS s
    ON s.event_id = p.event_id AND s.exhibitor_id = p.exhibitor_id AND s.scan_type = 'booth'
  GROUP BY p.event_id, p.exhibitor_id, p.booth_sqm, p.package_amount_inr
)
SELECT
  e.series,
  b.booth_sqm,
  COUNT(*) AS booths,
  ROUND(AVG(b.unique_visitors), 1) AS avg_unique_visitors,
  ROUND(AVG(b.unique_visitors / b.booth_sqm), 2) AS visitors_per_sqm,
  ROUND(SUM(b.package_amount_inr) / NULLIF(SUM(b.unique_visitors), 0), 0) AS cost_per_lead_inr
FROM booth AS b
JOIN event_analytics.dim_event AS e USING (event_id)
GROUP BY e.series, b.booth_sqm
ORDER BY e.series, b.booth_sqm;
