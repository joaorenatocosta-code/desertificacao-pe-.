"""
etl_pipeline.py

Pipeline de coleta de dados históricos e cálculo do índice de risco de
desertificação para os municípios do semiárido de Pernambuco.

Fluxo:
  1. Lê a geometria de cada município (já carregada no banco).
  2. Para cada um, busca no Google Earth Engine (gratuito, sem custo de
     processamento): NDVI (Sentinel-2), temperatura de superfície — LST
     (MODIS) e chuva acumulada/anomalia (CHIRPS).
  3. Calcula um índice de risco (ESAI adaptado) e classifica em
     baixo / médio / alto.
  4. Grava tudo no banco (Supabase Postgres) e gera alertas automáticos.

Rode isso periodicamente (ex.: 1x por semana) via GitHub Actions, cron ou
Cloud Scheduler + Cloud Run Job — NUNCA dentro do Lovable, que não executa
Python. O Lovable só lê o resultado, direto do Supabase.
"""

import datetime as dt
import os

import ee
import geopandas as gpd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.environ["SUPABASE_DB_URL"]

# Janela de análise do "instantâneo" atual
DATA_FIM = dt.date.today()
DATA_INICIO = DATA_FIM - dt.timedelta(days=90)
# Quantos anos olhar para trás para calcular a média histórica de chuva (anomalia)
JANELA_HISTORICA_ANOS = 15


def autenticar_gee():
    """Autentica no Earth Engine via conta de serviço.
    Crie a conta em console.cloud.google.com, ative a 'Earth Engine API'
    e baixe a chave JSON. Guarde o caminho em GEE_PRIVATE_KEY_PATH.
    """
    service_account = os.environ["GEE_SERVICE_ACCOUNT"]
    key_path = os.environ["GEE_PRIVATE_KEY_PATH"]
    credentials = ee.ServiceAccountCredentials(service_account, key_path)
    ee.Initialize(credentials)


def carregar_municipios(engine) -> gpd.GeoDataFrame:
    """Lê os municípios direto do banco (já carregados via carregar_municipios.py)."""
    return gpd.read_postgis(
        "select id, codigo_ibge, nome, geom as geometry from municipios",
        engine,
        geom_col="geometry",
    )


def ee_geometria(geom_shapely) -> ee.Geometry:
    """Converte uma geometria shapely para o formato do Earth Engine."""
    return ee.Geometry(geom_shapely.__geo_interface__)


def calcular_ndvi(geom_ee: ee.Geometry, inicio: str, fim: str):
    """NDVI médio do período (Sentinel-2, 10 m, com filtro de nuvem)."""
    colecao = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geom_ee)
        .filterDate(inicio, fim)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    )

    def calcular(imagem):
        return imagem.normalizedDifference(["B8", "B4"]).rename("ndvi")

    ndvi_medio = colecao.map(calcular).mean()
    resultado = ndvi_medio.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=geom_ee, scale=30, maxPixels=1e9
    )
    return resultado.get("ndvi").getInfo()


def calcular_lst(geom_ee: ee.Geometry, inicio: str, fim: str):
    """Temperatura de superfície média em °C (MODIS LST, 1 km)."""
    colecao = (
        ee.ImageCollection("MODIS/061/MOD11A2").filterBounds(geom_ee).filterDate(inicio, fim)
    )
    lst_media = colecao.select("LST_Day_1km").mean().multiply(0.02).subtract(273.15)
    resultado = lst_media.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=geom_ee, scale=1000, maxPixels=1e9
    )
    return resultado.get("LST_Day_1km").getInfo()


def calcular_chuva(geom_ee: ee.Geometry, inicio: str, fim: str):
    """Chuva acumulada (mm) e anomalia em relação à média histórica do mesmo
    intervalo de dias nos últimos JANELA_HISTORICA_ANOS anos (fonte: CHIRPS).
    """
    colecao = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterBounds(geom_ee)

    def acumulado(data_ini: str, data_fim: str):
        soma = colecao.filterDate(data_ini, data_fim).sum()
        resultado = soma.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=geom_ee, scale=5000, maxPixels=1e9
        )
        return resultado.get("precipitation").getInfo()

    chuva_atual = acumulado(inicio, fim)

    data_ini_dt = dt.date.fromisoformat(inicio)
    data_fim_dt = dt.date.fromisoformat(fim)
    duracao_dias = (data_fim_dt - data_ini_dt).days

    historico = []
    for anos_atras in range(1, JANELA_HISTORICA_ANOS + 1):
        ini_hist = data_ini_dt.replace(year=data_ini_dt.year - anos_atras)
        fim_hist = ini_hist + dt.timedelta(days=duracao_dias)
        historico.append(acumulado(ini_hist.isoformat(), fim_hist.isoformat()))

    media_historica = sum(historico) / len(historico) if historico else 0
    anomalia = (chuva_atual - media_historica) / media_historica if media_historica else 0
    return chuva_atual, anomalia


