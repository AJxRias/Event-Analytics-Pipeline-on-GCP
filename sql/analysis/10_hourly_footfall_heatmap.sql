-- Question: When do visitors arrive? Gate entries by hour of day for each day of a show (staffing & session planning).
-- Skills: DATE_DIFF, pivot with conditional aggregation, share-of-column with window SUM
WITH entries AS (
  SELECT
    DATE_DIFF(s.scan_date, e.start_date, DAY) + 1 AS show_day,
    s.scan_hour
  FROM event_analytics.fact_scans AS s
  JOIN event_analytics.dim_event AS e USING (event_id)
  WHERE s.scan_type = 'entry' AND e.series = 'metro'
)
SELECT
  scan_hour AS hour_of_day,
  COUNTIF(show_day = 1) AS day_1,
  COUNTIF(show_day = 2) AS day_2,
  COUNTIF(show_day = 3) AS day_3,
  COUNT(*) AS all_days,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_entries
FROM entries
GROUP BY scan_hour
ORDER BY scan_hour;
