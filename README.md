<div align="center">

# 🔭 OpenGlass

### Um looking glass de verdade — direto do terminal para um cartão que qualquer NOC entende

*Escolha o roteador, escolha o comando, receba um diagnóstico legível — com a saída bruta sempre a um clique de distância.*

**Versão 1.2.0** · Cisco IOS/IOS-XE e Huawei VRP com visualização completa

</div>

---

## 🧩 O problema que o OpenGlass resolve

Todo NOC já viveu isso: alguém precisa checar se um prefixo está anunciado
certinho, ou se um salto específico está dando timeout, mas a única forma de
saber é logar no roteador, digitar o comando e tentar interpretar uma saída
que só faz sentido pra quem decora sintaxe de CLI havia anos.

O **OpenGlass** tira essa fricção do caminho: um front-end simples entrega
`ping`, `traceroute` e detalhe de prefixo BGP como cartões traduzidos —
sucesso, parcial ou falha já resumidos — sem esconder o dado bruto de quem
quiser conferir.

---

## ⚙️ O que ele faz

| Comando | O que você vê |
|---|---|
| 🟢 **Ping** | Status (Sucesso/Parcial/Falha), enviados/recebidos, perda, latência min/avg/max e a resposta sonda a sonda |
| 🛰️ **Traceroute** | Salto a salto com IP, AS e tempo de cada sonda, indicando se o tráfego realmente chegou ao destino |
| 🌐 **BGP (detalhe de prefixo)** | Todos os caminhos com AS path, next hop, origem, localpref/metric/weight e flags — com o melhor caminho em destaque |
| 🧭 **BGP (AS Path)** | Prefixos agrupados por caminho, com a cadeia de AS e o melhor caminho de cada prefixo |
| 🏷️ **BGP (Community)** | Quem anuncia cada prefixo e por qual next hop, com as communities de cada rota |

Por trás dos cartões:

- **Execução segura** — whitelist de comandos por NOS, sanitização anti-injeção e timeouts configuráveis
- **Duas faces, um motor** — interface web (FastAPI + frontend estático, com logo/links/tema via `openglass.yaml`) e CLI interativa/one-shot

> **Cobertura atual:** **Cisco IOS/IOS-XE** e **Huawei VRP**, com os cinco
> comandos visualizados nos dois. Suporte a outros vendors (Juniper, MikroTik
> etc.) está em desenvolvimento.

---

## 🆕 Novidades da 1.2

### Visualização completa no Huawei VRP

O perfil `huawei_vrp.yaml` expõe cinco comandos e **agora todos os cinco têm
cartão**. O que faltava era o `bgp-community`, que voltava texto cru — e a
razão registrada no código para aquilo estava errada.

- **Correção de uma conclusão falsa.** O docstring do `bgp_community.py`
  afirmava que "o VRP deste lab não tem nenhuma community na tabela", com base
  em `no-export`, `65000:1` e `65000:666` voltando vazias. São communities que
  **não existem** naquele equipamento, não communities ausentes: as communities
  em uso são `65001:*` e uma community de política local, todas em política de
  export. O texto cru estava sendo aceito por uma conclusão errada, não por
  falta de dado.
- **Leitura própria da tabela do VRP.** A tabela de community do VRP tem coluna
  `Community` e **não tem coluna de AS path** — que é justamente a coluna que a
  leitura compartilhada exige. Novo `read_community_table` em `bgp_table.py`.
- **Estado vazio deixou de ser texto cru.** `Total Number of Routes: 0` vem sem
  tabela nenhuma, e sem tratamento caía no parser do IOS e virava erro. Agora
  responde com o estado vazio do card.
- **Agrupamento diferente, por formato.** Sem AS path não há AS de origem para
  deduzir: as rotas vêm da RIB com next hop `0.0.0.0`, ou seja, origem local, e
  vão para o grupo `local`. Em troca, cada entrada ganha a lista **completa** de
  communities que o VRP imprime por prefixo — a coluna `Communities` só entra
  na tabela quando o payload tem o dado, para o card do IOS não ganhar célula
  vazia.

### Bloqueio de faixas reservadas e ASNs privados

Duas validações novas, aplicadas **antes de qualquer conexão SSH**, nos dois
NOS:

- **Faixas especiais do IPv4 (RFC 6890).** `0.0.0.0/8`, `192.0.0.0/24`,
  `198.18.0.0/15`, `240.0.0.0/4`, `255.255.255.255/32` e `192.88.99.0/24`
  passam a ser recusadas. O que a IANA marca como `Globally Reachable = TRUE`
  foi deixado de fora de propósito (`192.31.196.0/24`, `192.52.193.0/24`,
  `192.175.48.0/24`): são anycast público, e consultar anycast é uso legítimo
  de looking glass.
