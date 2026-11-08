-- Question: How far ahead do people pre-register? What share of pre-registrations is already in at 60/30/14/7/3/1 days out?
-- Skills: running totals with SUM() OVER (ORDER BY ...), filtering after a window
WITH daily AS (
  SELECT e.series, r.lead_days, COUNT(*) AS regs
  FROM event_analytics.fact_registrations AS r
  JOIN event_analytics.dim_event AS e USING (event_id)
  WHERE r.lead_bucket != 'onsite'
  GROUP BY e.series, r.lead_days
),
curve AS (
  SELECT
    series,
    lead_days,
    SUM(regs) OVER (PARTITION BY series ORDER BY lead_days DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_regs,
    SUM(regs) OVER (PARTITION BY series) AS total_regs
  FROM daily
)
SELECT
  lead_days AS days_before_show,
  ROUND(100 * MAX(IF(series = 'metro', cumulative_regs / total_regs, NULL)), 1) AS metro_pct_registered,
  ROUND(100 * MAX(IF(series = 'regional', cumulative_regs / total_regs, NULL)), 1) AS regional_pct_registered
FROM curve
WHERE lead_days IN (60, 30, 14, 7, 3, 1)
GROUP BY lead_days
ORDER BY lead_days DESC;
