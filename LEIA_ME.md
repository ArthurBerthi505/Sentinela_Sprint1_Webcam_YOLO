# Sentinela — Sprint 1: webcam, API, banco e painel (revisão 1.2)

**Para iniciar a API, o banco SQLite e a tela, leia primeiro [GUIA_API_PAINEL.md](GUIA_API_PAINEL.md).**
O guia abaixo detalha a detecção com webcam, que continua disponível com os
mesmos parâmetros da revisão anterior. O envio para a API agora vem habilitado
por padrão; use `--sem-api` para trabalhar apenas com os registros locais.

Protótipo para o desafio FIAP/AMAZUL. Use uma webcam USB ou a câmera do notebook
para experimentar detecção de pessoas em um ambiente fictício. Não é necessário
baixar o dataset COCO: os pesos pré-treinados já incluem a classe `person`.

## Preparar o ambiente no Windows

Use Python 3.11 ou 3.12 e uma pasta extraída deste ZIP. Abra a pasta que contém
`requirements.txt` e `webcam_yolo.py` no VS Code. Execute os comandos no terminal
PowerShell. `py -m venv` usa a versão instalada selecionada pelo launcher.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe webcam_yolo.py
```

Esses comandos usam o executável do ambiente diretamente; não é necessário
alterar a política de execução do PowerShell. Feche Teams, Zoom e outros
programas que estejam usando a webcam.

Se você já está rodando a versão anterior, copie todos os arquivos atualizados
para a mesma pasta, preservando sua `.venv`, seus pesos e seus logs. Instale
as dependências novas com `pip install -r requirements_api.txt`, usando o Python
da sua `.venv`. Não é necessário recriar o ambiente. Consulte o guia da API.

## Melhorias da detecção, preservadas da revisão 1.1

- Captura em segundo plano mantém apenas o quadro mais recente, descartando os
  intermediários enquanto o modelo processa. O objetivo é evitar fila de imagens
  antigas; isso não acelera, por si só, a inferência do modelo.
- Entrada padrão 320, confiança mínima 0.65, presença em 0.5 s e ausência de
  detecção em 2 s. São parâmetros iniciais de teste, não valores validados para
  uma instalação real. Maior confiança pode reduzir falsas detecções e também
  perder pessoas; menor resolução pode perder detalhes.
- Além do filtro do YOLO, o código verifica explicitamente classe `person`, score
  mínimo e região antes de contar. Um erro do modelo com classe `person` e score
  alto ainda pode ser aceito; esses filtros não garantem reconhecimento perfeito.
- Painel separa detecções no quadro, estado temporal confirmado e espera restante.
- Tempo de inferência e idade desde a leitura são mostrados e registrados.
- Resultados com mais de 2 s desde a leitura não confirmam presença nem ausência.
- `--diagnostico` mostra classes e scores de outros objetos, em cinza; somente
  pessoas aceitas pelo limiar e pela região são contadas e marcadas em verde.

O modelo padrão é `yolo26n.pt`, da família apresentada atualmente na documentação
oficial. Os pesos são baixados no primeiro uso, que precisa de internet. A inferência
é executada no computador. Nenhuma gravação de imagem ou vídeo é habilitada
pelo script. Para usar outros pesos de detecção COCO, informe `--modelo yolo11n.pt`
ou um caminho local. O modelo escolhido é registrado em cada sessão.

Se `py` não for encontrado, instale Python com o launcher ou use o caminho do
executável correspondente. Instalação e captura precisam de um computador local
com acesso à webcam; um ambiente remoto sem câmera não valida a captura real.

## O que observar

- Caixas verdes nas pessoas detectadas dentro da região monitorada.
- Contagem instantânea de detecções; não identifica indivíduos.
- Estado confirmado, espera restante, tempo de inferência e idade da imagem.
- FPS IA representa 1 dividido pelo tempo da chamada de inferência; não é a taxa
  de atualização da webcam ou da janela.
- Confiança mínima ajustável com `+` e `-`, com a janela selecionada.
- `Q`, `Esc` ou o botão de fechar encerram o teste.

O score mostrado nas caixas não significa uma probabilidade calibrada de a pessoa
estar presente. É usado como critério de filtragem do detector.

| Estado | Significado |
| --- | --- |
| VERIFICANDO | Ainda não houve observações consecutivas suficientes. |
| PRESENCA_DETECTADA | Houve detecção contínua por pelo menos o tempo de confirmação. |
| SEM_PESSOAS_DETECTADAS | Nenhuma pessoa foi detectada durante o intervalo configurado. |
| MONITORAMENTO_INDISPONIVEL | Ocorreu uma falha de abertura, captura ou processamento. |

A mudança para presença exige inicialmente 0.5 segundo de observações positivas.
A mudança para sem pessoas detectadas exige inicialmente 2 segundos de
observações negativas. O estado anterior permanece durante essa confirmação;
por isso o estado pode indicar presença enquanto a contagem instantânea é zero.
Uma nova detecção reinicia o tempo sem detecção. Ajustar a confiança reinicia
a confirmação temporal. Intervalos acima de `--max-intervalo` também a reiniciam.

Falha de captura ou inferência registra indisponibilidade e encerra o programa;
execute-o novamente depois de resolver a falha. Não há reconexão automática.
O consumidor espera até 5 s por um quadro novo; se o driver bloquear, a espera
termina com erro. A leitura que estiver bloqueada no driver roda em uma thread
daemon e pode só ser liberada ao encerrar o processo.
Resultados atrasados registram indisponibilidade e são descartados, mas o loop
continua tentando processar quadros novos. A primeira inferência pode demorar
mais e cair nesse caso; observe as seguintes. A idade é medida a partir do retorno
de `read()`, não do instante físico de exposição da câmera.
Este protótipo não detecta imagem congelada ou obstrução de lente.
Uma imagem válida sem detecções **não certifica sala vazia**: enquadramento,
iluminação e objetos que escondem pessoas podem causar erros.

## Alterar parâmetros

Execute um teste por vez, alterando somente um parâmetro para comparar resultados.

```powershell
# Rodar com os novos padrões de teste
.\.venv\Scripts\python.exe webcam_yolo.py

