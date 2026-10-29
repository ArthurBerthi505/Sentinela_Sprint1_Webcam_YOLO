# Sentinela 1.2 — API, SQLite e painel

## Atualizar o projeto que já funciona

Copie o conteúdo da pasta `Sentinela_Sprint1` deste pacote para a pasta atual
do seu projeto. Substitua os arquivos antigos, preservando `.venv`, pesos do
YOLO, logs e a pasta `dados`, quando ela já existir.

No terminal dessa pasta, instale somente as novas dependências:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements_api.txt
```

**Terminal 1 — API e tela:**

```powershell
.\.venv\Scripts\python.exe servidor.py
```

Abra [http://127.0.0.1:8000](http://127.0.0.1:8000) no navegador e mantenha esse
terminal aberto. O banco é criado automaticamente; não é necessário instalar
um servidor de banco nem executar SQL manualmente.

**Terminal 2 — webcam:**

```powershell
.\.venv\Scripts\python.exe webcam_yolo.py --camera-id webcam-01 --sala "Sala de testes"
```

A sala aparece no painel depois que o detector processa a primeira imagem e
envia um evento. A inicialização do YOLO pode demorar mais que as leituras seguintes.

Para testar a tela sem usar a câmera, no Terminal 2 execute:

```powershell
.\.venv\Scripts\python.exe simular.py
```

O simulador funciona por 60 segundos e cadastra duas fontes com os IDs `demo-01`
e `demo-02`. As duas aparecem marcadas como **SIMULAÇÃO**. Para encerrar antes,
pressione Ctrl+C. Esses eventos são fictícios e ficam no histórico, separados
pelos IDs das fontes; não são uma medição da qualidade do detector.

## O que a tela mostra

- Câmeras que já enviaram eventos e quantas têm presença confirmada ou indisponibilidade.
- Uma ficha por câmera, com sala, estado confirmado, contagem no quadro e inferência.
- Histórico com filtros por câmera, estado e período, paginação e detalhes do JSON.
- Exportação CSV dos filtros, limitada aos últimos 10 mil registros.
- Atualização da tela a cada 2 segundos. A câmera envia atualizações periódicas
  a cada 5 segundos, além das mudanças de estado e falhas.

O painel recebe metadados de presença. O vídeo continua sendo exibido na janela
local do detector; não há transmissão de imagens para o navegador.

## Banco, fila e falhas

| Arquivo | Finalidade |
| --- | --- |
| `dados/sentinela.db` | Banco da API com sessões e eventos aceitos. Caminho relativo ao arquivo `servidor.py`. |
| `dados/fila_webcam-01.sqlite3` | Fila persistente do detector; o nome acompanha `--camera-id`. |
| `dados/fila_demo_1.sqlite3` e `fila_demo_2.sqlite3` | Filas de eventos fictícios do simulador. |
| `logs/sessao_*.csv` e `.json` | Registros locais de cada execução, preservados como antes. |

Arquivos auxiliares `-wal` e `-shm` podem aparecer enquanto o SQLite está aberto.
Para copiar o banco, encerre a API e o detector primeiro e preserve a pasta `dados`.

O detector insere na fila local e outra thread envia à API. Se a API estiver
desligada, os eventos permanecem no disco; as tentativas têm intervalo crescente
até 30 s. Ao iniciar novamente o detector com a mesma fila e endereço de API,
o envio pendente é retomado. O processo precisa estar rodando para esvaziar a fila.
Se trocar `--api-url`, eventos antigos ficam associados ao endereço original;
não são enviados silenciosamente a outro destino nem bloqueiam o destino novo.

Cada evento tem UUID e sequência por sessão. Reenvio do mesmo evento não cria
outra linha no banco. Dados antigos recebidos depois de uma falha ficam no
histórico sem substituir o estado mais recente da câmera. Rejeições definitivas
HTTP 4xx, exceto 408/429, vão para a tabela `rejected` da fila, com a resposta
da API, e não bloqueiam os próximos eventos. O terminal informa essa situação.

Sem dados recentes por mais de 15 s, o painel exibe **Sem comunicação** e a
contagem fica desconhecida. Esse estado é calculado na consulta; não é inventado
um evento de câmera. O encerramento normal do detector envia uma ocorrência de
indisponibilidade. Registros de atraso ou falha usam contagem `null`, não zero.

Os horários de evento são gerados em UTC e o painel os mostra no fuso do
navegador. São horários do registro após a análise, não do instante físico de
entrada da pessoa. `image_age_ms` mede o tempo desde a leitura pelo OpenCV.
Um relógio de origem mais de 60 s adiantado é rejeitado; mantenha o relógio do
computador atualizado ao testar.

## API disponível

| Método e caminho | Uso |
| --- | --- |
| `GET /api/health` | Verificar processo e acesso ao banco. |
| `POST /api/events` | Receber um evento validado; 201 novo, 200 reenvio idêntico, 409 conflito. |
| `GET /api/overview` | Resumo e último estado de cada câmera. |
| `GET /api/events` | Histórico com `camera_id`, `state`, `hours`, `only_changes`, `limit` e `offset`. |
| `GET /api/events/{event_id}` | Evento completo, incluindo configuração. |
| `GET /api/events.csv` | Exportação dos filtros. |
| `GET /docs` | Documentação interativa do FastAPI. |

Use `hours=0` para consultar todo o histórico, incluindo registros de meses
anteriores. A tela oferece essa opção no filtro de período.

O código do schema está em `servidor.py`, classe `EventIn`. O banco usa as tabelas
`sessions` e `events` e índices por câmera/horário. As inserções usam parâmetros
SQL. O FastAPI valida estado, tipos, valores numéricos, UUIDs e fuso horário.

A API escuta somente em `127.0.0.1`, para esse protótipo local. Não foi incluído
login nem publicação na internet. A tela é servida pela própria API, sem exigir
Node.js, instalação de frontend ou um segundo servidor web.

## Outras configurações

```powershell
# Continuar somente com webcam e arquivos locais
.\.venv\Scripts\python.exe webcam_yolo.py --sem-api

