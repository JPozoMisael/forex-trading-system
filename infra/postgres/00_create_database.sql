-- ==========================================================================
-- PASO 1 de 2: crear el usuario y la base de datos del sistema de trading.
-- ==========================================================================
-- Ejecútalo conectado como superusuario (normalmente "postgres") a la base
-- "postgres". Solo hace falta una vez.
--
--   HeidiSQL: conéctate con el usuario postgres, abre una pestaña de consulta,
--             pega este archivo y ejecútalo completo (F9).
--   psql:     psql -U postgres -d postgres -f infra/postgres/00_create_database.sql
--
-- Cambia la contraseña antes de ejecutarlo y pon la MISMA en tu .env
-- (POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT).
--
-- Después sigue con el PASO 2, que crea las tablas (ver docs/BASE_DE_DATOS.md):
--   .venv\Scripts\python scripts\init_db.py

CREATE ROLE forex WITH LOGIN PASSWORD 'cambia_esta_clave';

CREATE DATABASE forex_data
    WITH OWNER = forex
    ENCODING = 'UTF8'
    TEMPLATE = template0;

-- Opcional: si tu servidor tiene TimescaleDB instalado, conéctate a forex_data
-- como postgres y ejecuta esta línea ANTES del paso 2 (crear la extensión
-- requiere superusuario). Sin TimescaleDB el sistema funciona igual: init.sql
-- la detecta sola.
--   CREATE EXTENSION IF NOT EXISTS timescaledb;
