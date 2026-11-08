-- Question: For each 2026 metro show, which 3 exhibitors captured the most unique visitors, and how many were decision-makers?
-- Skills: QUALIFY with ROW_NUMBER, COUNT DISTINCT, filtered aggregation
WITH booth AS (
  SELECT
    s.event_id,
    s.exhibitor_id,
    COUNT(DISTINCT s.visitor_id) AS unique_visitors,
    COUNT(DISTINCT IF(v.visitor_type IN ('Architect', 'Builder/Developer', 'Dealer/Distributor', 'Interior Designer'),
                      s.visitor_id, NULL)) AS decision_maker_visitors
  FROM event_analytics.fact_scans AS s
  JOIN event_analytics.dim_visitor AS v USING (visitor_id)
  WHERE s.scan_type = 'booth'
  GROUP BY s.event_id, s.exhibitor_id
)
SELECT
  e.event_name,
  x.company_name,
  x.industry,
  p.booth_sqm,
  b.unique_visitors,
  ROUND(100 * b.decision_maker_visitors / b.unique_visitors, 1) AS decision_maker_pct,
  ROW_NUMBER() OVER (PARTITION BY b.event_id ORDER BY b.unique_visitors DESC) AS rank_in_show
FROM booth AS b
JOIN event_analytics.dim_event AS e USING (event_id)
JOIN event_analytics.dim_exhibitor AS x USING (exhibitor_id)
JOIN event_analytics.fact_participation AS p ON p.event_id = b.event_id AND p.exhibitor_id = b.exhibitor_id
WHERE e.event_year = 2026 AND e.series = 'metro'
QUALIFY rank_in_show <= 3
ORDER BY e.event_name, rank_in_show;
