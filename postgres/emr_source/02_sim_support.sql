-- 02_sim_support.sql: simulator support. Rerunnable. Run after 01_schema.sql.
CREATE SCHEMA IF NOT EXISTS sim;

CREATE TABLE IF NOT EXISTS sim.generator_runs (
  run_date    date PRIMARY KEY,            -- simulated business date
  run_type    text NOT NULL,               -- init | daily
  stats       jsonb,
  executed_at timestamptz NOT NULL DEFAULT now()
);

-- updated_at follows the simulated clock when emr.sim_now is set, else wall clock
CREATE OR REPLACE FUNCTION emr.set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := coalesce(nullif(current_setting('emr.sim_now', true), '')::timestamptz, now());
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;