# Plataforma de monitoramento de desertificação — semiárido de PE

Pipeline de dados + banco + API para alimentar um site (aqui ou no Lovable)
com histórico e risco de desertificação por município.

## Licença

Este código é distribuído sob licença MIT (adicione um arquivo `LICENSE`
com o texto padrão da MIT antes de subir ao GitHub). Isso permite que a
SEMAS/PE e a CPRH usem, modifiquem e mantenham o sistema livremente.

## Como as peças se encaixam

```
Google Earth Engine (satélite, clima)
        │
        ▼
etl_pipeline.py  ──────────►  Supabase (Postgres + PostGIS)  ◄──────────  Lovable (frontend)
        ▲                              ▲
        │                              │
carregar_municipios.py          api.py (opcional, só se
(carga única dos limites          NÃO for usar o Lovable)
municipais)
```

O `etl_pipeline.py` roda fora do Lovable (Lovable não executa Python) —
localmente, em GitHub Actions, ou num Cloud Scheduler. O Lovable só lê o
resultado, direto do banco Supabase.

## Passo a passo

### 1. Criar o projeto Supabase
- No Lovable: `Settings → Connectors → Supabase` (ou use o backend padrão
  do Lovable Cloud, que já é Supabase por baixo).
- Se preferir criar você mesmo: [supabase.com](https://supabase.com) → novo projeto.

### 2. Rodar o schema
- Abra o editor SQL do Supabase e cole o conteúdo de `schema.sql`. Isso cria
  as tabelas, a extensão PostGIS e a view `v_risco_atual`.

### 3. Preencher as variáveis de ambiente
- Copie `.env.example` para `.env` e preencha com:
  - `SUPABASE_DB_URL`: em Supabase → Settings → Database → Connection string.
  - `GEE_SERVICE_ACCOUNT` e `GEE_PRIVATE_KEY_PATH`: crie uma conta de serviço
    em [console.cloud.google.com](https://console.cloud.google.com), ative a
    "Earth Engine API" e baixe a chave JSON.

### 4. Instalar dependências e carregar os municípios (uma vez só)
```bash
pip install -r requirements.txt
python carregar_municipios.py
```
Antes disso, baixe a malha municipal do IBGE, filtre só os municípios do
semiárido de PE e salve como `municipios_semiarido_pe.geojson` (veja o
comentário no topo de `carregar_municipios.py`).

### 5. Rodar o pipeline de dados
```bash
python etl_pipeline.py
```
Isso busca NDVI, temperatura de superfície e chuva no Earth Engine, calcula
o índice de risco e grava tudo no Supabase. Agende para rodar 1x por semana
(GitHub Actions com `schedule: cron` é a forma mais simples e gratuita).

### 6. Construir o site no Lovable
No chat do Lovable, depois de conectar o Supabase, um prompt como este já
é suficiente para gerar a tela:

> "Conecte ao meu projeto Supabase. Crie uma página com um mapa mostrando
> os municípios da view `v_risco_atual`, coloridos por `classificacao`
> (verde=baixo, amarelo=medio, vermelho=alto). Ao clicar em um município,
> mostre um gráfico com o histórico de `ndvi_medio` e `chuva_mm` da tabela
> `indicadores_historicos`. Inclua também uma lista dos alertas não lidos
> da tabela `alertas`."

O Lovable gera o schema de acesso (Row Level Security), as queries e a
interface automaticamente a partir disso.

### 7. (Opcional) Rodar a API própria
Só necessário se você quiser hospedar o site aqui mesmo (fora do Lovable)
ou servir os dados para outro sistema:
```bash
uvicorn api:app --reload
```
Endpoints disponíveis: `/municipios/risco`, `/municipios/{id}/historico`,
`/alertas`.

## Próximos passos sugeridos
- Trocar a `pressao_antropica` (hoje um valor fixo de 0.5) por um dado real,
  como densidade populacional do IBGE ou densidade de rebanho da PPM/IBGE.
- Calibrar os limites de normalização em `calcular_indice_esai` com dados
  reais do semiárido de PE, em vez dos valores de referência da literatura.
- Adicionar dados da APAC/INMET/INSA como fontes complementares às do
  Earth Engine, para validação cruzada.
