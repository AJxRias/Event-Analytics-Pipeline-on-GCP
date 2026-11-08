-- Question: Which registration channels bring people who actually show up (and pay), by series?
-- Skills: multi-metric aggregation, share-of-total with window SUM, ROW_NUMBER for best channel
WITH ch AS (
  SELECT
    e.series,
    r.channel,
    COUNT(*) AS registrations,
    AVG(IF(r.attended, 1, 0)) AS attendance_rate,
    COUNTIF(r.amount_inr > 0) AS paid_registrations,
    SUM(COALESCE(r.amount_inr, 0)) AS ticket_revenue_inr
  FROM event_analytics.fact_registrations AS r
  JOIN event_analytics.dim_event AS e USING (event_id)
  WHERE r.channel != 'onsite'
  GROUP BY e.series, r.channel
)
SELECT
  series,
  channel,
  registrations,
  ROUND(100 * registrations / SUM(registrations) OVER (PARTITION BY series), 1) AS share_of_series_pct,
  ROUND(100 * attendance_rate, 1) AS attendance_rate_pct,
  paid_registrations,
  ticket_revenue_inr,
  ROW_NUMBER() OVER (PARTITION BY series ORDER BY attendance_rate DESC) AS attendance_rank_in_series
FROM ch
ORDER BY series, attendance_rank_in_series;