def normalizar(valor, minimo, maximo):
    """Normaliza um valor para a faixa 0-1, cortando nos extremos."""
    if valor is None:
        return 0.5  # neutro quando o dado não está disponível
    normalizado = (valor - minimo) / (maximo - minimo)
    return max(0.0, min(1.0, normalizado))


def calcular_indice_esai(ndvi, lst, chuva_anomalia, pressao_antropica=0.5) -> float:
    """Índice adaptado do ESAI (Environmentally Sensitive Area Index — MEDALUS),
    combinando quatro subíndices normalizados de 0 (baixo risco) a 1 (alto risco).
    Os limites de normalização (minimo/maximo) devem ser calibrados com dados
    reais do semiárido de PE conforme o histórico for se acumulando.
    """
    risco_vegetacao = 1 - normalizar(ndvi, minimo=0.1, maximo=0.6)
    risco_clima_temp = normalizar(lst, minimo=25, maximo=42)
    risco_clima_chuva = normalizar(-chuva_anomalia, minimo=-0.3, maximo=0.6)
    risco_pressao = pressao_antropica

    pesos = {"vegetacao": 0.35, "clima_temp": 0.15, "clima_chuva": 0.30, "pressao": 0.20}
    indice = (
        risco_vegetacao * pesos["vegetacao"]
        + risco_clima_temp * pesos["clima_temp"]
        + risco_clima_chuva * pesos["clima_chuva"]
        + risco_pressao * pesos["pressao"]
    )
    return round(indice, 3)


def classificar_risco(indice: float) -> str:
    if indice < 0.35:
        return "baixo"
    if indice < 0.6:
        return "medio"
    return "alto"


def processar_municipio(row, engine):
    nome = row["nome"]
    municipio_id = row["id"]
    geom_ee = ee_geometria(row["geometry"])

    ndvi = calcular_ndvi(geom_ee, DATA_INICIO.isoformat(), DATA_FIM.isoformat())
    lst = calcular_lst(geom_ee, DATA_INICIO.isoformat(), DATA_FIM.isoformat())
    chuva_mm, chuva_anomalia = calcular_chuva(
        geom_ee, DATA_INICIO.isoformat(), DATA_FIM.isoformat()
    )

    indice = calcular_indice_esai(ndvi, lst, chuva_anomalia)
    classificacao = classificar_risco(indice)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                insert into indicadores_historicos
                    (municipio_id, data, ndvi_medio, lst_medio, chuva_mm, chuva_anomalia)
                values (:municipio_id, :data, :ndvi, :lst, :chuva_mm, :chuva_anomalia)
                on conflict (municipio_id, data) do update set
                    ndvi_medio = excluded.ndvi_medio,
                    lst_medio = excluded.lst_medio,
                    chuva_mm = excluded.chuva_mm,
                    chuva_anomalia = excluded.chuva_anomalia
                """
            ),
            {
                "municipio_id": municipio_id,
                "data": DATA_FIM,
                "ndvi": ndvi,
                "lst": lst,
                "chuva_mm": chuva_mm,
                "chuva_anomalia": chuva_anomalia,
            },
        )

        conn.execute(
            text(
                """
                insert into risco_desertificacao
                    (municipio_id, data_calculo, indice_esai, classificacao)
                values (:municipio_id, :data, :indice, :classificacao)
                on conflict (municipio_id, data_calculo) do update set
                    indice_esai = excluded.indice_esai,
                    classificacao = excluded.classificacao
                """
            ),
            {
                "municipio_id": municipio_id,
                "data": DATA_FIM,
                "indice": indice,
                "classificacao": classificacao,
            },
        )

        if classificacao == "alto":
            conn.execute(
                text(
                    """
                    insert into alertas (municipio_id, tipo, descricao)
                    values (:municipio_id, 'risco_alto', :descricao)
                    """
                ),
                {
                    "municipio_id": municipio_id,
                    "descricao": f"{nome} atingiu classificação de risco alto (índice {indice}).",
                },
            )

    print(
        f"[ok] {nome}: NDVI={ndvi:.3f} LST={lst:.1f}C chuva_anom={chuva_anomalia:.2f} "
        f"-> risco={classificacao}"
    )


def main():
    autenticar_gee()
    engine = create_engine(DATABASE_URL)
    municipios = carregar_municipios(engine)

    for _, row in municipios.iterrows():
        try:
            processar_municipio(row, engine)
        except Exception as erro:
            print(f"[erro] {row['nome']}: {erro}")


if __name__ == "__main__":
    main()
