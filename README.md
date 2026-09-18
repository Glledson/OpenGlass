# OpenGlass

Ferramenta web de diagnóstico de rede em roteadores de borda (Cisco IOS/IOS-XE na
fase atual), permitindo rodar comandos como `ping`, `traceroute`, `show ip route`
e `show ip bgp` com output bruto para validação.

**Fase atual:** backend de conexão + execução via CLI + frontend web (FastAPI),
com a identidade visual vinda do `openglass.yaml` (título, logo, links, menus e
tema).

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
  commands.py                  # camada de COMANDOS: whitelist (nodes/*.yaml) + execução
  nodes.py                     # carrega/valida perfis de NOS (templates de comando)
  site.py                      # carrega openglass.yaml (site/UI para o frontend)
  security.py                  # camada de SEGURANÇA: sanitização anti-injeção
  cli.py                       # CLI interativo/one-shot
  api.py                       # API + serviço do front estático (FastAPI)
  static/index.html            # frontend
  static/images/               # logo/favicon referenciados pelo openglass.yaml
tests/                         # security, commands, inventory, connection, nodes, api, site
```

A arquitetura mantém **conexão** × **comandos** × **parsing** separadas: a camada de
parsing entra depois plugando em `nodes.NodeCommand.parser`, sem reescrever as outras.

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
no modelo para a fase futura de parsing/filtro).

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

No navegador: escolher o roteador, o comando e digitar o IP/destino. O resultado
sai em um painel estilo terminal, com botão **Copiar**. As credenciais nunca vão
para o navegador.

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

| chave              | comando                              |
| ------------------ | ------------------------------------ |
| `show-ip-route`    | `show ip route`                      |
| `show-bgp-summary` | `show ip bgp summary`                |
| `show-bgp-prefix`  | `show ip bgp <prefixo>`              |
| `ping`             | `ping <ip> source <source_address>`  |
| `traceroute`       | `traceroute <destino>`               |

`<source_address>` é preenchido automaticamente com o `source_address` do VRF do
device no `devices.yaml` (IPv4 ou IPv6 conforme o destino) — nunca vem do usuário.
Os templates/whitelist ficam em `nodes/<nos>.yaml`.

## Testes

```bash
uv run pytest
```

Não conecta em dispositivo real: usa Netmiko mockado (camada de conexão) — o
teste real em roteador se faz pelo CLI do item "Uso".