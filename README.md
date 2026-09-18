# OpenGlass

Ferramenta web de diagnóstico de rede em roteadores de borda (Cisco IOS/IOS-XE na
fase atual), permitindo rodar comandos como `ping`, `traceroute`, `show ip route`
e `show ip bgp`, com a saída estruturada (parseada) quando disponível e o output
bruto como fallback.

**Versão:** 0.0.2

**Fase atual:** backend de conexão + execução via CLI + frontend web (FastAPI) +
parsing do output (`ping` e detalhe de prefixo BGP), com a identidade visual vinda
do `openglass.yaml` (título, logo, links, menus e tema).

## Estrutura (camadas)

```
main.py                        # entrada do CLI: python main.py
devices.yaml(.example)         # inventário (CREDENCIAIS — não versionado)
openglass.yaml                 # configuração de site/UI (título, logo, links, tema)
.env(example)                  # configuração (timeouts, caminhos)
nodes/cisco_ios.yaml           # whitelist + formatos de comando por NOS
src/openglass/
  inventory.py                 # carrega devices do YAML + interpolação de ${VAR}
  connection.py                # camada de CONEXÃO: sessão SSH (Netmiko), erros tipados
  commands.py                  # camada de COMANDOS: whitelist (nodes/*.yaml) + execução (CommandResult)
  nodes.py                     # carrega/valida perfis de NOS (templates de comando)
  parsers/__init__.py          # registro de parsers (PARSERS / PARSER_NAMES)
  parsers/base.py              # ParserError
  parsers/ping.py              # parser do output do ping (resumo estruturado)
  parsers/bgp_prefix.py        # parser do detalhe de prefixo BGP (paths/atributos)
  site.py                      # carrega openglass.yaml (site/UI para o frontend)
  security.py                  # camada de SEGURANÇA: sanitização anti-injeção
  cli.py                       # CLI interativo/one-shot
  api.py                       # API + serviço do front estático (FastAPI)
  static/index.html            # frontend
  static/images/               # logo/favicon referenciados pelo openglass.yaml
tests/                         # security, commands, inventory, connection, nodes, api, site, parsers
```

A arquitetura mantém **conexão** × **comandos** × **parsing** separadas. O parsing
é plugado por comando via o campo `parser:` em `nodes/<nos>.yaml` (ex.: `ping:
parser: ping`), que referencia o registro em `openglass/parsers`. Sem parser — ou
se o parser falhar — o resultado sai apenas com o output bruto.

## Setup

```bash
uv sync                      # instala deps (netmiko, pyyaml, pydantic-settings, fastapi, uvicorn)
cp devices.yaml.example devices.yaml   # preencha com seus roteadores
cp .env.example .env                   # ajuste timeouts se quiser
```

Credenciais: dentro de `credential: {username, password}` ou com `key_path`
(chave SSH). Podem vir do ambiente (`password: ${ROUTER_PASSWORD}`) para não
ficarem em texto no arquivo. O YAML segue o formato do hyperglass
(`routers:`, `address`, `nos`, e os campos `network`/`vrfs` já são armazenados
no modelo para a fase futura de filtro).

## Uso

```bash
# listar inventário e comandos permitidos
uv run python main.py --list-devices
uv run python main.py --list-commands

# interativo: escolhe device, conecta, roda comandos (help / exit)
uv run python main.py

# one-shot
uv run python main.py --device edge-router --command show-ip-route
uv run python main.py --device edge-router --command ping --param ip=8.8.8.8
uv run python main.py --device edge-router --command show-bgp-prefix --param prefix=8.8.8.0/24
uv run python main.py --device edge-router --command traceroute --param destination=1.1.1.1
```

Exit codes: `0` ok | `2` erro (inventário/conexão/segurança) | `130` abortado (Ctrl+C).

## Frontend web

```bash
uv run openglass-web            # sobe em listen_address:listen_port do openglass.yaml
```

No navegador: escolher o roteador, o comando e digitar o IP/destino. Quando o
comando tem parser, o resultado aparece em um **cartão didático**:

- `ping`: badge Sucesso/Parcial/Falha, chips por resposta, enviados/recebidos/perda
  e barras de latência;
- `show-bgp-prefix`: destaque do melhor caminho, AS path, next hop, origem,
  atributos (localpref/metric/weight), agregações e flags.

