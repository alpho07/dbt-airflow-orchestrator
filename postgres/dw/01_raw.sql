-- dw.raw: landing zone for emr_source. Append-only; each extraction window owns its slice.
CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.emr_facilities (
  facility_id    int,
  mfl_code       text,
  facility_name  text,
  county         text,
  sub_county     text,
  keph_level     smallint,
  ownership      text,
  is_active      boolean,
  created_at     timestamptz,
  updated_at     timestamptz,
  _window_start  timestamptz NOT NULL,
  _window_end    timestamptz NOT NULL,
  _batch_id      text        NOT NULL,
  _loaded_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.emr_patients (
  patient_id     int,
  facility_id    int,
  upi            text,
  first_name     text,
  last_name      text,
  sex            text,
  date_of_birth  date,
  phone          text,
  created_at     timestamptz,
  updated_at     timestamptz,
  deleted_at     timestamptz,
  _window_start  timestamptz NOT NULL,
  _window_end    timestamptz NOT NULL,
  _batch_id      text        NOT NULL,
  _loaded_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.emr_visits (
  visit_id       bigint,
  patient_id     int,
  facility_id    int,
  visit_date     date,
  visit_type     text,
  anc_visit_no   smallint,
  created_at     timestamptz,
  updated_at     timestamptz,
  _window_start  timestamptz NOT NULL,
  _window_end    timestamptz NOT NULL,
  _batch_id      text        NOT NULL,
  _loaded_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.emr_lab_results (
  lab_result_id  bigint,
  visit_id       bigint,
  test_code      text,
  result_value   text,
  result_date    date,
  created_at     timestamptz,
  updated_at     timestamptz,
  _window_start  timestamptz NOT NULL,
  _window_end    timestamptz NOT NULL,
  _batch_id      text        NOT NULL,
  _loaded_at     timestamptz NOT NULL DEFAULT now()
);

-- One row per table per extraction run (audit + monitoring)
CREATE TABLE IF NOT EXISTS raw._load_log (
  batch_id      text        NOT NULL,
  source_table  text        NOT NULL,
  window_start  timestamptz NOT NULL,
  window_end    timestamptz NOT NULL,
  rows_deleted  int,
  rows_loaded   int,
  status        text        NOT NULL,      -- running | success | failed
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  PRIMARY KEY (batch_id, source_table)
);

-- Supports the delete-then-insert of a window
CREATE INDEX IF NOT EXISTS ix_raw_emr_facilities_window  ON raw.emr_facilities  (_window_start);
CREATE INDEX IF NOT EXISTS ix_raw_emr_patients_window    ON raw.emr_patients    (_window_start);
CREATE INDEX IF NOT EXISTS ix_raw_emr_visits_window      ON raw.emr_visits      (_window_start);
CREATE INDEX IF NOT EXISTS ix_raw_emr_lab_results_window ON raw.emr_lab_results (_window_start);