# Investigar quais classes o modelo está atribuindo aos objetos
.\.venv\Scripts\python.exe webcam_yolo.py --diagnostico

# Comparar uma entrada maior se pessoas pequenas não forem detectadas
.\.venv\Scripts\python.exe webcam_yolo.py --imgsz 640

# Experimentar um limiar mais permissivo
.\.venv\Scripts\python.exe webcam_yolo.py --confianca 0.30

# Experimentar um limiar mais restritivo
.\.venv\Scripts\python.exe webcam_yolo.py --confianca 0.65

# Confirmar ausência de detecção após 1 segundo, somente para comparar o filtro
.\.venv\Scripts\python.exe webcam_yolo.py --ausencia 1

# Confirmar presença após 2 segundos
.\.venv\Scripts\python.exe webcam_yolo.py --confirmacao 2

# Tentar outra webcam ou outro backend do Windows
.\.venv\Scripts\python.exe webcam_yolo.py --camera 1
.\.venv\Scripts\python.exe webcam_yolo.py --backend dshow

# Monitorar apenas a área central da imagem
.\.venv\Scripts\python.exe webcam_yolo.py --roi 0.1 0.1 0.9 0.9
```

| Parâmetro | Padrão | O que comparar |
| --- | --- | --- |
| `--confianca` | 0.65 | Valores menores aceitam mais detecções, podendo incluir erros. Valores maiores podem perder pessoas. |
| `--imgsz` | 320 | Entrada menor pode acelerar o processamento e perder detalhes; compare com 640. |
| `--confirmacao` | 0.5 | Quanto esperar antes de confirmar presença. |
| `--ausencia` | 2 | Quanto esperar sem detecções antes de mudar o estado. |
| `--max-intervalo` | 5 | Intervalo máximo entre análises consecutivas válidas. |
| `--max-atraso` | 2 | Idade máxima, em segundos desde a leitura, para aceitar um resultado. |
| `--camera` | 0 | Índice local da webcam. |
| `--roi` | Imagem inteira | Retângulo normalizado: X1 Y1 X2 Y2, entre 0 e 1. |
| `--device` | cpu | CPU por padrão; GPU é opcional e depende da instalação. |
| `--diagnostico` | Desligado | Mostra outros objetos em cinza, sem contá-los como pessoas. |

A região usa o **centro da caixa** como referência: uma pessoa na borda pode
mudar de região conforme a detecção varia. A inferência continua analisando
a imagem inteira, mas somente as detecções cujo centro está na região são contadas.

## Registros e validação

Cada execução cria um CSV e um JSON em `logs/`. O CSV tem separador `;` e BOM
UTF-8 para facilitar a abertura no Excel. Registra mudanças de estado e uma
amostra aproximadamente a cada segundo, quando há quadros processados.
O JSON guarda parâmetros e versões das dependências; ajustes de confiança
feitos pelo teclado aparecem na coluna `confianca_minima` do CSV.

Os logs são amostras, não um registro de todos os quadros. FPS não é o tempo
total entre uma entrada real e o alerta. Para avaliar essa latência, anote o
instante da entrada e o instante da mudança de estado. As colunas `inferencia_ms`
e `idade_imagem_ms` ajudam a diferenciar custo do modelo de atraso da imagem.
Quando a imagem está atrasada ou ocorre falha, a contagem fica em branco, não zero.

Use `roteiro_testes.csv` para registrar observação humana e resultado do sistema.
Execute o mesmo cenário com confiança 0.30, 0.45 e 0.65, mantendo os demais
parâmetros. Compare pessoas não detectadas, falsas detecções e tempo de confirmação.
A contagem é uma estimativa do quadro, sem rastreamento de identidade.

Para registrar exatamente as dependências instaladas no seu computador:

```powershell
.\.venv\Scripts\python.exe -m pip freeze > requirements_lock.txt
```

## Verificações do código

Os testes automatizados usam a biblioteca padrão e entradas simuladas; podem ser
executados antes da instalação de YOLO e OpenCV:

```powershell
py -m unittest -v test_webcam_yolo
```

São 20 testes que cobrem confirmação temporal, interrupções, região de interesse,
classe/score, parâmetros inválidos, registro da sessão, falhas de captura/inferência,
descarte de quadros antigos, entrega única e invalidação de resultados atrasados.
Eles não medem
a qualidade do modelo nem substituem os testes com sua webcam. O protótipo foi
verificado com esses testes, mas a captura e a inferência real não foram executadas
no ambiente de preparação, que não dispõe de webcam nem dessas dependências.

## Problemas comuns

- **Webcam não abre:** feche outros apps, tente `--camera 1` e confira a permissão
  de acesso à câmera para aplicativos da área de trabalho no Windows. Teste também
  `--backend dshow` ou `--backend msmf`.
- **Lentidão:** observe `Inferencia` e `Idade da imagem`. Se a inferência for alta,
  o custo do modelo/computador continua limitando a resposta, mesmo sem fila de
  quadros. Compare o mesmo cenário em 320 e 640; a entrada padrão já é 320.
  A câmera solicita 640 × 480, mas o driver pode usar outra resolução.
- **Objetos marcados como pessoas:** teste `--diagnostico` e registre o objeto,
  a classe e o score. Experimente confiança 0.75 mantendo o resto igual; isso
  pode perder pessoas. Fotos e pessoas em telas também podem ser detectadas.
  Erros persistentes devem ser avaliados em vídeos próprios antes de decidir
  sobre ajuste do modelo ou treinamento com exemplos negativos.
- **MONITORAMENTO_INDISPONIVEL no primeiro quadro:** a inicialização do modelo
  pode demorar. Se os seguintes ficarem disponíveis, compare esses quadros.
  Se todos excederem `--max-atraso`, investigue o tempo de inferência; aumentar
  esse limite permite usar resultados mais antigos e não acelera o programa.
- **Modelo não é baixado:** confira a conexão; também é possível usar `--modelo`
  com o caminho de pesos já baixados.
- **Erro de janela OpenCV:** este teste precisa de interface gráfica e
  `opencv-python`, em vez de uma instalação apenas `headless`.
- **Estado fica verificando:** verifique se as detecções oscilam ou se o tempo entre
  análises supera `--max-intervalo`. Analise desempenho e iluminação antes de
  aumentar esse limite.
- **Você continua aparecendo sentado:** isso é esperado; detecção de pessoas não
  exige movimento para cada quadro.

## Documentação consultada

- [Instalação e webcam com Ultralytics](https://docs.ultralytics.com/quickstart/)
- [Inferência, confiança, classes e tamanho de entrada](https://docs.ultralytics.com/modes/predict/)
- [COCO e classe person](https://docs.ultralytics.com/datasets/detect/coco/)
- [Contagem por região](https://docs.ultralytics.com/guides/region-counting/)
- [Captura de câmera com OpenCV](https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html)
