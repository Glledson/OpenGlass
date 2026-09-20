# OpenGlass

Ferramenta web de diagnóstico de rede para roteadores de borda: escolha o
roteador e o comando, e receba o resultado da análise em um cartão didático —
com a saída bruta sempre disponível como referência.

**Versão:** 1.0.0

## Funcionalidades

- **Ping** — status Sucesso/Parcial/Falha, enviados/recebidos, perda, latência
  (min/avg/max) e resposta sonda a sonda.
- **Traceroute** — salto a salto com IP, AS e tempo de cada sonda, indicando se
  o tráfego chegou ao destino.
- **BGP (detalhe de prefixo)** — caminhos com AS path, next hop, origem,
  localpref/metric/weight e flags, com destaque do melhor caminho.
- **Execução segura** — whitelist de comandos por NOS, sanitização anti-injeção
  e timeouts configuráveis.
- **Interface web** (FastAPI + frontend estático, com logo/links/tema via
  `openglass.yaml`) e **CLI** interativo/one-shot.

> **Suporte:** por enquanto apenas **Cisco IOS/IOS-XE**. Suporte a outras
> vendors (Juniper, Mikrotik, etc.) está em desenvolvimento.

## Instalação

### Instalador (produção)

```bash
git clone https://github.com/Glledson/OpenGlass.git
cd OpenGlass
sudo bash install.sh
```

O instalador, de forma interativa (TUI com whiptail; use `--text` para
prompts em modo texto, útil em automação):

1. verifica root, detecta o SO (Debian 11+/Ubuntu 20.04+) e atualiza o sistema;
2. instala dependências (git, curl, python3, uv, whiptail), clona o projeto em
   `/opt/openglass` e cria a pasta `/etc/openglass/`;
3. se já houver instalação, pergunta se deseja **reconfigurar do zero**,
   **editar** (mantém ativos atuais) ou cancelar;
4. configura o **site** (ASN, nome do provedor, site e contato do NOC) com
   validação em tempo real e tela de confirmação (**Confirmar/Corrigir/
   Cancelar** — "Corrigir" volta com o formulário pré-preenchido);
5. configura os **ativos** (roteadores: nome, vendor vindo de `nodes/`, IPv4,
   source IPv4, comunidade SNMP, usuário/senha SSH e porta) com confirmação e
   repetição;
6. gera `/etc/openglass/openglass.yaml` e `/etc/openglass/devices.yaml` (com
   backup `.bak.<timestamp>` antes de sobrescrever), cria o `.env` do serviço
   e roda um teste do CLI.

Como serviço web (systemd, no boot):

```bash
sudo bash install.sh --service
```

Remover o serviço e os symlinks (mantém as configurações):

```bash
sudo bash install.sh --uninstall
```

Opções extras: `--no-apt` (pula atualização/instalação de pacotes), `--no-uv`,
`--no-symlinks`, `--text`, `-d DIR` (diretório do repositório), `-c DIR`
(config).

### Instalação manual (desenvolvimento)

```bash
uv sync
cp .env.example .env                        # timeouts, se quiser ajustar
cp openglass.yaml.example openglass.yaml    # ajuste org_name, primary_asn…
cp devices.yaml.example devices.yaml        # preencha com seus roteadores
```

## Uso

```bash
uv run python main.py --list-devices        # inventário
uv run python main.py --list-commands       # comandos permitidos
uv run python main.py                       # modo interativo
uv run python main.py --device edge-r1 --command ping --param ip=8.8.8.8
```

Web:

```bash
uv run openglass-web     # depois abra o endereço em http://localhost (ou o configurado)
```

Exit codes: `0` ok | `2` erro | `130` abortado.

## Comandos e parsers

| comando      | exemplo                  | parser       |
| ------------ | ------------------------ | ------------ |
| `ping`       | `ping 8.8.8.8`           | `ping`       |
| `traceroute` | `traceroute 1.1.1.1`     | `traceroute` |
| `bgp-route`  | `show ip bgp 8.8.8.0/24` | `bgp_prefix` |

A origem do ping/traceroute é preenchida automaticamente com o `source_address`
do VRF do roteador. Os comandos e parsers ficam em `nodes/cisco_ios.yaml`;
novos NOS entram com um perfil por arquivo em `nodes/`.

## Segurança

- Apenas comandos da whitelist do NOS são aceitos (nada de `show running-config`).
- Parâmetros validados (IP/prefixo/hostname) e caracteres de injeção rejeitados.
- Timeouts de conexão/leitura protegem contra roteador que não responde.

## Testes

```bash
uv run pytest
```

**165 testes** (com Netmiko mockado e saídas reais de exemplo; sem dispositivo real).

## Estrutura

```
nodes/<nos>.yaml        whitelist + templates por NOS
src/openglass/
  inventory.py          inventário (devices.yaml)
  connection.py         sessão SSH (Netmiko) e erros tipados
  commands.py           whitelist, validação, execução (CommandResult)
  parsers/              parsers de output (ping, traceroute, bgp_prefix)
  security.py           sanitização anti-injeção
  cli.py                CLI interativo/one-shot
  api.py                API + frontend estático (FastAPI)
  static/index.html     frontend
tests/                  suíte de testes (165)
install.sh              instalador interativo (root: sudo bash install.sh)
```