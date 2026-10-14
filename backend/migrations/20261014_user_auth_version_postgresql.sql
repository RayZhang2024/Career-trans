BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS auth_version INTEGER NOT NULL DEFAULT 0;

UPDATE users SET auth_version = 0 WHERE auth_version IS NULL;

ALTER TABLE users
    ALTER COLUMN auth_version SET DEFAULT 0,
    ALTER COLUMN auth_version SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_users_auth_version_nonnegative'
          AND conrelid = 'users'::regclass
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT ck_users_auth_version_nonnegative CHECK (auth_version >= 0);
    END IF;
END $$;

COMMIT;
