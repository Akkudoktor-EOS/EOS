-- MariaDB 10.6+. Latest collector snapshot; independent of dashboard time range.
-- Required telemetry table: eos_pv_learning_status(observed_ms, status_json).
-- Each scope/horizon is kept separate. Negative error changes mean improvement.
WITH latest AS (
  SELECT observed_ms, status_json,
    observed_ms >= UNIX_TIMESTAMP(CURRENT_TIMESTAMP(3))*1000-900000 AS fresh
  FROM eos_pv_learning_status ORDER BY observed_ms DESC LIMIT 1
), response AS (
  SELECT *, JSON_EXTRACT(status_json, '$.long_response') AS model
  FROM latest
), scopes AS (
  SELECT r.*, k.scope,
    JSON_EXTRACT(model, CONCAT('$.selection.', JSON_QUOTE(k.scope))) AS selection
  FROM response r
  CROSS JOIN JSON_TABLE(JSON_KEYS(JSON_EXTRACT(model, '$.selection')),
    '$[*]' COLUMNS(scope VARCHAR(255) PATH '$')) k
), horizons AS (
  SELECT 1 AS ordering, '2-6h' AS horizon
  UNION ALL SELECT 2, '6-24h'
  UNION ALL SELECT 3, '24-48h'
), paired AS (
  SELECT s.*, h.*,
    JSON_EXTRACT(selection, CONCAT('$.', JSON_QUOTE(horizon))) AS evidence
  FROM scopes s CROSS JOIN horizons h
), values_by_scope AS (
  SELECT *,
    JSON_UNQUOTE(JSON_EXTRACT(evidence, '$.method')) AS method,
    CAST(JSON_EXTRACT(evidence, '$.days') AS UNSIGNED) AS days,
    CAST(JSON_EXTRACT(evidence, '$.samples') AS UNSIGNED) AS samples,
    CAST(JSON_EXTRACT(model, '$.identity[5]') AS UNSIGNED) AS minimum_days,
    CAST(JSON_EXTRACT(model, '$.response_version') AS UNSIGNED) AS version,
    CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(evidence, '$.mae_w.calibrated')), 'null') AS DOUBLE) AS base_mae,
    CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(evidence, '$.mae_w.solar')), 'null') AS DOUBLE) AS solar_mae,
    CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(evidence, '$.mae_w.weather')), 'null') AS DOUBLE) AS weather_mae,
    CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(evidence, '$.daily_energy_error_kwh.calibrated')), 'null') AS DOUBLE) AS base_energy,
    CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(evidence, '$.daily_energy_error_kwh.solar')), 'null') AS DOUBLE) AS solar_energy,
    CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(evidence, '$.daily_energy_error_kwh.weather')), 'null') AS DOUBLE) AS weather_energy
  FROM paired
)
SELECT
  CASE WHEN scope='reference' THEN 'Referenz' ELSE REPLACE(scope, 'plane:', '') END AS Bereich,
  horizon AS Horizont,
  CASE WHEN NOT fresh THEN 'Veraltet'
    WHEN version IS NULL OR version<>2 THEN 'Andere Version'
    WHEN evidence IS NULL THEN 'Keine Daten'
    WHEN method='solar' THEN 'Solar aktiv'
    WHEN method='weather' THEN 'Wetter aktiv'
    WHEN JSON_UNQUOTE(JSON_EXTRACT(evidence, '$.validated'))='true' THEN 'Basis'
    ELSE 'Basis · lernt' END AS Freigabe,
  CONCAT(COALESCE(days,0), '/', COALESCE(minimum_days,3)) AS Tage,
  samples AS n,
  CASE WHEN fresh AND version=2 AND samples>0 AND base_mae>0
    THEN ROUND(100*(solar_mae/base_mae-1),1) END AS `Solar ΔMAE %`,
  CASE WHEN fresh AND version=2 AND samples>0 AND base_mae>0
    THEN ROUND(100*(weather_mae/base_mae-1),1) END AS `Wetter ΔMAE %`,
  CASE WHEN fresh AND version=2 AND samples>0
    THEN ROUND(solar_energy-base_energy,3) END AS `Solar ΔkWh`,
  CASE WHEN fresh AND version=2 AND samples>0
    THEN ROUND(weather_energy-base_energy,3) END AS `Wetter ΔkWh`
FROM values_by_scope
ORDER BY CASE WHEN scope='reference' THEN 0 ELSE 1 END, scope, ordering;
