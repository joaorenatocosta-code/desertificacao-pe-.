"""
etl_pipeline.py (v2)

Busca dados históricos de satélite no Google Earth Engine e ENVIA para o
endpoint de ingestão que o Lovable criou (/api/public/ingest) — não conecta
mais direto no banco de dados.

Fluxo:
  1. Lê a lista de municípios de um arquivo GeoJSON local (pe_municipios.json).
  2. Para cada um, busca no Earth Engine: NDVI (Sentinel-2), temperatura de
     superfície - LST (MODIS) e chuva acumulada/anomalia (CHIRPS).
  3. Calcula um índice de risco (ESAI adaptado).
  4. Envia tudo em lotes (POST) para o endpoint do Lovable, que grava no
     banco e calcula a classificação de risco automaticamente.

Rode isso periodicamente (ex.: 1x por semana) via GitHub Actions.
"""

import datetime as dt
import os

import ee
import geopandas as gpd
import requests
from dotenv import load_dotenv

load_dotenv()

INGEST_URL = os.environ["INGEST_URL"]      # ex: https://seu-projeto.lovable.app/api/public/ingest
INGEST_TOKEN = os.environ["INGEST_TOKEN"]  # o token gerado com openssl rand -hex 32
MUNICIPIOS_GEOJSON = os.environ.get("MUNICIPIOS_GEOJSON", "pe_municipios.json")

# Janela de análise do "instantâneo" atual
DATA_FIM = dt.date.today()
DATA_INICIO = DATA_FIM - dt.timedelta(days=90)
# Quantos anos olhar para trás para calcular a média histórica de chuva (anomalia)
JANELA_HISTORICA_ANOS = 15
# Limite de registros por chamada, informado pelo endpoint do Lovable
TAMANHO_LOTE = 500


def autenticar_gee():
    """Autentica no Earth Engine via conta de serviço.
    GEE_SERVICE_ACCOUNT: e-mail da conta de serviço (ex: desertificacao-pe@desertificacao-pe.iam.gserviceaccount.com)
    GEE_PRIVATE_KEY_PATH: caminho do arquivo .json baixado no Google Cloud Console
    """
    service_account = os.environ["GEE_SERVICE_ACCOUNT"]
    key_path = os.environ["GEE_PRIVATE_KEY_PATH"]
    credentials = ee.ServiceAccountCredentials(service_account, key_path)
    ee.Initialize(credentials)


def carregar_municipios() -> gpd.GeoDataFrame:
    """Lê os municípios e suas geometrias de um GeoJSON local."""
    return gpd.read_file(MUNICIPIOS_GEOJSON)


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
    """Índice adaptado do ESAI (Environmentally Sensitive Area Index — MEDALUS).
    0 = risco mínimo, 1 = risco crítico. Os limites de normalização devem ser
    calibrados com dados reais do semiárido de PE conforme o histórico crescer.
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


def montar_registro(row) -> dict:
    """Busca os indicadores de um município no Earth Engine e monta o
    registro no formato esperado pelo endpoint de ingestão."""
    codigo = str(row["id"])
    nome = row["name"]
    geom_ee = ee_geometria(row["geometry"])

    ndvi = calcular_ndvi(geom_ee, DATA_INICIO.isoformat(), DATA_FIM.isoformat())
    lst = calcular_lst(geom_ee, DATA_INICIO.isoformat(), DATA_FIM.isoformat())
    chuva_mm, chuva_anomalia = calcular_chuva(
        geom_ee, DATA_INICIO.isoformat(), DATA_FIM.isoformat()
    )
    indice = calcular_indice_esai(ndvi, lst, chuva_anomalia)

    print(
        f"[ok] {nome}: NDVI={ndvi:.3f} LST={lst:.1f}C chuva_anom={chuva_anomalia:.2f} "
        f"indice_esai={indice}"
    )

    return {
        "codigo_ibge": codigo,
        "data": DATA_FIM.isoformat(),
        "ndvi_medio": round(ndvi, 3) if ndvi is not None else None,
        "chuva_mm": round(chuva_mm, 1) if chuva_mm is not None else None,
        "chuva_anomalia": round(chuva_anomalia, 3) if chuva_anomalia is not None else None,
        "lst_medio": round(lst, 1) if lst is not None else None,
        "pressao_antropica": 0.5,
        "indice_esai": indice,
    }


def enviar_lote(registros: list) -> dict:
    """Envia um lote de registros para o endpoint de ingestão do Lovable."""
    resposta = requests.post(
        INGEST_URL,
        headers={"x-ingest-token": INGEST_TOKEN},
        json={"registros": registros},
        timeout=60,
    )
    resposta.raise_for_status()
    return resposta.json()


def main():
    autenticar_gee()
    municipios = carregar_municipios()

    registros = []
    for _, row in municipios.iterrows():
        try:
            registros.append(montar_registro(row))
        except Exception as erro:
            print(f"[erro] {row.get('name')}: {erro}")

    for inicio in range(0, len(registros), TAMANHO_LOTE):
        lote = registros[inicio : inicio + TAMANHO_LOTE]
        resultado = enviar_lote(lote)
        print(f"[envio] lote de {len(lote)} registros -> {resultado}")


if __name__ == "__main__":
    main()
