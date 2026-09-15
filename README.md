Plataforma de monitoramento de desertificação — semiárido de PE

Pipeline de dados que busca histórico de satélite e envia para o site feito no Lovable, via endpoint de ingestão protegido por token.

Licença

Este código é distribuído sob licença MIT (adicione um arquivo LICENSE com o texto padrão da MIT). Isso permite que a SEMAS/PE e a CPRH usem, modifiquem e mantenham o sistema livremente.

Como as peças se encaixam
Google Earth Engine (satélite, clima)
        │
        ▼
etl_pipeline.py  ──── POST com token ────►  /api/public/ingest (Lovable)
        │                                          │
   pe_municipios.json                       grava no banco e
   (lista de municípios                     calcula a classe de
   e suas geometrias)                       risco automaticamente

O etl_pipeline.py roda fora do Lovable (localmente ou via GitHub Actions agendado) — o Lovable não executa Python. Ele só recebe os dados prontos pelo endpoint HTTP que o próprio Lovable criou.

Passo a passo
1. Publicar o app no Lovable

O endpoint de ingestão tem uma URL de "preview" (temporária) e uma de "produção" (depois de publicar). Para automação de verdade, publique o app e use a URL de produção.

2. Preencher as variáveis de ambiente

Copie .env.example para .env e preencha:

INGEST_URL: a URL de produção do endpoint (.../api/public/ingest).
INGEST_TOKEN: o mesmo token que você gerou e salvou como secret no Lovable.
GEE_SERVICE_ACCOUNT e GEE_PRIVATE_KEY_PATH: da conta de serviço criada no Google Cloud (veja o arquivo .json baixado).
3. Instalar dependências
bash
pip install -r requirements.txt
4. Rodar o pipeline
bash
python etl_pipeline.py

Isso busca NDVI, temperatura de superfície e chuva no Earth Engine para cada município listado em pe_municipios.json, calcula o índice de risco e envia tudo em lotes para o endpoint do Lovable. Agende para rodar 1x por semana (GitHub Actions com schedule: cron é a forma mais simples e gratuita).

Próximos passos sugeridos
Filtrar pe_municipios.json para conter só os municípios do semiárido, em vez dos 185 municípios do estado inteiro.
Trocar a pressao_antropica (hoje fixa em 0.5) por um dado real, como densidade populacional do IBGE.
Calibrar os limites de normalização em calcular_indice_esai com dados reais do semiárido de PE.
