-- Run this once as a MySQL admin user before the app starts.
-- The app itself will create tables via EF Core migrations (db.Database.Migrate()
-- in Program.cs), so this script only needs to create the database and a
-- least-privilege application user.

CREATE DATABASE IF NOT EXISTS agitator_design_suite
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'agitator_app'@'%' IDENTIFIED BY 'CHANGE_ME';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, DROP
    ON agitator_design_suite.* TO 'agitator_app'@'%';

FLUSH PRIVILEGES;
