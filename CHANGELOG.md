# Changelog

Todas as mudanças relevantes do OpenGlass são documentadas aqui. Formato
inspirado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [1.2.0] — visualização completa no Huawei VRP e bloqueio de faixas/ASNs reservados

### Visualizador de Community no Huawei VRP

**Feature — `bgp-community` deixa de voltar texto cru no VRP**
- `huawei_vrp.yaml` passou a declarar `parser: bgp_community`, e o nome do
  parser agora passa pelo `dispatch.py`. Com isso **nenhum** comando do perfil
  VRP fica sem visualização: `bgp-route`, `bgp-as-path`, `bgp-community`,
  `ping` e `traceroute` têm card.
- O comando não voltava card porque a leitura da tabela do VRP exige uma coluna
  de AS path (`Path`/`Path-Ogn`) que **a tabela de community não tem**. Novo
  `read_community_table` em `bgp_table.py` lê a outra forma: o cabeçalho tem
  `Community` e não tem AS path, que é a assinatura do VRP.

**Correção — a afirmação "o VRP deste lab não tem community" era falsa**
- O docstring de `bgp_community.py` e o comentário do perfil afirmavam que o
  equipamento não tinha nenhuma community, com base em `no-export`, `65000:1` e
  `65000:666` voltando vazias. A evidência não sustentava: eram communities que
  **não existem** nesse equipamento. O VRP usa `65001:*` (`65001:1000`,
  `65001:10301`, `65001:10400`, `65001:20100`, `65001:40100`) e
  `community de política local`, todas em política de export.
- `display bgp routing-table community 65001:40100` devolve 3 rotas na RIB. O
  texto cru estava sendo aceito por causa de uma conclusão errada, não por
  falta de dado.

**Change — o card do VRP não agrupa por AS de origem, e mostra as communities**
- Sem coluna de AS path não há AS de origem para deduzir. As rotas que o filtro
  traz na RIB não têm caminho, e o next hop `0.0.0.0` confirma que foram
  originadas localmente: tudo vai para o grupo `local`, que a card já renderiza.
- Cada entrada ganha `communities` com a lista inteira que o VRP imprime por
  prefixo, então o card mostra o contexto de quem segura o prefixo e não só a
  community que motivou a busca. A coluna `Communities` só entra no `<thead>`
  quando o payload tem o dado, para a card do IOS não ganhar célula vazia.
- `Total Number of Routes: 0` deixou de ser caminho para texto cru: community sem
  uso no VRP não vem com tabela nenhuma, e `read_community_table` devolve lista
  vazia para o card mostrar o estado vazio. A assinatura do dispatch passou a
  aceitar esse caso (`Total Number of Routes: 0`), senão ele cairia no parser do
  IOS e viraria erro.
- `MAX_ORIGINS` saiu de `bgp_community.py` para `bgp_table.py`: os dois lados
  agora compartilham o teto, e importar um do outro criaria ciclo com o
  `__init__` dos parsers.

### Bloqueio de faixas reservadas e ASNs privados

