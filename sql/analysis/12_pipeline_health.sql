-- Question: Is the pipeline healthy? Latest runs, and what the latest run had to clean or quarantine.
-- Skills: operational monitoring queries over run logs, scalar subquery
SELECT
  q.source,
  q.reason,
  COUNT(*) AS quarantined_rows,
  (SELECT MAX(finished_at) FROM event_analytics.pipeline_runs WHERE status = 'success') AS last_successful_run
FROM event_analytics.quarantine AS q
GROUP BY q.source, q.reason
ORDER BY quarantined_rows DESC;
