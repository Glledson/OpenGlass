# OpenGlass

Ferramenta web de diagnóstico de rede em roteadores de borda (Cisco IOS/IOS-XE na
fase atual), permitindo rodar comandos como `ping`, `traceroute`, `show ip route`
e `show ip bgp` com output bruto para validação.

**Fase atual:** somente backend de conexão + execução via CLI. Sem API/frontend ainda.

## Estrutura (camadas)

```
main.py                        # entrada do CLI: python main.py
devices.yaml(.example)         # inventário (CREDENCIAIS — não versionado)
.env(example)                  # configuração (timeouts, caminhos)
nodes/cisco_ios.yaml           # whitelist + formatos de comando por NOS
src/openglass/
  inventory.py                 # carrega devices do YAML + interpolação de ${VAR}
  connection.py                # camada de CONEXÃO: sessão SSH (Netmiko), erros tipados
  commands.py                  # camada de COMANDOS: whitelist (nodes/*.yaml) + execução
  nodes.py                     # carrega/valida perfis de NOS (templates de comando)
  security.py                  # camada de SEGURANÇA: sanitização anti-injeção
  cli.py                       # CLI interativo/one-shot
tests/                         # security, commands, inventory, connection, nodes
```

A arquitetura mantém **conexão** × **comandos** × **parsing** separadas: a camada de
parsing entra depois plugando em `nodes.NodeCommand.parser`, sem reescrever as outras.

## Setup

```bash
uv sync                      # instala deps (netmiko, pyyaml, pydantic-settings)
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
uv run python main.py --device edge-r1 --command show-ip-route
uv run python main.py --device edge-r1 --command ping --param destination=8.8.8.8
uv run python main.py --device r1 --command show-bgp-prefix --param prefix=8.8.8.0/24
uv run python main.py --device r1 --command traceroute --param destination=1.1.1.1
```

Exit codes: `0` ok | `2` erro (inventário/conexão/segurança) | `130` abortado (Ctrl+C).

## Segurança

- **Whitelist:** nenhum comando arbitrário é aceito — só os registrados em
  `commands.COMMANDS` (não dá para mandar `show running-config` nem
  `enable`).
- **Sanitização:** parâmetros (`destination`, `prefix`) são validados
  (IP/prefixo IPv4/IPv6 rigoroso, hostname simples) e caracteres de injeção
  (`; & | > < \` $ () ...`) são rejeitados antes de montar o comando.
- **Timeouts:** conexão e leitura de resposta têm timeout configurável — o
  roteador não trava a aplicação.
- **Fechamento garantido:** a sessão é fechada via context manager, inclusive
  em caso de exceção.

## Comandos disponíveis

| chave              | comando                    |
| ------------------ | -------------------------- |
| `show-ip-route`    | `show ip route`            |
| `show-bgp-summary` | `show ip bgp summary`      |
| `show-bgp-prefix`  | `show ip bgp <prefixo>`    |
| `ping`             | `ping <destino>`           |
| `traceroute`       | `traceroute <destino>`     |

## Testes

```bash
uv run pytest
```

Não conecta em dispositivo real: usa Netmiko mockado (camada de conexão) — o
teste real em roteador se faz pelo CLI do item "Uso".