- **ASNs privados (RFC 6996).** `64512-65534` e `4200000000-4294967294` são
  recusados no `bgp-as-path`. `65535` e `4294967295` continuam válidos, por
  serem reservados e não privados. Vale registrar que o RFC 6996 é o registro
  de AS privado, não de IP — a confusão entre os dois documentos é comum.
- As duas recusas compartilham o mesmo alert card na interface, com
  `code: reserved_range` na API.

### Correções

- **Versão da API desalinhada.** `api.py` anunciava `1.0.0` enquanto o pacote
  e o README diziam `1.1.0`. As três passam a declarar a mesma versão.
- **Card de community com uma célula a mais.** A coluna `Communities` era
  condicional, mas a célula era emitida sempre — a tabela do IOS saía com 4
  cabeçalhos e 5 células, deslocando next hop e "best". As duas saem agora da
  mesma condição.
- **Documentação que afirmava o oposto do comportamento.** A seção do
  `bgp-community` dizia que o VRP não tinha community; está corrigida acima.

---

## 🚀 Instalação

### Via instalador (produção)

```bash
git clone https://github.com/Glledson/OpenGlass.git
cd OpenGlass
sudo bash install.sh
```

O instalador é uma TUI interativa (whiptail) do início ao fim, com barra de
progresso nas fases mais longas. Sem terminal interativo, ele cai
automaticamente para prompts em modo texto. Todo o log fica em
`/root/openglass-install.log`.

<details>
<summary><strong>O que o instalador faz, passo a passo</strong></summary>

1. Verifica root, detecta o SO (Debian 11+/Ubuntu 20.04+) e atualiza o sistema
2. Instala dependências (`git`, `curl`, `python3`, `uv`, `whiptail`) e cria `/etc/openglass/`
3. Usa o repositório **já clonado** — o diretório onde o `install.sh` está — sem baixar de novo (use `-d DIR` se o clone estiver em outro lugar)
4. Detecta instalação existente (`openglass.yaml` + `devices.yaml`) e pergunta: usar configs atuais, reconfigurar do zero, editar mantendo os ativos, ou cancelar
5. Configura o **site** (ASN, nome do provedor, site, contato do NOC), com validação em tempo real e tela de **Confirmar / Corrigir / Cancelar**
6. Configura os **ativos** — roteador por roteador: nome, vendor (lido de `nodes/`), IPv4, source IPv4, comunidade SNMP, usuário/senha SSH, porta — com confirmação e repetição para quantos ativos forem necessários
7. Gera `/etc/openglass/openglass.yaml` e `/etc/openglass/devices.yaml` (com backup `.bak.<timestamp>` se já existirem), cria o `.env` do serviço e testa o CLI

</details>

Por padrão, sobe como **serviço systemd** (`openglass`), iniciando no boot,
rodando sob o usuário dedicado `openglass` e lendo config de `/etc/openglass`.
Se o repositório estiver em área restrita (ex: `/root/OpenGlass`), o serviço
roda como root — o instalador avisa nesse caso.

```bash
sudo bash install.sh --no-service   # sem criar o serviço
sudo bash install.sh --uninstall    # remove serviço e symlinks, preserva configs
```

Outras flags: `--no-apt`, `--no-uv`, `--no-symlinks`, `-d DIR` (diretório do
repo), `-c DIR` (diretório de config).

### Modo desenvolvimento (manual)

```bash
uv sync
cp .env.example .env                        # timeouts, se quiser ajustar
cp openglass.yaml.example openglass.yaml    # org_name, primary_asn…
cp devices.yaml.example devices.yaml        # seus roteadores
```

---

## 🖥️ Uso

```bash
uv run python main.py --list-devices        # inventário de roteadores
uv run python main.py --list-commands       # comandos permitidos
uv run python main.py                       # modo interativo
uv run python main.py --device edge-r1 --command ping --param ip=8.8.8.8
```

Subindo a interface web:

```bash
uv run openglass-web
# abra http://localhost (ou o endereço configurado)
```

**Exit codes:** `0` sucesso · `2` erro · `130` abortado pelo usuário

---

## 🛠️ Comandos e parsers

