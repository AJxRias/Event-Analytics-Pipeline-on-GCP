-- Question: Of the people who first attended in a given year, how many came back to a show in later years?
-- Skills: cohort analysis, MIN() OVER (PARTITION BY) to find first touch, COUNT DISTINCT
WITH attended AS (
  SELECT DISTINCT r.visitor_id, e.event_year, e.series
  FROM event_analytics.fact_registrations AS r
  JOIN event_analytics.dim_event AS e USING (event_id)
  WHERE r.attended
),
cohorts AS (
  SELECT
    visitor_id,
    event_year,
    MIN(event_year) OVER (PARTITION BY visitor_id) AS cohort_year
  FROM attended
)
SELECT
  cohort_year,
  COUNT(DISTINCT visitor_id) AS cohort_size,
  COUNT(DISTINCT IF(event_year = cohort_year + 1, visitor_id, NULL)) AS returned_next_year,
  ROUND(100 * COUNT(DISTINCT IF(event_year = cohort_year + 1, visitor_id, NULL))
        / COUNT(DISTINCT visitor_id), 1) AS next_year_retention_pct,
  COUNT(DISTINCT IF(event_year > cohort_year, visitor_id, NULL)) AS returned_any_later_year
FROM cohorts
WHERE cohort_year < (SELECT MAX(event_year) FROM event_analytics.dim_event)  -- latest cohort can't return yet
GROUP BY cohort_year
ORDER BY cohort_year;
