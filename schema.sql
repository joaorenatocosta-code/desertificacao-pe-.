-- Schema da plataforma de monitoramento de desertificação — Semiárido de PE
-- Rode este arquivo no editor SQL do Supabase (ou psql), uma única vez.

-- Habilita suporte a dados geoespaciais (Supabase já vem com a extensão disponível)
create extension if not exists postgis;

-- Municípios monitorados (carregados uma vez via carregar_municipios.py)
create table if not exists municipios (
    id serial primary key,
    codigo_ibge text unique not null,
    nome text not null,
    geom geometry(MultiPolygon, 4326) not null
);
create index if not exists municipios_geom_idx on municipios using gist (geom);

-- Série histórica de indicadores brutos, um registro por município por data
create table if not exists indicadores_historicos (
    id bigserial primary key,
    municipio_id integer references municipios(id) on delete cascade,
    data date not null,
    ndvi_medio numeric,
    lst_medio numeric,          -- temperatura de superfície (°C)
    chuva_mm numeric,           -- precipitação acumulada no período (CHIRPS)
    chuva_anomalia numeric,     -- desvio em relação à média histórica do mesmo período
    pressao_antropica numeric,  -- proxy de densidade populacional/uso do solo (0 a 1)
    unique (municipio_id, data)
);
create index if not exists indicadores_municipio_data_idx on indicadores_historicos (municipio_id, data);

-- Índice de risco calculado (ESAI adaptado)
create table if not exists risco_desertificacao (
    id bigserial primary key,
    municipio_id integer references municipios(id) on delete cascade,
    data_calculo date not null,
    indice_esai numeric not null,   -- 0 (risco mínimo) a 1 (risco crítico)
    classificacao text not null,    -- 'baixo', 'medio', 'alto'
    unique (municipio_id, data_calculo)
);
create index if not exists risco_municipio_data_idx on risco_desertificacao (municipio_id, data_calculo);

-- Alertas gerados automaticamente pelo pipeline
create table if not exists alertas (
    id bigserial primary key,
    municipio_id integer references municipios(id) on delete cascade,
    tipo text not null,        -- ex: 'risco_alto', 'queda_ndvi', 'seca_prolongada'
    descricao text not null,
    data timestamptz not null default now(),
    lido boolean not null default false
);
create index if not exists alertas_municipio_idx on alertas (municipio_id);

-- View pronta para o frontend: risco mais recente por município, já com geometria
-- (é essa view que o Lovable/Supabase vai consultar para desenhar o mapa)
create or replace view v_risco_atual as
select
    m.id as municipio_id,
    m.nome,
    m.geom,
    r.data_calculo,
    r.indice_esai,
    r.classificacao
from municipios m
join lateral (
    select * from risco_desertificacao rd
    where rd.municipio_id = m.id
    order by rd.data_calculo desc
    limit 1
) r on true;