| Comando | Exemplo (cisco_ios) | Exemplo (huawei_vrp) | Parser |
|---|---|---|---|
| `ping` | `ping 8.8.8.8 repeat 5 source 192.0.2.10` | `ping -c 5 -a <src> 8.8.8.8` | `ping` |
| `traceroute` | `traceroute 1.1.1.1 timeout 1 probe 2 source 192.0.2.10` | `tracert -q 2 -f 1 -a <src> 1.1.1.1` | `traceroute` |
| `bgp-route` | `show bgp ipv4 unicast 8.8.8.0/24 \| exclude pathid:\|Epoch` | `display bgp routing-table 8.8.8.0 24` | `bgp_prefix` |
| `bgp-as-path` | `show bgp ipv4 unicast quote-regexp "(_65000_)"` | `display bgp routing-table regular-expression _65000_` | `bgp_as_path` |
| `bgp-community` | `show bgp ipv4 unicast community 65000:666` | `display bgp routing-table community 65000:666` | `bgp_community` |

A origem de ping/traceroute é preenchida automaticamente com o
`source_address` do VRF do roteador. Comandos e parsers vivem no perfil do
NOS em `nodes/` — hoje `cisco_ios.yaml` e `huawei_vrp.yaml`.

`ping`, `traceroute`, `bgp_prefix`, `bgp_as_path` e `bgp_community` são
multiplataforma: o dispatch em `parsers/dispatch.py` decide entre a implementação
do IOS e a do VRP pelo **formato da saída recebida**, não pelo NOS do profile. É
o texto que o equipamento devolve que é evidência, então um profile com o NOS
trocado ainda renderiza certo, e nenhuma das duas saídas cai no parser da outra.
`bgp_as_path` vai mais longe e lê a mesma tabela com um parser só, porque o VRP e
o IOS imprimem a tabela com o mesmo formato.

O `bgp_community` precisava de leitura própria no VRP porque as duas tabelas
**não** têm a mesma forma: a do IOS traz o AS path, a do VRP traz a coluna
`Community` e nenhuma columna de caminho. A assinatura do cabeçalho é o que
separa as duas, e é por isso que o dispatch não pode escolher só pelo NOS.

### Visualizador de Community

`bgp-community` agrupa por **AS de origem** — o último AS do caminho, ou seja a
borda que está anunciando. O filtro já devolveu os prefixos e a saída não
repete a community em nenhuma coluna, então a pergunta que sobra é *quem está
vazando essa community*, e a resposta é a lista de quem anuncia e por qual next
hop. No equipamento real, `no-export` casou 32 prefixos de **12 ASs
diferentes**.

O card mostra rotas, quantos ASs anunciam, prefixos distintos e quantas são
best path. Communities bem conhecidas (`no-export`, `no-advertise`, `internet`,
`local-AS`) aparecem com a descrição, já que o IOS aceita esses nomes no filtro.

A tabela é manipulável: busca por prefixo, next hop ou AS, um seletor de AS de
origem, o filtro "só best path" e ordenação por qualquer coluna — o clique no
cabeçalho ordena e o segundo clique inverte. Uma linha é um prefixo por AS de
origem, e ela descreve a **melhor rota** daquele prefixo: quando a saída tem
additional-path, o next hop mostrado é o da linha `*>`, não o da primeira linha.

No **VRP** as duas entradas viram de lado. A tabela dele tem coluna
`Community` — com a lista inteira de communities de cada prefixo, não só a
consultada — e **não** tem coluna de AS path, então não existe AS de origem
para deduzir: o que o filtro traz são rotas da RIB, e o card as marca como
`local` (o next hop `0.0.0.0` confirma origem local). A coluna `Communities`
aparece só quando o payload tem o dado, então a card do IOS não ganha uma
célula vazia. No equipamento real, `65001:40100` casou 3 prefixos
(`198.51.100.0/23`, `198.51.100.0/24` e `198.51.101.0/24`), e o `/23` aparece
com as 5 communities que o seguram.

Uma community que ninguém usa responde `Total Number of Routes: 0` sem vir
tabela nenhuma. Isso é resposta, não erro: o card mostra o estado vazio em vez
de a consulta voltar como texto cru.

### Visualizador de AS Path

`bgp-as-path` agrupa os prefixos por caminho, porque a pergunta real de uma
consulta "quem passa por este AS" é quais cadeias de AS existem, e não uma
lista de milhares de prefixos. O card mostra prefixos e rotas, o número de
caminhos distintos, o maior caminho e quantos ASs aparecem; cada cadeia abre
uma tabela com os prefixos. O AS pesquisado fica destacado na cadeia.

