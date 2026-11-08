-- Question: Which markets are growing fastest year over year (registrations and attendees)?
-- Skills: LAG() for period-over-period growth, SAFE_DIVIDE, filtering on window output in an outer query
WITH yearly AS (
  SELECT
    c.city,
    c.tier,
    e.event_year,
    COUNT(*) AS registrations,
    COUNTIF(r.attended) AS attendees
  FROM event_analytics.fact_registrations AS r
  JOIN event_analytics.dim_event AS e USING (event_id)
  JOIN event_analytics.dim_city AS c ON c.city_id = e.city_id
  GROUP BY c.city, c.tier, e.event_year
),
growth AS (
  SELECT
    *,
    LAG(registrations) OVER (PARTITION BY city ORDER BY event_year) AS prev_registrations,
    LAG(attendees) OVER (PARTITION BY city ORDER BY event_year) AS prev_attendees
  FROM yearly
)
SELECT
  city,
  tier,
  event_year,
  registrations,
  attendees,
  ROUND(100 * SAFE_DIVIDE(registrations - prev_registrations, prev_registrations), 1) AS registrations_yoy_pct,
  ROUND(100 * SAFE_DIVIDE(attendees - prev_attendees, prev_attendees), 1) AS attendees_yoy_pct
FROM growth
WHERE event_year = 2026 AND prev_registrations IS NOT NULL
ORDER BY registrations_yoy_pct DESC;
