# Sentinela — Sprint 1

Protótipo acadêmico para o Challenge FIAP/AMAZUL: monitoramento de presença de
pessoas em ambientes fictícios com webcam, YOLO, API FastAPI, SQLite e painel web
local. Esta versão implementa a primeira etapa de um projeto com horizonte de
entrega em agosto de 2027.

## Funcionalidades

- Detecção da classe `person`, com limiar de confiança e região de interesse.
- Confirmação temporal de presença e ausência de detecção.
- Captura em segundo plano, usando o quadro mais recente.
- Métricas de inferência, idade da imagem e registros CSV/JSON.
- Envio de eventos à API por uma fila SQLite persistente, com reenvio e prevenção
  de duplicação.
- Painel com estado das câmeras, salas, histórico, filtros, detalhes e exportação CSV.
- Simulador de eventos, identificado na tela, para testar sem webcam.

O detector processa a imagem localmente e envia metadados para a API. A tela
consulta o histórico e os estados calculados a partir desses eventos. O vídeo
permanece na janela local do detector.

## Executar no Windows

Requisitos: Python 3.11 ou 3.12 e uma webcam para os testes reais.

Na pasta do projeto, use o PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

No primeiro terminal, inicie a API:

```powershell
.\.venv\Scripts\python.exe servidor.py
```

Abra [http://127.0.0.1:8000](http://127.0.0.1:8000). O banco é criado
automaticamente em `dados/sentinela.db`.

Em outro terminal, na mesma pasta, inicie o detector:

```powershell
.\.venv\Scripts\python.exe webcam_yolo.py --camera-id webcam-01 --sala "Sala de testes"
```

Os pesos do modelo são baixados no primeiro uso; esse download precisa de
internet. Não é necessário baixar o dataset COCO. Mantenha os dois terminais
abertos durante o teste.

### Testar sem webcam

É possível executar somente a API e o simulador, sem instalar as dependências
de visão computacional:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements_api.txt
.\.venv\Scripts\python.exe servidor.py
```

Em outro terminal:

```powershell
.\.venv\Scripts\python.exe simular.py
```

O simulador funciona por 60 segundos e envia dados fictícios de duas fontes,
exibidas como **SIMULAÇÃO**. Use Ctrl+C para encerrar antes.

### Ajustar a detecção

```powershell
.\.venv\Scripts\python.exe webcam_yolo.py --diagnostico
.\.venv\Scripts\python.exe webcam_yolo.py --confianca 0.75
.\.venv\Scripts\python.exe webcam_yolo.py --imgsz 640
.\.venv\Scripts\python.exe webcam_yolo.py --sem-api
```

Os padrões são confiança `0.65`, entrada `320`, confirmação de presença em
`0.5` segundo e confirmação sem detecções em `2` segundos. São parâmetros
iniciais para testes; compare uma alteração por vez no mesmo cenário.

## Dados e disponibilidade

`dados/` contém o banco da API e as filas de envio. `logs/` contém os registros
locais das sessões. Essas pastas e os pesos do modelo estão excluídos do Git.

O detector envia mudanças e falhas, além de atualizações periódicas a cada
5 segundos. A tela consulta a API a cada 2 segundos. Quando faltam dados
recentes por mais de 15 segundos, o estado passa a **Sem comunicação**, com
contagem desconhecida. A API mantém eventos atrasados no histórico sem usá-los
para substituir uma observação mais recente.

**Ausência de detecção não comprova sala vazia.** Oclusão, pontos cegos,
iluminação e falsas classificações afetam o resultado. O sistema detecta
pessoas visíveis; a autorização de acesso e as regras de risco pertencem
às próximas etapas do projeto. A API desta versão é local, em `127.0.0.1`.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements_testes.txt
.\.venv\Scripts\python.exe -m unittest -v
```

A suíte tem **42 testes** de confirmação temporal, captura simulada, filtros,
API, persistência, reenvio, deduplicação e indisponibilidade. A avaliação da
qualidade do modelo exige testes com imagens reais e referência anotada.

## Arquivos principais

| Arquivo | Responsabilidade |
| --- | --- |
| `webcam_yolo.py` | Captura, inferência, confirmação temporal e registros locais. |
| `envio_api.py` | Fila persistente e envio HTTP em segundo plano. |
| `servidor.py` | API, schema de eventos, banco SQLite e arquivos do painel. |
| `painel/` | Interface HTML, CSS e JavaScript. |
| `simular.py` | Publicação de eventos fictícios para testes de integração. |
| `test_webcam_yolo.py` e `test_api.py` | Testes automatizados. |
| `roteiro_testes.csv` | Roteiro para registrar a validação com webcam. |

Consulte [GUIA_API_PAINEL.md](GUIA_API_PAINEL.md) para detalhes da integração e
[LEIA_ME.md](LEIA_ME.md) para parâmetros, estados e limitações da detecção.
A documentação interativa da API fica em [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