Para adicionar um visualizador, siga o mesmo padrão dos existentes: escreva a
card, registre o `parsed.type` no `VISUALIZERS` (front) e no
`_PARSED_FORMATTERS` (CLI), e a interface a encontra sozinha. Tipos sem
visualizador caem numa tabela de campos, nunca quebram.

### O que muda no visualizador com o VRP

`ping`, `traceroute` e `bgp-route` reaproveitam as mesmas cards do IOS, e cada
uma ganha o que só o VRP mede. São campos a mais, nunca campos inventados: o
que o equipamento não imprime fica vazio em vez de receber um default que
parece medido.

**Ping.** O VRP imprime uma linha por sonda, com TTL e tempo individuais, onde o
IOS só entrega `!!!!!`. O chip de cada sonda mostra `Resposta · TTL 111 · 38 ms`
nesse title. A perda sai como `Request time out` e vira sonda expirada — sem TTL
e sem tempo, porque um timeout não mede latência. O `Tempo limite` fica em
branco: o VRP usa 3 s por padrão e não imprime esse número, e um "2s" ali
seria um valor que o operador não mediu.

**Traceroute.** A diferença é estrutural: o IOS imprime o next hop uma vez por
salto, e o VRP pode repetir o endereço **quando cada sonda saiu por um caminho
diferente** — no equipamento do lab, o salto 4 respondeu por `209.85.244.163` e
por `192.178.84.13` na mesma linha. Ler só o primeiro endereço, como no IOS,
mostraria um next hop que não é o do segundo pacote. Cada sonda carrega o seu
endereço, e o salto ganha a marca `+1` com os alternativos no title. O VRP
também não decora o salto com `[AS N]`, então o AS fica vazio em vez de
preenchido.

**Detalhe de prefixo.** O VRP abre um bloco por caminho com `From:`, e traz
quatro coisas que o detalhe do IOS não tem: as **communities** do caminho, a
**interface de saída** (`Direct Out-interface`), há **quanto tempo** a rota está
na tabela (`Route Duration`) e — o mais útil quando há dois caminhos para o
mesmo prefixo — o **motivo da derrota** (`not preferred for router ID`,
`not preferred for AS-Path`). A card mostra esse motivo numa faixa própria,
antes das flags. O `pref-val` (valor de preferência, quanto maior melhor)
aparece com o nome do VRP e **não** é mapeado para `metric`: a métrica do IOS
é outra grandeza, e somá-las na mesma linha diria que se comparam.

O prefixo inexistente responde `Info: The network does not exist.` e vira o
aviso de "não encontrado" — o mesmo tratamento do `% Network not in table` do
IOS, com textos que não se confundem.

### Templates por família

Um comando pode declarar um template só ou um por família de destino; a engine
escolhe a variante pelo parâmetro de endereço informado:

```yaml
ping:
  params:
    ip:
      type: destination
  template:
    ipv4: "ping $ip repeat 5 source $source_address"
    ipv6: "ping ipv6 $ip repeat 5 source $source_address"
```

Tipos de parâmetro: `destination`, `ip`, `prefix`, `hostname`, `asn` e
`community`.

### Sobre o AS Path

O usuário **não** informa uma regex. Ele informa o(s) AS(ns) e a engine monta a
regex. A forma depende do NOS, declarada em `asn_regexp:` no perfil:

| NOS | `asn_regexp` | Entrada `65000` | Entrada `65000,15169` |
|---|---|---|---|
| `cisco_ios` | `"({asns})"` | `quote-regexp "(_65000_)"` | `quote-regexp "(_65000_\|_15169_)"` |
| `huawei_vrp` | `"{asn}"` | `regular-expression _65000_` | recusado (o VRP usa `\|` como modificador de output) |

Os `_` ao redor do AS fazem casar o número **no meio** do caminho, sem depender
de âncora. Só entram dígitos (validados em 1..4294967295, até 10 AS por
consulta), então não existe superfície de injeção: o usuário nunca digita
metacaractere.

**Consultar o AS do próprio roteador é recusado.** O IOS e o VRP prefixam o AS
local em todo caminho originado ali, então `_<asn-local>_` casa praticamente a
tabela inteira — foram 1.1M de linhas e 211s no `edge-r1`. Para travar isso, o
inventário tem `asn:` por device e a engine recusa a consulta:

```yaml
- name: edge-r1
  nos: cisco_ios
  asn: 265269
```

