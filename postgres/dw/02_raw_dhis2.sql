-- 02_raw_dhis2.sql: DHIS2 analytics responses, stored as received
CREATE TABLE IF NOT EXISTS raw.dhis2_analytics (
  as_of        date        NOT NULL,   -- run's business date; window = N complete months before it
  request_key  text        NOT NULL,   -- identifies the slice requested (dx, ou, window length)
  request_url  text        NOT NULL,
  periods      text[]      NOT NULL,
  payload      jsonb       NOT NULL,   -- full response, unparsed
  row_count    int         NOT NULL,
  _batch_id    text        NOT NULL,
  _loaded_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (as_of, request_key)
);