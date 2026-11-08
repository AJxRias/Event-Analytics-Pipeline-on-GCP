-- Question: Do visitors gravitate to exhibitors in the industry they said they're interested in? (validates CRM interest tags for targeting)
-- Skills: join across three tables, comparison against a baseline share
WITH visits AS (
  SELECT
    v.visitor_type,
    v.interest_industry = x.industry AS interest_match
  FROM event_analytics.fact_scans AS s
  JOIN event_analytics.dim_visitor AS v USING (visitor_id)
  JOIN event_analytics.dim_exhibitor AS x USING (exhibitor_id)
  WHERE s.scan_type = 'booth' AND v.interest_industry IS NOT NULL
)
SELECT
  visitor_type,
  COUNT(*) AS booth_visits,
  ROUND(100 * AVG(IF(interest_match, 1, 0)), 1) AS pct_visits_matching_interest,
  ROUND(100 * AVG(IF(interest_match, 1, 0)) / 12.5, 1) AS lift_vs_random_x  -- 8 industries => 12.5% by chance
FROM visits
GROUP BY visitor_type
ORDER BY pct_visits_matching_interest DESC;
