"""
carregar_municipios.py

Carga única: lê um GeoJSON com os limites dos municípios do semiárido de PE
e grava na tabela `municipios` do banco. Rode isso UMA VEZ, antes do
etl_pipeline.py.

Onde conseguir o arquivo (gratuito):
https://www.ibge.gov.br/geociencias/organizacao-do-territorio/malhas-territoriais
-> baixe a malha municipal de Pernambuco e filtre apenas os municípios
   do semiárido (ex.: usando a lista oficial de municípios em área
   susceptível à desertificação do Ministério do Meio Ambiente/INSA).
Salve o resultado filtrado como municipios_semiarido_pe.geojson nesta pasta.
"""

import os

import geopandas as gpd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DATABASE_URL = os.environ["SUPABASE_DB_URL"]
ARQUIVO_GEOJSON = os.environ.get("MUNICIPIOS_GEOJSON", "municipios_semiarido_pe.geojson")


def main():
    gdf = gpd.read_file(ARQUIVO_GEOJSON).to_crs(epsg=4326)

    # Ajuste os nomes das colunas conforme o arquivo baixado do IBGE
    gdf = gdf.rename(columns={"CD_MUN": "codigo_ibge", "NM_MUN": "nome"})
    gdf = gdf[["codigo_ibge", "nome", "geometry"]].rename(columns={"geometry": "geom"})
    gdf = gdf.set_geometry("geom")

    engine = create_engine(DATABASE_URL)
    gdf.to_postgis("municipios", engine, if_exists="append", index=False)
    print(f"{len(gdf)} municípios carregados com sucesso.")


if __name__ == "__main__":
    main()