**Change — `security.py` fecha o registro IPv4 de uso especial**
- `RFC_RESERVED_NETWORKS` ganhou as faixas que faltavam do registro IPv4 de
  uso especial ([RFC 6890](https://www.rfc-editor.org/rfc/rfc6890)):
  `0.0.0.0/8` (This network), `192.0.0.0/24` (IETF Protocol Assignments),
  `198.18.0.0/15` (Benchmarking), `240.0.0.0/4` (Reservado),
  `255.255.255.255/32` (Limited broadcast) e `192.88.99.0/24` (6to4 Relay
  Anycast, alocação encerrada em 2015-03 pelo RFC 7526).
- As entradas com *Globally Reachable = TRUE* no registro — `192.31.196.0/24`
  (AS112-v4), `192.52.193.0/24` (AMT) e `192.175.48.0/24` (Direct Delegation
  AS112) — foram deixadas de fora de propósito: são anycast público real e
  consultar anycast é o uso legítimo de um looking glass.
- `255.255.255.255/32` foi posto antes de `240.0.0.0/4` na tabela porque
  `_find_reserved` devolve a primeira entrada que casa; sem isso o alerta
  citava o `/4` em vez da faixa específica.

**Change — ASNs privados barrados no `bgp-as-path`**
- `validate_asn` agora rejeita as faixas privadas do
  [RFC 6996](https://www.rfc-editor.org/rfc/rfc6996) — `64512-65534` e
  `4200000000-4294967294` — com o novo `ReservedAsnError`. As duas faixas são
  inclusivas e `4294967295` (o máximo de 4 bytes) continua válido.
- O erro entra no mesmo alert card das faixas de IP (`code: reserved_range`),
  com `network` carregando o intervalo numérico em vez do CIDR.
- O bloqueio vale para o `bgp-as-path` dos dois NOS, porque está ligado pelo
  `type: asn` do perfil e não pelo comando.

**Nota — o prefixo da fixture de prefixo inexistente mudou**
- `198.18.0.0/24` virou consulta bloqueada (é benchmarking), então a âncora de
  proveniência da fixture `huawei_vrp_bgp_route_notfound.txt` passou a
  `display bgp routing-table 45.0.0.0 24`, verificado no equipamento: a saída
  real é a mesma linha `Info: The network does not exist.`

### Visualizador do Huawei VRP

**Feature — `ping`, `traceroute` e `bgp-route` no VRP**
- `ping`, `traceroute` e `bgp-route` deixaram de devolver texto cru no
  `huawei_vrp.yaml`. Antes, o profile só declarava `bgp_as_path` porque esses
  três parsers só reconheciam saída do Cisco IOS.
- Novos módulos `parsers/vrp_ping.py`, `parsers/vrp_traceroute.py` e
  `parsers/vrp_bgp_prefix.py`, com o mesmo contrato de payload dos módulos do
  IOS — as cards existentes renderizam os dois NOS sem duplicação.
- Novo `parsers/dispatch.py`: `ping`, `traceroute` e `bgp_prefix` decidem entre
  a implementação do IOS e a do VRP pelo **formato da saída recebida**, e não
  pelo NOS do profile. O texto do equipamento é a evidência, então um profile
  com o NOS trocado ainda renderiza certo. `bgp_as_path` e `bgp_community` não
  passam pelo dispatch: o primeiro lê a mesma tabela nos dois, e o segundo é
  do IOS de propósito.
- O ping do VRP imprime uma linha por sonda com TTL e tempo, onde o IOS só
  entrega `!!!!!`; a perda sai como `Request time out` e vira sonda expirada.
  A card mostra `Resposta · TTL 111 · 38 ms` no title do chip. `source` e
  `timeout_s` ficam `None`: o VRP não imprime nenhum dos dois, e preencher um
  default mostraria ao operador um número que ele não mediu.
- O `tracert` do VRP decora **endereço por sonda**, não por salto: quando cada
  sonda sai por um caminho diferente, o endereço se repete na mesma linha (no
  equipamento do lab, o salto 4 respondeu por `209.85.244.163` e
  `192.178.84.13`). Ler o primeiro endereço — como o IOS permite — esconderia o
  caminho alternativo. Cada sonda carrega o seu `ip`, o salto ganha `addresses`,
  e a card mostra a marca `+1` com os extras no title. O VRP não decora o salto
  com AS, então `asn` é `None` em vez de preenchido.
- O detalhe de prefixo do VRP abre um bloco por caminho com `From:` e traz o que
  o IOS não devolve: `communities`, `out_interface`, `original_next_hop`,
  `duration` e o **motivo da derrota** (`not preferred for router ID`,
  `not preferred for AS-Path`), que a card mostra em faixa própria.
- `pref-val` do VRP **não** é mapeado para `metric`. É o valor de preferência do
  caminho (maior melhor) e a métrica do IOS é outra grandeza; sobrescrever um
  pelo outro faria a card comparar o que não se compara. A card mostra
  `pref-val N` pelo nome do VRP.
- O AGGREGATOR do VRP (`AS 13335, Aggregator ID ...`) entra no mesmo
  `aggregations` que o IOS já usava.
- O prefixo inexistente responde `Info: The network does not exist.` e vira o
  aviso de "não encontrado", com o prefixo vindo do contexto da consulta. A
  mensagem estava no caminho do dispatch porque essa resposta **não** tem
  cabeçalho de entrada: sem ela no padrão de detecção, a consulta vazia caía no
  parser do IOS e voltava 502 em vez do aviso.

**Fix — timeout do `traceroute` do VRP**
- `read_timeout` é o orçamento do comando inteiro, não de cada leitura. No pior
  caso o VRP gasta 30 saltos × 2 sondas × 3 s = 180 s só de silêncio, então o
  valor default de 90 s (e um teste de 180 s) morria em `Pattern not detected`.
- O `traceroute.yaml` foi para 240 s, com a conta escrita no arquivo. Validado
  contra o equipamento: destino que não responde devolve os 30 saltos em 237 s.

**Testes — parsers e cards do VRP**
- `tests/test_vrp_parsers.py`: 42 testes sobre **8 capturas reais** de um
  NetEngine 8000 (VRP 8.200) — ping com sucesso e com perda total, tracert com
  caminho alternativo e com `*` até o salto 30, detalhe BGP com um e com dois
  caminhos, com agregação e de prefixo inexistente.
- `tests/js/vrp_cards.test.js`: 23 testes das três cards contra os payloads
  gerados das capturas, incluindo o par IOS de cada um. Renderizar a card do VRP
  certo não basta: um campo que o IOS não devolve não pode vazar para a card do
  IOS, e vice-versa.
- `TestJsPayloadFixture` amarra os 7 JSONs de `tests/js/fixtures/vrp_*.json` ao
  parser, e `TestFixtureProvenance` amarra as 8 capturas ao comando que as
  produziu. As asserções de format drift que existiam foram reescritas depois de
  a suíte achar três defeitos reais: a mensagem de prefixo inexistente
  inventada, o endereço por sonda que o parser descartava, e o AS path vazando
  para a lista de flags.
- `tests/js/helpers.js` passou a aceitar o payload como objeto, para os testes de
  card do IOS desenharem payloads montados à mão sem gravar arquivo.

### Visualizador de Community

**Feature — parser `bgp_community`**
- Novo `src/openglass/parsers/bgp_community.py`, declarado no `cisco_ios.yaml`.
- Agrupa por **AS de origem** (último AS do caminho) em vez de por cadeia: o
  filtro por community já devolveu os prefixos, e a saída não repete a
  community em nenhuma coluna. A pergunta que sobra é quem está anunciando com
  ela. No equipamento real, `no-export` casou 32 prefixos de 12 ASs distintos.
- Cada AS de origem traz os next hops usados, quantas rotas são best path e os
  pares prefixo/next hop.
- Tetos de payload (40 ASs, 250 prefixos e 10 next hops por AS) com
  `capped_origins`/`capped_prefixes` sinalizando o corte. As 68 rotas do
  equipamento real viram 5,3 KB de JSON.
- Cada entry descreve a **melhor rota** do prefixo: quando a mesma tabela tem
  additional-path, a linha `*>` substitui a `*` anterior. Antes a entry ficava
  com o next hop da primeira linha e `best: false`, então a card mostrava um
  next hop que o equipamento não escolheu. `test_best_entry_uses_best_next_hop`
  e `test_best_entries_match_best_paths_count` travam isso.
- O `huawei_vrp.yaml` **não** declara este parser, de propósito: o VRP do lab não
  tem nenhuma community na tabela — `no-export`, `65000:1` e `65000:666` voltam
  com `Total Number of Routes: 0`, e a configuração só tem
  `advertise-community`, sem community atribuída. Sem saída real para conferir,
  um parser de community para o VRP seria chute; a saída fica em texto cru até
  existir dado.

**Feature — card de community interativa**
- A tabela de prefixos passou a ser manipulável: busca por prefixo, next hop ou
  AS, seletor de AS de origem, filtro "só best path" e ordenação por qualquer
  coluna (clique no cabeçalho, segunda vez inverte).
- O toolbar nasce uma vez e só o corpo da tabela é trocado ao filtrar. Renderizar
  o card inteiro a cada tecla destruía o campo de busca no meio da digitação e
  exigia restaurar foco e cursor na mão.
- O estado do filtro vive em `communityView` e é resetado em `renderParsed`, ou
  seja, junto de quem recebe o resultado: um filtro de uma consulta anterior não
  sobrevive para a próxima, mesmo que a consulta venha por outro caminho.
- O contador diz "N de M prefixos" e não "rotas": a linha da tabela é um prefixo
  por AS de origem, não um caminho (68 rotas viram 32 linhas no equipamento
  real). O número de rotas continua no cartão de estatísticas.

**Testes — suíte da interface (`tests/js`)**
- Os visualizadores são JS puro dentro do `index.html` e estavam sem nenhum
  teste. Não é Theoretical: o `ReferenceError: rows is not defined` na card
  passou pela revisão, pelo lint e por 300 testes de Python, e só apareceu ao
  desenhar a tabela de verdade.
- `tests/js/community_card.test.js` carrega o `index.html` num jsdom e dispara
  evento como o usuário faria — digitar na busca, clicar nos cabeçalhos, marcar
  "só best path", escolher AS. 21 testes, com o test runner do próprio Node
  (`node --test`); a única dependência é o jsdom.
- O `fetch` do carregamento inicial é stubado em `beforeParse` com o formato que
  a API devolve de fato. Definido depois, o script já rodou e o erro de load
  passa despercebido.
- O teste de escape compara a **estrutura do DOM** com e sem o dado hostil, em
  vez de procurar `<script>` no HTML: os ícones da própria página já são `<svg>`,
  e o jsdom reserializa `&lt;` dentro de atributo de volta como `<`, o que faz
  o teste passar vergonhosamente.
- `tests/js/fixtures/community_no-export.json` é gerado do parser sobre a
  captura real. `TestJsPayloadFixture` no pytest falha se o arquivo divergir do
  parser, senão os dois lados envelhecem em separado e a card testada deixa de
  ser a card real.
- A captura completa entrou como `tests/fixtures/cisco_ios_community_full.txt`
  (68 rotas, 32 prefixos, 12 ASs) ao lado do recorte já existente.

**Refactor — `bgp_table`**
- A leitura da tabela BGP (colunas do cabeçalho, prefixo herdado nos caminhos
  adicionais, AS sets, origem do VRP grudada) foi extraída para
  `src/openglass/parsers/bgp_table.py`, compartilhada por `bgp_as_path` e
  `bgp_community`. Os dois comandos devolvem a mesma tabela; o que difere é o
  agrupamento, e duplicar a parte delicada seria chamar o mesmo bug de corrigir
  duas vezes.

### Visualizador de AS Path

**Feature — parser `bgp_as_path` multiplataforma**
- Novo `src/openglass/parsers/bgp_as_path.py`, cobrindo a saída do Cisco IOS e
  a do Huawei VRP, declarado nos dois perfis (`parser: bgp_as_path`).
- Agrupa as rotas por cadeia de AS em vez de despejar a tabela inteira: a
  consulta "quem passa por este AS" vira "quais caminhos existem".
- Duas armadilhas medidas em equipamento real e que motivam ler a tabela por
  coluna do cabeçalho em vez de tokenizar:
  - O caminho não é a última palavra da linha: `0 265269 13335` (peso + 2 AS) e
    `200 0 13335` (métrica + peso + 1 AS) são indistinguíveis por token.
  - O IOS **realoca a largura das colunas** conforme o conteúdo, e usa a coluna
    Network **vazia** em caminhos adicionais e rotas suprimidas. Sem tratar
    isso, o endereço do next hop era lido como prefixo e o prefixo verdadeiro
    se perdia.
- AS sets e confederation sets (`(13335 15169)`, `{...}`) contam como um salto.
- O parser repete o prefixo exatamente como o equipamento o imprimiu: numa
  mesma tabela o IOS mostrava 1496 rotas sem máscara (peer anunciando com
  máscara 0/0), e elas não são "completadas" com um `/32` inventado.
- Teto de payload (60 caminhos, 250 prefixos por caminho) com
  `capped_chains`/`capped_prefixes` sinalizando o que foi cortado: a resposta
  da API não pode ser o gargalo de uma tabela com centenas de milhares de
  prefixos. No equipamento real, 8910 rotas viram 77 KB de JSON.

**Feature — padrão de visualizador na web e no CLI**
- `VISUALIZERS` (front) e `_PARSED_FORMATTERS` (CLI) registram os visualizadores
  pelo `parsed.type`. Para adicionar um: escrever a card, registrar, e a
  interface a encontra sozinha.
- Primitivos compartilhados `visHead`, `visStats`, `visTable`, `visEmpty` e
  `asChain`, usados por todos os visualizadores.
- Tipo sem visualizador registrado cai numa tabela de campos em vez de
  falhar em silêncio (antes o `parsed` era simplesmente ignorado).
- CLI: resumo do AS Path no mesmo formato dos demais comandos.


### Nós e comandos

**Feature — novo perfil `huawei_vrp`**
- `nodes/huawei_vrp.yaml` com `bgp-route`, `bgp-as-path`, `bgp-community`, `ping`
  e `traceroute`, com a sintaxe verificada em NetEngine 8000 (VRP 8.200).
- Três diferenças do VRP que o Cisco não tem, e que quebrariam a query:
  - `display bgp routing-table` **não aceita CIDR** (`1.1.1.0/24` → `Wrong
    parameter`). Exige endereço e máscara separados, daí os placeholders novos
    `$prefix_address` e `$prefix_length`.
  - `regular-expression` **não aceita a regex entre aspas** — a aspa entra como
    caractere do padrão e a consulta volta vazia. Por isso o `asn_regexp` do IOS
    (`"({asns})"`, com aspas) e o do VRP (`{asn}`, sem) são diferentes.
  - O VRP usa `|` como separador de modificador de output, então a alternância
    entre AS não é utilizável: o perfil responde a **um AS por consulta** e
    recusa a lista com mensagem explícita.
- **Este perfil não declara `parser:`** — os parsers do projeto só reconhecem
  saída do Cisco IOS e levantariam `ParserError` em toda consulta do VRP. A
  saída do Huawei fica em texto cru até existir parser para ele.

**Feature — `asn:` no inventário e recusa do próprio AS**
- `devices.yaml` aceita `asn:` por device. O IOS/VRP prefixa o AS local em todo
  caminho originado ali, então `_<asn-local>_` casa praticamente a tabela
  inteira: 1.1M de linhas e 211s no `edge-r1`. A engine recusa a consulta com
  mensagem explicando o motivo. Device sem `asn:` não é afetado.

**Feature — placeholders derivados de prefixo**
- `$prefix_address` e `$prefix_length` resolvem o prefixo informado em endereço
  e tamanho de máscara (host vira /32 ou /128). Resolvidos pelo *tipo* do
  parâmetro, igual à seleção de família.

**Feature — `asn_regexp:` por comando**
- O formato da regex de AS Path passa a ser declarado no perfil, porque a
  gramática muda por NOS. Marcadores `{asn}` (um AS) e `{asns}` (lista) são
  validados no load: o profile tem que declarar exatamente um, e o usuário
  nunca digita regex — só entram dígitos já validados.
- Commando que usa `$as_path_regexp` sem declarar `asn_regexp` é rejeitado.

**Feature — template por família de destino**
- `template:` em `nodes/<nos>.yaml` aceita uma string **ou** um mapa
  `{ipv4, ipv6}`. A engine escolhe a variante pelo parâmetro de endereço
  informado pelo usuário, então `ping`/`ping ipv6` e `show bgp ipv4|ipv6
  unicast` convivem no mesmo comando.
- A família vem do *tipo* do parâmetro (`destination`/`ip`/`prefix`), não do
  texto: uma community como `65000:666` não é confundida com IPv6.

**Feature — novo comando `bgp-as-path` no cisco_ios**
- `show bgp ipv4|ipv6 unicast quote-regexp "<regex>"`.
- O usuário informa o(s) AS(ns) e a engine monta a regex: `65000` vira
  `(_65000_)`, `65000,15169` vira `(_65000_|_15169_)`. Os `_` fazem casar o AS
  no meio do caminho, sem âncora.
- O usuário nunca digita regex: só entram dígitos, validados em 1..4294967295
  (até 10 AS por consulta). Sem superfície de injeção por construção.
- O `parser: bgp_prefix` que estava declarado foi removido: o parser é de
  detalhe de prefixo, não de saída de `quote-regexp`.

**Change — `bgp-as-path` deixa de usar regex pré-aprovada do `openglass.yaml`**
- As regex em `queries.bgp_aspath.pattern` (`asdot`/`asplain`) não são padrões
  de busca: são padrões que *validam a forma* de uma regex digitada. Injetá-las
  no `quote-regexp` gerava consulta sem resultado (verificado em IOS-XE 16.9).
- `SiteConfig.as_path_pattern()`, o schema `queries` e o `validate_trusted_pattern`
  foram removidos junto com `site.py` voltando ao estado anterior — a config
  continua aceitando `queries:` (ignorado), sem erro de validação.

**Feature — novo comando `bgp-community` no cisco_ios**
- `show bgp ipv4|ipv6 unicast community <community>`, com o novo tipo de
  parâmetro `community` (`aa:nn`, nomes bem conhecidos e listas curtas).
- `aa:nn` é validado em 0..65535 por parte, então valor inválido é recusado
  no cliente com mensagem clara em vez de virar `% Invalid input` do IOS.

**Change — timeout e aviso de output grande**
- `bgp-as-path` e `bgp-community` varrem a tabela BGP inteira e agora declaram
  `timeout: 300`.
- `CommandResult` ganhou `line_count` e `truncated` (>5000 linhas), expostos na
  resposta de `/api/run`. O output bruto continua indo inteiro; a flag serve
  para a interface avisar que a renderização pode demorar. O CLI já truncava
  em 500 linhas com aviso próprio.

**Change — comandos do cisco_ios passam a usar `show bgp <fam> unicast`**
- `ping`/`traceroute`/`bgp-route` com flags de ORIGEM (`repeat 5`, `timeout 1
  probe 2`) e `| exclude pathid:|Epoch` no `bgp-route`, reduzindo o output.

### Segurança

- **Template do admin é confiável**: `validate_final_command` passou a bloquear
  apenas encadeamento (`;`, `&`), substituição de linha, redirecionamento e
  `$` de placeholder não resolvido. Pipes (`| include`, `| no-more`) e aspas
  (`vtysh -c "..."`) são necessários para os comandos reais dos NOS.
- **Parâmetros do usuário seguem estritos**: `sanitize_parameter` continua
  barrando `;&|\`$!(){}[]<>'"\` e quebras de linha, então nenhum parâmetro
  consegue introduzir pipe, aspas ou encadeamento no comando montado.
- **Regex de AS Path é construída pela engine**, não vem do usuário nem de
  pattern pré-aprovado: só dígitos validados entram em `_65000_`.

### Testes

- Suíte de 167 para **238 testes**.

## [1.1.0] — 2026-09-21

### Instalador (`install.sh`)

**Feature — interface totalmente TUI (whiptail)**
- Instalação inteira com `whiptail` do início ao fim, incluindo **gauge de
  progresso** nas fases longas (do apt/uv ao `uv sync`). Removida a opção
  `--text`.
- Sem terminal interativo (`[ -t 0 ]`), os prompts caem para modo texto e
  terminam em EOF em vez de ficarem aguardando.
- `USE_TUI` é **reavaliado após instalar pacotes**: se o `whiptail` acabou de
  ser instalado na mesma execução, a interface passa a usá-lo a partir daí.

**Feature — log de instalação**
- Todo o output da instalação é gravado em `/root/openglass-install.log`
  (teletipo e log via `tee`).

**Feature — serviço systemd por padrão**
- O `openglass.service` é **criado e habilitado por padrão** (`systemctl
  enable --now openglass`). `--no-service` desliga; `--service` mantido por
  compatibilidade.
- Unit com `WorkingDirectory=<repo>` e `ExecStart=<repo>/.venv/bin/openglass-web`;
  lê o `.env` e os configs via caminhos absolutos.
- Usuário do serviço: **`openglass`** dedicado; se o repositório estiver em
  área restrita (ex.: `/root/OpenGlass`), roda como **root** com aviso.

**Feature — idempotência e assistentes**
- Menu "Instalação existente" quando já há config em `/etc/openglass`:
  **Usar existentes / Reconfigurar / Editar / Cancelar** (com backup
  `.bak.<timestamp>` antes de sobrescrever).
- Assistente de ativos com **menu dinâmico de vendors** a partir de
  `nodes/*.yaml`; `source IPv4` opcional aceita vazio incondicionalmente
  (correção de fluxo no modo Corrigir).
- **Nenhum download do repositório**: usa o próprio diretório do `install.sh`
  (`-d DIR` para apontar outro).

**Feature — uv**
- `uv` instalado em `~/.local/bin` e colocado no PATH dos shells via
  `/etc/profile.d/openglass-uv.sh`.

**Bugfix — instalação "travada" no fim**
- A caixa "Concluído" era um `--msgbox` bloqueante: em sessão sem terminal
  visível a instalação parecia nunca terminar. Agora o resumo final é exibido
  como **texto sem TTY** e como caixa apenas em terminal interativo — a
  instalação sempre conclui.
- `uv sync`/instalação do uv com **mensagem de erro explícita** em caso de
  falha (antes abortava mudo sob `set -e`).

### Correções e ajustes gerais
- Restaurado `fail` explícito na instalação do uv (`curl | sh`).
- Exemplo de IP de gerenciamento no assistente ajustado (`192.168.0.1`).

### Testes
- Suíte ampliada de 165 para **167 testes**.

[1.1.0]: https://github.com/Glledson/OpenGlass/compare/v1.0.0...v1.1.0