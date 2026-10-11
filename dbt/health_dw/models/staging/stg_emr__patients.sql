with source as (

    select * from {{ source('emr', 'emr_patients') }}

),

latest as (

    -- raw is append-only: keep the most recent extracted version of each patient
    select
        *,
        row_number() over (
            partition by patient_id
            order by updated_at desc, _loaded_at desc
        ) as _version_rank
    from source

),

prepared as (

    -- clean once; downstream uses only the cleaned columns
    select
        *,
        regexp_replace(phone, '\D', '', 'g') as phone_digits,
        lower(trim(sex))                     as sex_clean
    from latest
    where _version_rank = 1

),

cleaned as (

    select
        patient_id,
        facility_id,
        upper(trim(upi))            as upi,
        initcap(trim(first_name))   as first_name,
        initcap(trim(last_name))    as last_name,
        case
            when sex_clean in ('f', 'female') then 'F'
            when sex_clean in ('m', 'male')   then 'M'
        end                         as sex,
        date_of_birth,
        case
            when phone_digits similar to '07[0-9]{8}'   then '+254' || substring(phone_digits from 2)
            when phone_digits similar to '2547[0-9]{8}' then '+' || phone_digits
        end                         as phone,          -- '+2547…' stripped to '2547…' hits branch 2
        deleted_at is not null      as is_deleted,
        deleted_at,
        created_at,
        updated_at,
        _loaded_at
    from prepared

)

select * from cleaned