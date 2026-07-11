-- Run after v2_schema.sql. This migration is idempotent and preserves public.*.
DO $$
BEGIN
    IF to_regclass('public.users') IS NULL OR to_regclass('public.spending_record') IS NULL THEN
        RAISE NOTICE 'Legacy tables not found; migration skipped.';
        RETURN;
    END IF;

    INSERT INTO biz.customer (customer_no, name, email, phone, legacy_user_id, registered_at)
    SELECT 'LEGACY-' || u.id, COALESCE(NULLIF(u.full_name, ''), u.username), u.email, u.phone,
           u.id, COALESCE(u.created_at, now())
    FROM public.users u
    ON CONFLICT (legacy_user_id) DO NOTHING;

    INSERT INTO ods.legacy_spending (source_key, raw_data)
    SELECT 'spending_record:' || s.record_id, to_jsonb(s)
    FROM public.spending_record s
    ON CONFLICT (source_key) DO NOTHING;

    RAISE NOTICE 'Legacy users copied to biz.customer and raw spending copied to ods.legacy_spending.';
END $$;

