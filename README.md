<div align="center">

# 🔭 OpenGlass

### Um looking glass de verdade — direto do terminal para um cartão que qualquer NOC entende

*Escolha o roteador, escolha o comando, receba um diagnóstico legível — com a saída bruta sempre a um clique de distância.*

**Versão 1.1.0** · Cisco IOS/IOS-XE hoje, mais vendors amanhã

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

Por trás dos cartões:

- **Execução segura** — whitelist de comandos por NOS, sanitização anti-injeção e timeouts configuráveis
- **Duas faces, um motor** — interface web (FastAPI + frontend estático, com logo/links/tema via `openglass.yaml`) e CLI interativa/one-shot

> **Cobertura atual:** apenas **Cisco IOS/IOS-XE**. Suporte a outros vendors
> (Juniper, MikroTik etc.) está em desenvolvimento.

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

| Comando | Exemplo | Parser |
|---|---|---|
| `ping` | `ping 8.8.8.8` | `ping` |
| `traceroute` | `traceroute 1.1.1.1` | `traceroute` |
| `bgp-route` | `show ip bgp 8.8.8.0/24` | `bgp_prefix` |

A origem de ping/traceroute é preenchida automaticamente com o
`source_address` do VRF do roteador. Comandos e parsers vivem em
`nodes/cisco_ios.yaml` — cada novo NOS entra como um perfil próprio dentro de
`nodes/`.

---

## 🔒 Segurança em primeiro lugar

- Somente comandos da whitelist do NOS são aceitos — nada de `show running-config` escapando pela brecha
- Parâmetros validados (IP/prefixo/hostname); caracteres de injeção são rejeitados na porta
- Timeouts de conexão e leitura protegem contra roteador que simplesmente não responde

---

## ✅ Testes

```bash
uv run pytest
```

**167 testes**, com Netmiko mockado e saídas reais de exemplo — sem depender
de dispositivo físico para validar a suíte.

---

## 📁 Estrutura do projeto

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
tests/                  suíte de testes (167)
install.sh              instalador interativo (root: sudo bash install.sh)
CHANGELOG.md            histórico de versões
```

---

<div align="center">

*Feito para quem vive de plantão e não tem tempo a perder decifrando CLI.*

</div>