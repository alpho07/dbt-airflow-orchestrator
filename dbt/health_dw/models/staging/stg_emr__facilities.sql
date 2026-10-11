with source as (

    select * from {{ source('emr', 'emr_facilities') }}

),

latest as (

    -- raw is append-only: keep the most recent extracted version of each facility
    select
        *,
        row_number() over (
            partition by facility_id
            order by updated_at desc, _loaded_at desc
        ) as _version_rank
    from source

),

cleaned as (

    select
        facility_id,
        trim(mfl_code)                  as mfl_code,
        trim(facility_name)             as facility_name,
        initcap(trim(county))           as county,       -- 'KAKAMEGA', 'nairobi ' -> 'Kakamega', 'Nairobi'
        initcap(trim(sub_county))       as sub_county,
        keph_level,
        upper(trim(ownership))          as ownership,    -- MOH, FBO, PRIVATE, NGO
        is_active,
        created_at,
        updated_at,
        _loaded_at
    from latest
    where _version_rank = 1

)

select * from cleaned