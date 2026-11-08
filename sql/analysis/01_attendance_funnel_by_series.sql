-- Question: How does the funnel (registered -> attended -> highly engaged (10+ booths)) differ between metro and regional shows?
-- Skills: CTEs, LEFT JOIN, conditional aggregation (COUNTIF), SAFE_DIVIDE
WITH booth_visits AS (
  SELECT registration_id, COUNT(DISTINCT exhibitor_id) AS booths_visited
  FROM event_analytics.fact_scans
  WHERE scan_type = 'booth'
  GROUP BY registration_id
),
funnel AS (
  SELECT
    e.series,
    c.tier,
    r.registration_id,
    r.attended,
    COALESCE(b.booths_visited, 0) AS booths_visited
  FROM event_analytics.fact_registrations AS r
  JOIN event_analytics.dim_event AS e USING (event_id)
  JOIN event_analytics.dim_city AS c ON c.city_id = e.city_id
  LEFT JOIN booth_visits AS b USING (registration_id)
)
SELECT
  series,
  tier,
  COUNT(*) AS registrations,
  COUNTIF(attended) AS attendees,
  COUNTIF(booths_visited >= 10) AS highly_engaged,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(attended), COUNT(*)), 1) AS attendance_rate_pct,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(booths_visited >= 10), COUNTIF(attended)), 1) AS highly_engaged_pct_of_attendees,
  ROUND(AVG(IF(attended, booths_visited, NULL)), 2) AS avg_booths_per_attendee
FROM funnel
GROUP BY series, tier
ORDER BY tier;
