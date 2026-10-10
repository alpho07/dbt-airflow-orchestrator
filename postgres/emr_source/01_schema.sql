-- emr_source: simulated EMR (OLTP). Constraints are intentionally loose.
-- Rerunnable: drops and rebuilds the emr schema.
DROP SCHEMA IF EXISTS emr CASCADE;
CREATE SCHEMA emr;
SET search_path = emr;

-- Keeps updated_at accurate on every UPDATE (incremental extraction depends on it)
CREATE OR REPLACE FUNCTION emr.set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE facilities (
  facility_id    serial PRIMARY KEY,
  mfl_code       varchar(10) NOT NULL UNIQUE,
  facility_name  text NOT NULL,
  county         text,                 -- inconsistent casing occurs
  sub_county     text,
  keph_level     smallint,             -- 2..6
  ownership      text,                 -- MOH, FBO, Private, NGO
  is_active      boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE patients (
  patient_id     serial PRIMARY KEY,
  facility_id    int NOT NULL REFERENCES facilities(facility_id),
  upi            varchar(20),          -- no UNIQUE: duplicate registrations occur
  first_name     text,
  last_name      text,
  sex            text,                 -- free text: 'F', 'Female', 'female', NULL
  date_of_birth  date,
  phone          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz           -- soft delete
);

CREATE TABLE visits (
  visit_id       bigserial PRIMARY KEY,
  patient_id     int NOT NULL REFERENCES patients(patient_id),
  facility_id    int NOT NULL REFERENCES facilities(facility_id),
  visit_date     date NOT NULL,
  visit_type     text NOT NULL,        -- ANC, PNC, HIV_CARE, OPD
  anc_visit_no   smallint,             -- only for ANC
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE lab_results (
  lab_result_id  bigserial PRIMARY KEY,
  visit_id       bigint,               -- no FK: comes from a separate lab system, orphans possible
  test_code      text NOT NULL,        -- VL, HB, HIV_RAPID
  result_value   text,                 -- '<50', 'LDL', '1200', 'Detected'
  result_date    date,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_facilities_upd  BEFORE UPDATE ON facilities  FOR EACH ROW EXECUTE FUNCTION emr.set_updated_at();
CREATE TRIGGER trg_patients_upd    BEFORE UPDATE ON patients    FOR EACH ROW EXECUTE FUNCTION emr.set_updated_at();
CREATE TRIGGER trg_visits_upd      BEFORE UPDATE ON visits      FOR EACH ROW EXECUTE FUNCTION emr.set_updated_at();
CREATE TRIGGER trg_lab_results_upd BEFORE UPDATE ON lab_results FOR EACH ROW EXECUTE FUNCTION emr.set_updated_at();

-- Supports incremental extraction by watermark
CREATE INDEX ix_facilities_updated_at  ON facilities  (updated_at);
CREATE INDEX ix_patients_updated_at    ON patients    (updated_at);
CREATE INDEX ix_visits_updated_at      ON visits      (updated_at);
CREATE INDEX ix_lab_results_updated_at ON lab_results (updated_at);