# Outra sala/câmera: use um camera-id diferente para cada fonte física
.\.venv\Scripts\python.exe webcam_yolo.py --camera 1 --camera-id webcam-02 --sala "Sala técnica"

# Caso a porta 8000 esteja ocupada
.\.venv\Scripts\python.exe servidor.py --port 8001
.\.venv\Scripts\python.exe webcam_yolo.py --api-url http://127.0.0.1:8001
```

Nesse último caso, abra o navegador em `http://127.0.0.1:8001`. Para testar o
reenvio, deixe a webcam rodando, pare somente a API com Ctrl+C, faça algumas
mudanças no enquadramento e inicie a API novamente. Os eventos devem aparecer
sem duplicação, mantendo o horário original.

## Verificação do código

Para executar a suíte completa, incluindo API, banco e envio:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements_testes.txt
.\.venv\Scripts\python.exe -m unittest -v
```

São 42 testes: 20 da detecção/captura simulada e 22 da API, banco e envio.
Cobrem persistência, reenvio sem duplicação, conflitos, filtros, exportação,
indisponibilidade, eventos atrasados e troca de destino. A integração HTTP é
verificada com o cliente ASGI do FastAPI e o envio conectado a ele durante os
testes. A captura real depende da webcam do computador do usuário.

O HTML, os arquivos estáticos e a sintaxe JavaScript também foram verificados.
Não foi feita inspeção visual em um navegador no ambiente de preparação.

## Referências

- [FastAPI e bancos relacionais](https://fastapi.tiangolo.com/tutorial/sql-databases/)
- [Arquivos estáticos no FastAPI](https://fastapi.tiangolo.com/tutorial/static-files/)
- [SQLite na biblioteca padrão do Python](https://docs.python.org/3/library/sqlite3.html)