`bgp-as-path` e `bgp-community` varrem a tabela BGP inteira, por isso declaram
`timeout: 300`. Saídas acima de 5000 linhas voltam inteiras, mas com
`truncated: true` e `line_count` na resposta da API — a interface avisa que a
tela pode demorar a renderizar.

---

## 🔒 Segurança em primeiro lugar

- Somente comandos da whitelist do NOS são aceitos — nada de `show running-config` escapando pela brecha
- Parâmetros validados (IP/prefixo/hostname/AS/community); caracteres de injeção são rejeitados na porta
- **Faixas de IP reservadas são barradas como destino de consulta** — além das faixas privadas, o registro IPv4 de uso especial ([RFC 6890](https://www.rfc-editor.org/rfc/rfc6890)) cobre `0.0.0.0/8`, `192.0.0.0/24`, `198.18.0.0/15` (benchmarking), `240.0.0.0/4`, `255.255.255.255/32` e o anycast 6to4 `192.88.99.0/24` (alocação encerrada em 2015). As entradas marcadas como *Globally Reachable = TRUE* no registro — `192.31.196.0/24` (AS112), `192.52.193.0/24` (AMT) e `192.175.48.0/24` (Direct Delegation AS112) — continuam liberadas, porque consultar anycast é uso legítimo de looking glass
- **ASNs privados são barrados no `bgp-as-path`** — as faixas [RFC 6996](https://www.rfc-editor.org/rfc/rfc6996) `64512-65534` e `4200000000-4294967294` não são globalmente únicas e não aparecem na tabela BGP global, então a consulta só voltaria vazia
- O bloqueio acontece na validação do parâmetro, **antes de qualquer conexão SSH**, e a UI mostra um card de alerta com a faixa, o RFC e a finalidade
- O template do comando vem da config do admin (`nodes/*.yaml`), então pipes e aspas são permitidos; encadeamento (`;`, `&`), redirecionamento e `$` de placeholder não resolvido continuam bloqueados
- Timeouts de conexão e leitura protegem contra roteador que simplesmente não responde

---

## ✅ Testes

```bash
uv run pytest
```

**347 testes**, com Netmiko mockado e recortes reais de saída de equipamento
como fixtures (o `bgp_as_path` é testado contra IOS-XE e VRP reais, e ping,
traceroute e bgp-route têm captura de NetEngine 8000/VRP 8.200) — sem depender
de dispositivo físico para validar a suíte.

A interface tem suíte própria, porque é JS puro dentro do `index.html` e um
`ReferenceError` numa função de render passa em qualquer checagem de sintaxe:

```bash
cd tests/js && npm install && npm test
```

**44 testes** que carregam o `index.html` de verdade num jsdom e disparam os
eventos como o usuário faria: digitação na busca, clique nos cabeçalhos, filtro
de AS e de best path, estado vazio e escape de dado hostil. O payload vem da
captura real do equipamento, e `TestJsPayloadFixture` no pytest falha se esse
JSON divergir do parser — os dois lados não podem envelhecer em separado.

Os testes do VRP têm o par IOS correspondente em cada caso. Não basta o card do
VRP renderizar certo: um campo que o IOS não devolve não pode aparecer na card
do IOS, e vice-versa. Por isso o mesmo `traceroute`, `ping` e `bgp_prefix` é
testado nos dois formatos, e o dispatch tem teste próprio para cada assinatura
— inclusive para os textos de prefixo inexistente, que são diferentes
(`% Network not in table` no IOS, `Info: The network does not exist.` no VRP).

---

## 📁 Estrutura do projeto

```
nodes/<nos>.yaml        whitelist + templates por NOS
src/openglass/
  inventory.py          inventário (devices.yaml)
  connection.py         sessão SSH (Netmiko) e erros tipados
  commands.py           whitelist, validação, execução (CommandResult)
  parsers/              parsers de output (ping, traceroute, bgp_prefix, bgp_as_path,
                        bgp_community) + bgp_table (leitura de tabela compartilhada)
  security.py           sanitização anti-injeção
  cli.py                CLI interativo/one-shot
  api.py                API + frontend estático (FastAPI)
  static/index.html     frontend
tests/                  suíte de testes Python (305)
  fixtures/             recortes reais de saída de IOS e VRP
  js/                   suíte da interface (jsdom + node --test)
install.sh              instalador interativo (root: sudo bash install.sh)
CHANGELOG.md            histórico de versões
```

---

<div align="center">

*Feito para quem vive de plantão e não tem tempo a perder decifrando CLI.*

</div>