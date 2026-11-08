-- Question: Which cities have the worst registration-to-attendance drop-off, and how do they compare with their series average?
-- Skills: window functions (RANK, AVG OVER PARTITION), derived metrics
WITH city_stats AS (
  SELECT
    c.city,
    c.tier,
    e.series,
    COUNT(DISTINCT e.event_id) AS shows,
    COUNT(*) AS pre_registrations,
    COUNTIF(NOT r.attended) AS no_shows
  FROM event_analytics.fact_registrations AS r
  JOIN event_analytics.dim_event AS e USING (event_id)
  JOIN event_analytics.dim_city AS c ON c.city_id = e.city_id
  WHERE r.lead_bucket != 'onsite'          -- walk-ins can't be no-shows
  GROUP BY c.city, c.tier, e.series
)
SELECT
  city,
  tier,
  series,
  shows,
  pre_registrations,
  ROUND(100 * no_shows / pre_registrations, 1) AS no_show_rate_pct,
  ROUND(100 * no_shows / pre_registrations
        - 100 * AVG(no_shows / pre_registrations) OVER (PARTITION BY series), 1) AS vs_series_avg_pp,
  RANK() OVER (ORDER BY no_shows / pre_registrations DESC) AS drop_off_rank
FROM city_stats
ORDER BY drop_off_rank;
