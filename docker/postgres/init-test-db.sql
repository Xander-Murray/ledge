SELECT 'CREATE DATABASE ledge_test OWNER ledge'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ledge_test')\gexec
