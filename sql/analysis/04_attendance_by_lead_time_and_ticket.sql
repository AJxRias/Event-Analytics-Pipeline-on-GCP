-- Question: Do early-bird registrants actually turn up? How does attendance vary by lead time and ticket type?
-- Skills: GROUP BY on derived buckets, conditional aggregation, ratio of ratios
SELECT
  lead_bucket,
  ticket_type,
  COUNT(*) AS registrations,
  ROUND(100 * AVG(IF(attended, 1, 0)), 1) AS attendance_rate_pct,
  ROUND(100 * AVG(IF(attended, 1, 0))
        / AVG(AVG(IF(attended, 1, 0))) OVER (PARTITION BY ticket_type), 0) AS index_vs_ticket_avg
FROM event_analytics.fact_registrations
GROUP BY lead_bucket, ticket_type
HAVING COUNT(*) >= 100
ORDER BY ticket_type, lead_bucket;