A **saída bruta** fica recolhida em “Ver saída bruta”, com botão **Copiar**.
Comandos sem parser exibem apenas a saída bruta. As credenciais nunca vão para o
navegador.

A identidade visual é lida do `openglass.yaml` (via `GET /api/site`):

- `site_title`, `site_description`, `org_name`, `primary_asn`
- `web.logo` (`dark`/`light`/`favicon`) — coloque os arquivos em
  `src/openglass/static/images/` e referencie como `static/images/<arquivo>`
- `web.links` e `web.menus` — exibidos no rodapé
- `web.theme.colors` — `primary`, `secondary`, `text`, `background`

## API

| método | rota                            | descrição                                  |
| ------ | ------------------------------- | ------------------------------------------ |
| GET    | `/`                             | frontend                                   |
| GET    | `/api/site`                     | configuração de site/UI do `openglass.yaml`|
| GET    | `/api/devices`                  | devices do inventário (sem credenciais)    |
| GET    | `/api/devices/{nome}/commands`  | comandos permitidos do NOS do device       |
| POST   | `/api/run`                      | executa um comando (`{device, command, params}`) |

Resposta do `POST /api/run`:

| campo         | descrição                                              |
| ------------- | ------------------------------------------------------ |
| `command`     | comando final montado (ex.: `ping 8.8.8.8 source ...`) |
| `description` | descrição do comando                                   |
| `output`      | saída bruta do roteador                                |
| `parser`      | parser aplicado (`null` se não houver)                 |
| `parsed`      | dados estruturados (`null` sem parser ou se falhar)    |

Exemplo de `parsed` para o `ping`:

```json
{
  "type": "ping",
  "status": "success",
  "target": "1.1.1.1",
  "source": "45.5.40.255",
  "sent": 5, "received": 5,
  "loss_percent": 0, "success_percent": 100,
  "size_bytes": 100, "timeout_s": 2,
  "rtt_ms": { "min": 50, "avg": 60, "max": 99 },
  "probes": [
    { "symbol": "!", "result": "reply", "label": "Resposta" }
  ]
}
```

## Segurança

- **Whitelist:** nenhum comando arbitrário é aceito — só os definidos no perfil
  do NOS em `nodes/<nos>.yaml` (não dá para mandar `show running-config` nem
  `enable`).
- **Sanitização:** parâmetros (`destination`, `prefix`) são validados
  (IP/prefixo IPv4/IPv6 rigoroso, hostname simples) e caracteres de injeção
  (`; & | > < \` $ () ...`) são rejeitados antes de montar o comando.
- **Timeouts:** conexão e leitura de resposta têm timeout configurável — o
  roteador não trava a aplicação.
- **Fechamento garantido:** a sessão é fechada via context manager, inclusive
  em caso de exceção.

## Comandos disponíveis

| chave              | comando                              | parser |
| ------------------ | ------------------------------------ | ------ |
| `show-ip-route`    | `show ip route`                      | —            |
| `show-bgp-summary` | `show ip bgp summary`                | —            |
| `show-bgp-prefix`  | `show ip bgp <prefixo>`              | `bgp_prefix` |
| `ping`             | `ping <ip> source <source_address>`  | `ping`       |
| `traceroute`       | `traceroute <destino>`               | —            |

`<source_address>` é preenchido automaticamente com o `source_address` do VRF do
device no `devices.yaml` (IPv4 ou IPv6 conforme o destino) — nunca vem do usuário.
A coluna `parser` referencia um parser registrado em `openglass/parsers` (hoje:
`ping` e `bgp_prefix`). Os templates/whitelist ficam em `nodes/<nos>.yaml`.

> Obs.: o detalhe de prefixo cobre IPv4 (`show ip bgp <prefixo>`). IPv6 exige
> `show bgp ipv6 unicast <prefixo>` (a ser adicionado como comando próprio).

## Testes

```bash
uv run pytest
```

**110 testes.** Não conecta em dispositivo real: usa Netmiko mockado (camada de
conexão) e saídas reais de exemplo para os parsers — `tests/test_parsers.py`
(`ping`) e `tests/test_bgp_prefix.py` (2 paths, agregado, origem `Local`,
update-groups e `% Network not in table`). O teste real em roteador se faz pelo
CLI do item "Uso".