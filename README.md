# News Machine

Primeiro marco da arquitetura: eventos de fala entram em lotes curtos, um grafo de compreensão
incremental cria discursos e claims rastreáveis, e o estado é persistido em SQLite sem bloquear a captura.

## Executar

```powershell
python -m pip install -e ".[dev]"
$env:OLLAMA_ENABLED="false"
python -m news_machine.main ingest transcription.json --stream-id demo --no-ollama
python -m news_machine.main serve
```

Durante o desenvolvimento do frontend/backend, use recarga automática para evitar que assets novos
chamem rotas de um processo Python antigo:

```powershell
python -m news_machine.main serve --reload
```

Com Ollama, remova `--no-ollama` e tenha o modelo de `OLLAMA_MODEL` disponível. A API fica em
`http://127.0.0.1:8000/docs` e aceita lotes em `POST /streams/{stream_id}/speech-events`.

O fallback determinístico permite desenvolver e testar o fluxo sem um modelo local. Ele não tenta
substituir análise editorial: histórias só são criadas quando o modelo retorna um candidato explícito.

## Transcrição conectada ao pipeline

O transcritor pode iniciar um worker de compreensão embutido. Cada segmento confirmado entra na fila
`speech.confirmed`; captura e inferência jornalística rodam em threads diferentes:

```powershell
python __main__.py "URL_DA_LIVE" --analyze --stream-id canal-data --news-db news_machine.db
```

Acrescente `--no-ollama` para validar somente a infraestrutura. O `StoryManager` compara candidatos
com histórias ativas da mesma transmissão e mescla claims quando o assunto reaparece.

## Investigação e cristalização

Configure uma instância SearxNG que exponha resultados JSON:

```powershell
$env:SEARXNG_BASE_URL="http://localhost:8080"
python -m news_machine.main serve
```

No modo integrado, candidatos entram automaticamente em um worker de investigação independente. Também
é possível iniciar ou repetir a investigação com `POST /stories/{story_id}/investigate`. O grafo formula consultas a partir
dos claims, aceita apenas URLs retornadas pelo motor, recupera o conteúdo, preserva URL e hash, compara
suporte/contradição e grava uma avaliação de maturidade. Uma história só muda para `crystallized` com:

- pelo menos duas fontes independentes;
- cobertura mínima de 80% dos claims;
- qualidade média mínima de 65%;
- consistência mínima de 80%;
- estabilidade suficiente e nenhuma contradição bloqueante.

Sem fontes ou com falhas externas, a história muda para `waiting` e registra perguntas pendentes.

## Redação e auditoria

O Qwen compara semanticamente cada claim com cada evidência e devolve postura, confiança, justificativa
e trecho de apoio. Resultados de baixa confiança viram apenas contexto. Se o Ollama estiver indisponível,
o sistema identifica claramente o uso do fallback lexical.

Histórias cristalizadas seguem automaticamente para o worker de publicação, ou podem ser processadas
com `POST /stories/{story_id}/draft`. A redação recebe somente o dossiê consolidado. O auditor confere
deterministicamente se cada claim e cada evidência citada existem e estão relacionados. Saídas inválidas
ficam em `review`; somente uma auditoria aprovada produz o estado `publishable`.

Os vereditos semânticos ficam em `GET /stories/{story_id}/verdicts` e os artigos em
`GET /stories/{story_id}/articles`.

## Sala de Situação

Com a API em execução, abra `http://127.0.0.1:8000/`. A tela inicial lista transmissões e permite iniciar
uma nova coleta. Cada transmissão abre um workspace em `/stream/{stream_id}` com gráficos de atividade,
participação das vozes e tipos de claims, além de discurso, perfis operacionais dos interlocutores,
pessoas e entidades citadas, conceitos, histórias e referências. A Sala de Situação global permanece em
`/operations`; seu snapshot consolidado está em `GET /operations/snapshot`.

O estado visual diferencia processo aberto de áudio confirmado: `iniciando` enquanto o modelo/captura
carrega, `ao vivo` apenas após segmentos recentes, `sem sinal` quando o processo continua sem áudio,
e `encerrada`, `concluída` ou `falhou` após o término. A coleta pode ser encerrada pela tela inicial ou
pelo workspace; a API correspondente é `POST /streams/{stream_id}/stop`.

Cada nova coleta também grava `media/{stream_id}.wav` em paralelo à transcrição. O workspace oferece
player sincronizado: clicar em uma fala ou na atividade temporal posiciona o áudio no instante de origem.
Um agrupador acústico local cria clusters `speaker_01`, `speaker_02` etc.; eles são auxiliares e não
constituem identificação biométrica. A redação pode nomear perfis e unificar clusters duplicados, o que
reatribui falas/claims e preserva amostras para um classificador de voz futuro.

O editor usa timestamps por palavra fornecidos pelo Faster-Whisper. Transcrições antigas recebem
intervalos estimados proporcionais. Duplo clique edita uma palavra diretamente no clipe; o menu de
contexto permite dividir o segmento antes de qualquer palavra interna. Ambas as operações mantêm
histórico e invalidam claims dependentes para nova verificação.

```text
PATCH /streams/{stream_id}/speakers/{speaker_id}
POST  /streams/{stream_id}/speakers/merge
GET   /streams/{stream_id}/audio
```

## Limites deste marco

- SQLite e fila local são os adaptadores iniciais; Redis/PostgreSQL entram na escala multiprocesso.
- Publicação externa continua fora do escopo: `publishable` significa pronto para revisão/publicação,
  não enviado automaticamente a um CMS.
- O transcritor existente permanece independente; sua saída JSON já pode alimentar este pipeline.
