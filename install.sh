#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# OpenGlass — instalador interativo (TUI com whiptail)
# ---------------------------------------------------------------------------
# Fluxo:
#   1. Root check, detecção do OS (Debian 11+/Ubuntu 20.04+) e atualização
#   2. Pacotes necessários (instala apenas os ausentes)
#   3. Pasta /etc/openglass + idempotência (reconfigurar / editar / cancelar)
#   4. Clone do repositório e uv sync
#   5. Assistente do site (ASN, provedor, site, NOC) com confirmação
#      Confirmar / Corrigir (pré-preenchido) / Cancelar
#   6. Assistente de ativos: nome, vendor (menu nodes/*.yaml), IPv4, source,
#      SNMP, usuário/senha SSH, porta — com confirmação e repetição
#   7. Gera /etc/openglass/openglass.yaml e devices.yaml (com backup)
#   8. .env, symlinks, serviço systemd (--service), resumo final
#
# Uso:
#   sudo bash install.sh                 # instalação completa
#   sudo bash install.sh --service       # + unit systemd (serviço web)
#   sudo bash install.sh --uninstall     # remove serviço e symlinks
#
# Opções:
#   --no-apt           pula atualização/instalação de pacotes
#   --no-uv            não instala o uv (reusa o existente)
#   --no-symlinks      não cria symlinks em /usr/local/bin
#   --text             força prompts em modo texto (sem TUI; útil p/ automação)
#   -d DIR             diretório do repositório (padrão /opt/openglass)
#   -c DIR             diretório de configuração (padrão /etc/openglass)
# ---------------------------------------------------------------------------

set -euo pipefail

C_RED='\033[0;31m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'
C_CYAN='\033[0;36m'; C_BOLD='\033[1m'; C_RESET='\033[0m'

REPO_URL="https://github.com/Glledson/OpenGlass.git"
REPO_DIR="/opt/openglass"
CONFIG_DIR="/etc/openglass"
APT_LOG="/var/log/openglass-install.log"
BANNER="OpenGlass — instalador"
DO_SERVICE=0
DO_UNINSTALL=0
DO_APT=1
DO_UV=1
DO_SYMLINKS=1
FORCE_TEXT=0
PROMPT_EOF=0

info()  { printf "${C_CYAN}%s${C_RESET}\n" "• $*"; }
ok()    { printf "${C_GREEN}%s${C_RESET}\n" "✓ $*"; }
warn()  { printf "${C_YELLOW}%s${C_RESET}\n" "⚠ $*" >&2; }
fail()  { printf "${C_RED}%s${C_RESET}\n" "✗ $*" >&2; exit 1; }

_cleanup() {
    printf "${C_RED}instalação interrompida${C_RESET}\n" >&2
    exit 130
}
trap _cleanup INT TERM

usage() {
    cat <<'EOF'
OpenGlass — instalador interativo

Uso:
  sudo bash install.sh [opções]

Opções:
  --service        cria e habilita a unit do systemd (serviço web)
  --uninstall      remove a unit do systemd e os symlinks
  --no-apt         pula atualização/instalação de pacotes
  --no-uv          não instala o uv (reusa o existente)
  --no-symlinks    não cria symlinks em /usr/local/bin
  --text           força prompts em modo texto (sem whiptail)
  -d DIR           diretório do repositório (padrão /opt/openglass)
  -c DIR           diretório de configuração (padrão /etc/openglass)
  -h, --help       mostra esta ajuda
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --service)        DO_SERVICE=1; shift ;;
        --uninstall)      DO_UNINSTALL=1; shift ;;
        --no-apt)         DO_APT=0; shift ;;
        --no-uv)          DO_UV=0; shift ;;
        --no-symlinks)    DO_SYMLINKS=0; shift ;;
        --text)           FORCE_TEXT=1; shift ;;
        -d)               REPO_DIR="$2"; shift 2 ;;
        --dir)            REPO_DIR="$2"; shift 2 ;;
        -c)               CONFIG_DIR="$2"; shift 2 ;;
        --config-dir)     CONFIG_DIR="$2"; shift 2 ;;
        -h|--help)        usage; exit 0 ;;
        *)                fail "opção desconhecida: $1 (use --help)" ;;
    esac
done

CONFIG_FILE="$CONFIG_DIR/openglass.yaml"
DEVICES_FILE="$CONFIG_DIR/devices.yaml"
HOME_BIN="${HOME}/.local/bin"
UV_BIN="$HOME_BIN/uv"
PYTHON_BIN=""

# Modo TUI (whiptail) sempre que possível: terminal interativo e whiptail.
USE_TUI=0
if [ "$FORCE_TEXT" -eq 0 ] && [ -t 0 ] && command -v whiptail >/dev/null 2>&1; then
    USE_TUI=1
fi

# ---------------------------------------------------------------------------
# Helpers de prompt (modo texto) — rótulos no stderr, valor via REPLY
# ---------------------------------------------------------------------------
prompt() { # $1=label  $2=default — seta REPLY
    local value default="${2:-}"
    while :; do
        if [ -n "$default" ]; then
            printf "${C_BOLD}%s${C_RESET} [%s]: " "$1" "$default" >&2
        else
            printf "${C_BOLD}%s${C_RESET}: " "$1" >&2
        fi
        if ! IFS= read -r value; then PROMPT_EOF=1; return 1; fi
        value="${value//$'\r'/}"
        if [ -n "$value" ]; then REPLY="$value"; return 0; fi
        if [ -n "$default" ]; then REPLY="$default"; return 0; fi
        warn "campo obrigatório"
    done
}

prompt_secret() { # $1=label — seta REPLY (não ecoa)
    printf "${C_BOLD}%s${C_RESET}: " "$1" >&2
    if ! IFS= read -r -s value; then PROMPT_EOF=1; printf '\n' >&2; return 1; fi
    printf '\n' >&2
    REPLY="${value//$'\r'/}"
    return 0
}

prompt_optional() { # $1=label  $2=default — permite vazio
    local default="${2:-}"
    if [ -n "$default" ]; then
        printf "${C_BOLD}%s${C_RESET} [%s]: " "$1" "$default" >&2
    else
        printf "${C_BOLD}%s${C_RESET}: " "$1" >&2
    fi
    if ! IFS= read -r value; then PROMPT_EOF=1; return 1; fi
    REPLY="${value//$'\r'/}"
    return 0
}

confirm_text() { # $1=pergunta — 0 sim / 1 não
    local answer
    while :; do
        printf "${C_BOLD}%s${C_RESET} [s/N]: " "$1" >&2
        if ! IFS= read -r answer; then PROMPT_EOF=1; return 1; fi
        answer="${answer//$'\r'/}"
        case "$answer" in
            s|S|sim|SIM|y|Y|yes|YES) return 0 ;;
            ""|n|N|nao|não|no|NO)     return 1 ;;
        esac
    done
}

text_menu() { # $1=prompt, pares label/desc — seta REPLY com o label escolhido
    local prompt_text="$1" i=1 item desc
    shift
    local -a pairs=("$@")
    local -a items=()
    printf '\n' >&2
    printf "${C_BOLD}%b${C_RESET}\n" "$prompt_text" >&2
    while [ $i -le "${#pairs[@]}" ]; do
        item="${pairs[$((i - 1))]}"
        desc="${pairs[$i]}"
        printf '  %d) %s — %s\n' "$(((i + 1) / 2))" "$item" "$desc" >&2
        items+=("$item")
        i=$((i + 2))
    done
    while :; do
        printf "${C_BOLD}Escolha (1-%d)${C_RESET}: " "${#items[@]}" >&2
        IFS= read -r answer || { PROMPT_EOF=1; return 1; }
        answer="${answer//$'\r'/}"
        case "$answer" in
            ''|*[!0-9]*) warn "escolha um número" ; continue ;;
        esac
        if [ "$answer" -ge 1 ] && [ "$answer" -le "${#items[@]}" ]; then
            REPLY="${items[$((answer - 1))]}"
            return 0
        fi
        warn "número fora da lista"
    done
}

# ---------------------------------------------------------------------------
# Helpers unificados (TUI ou texto) — entrada via REPLY, 0 ok / 1 cancelado
# ---------------------------------------------------------------------------
get_input() { # $1=título  $2=prompt  $3=default
    local value
    if [ "$USE_TUI" -eq 1 ]; then
        value=$(whiptail --backtitle "$BANNER" --title "$1" \
            --inputbox "$2" 0 0 "$3" 3>&1 1>&2 2>&3) || return 1
    else
        prompt "$2" "$3" || return 1
        value="$REPLY"
    fi
    REPLY="${value//$'\r'/}"
    return 0
}

get_secret() { # $1=título  $2=prompt
    local value
    if [ "$USE_TUI" -eq 1 ]; then
        value=$(whiptail --backtitle "$BANNER" --title "$1" \
            --passwordbox "$2" 0 0 "" 3>&1 1>&2 2>&3) || return 1
    else
        prompt_secret "$2" || return 1
        value="$REPLY"
    fi
    REPLY="$value"
    return 0
}

get_input_optional() { # $1=título  $2=prompt  $3=default — permite resposta vazia
    local value
    if [ "$USE_TUI" -eq 1 ]; then
        value=$(whiptail --backtitle "$BANNER" --title "$1" \
            --inputbox "$2" 0 0 "$3" 3>&1 1>&2 2>&3) || return 1
    else
        prompt_optional "$2" "$3" || return 1
        value="$REPLY"
    fi
    REPLY="${value//$'\r'/}"
    return 0
}

menu_select() { # $1=título  $2=prompt  pares label/desc  — seta REPLY
    local value
    if [ "$USE_TUI" -eq 1 ]; then
        value=$(whiptail --backtitle "$BANNER" --title "$1" \
            --menu "$2" 0 0 0 "${@:3}" 3>&1 1>&2 2>&3) || return 1
    else
        text_menu "$2" "${@:3}" || return 1
        value="$REPLY"
    fi
    REPLY="$value"
    return 0
}

ask_yesno() { # $1=título  $2=pergunta — 0 sim / 1 não
    if [ "$USE_TUI" -eq 1 ]; then
        whiptail --backtitle "$BANNER" --title "$1" --yesno "$2" 0 0
    else
        confirm_text "$2"
    fi
}

show_msg() { # $1=título  $2=texto
    if [ "$USE_TUI" -eq 1 ]; then
        whiptail --backtitle "$BANNER" --title "$1" --msgbox "$2" 0 0
    else
        printf '\n-- %s --\n%b\n' "$1" "$2"
    fi
}

# ---------------------------------------------------------------------------
# Validações em tempo real (loop até ser válido)
# ---------------------------------------------------------------------------
valid_ipv4() {
    [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
    local octet
    for octet in "${BASH_REMATCH[@]:1}"; do
        [ "$octet" -le 255 ] || return 1
    done
    return 0
}

valid_asn() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ] && [ "$1" -le 4294967295 ]
}

valid_port() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

valid_url() {
    [[ "$1" =~ ^https?://[^[:space:]]+$ ]]
}

valid_contact() { # e-mail ou telefone
    if [[ "$1" == *@* ]]; then
        [[ "$1" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]{2,}$ ]]
    else
        printf '%s' "$1" | grep -Eq '^\+?[0-9][0-9 ()-]{5,}$'
    fi
}

# ---------------------------------------------------------------------------
# 1. Root check
# ---------------------------------------------------------------------------
step_root() {
    info "Verificando privilégios de root…"
    [ "$EUID" -eq 0 ] || fail "execute como root: sudo bash install.sh"
    ok "você é root"
}

# ---------------------------------------------------------------------------
# 2. Detecção do OS (Debian 11+/Ubuntu 20.04+)
# ---------------------------------------------------------------------------
step_os() {
    info "Detectando sistema operacional…"
    OS_ID="" ; OS_VERSION=""
    [ -r /etc/os-release ] && { . /etc/os-release; OS_ID="$ID"; OS_VERSION="${VERSION_ID:-0}"; }

    OS_OK=0
    case "$OS_ID" in
        debian)  [ "${OS_VERSION%%.*}" -ge 11 ] && OS_OK=1 ;;
        ubuntu)  [ "${OS_VERSION%%.*}" -ge 20 ] && OS_OK=1 ;;
    esac

    if [ "$OS_OK" -eq 1 ]; then
        ok "${NAME:-Linux} ${OS_VERSION}"
    else
        warn "SO não suportado: ${NAME:-desconhecido} ${OS_VERSION:-}"
        if ask_yesno "SO não suportado" \
            "Este instalador foi testado em Debian 11+ / Ubuntu 20.04+.\n\nDeseja continuar mesmo assim? (risco por sua conta)"; then
            warn "continuando com SO não suportado"
        else
            fail "instalação cancelada"
        fi
    fi
}

# ---------------------------------------------------------------------------
# 3. Atualizar (apt update/upgrade com log)
# ---------------------------------------------------------------------------
step_update() {
    [ "$DO_APT" -eq 1 ] || { warn "pulando atualização do sistema (--no-apt)"; return 0; }
    touch "$APT_LOG"
    info "Atualizando o sistema (apt update)…"
    DEBIAN_FRONTEND=noninteractive apt-get update -y >>"$APT_LOG" 2>&1 \
        || fail "apt update falhou (veja $APT_LOG)"
    info "Atualizando o sistema (apt upgrade)…"
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y >>"$APT_LOG" 2>&1 \
        || fail "apt upgrade falhou (veja $APT_LOG)"
    ok "sistema atualizado (log: $APT_LOG)"
}

# ---------------------------------------------------------------------------
# 4 e 5. Verificar e instalar pacotes (só os ausentes)
# ---------------------------------------------------------------------------
step_packages() {
    local missing=()
    for pkg in git curl python3 python3-venv whiptail; do
        if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
            ok "$pkg instalado"
        else
            warn "$pkg ausente"
            missing+=("$pkg")
        fi
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        if [ "$DO_APT" -eq 1 ]; then
            info "Instalando: ${missing[*]}"
            DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}" >>"$APT_LOG" 2>&1 \
                || fail "falha ao instalar pacotes (veja $APT_LOG)"
            ok "pacotes instalados"
        else
            warn "pacotes faltando (pulados com --no-apt): ${missing[*]}"
        fi
    fi
}

step_uv() {
    [ "$DO_UV" -eq 1 ] || { warn "pulando instalação do uv (--no-uv)"; return 0; }
    if [ -x "$UV_BIN" ]; then
        ok "uv já instalado ($UV_BIN)"
    else
        command -v curl >/dev/null 2>&1 || fail "curl é necessário para instalar o uv"
        info "Instalando uv em $HOME_BIN…"
        install -d "$HOME_BIN"
        curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
        ok "uv instalado"
    fi
}

# ---------------------------------------------------------------------------
# 6. /etc/openglass + idempotência
# ---------------------------------------------------------------------------
step_config_dir() {
    if [ -d "$CONFIG_DIR" ]; then
        info "$CONFIG_DIR já existe — verificando idempotência…"
        if menu_select "Instalação existente" \
            "Foram encontrados arquivos em $CONFIG_DIR. O que deseja fazer?" \
            "Reconfigurar" "Gerar tudo do zero (backup do existente)" \
            "Editar" "Manter ativos atuais e complementar a configuração" \
            "Cancelar" "Abortar a instalação"; then
            case "$REPLY" in
                Reconfigurar) INSTALL_MODE="fresh" ;;
                Editar)       INSTALL_MODE="edit" ;;
                Cancelar)     fail "instalação cancelada pelo usuário" ;;
            esac
        else
            fail "instalação cancelada pelo usuário"
        fi
    else
        INSTALL_MODE="fresh"
    fi

    install -d -m 0750 "$CONFIG_DIR"
    ok "pasta $CONFIG_DIR pronta (modo: $INSTALL_MODE)"
}

backup_file() { # $1=arquivo — copia para .bak.<timestamp>
    [ -f "$1" ] || return 0
    local backup="$1.bak.$(date +%Y%m%d%H%M%S)"
    cp -p "$1" "$backup"
    ok "backup criado: $backup"
}

# ---------------------------------------------------------------------------
# 7. Clone + dependências
# ---------------------------------------------------------------------------
step_clone() {
    info "Preparando repositório em $REPO_DIR …"
    if [ -d "$REPO_DIR/.git" ]; then
        ok "repositório já existe; atualizando…"
        git -C "$REPO_DIR" pull --ff-only
    elif [ -e "$REPO_DIR" ]; then
        fail "$REPO_DIR existe mas não é um clone do OpenGlass"
    else
        git clone "$REPO_URL" "$REPO_DIR"
        ok "repositório clonado"
    fi
    info "Instalando dependências (uv sync)…"
    [ -x "$UV_BIN" ] || UV_BIN="$(command -v uv 2>/dev/null || true)"
    if [ -z "$UV_BIN" ]; then
        fail "uv não encontrado — remova --no-uv ou instale o uv"
    fi
    (cd "$REPO_DIR" && "$UV_BIN" sync)
    PYTHON_BIN="$REPO_DIR/.venv/bin/python"
    [ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"
    ok "dependências instaladas"
}

# ---------------------------------------------------------------------------
# 8. Assistente do site (openglass.yaml)
# ---------------------------------------------------------------------------
prefill_site_from_config() { # carrega valores atuais no modo Editar
    [ "$INSTALL_MODE" = "edit" ] && [ -f "$CONFIG_FILE" ] || return 0
    local asn org title noc site_vals
    mapfile -t site_vals < <("$PYTHON_BIN" - "$CONFIG_FILE" <<'PYE'
import sys, re
import yaml

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
except Exception:
    raise SystemExit(0)
if not isinstance(data, dict):
    raise SystemExit(0)
print(data.get("primary_asn") or "")
print(data.get("org_name") or "")
print(data.get("site_title") or "")
noc = ""
for menu in (data.get("web") or {}).get("menus") or []:
    if str(menu.get("title", "")).lower() in ("contato", "contact"):
        match = re.search(r"\]\(mailto:([^)]+)\)", str(menu.get("content") or ""))
        noc = match.group(1) if match else ""
        break
print(noc)
PYE
)
    asn="${site_vals[0]}"; org="${site_vals[1]}"; title="${site_vals[2]}"; noc="${site_vals[3]}"
    [ -n "$asn$org$title$noc" ] && {
        info "Preenchendo com os valores atuais de $CONFIG_FILE"
        ASN="${asn//[^0-9]/}"
        ORG_NAME="$org"; SITE_TITLE="$title"; NOC_EMAIL="$noc"
    }
    return 0
}

wizard_site() {
    local summary
    printf '\n'
    info "Assistente do site (openglass.yaml)"

    # Defaults vêm de uma instalação prévia (modo Editar)
    ASN="${ASN:-}"; ORG_NAME="${ORG_NAME:-}"; SITE_TITLE="${SITE_TITLE:-}"; NOC_EMAIL="${NOC_EMAIL:-}"

    while :; do
        # ASN
        while :; do
            if ! get_input "ASN" "Número ASN do AS (ex.: 64500)" "$ASN"; then fail "cancelado"; fi
            valid_asn "$REPLY" && { ASN="$REPLY"; break; }
            show_msg "ASN inválido" "O ASN deve ser um número entre 1 e 4294967295."
        done
        # Nome do provedor
        while :; do
            if ! get_input "Provedor" "Nome do provedor (ex.: LG Telecom)" "$ORG_NAME"; then fail "cancelado"; fi
            [ -n "$REPLY" ] && { ORG_NAME="$REPLY"; break; }
            show_msg "Campo obrigatório" "Informe o nome do provedor."
        done
        # Site do provedor
        while :; do
            if ! get_input "Site" "URL do site do provedor (ex.: https://www.lg.com.br)" "$SITE_TITLE"; then fail "cancelado"; fi
            valid_url "$REPLY" && { SITE_TITLE="$REPLY"; break; }
            show_msg "URL inválida" "A URL deve começar com http:// ou https://."
        done
        # Contato do NOC
        while :; do
            if ! get_input "NOC" "Contato do NOC (e-mail ou telefone)" "$NOC_EMAIL"; then fail "cancelado"; fi
            valid_contact "$REPLY" && { NOC_EMAIL="$REPLY"; break; }
            show_msg "Contato inválido" "Informe um e-mail (noc@provedor.net) ou telefone (+55 11 1234-5678)."
        done

        summary="ASN            : $ASN\nProvedor        : $ORG_NAME\nSite            : $SITE_TITLE\nContato do NOC : $NOC_EMAIL"

        show_msg "Dados do site" "$summary"

        if menu_select "Confirmação do site" "$summary\n\nO que deseja fazer?" \
            "Confirmar" "Usar estas informações e continuar" \
            "Corrigir" "Voltar ao formulário pré-preenchido" \
            "Cancelar" "Abortar a instalação"; then
            case "$REPLY" in
                Confirmar) break ;;
                Corrigir)  continue ;;
                Cancelar)  fail "instalação cancelada pelo usuário" ;;
            esac
        else
            fail "instalação cancelada pelo usuário"
        fi
    done
    ok "dados do site coletados"
}

# ---------------------------------------------------------------------------
# Geração do openglass.yaml (Python/ruamel preserva comentários do template)
# ---------------------------------------------------------------------------
write_site_config() {
    info "Gerando $CONFIG_FILE …"
    backup_file "$CONFIG_FILE"
    ASN="$ASN" ORG_NAME="$ORG_NAME" SITE_TITLE="$SITE_TITLE" NOC_EMAIL="$NOC_EMAIL" \
        "$PYTHON_BIN" - "$REPO_DIR/openglass.yaml.example" "$CONFIG_FILE" <<'PYENV'
import os, sys
import ruamel.yaml

src, dst = sys.argv[1], sys.argv[2]
yaml = ruamel.yaml.YAML()
with open(src, encoding="utf-8") as fh:
    data = yaml.load(fh)

data["primary_asn"] = int(os.environ["ASN"])
data["org_name"] = os.environ["ORG_NAME"]
data["site_title"] = os.environ["SITE_TITLE"]
for menu in data.get("web", {}).get("menus", []):
    if str(menu.get("title", "")).lower() in ("contato", "contact"):
        menu["content"] = (
            "Please contact [{email}](mailto:{email}) to get support."
        ).format(email=os.environ["NOC_EMAIL"])

with open(dst, "w", encoding="utf-8") as fh:
    yaml.dump(data, fh)
PYENV
    chmod 0640 "$CONFIG_FILE"
    ok "openglass.yaml gravado"
}

# ---------------------------------------------------------------------------
# 9. Assistente de ativos (devices.yaml)
# ---------------------------------------------------------------------------
vendor_menu() {
    local file vendor i=1
    VENDORS=()
    VENDOR_ITEMS=()
    for file in "$REPO_DIR"/nodes/*.yaml; do
        [ -e "$file" ] || continue
        vendor="${file##*/}"; vendor="${vendor%.yaml}"
        VENDORS+=("$vendor")
    done
    if [ "${#VENDORS[@]}" -eq 0 ]; then
        fail "nenhum vendor encontrado em $REPO_DIR/nodes"
    fi
    for vendor in "${VENDORS[@]}"; do
        VENDOR_ITEMS+=("$vendor" "perfil em nodes/$vendor.yaml")
    done
}

device_block() { # $1=nome $2=vendor $3=ip $4=src $5=snmp $6=user $7=pass $8=port
    printf '  - name: %s\n' "$1"
    printf '    address: %s\n' "$3"
    printf '    credential:\n'
    printf '      username: %s\n' "$6"
    printf '      password: %s\n' "$7"
    printf '    port: %s\n' "$8"
    printf '    nos: %s\n' "$2"
    if [ -n "$5" ]; then
        printf '    snmp:\n'
        printf '      community: %s\n' "$5"
    fi
    if [ -n "$4" ]; then
        printf '    vrfs:\n'
        printf '      - name: global\n'
        printf '        default: true\n'
        printf '        ipv4:\n'
        printf '          source_address: %s\n' "$4"
    fi
}

wizard_device() {
    local summary
    while :; do
        # Nome
        while :; do
            if ! get_input "Ativo — nome" "Nome do ativo (ex.: edge-r1)" "${dev_name:-}"; then fail "cancelado"; fi
            [ -n "$REPLY" ] && { dev_name="$REPLY"; break; }
            show_msg "Campo obrigatório" "Informe o nome do ativo."
        done
        # Vendor (menu dinâmico de nodes/*.yaml) — atual vem primeiro
        vendor_menu
        if [ -n "${dev_vendor:-}" ] && [ "${VENDOR_ITEMS[0]:-}" != "$dev_vendor" ]; then
            local -a ordered=("$dev_vendor" "perfil em nodes/$dev_vendor.yaml")
            local j
            for ((j = 0; j < ${#VENDOR_ITEMS[@]}; j += 2)); do
                [ "${VENDOR_ITEMS[j]}" = "$dev_vendor" ] && continue
                ordered+=("${VENDOR_ITEMS[j]}" "${VENDOR_ITEMS[j + 1]}")
            done
            VENDOR_ITEMS=("${ordered[@]}")
        fi
        if ! menu_select "Ativo — vendor" "Selecione o vendor (perfil em nodes/):" "${VENDOR_ITEMS[@]}"; then fail "cancelado"; fi
        dev_vendor="$REPLY"
        # IPv4
        while :; do
            if ! get_input "Ativo — IPv4" "Endereço IPv4 de gerenciamento (ex.: 45.5.40.255)" "${dev_ip:-}"; then fail "cancelado"; fi
            valid_ipv4 "$REPLY" && { dev_ip="$REPLY"; break; }
            show_msg "IPv4 inválido" "Informe um endereço IPv4 válido (ex.: 45.5.40.255)."
        done
        # Source IPv4
        while :; do
            if ! get_input "Ativo — source IPv4" "IPv4 de origem das consultas (interface de origem)" "${dev_src:-}"; then fail "cancelado"; fi
            [ -z "$REPLY" ] && [ -n "${dev_src:-}" ] && break
            if [ -n "$REPLY" ] && valid_ipv4 "$REPLY"; then { dev_src="$REPLY"; break; }; fi
            show_msg "Source IPv4 inválido" "Informe um endereço IPv4 válido."
        done
        # Comunidade SNMP
        if ! get_input_optional "Ativo — SNMP" "Comunidade SNMP (opcional)" "${dev_snmp:-}"; then fail "cancelado"; fi
        [ -n "$REPLY" ] && dev_snmp="$REPLY"
        # Usuário SSH
        while :; do
            if ! get_input "Ativo — usuário SSH" "Usuário SSH" "${dev_user:-}"; then fail "cancelado"; fi
            [ -n "$REPLY" ] && { dev_user="$REPLY"; break; }
            show_msg "Campo obrigatório" "Informe o usuário SSH."
        done
        # Senha SSH (nunca ecoa; vazio mantém a anterior no modo Corrigir)
        while :; do
            if ! get_secret "Ativo — senha SSH" "Senha SSH (não será exibida)"; then fail "cancelado"; fi
            [ -n "$REPLY" ] && { dev_pass="$REPLY"; break; }
            [ -n "${dev_pass:-}" ] && break
            show_msg "Campo obrigatório" "Informe a senha SSH."
        done
        # Porta SSH
        while :; do
            if ! get_input "Ativo — porta SSH" "Porta SSH do ativo" "${dev_port:-22}"; then fail "cancelado"; fi
            valid_port "$REPLY" && { dev_port="$REPLY"; break; }
            show_msg "Porta inválida" "A porta deve ser um número entre 1 e 65535."
        done

        summary="Nome        : $dev_name\nVendor      : $dev_vendor\nIPv4        : $dev_ip\nSource IPv4 : ${dev_src:-—}\nSNMP        : ${dev_snmp:-(sem)}\nUsuário SSH : $dev_user\nPorta SSH   : $dev_port"

        show_msg "Dados do ativo" "$summary"

        if menu_select "Confirmação do ativo" "$summary\n\nO que deseja fazer?" \
            "Confirmar" "Usar este ativo e continuar" \
            "Corrigir" "Voltar ao formulário pré-preenchido" \
            "Descartar" "Cancelar este ativo"; then
            case "$REPLY" in
                Confirmar)  break ;;
                Corrigir)   continue ;;
                Descartar)  return 1 ;;
            esac
        else
            return 1
        fi
    done

    DEV_NAMES+=("${dev_name:-}"); DEV_VENDORS+=("$dev_vendor"); DEV_IPS+=("${dev_ip:-}")
    DEV_SRCS+=("${dev_src:-}"); DEV_SNMPS+=("${dev_snmp:-}"); DEV_USERS+=("${dev_user:-}")
    DEV_PASSES+=("$dev_pass"); DEV_PORTS+=("$dev_port")
}

wizard_devices() {
    local new_count=0
    DEV_NAMES=(); DEV_VENDORS=(); DEV_IPS=(); DEV_SRCS=()
    DEV_SNMPS=(); DEV_USERS=(); DEV_PASSES=(); DEV_PORTS=()
    printf '\n'
    info "Assistente de ativos (roteadores)"
    while :; do
        if wizard_device; then
            new_count=$((new_count + 1))
        fi
        if ! ask_yesno "Adicionar ativo" "Adicionar outro ativo?"; then
            break
        fi
    done
    if [ "$new_count" -eq 0 ]; then
        if [ "$INSTALL_MODE" = "edit" ] && [ -s "$DEVICES_FILE" ]; then
            ok "mantendo ativos já existentes (nenhum novo informado)"
        else
            fail "nenhum ativo informado — cancelando"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Geração do devices.yaml (Python mescla ativos existentes + novos)
# ---------------------------------------------------------------------------
write_devices_config() {
    local frag new_devs
    info "Gerando $DEVICES_FILE …"
    backup_file "$DEVICES_FILE"

    frag="/tmp/openglass-new-devices.yaml"
    : > "$frag"
    local i
    for i in "${!DEV_NAMES[@]}"; do
        device_block \
            "${DEV_NAMES[$i]}" "${DEV_VENDORS[$i]}" "${DEV_IPS[$i]}" \
            "${DEV_SRCS[$i]}" "${DEV_SNMPS[$i]}" "${DEV_USERS[$i]}" \
            "${DEV_PASSES[$i]}" "${DEV_PORTS[$i]}" >> "$frag"
    done

    KEEP_EXISTING=0
    [ "$INSTALL_MODE" = "edit" ] && [ -f "$DEVICES_FILE" ] && KEEP_EXISTING=1

    KEEP="$KEEP_EXISTING" NEW_FRAG="$frag" \
        "$PYTHON_BIN" - "$DEVICES_FILE" <<'PYENV'
import os, sys
import ruamel.yaml
from ruamel.yaml.comments import CommentedMap

dst = sys.argv[1]
yaml = ruamel.yaml.YAML()
yaml.indent(mapping=2, sequence=4, offset=2)

routers = []
if os.environ.get("KEEP") == "1" and os.path.exists(dst):
    with open(dst, encoding="utf-8") as fh:
        existing = yaml.load(fh)
    if isinstance(existing, dict):
        routers = existing.get("routers") or []

with open(os.environ["NEW_FRAG"], encoding="utf-8") as fh:
    new_routers = yaml.load(fh) or []

routers = routers + new_routers
if not routers:
    raise SystemExit("lista de ativos vazia")

data = CommentedMap()
data["routers"] = routers

with open(dst, "w", encoding="utf-8") as fh:
    fh.write("# OpenGlass — inventário (gerado pelo instalador)\n")
    fh.write("# Contém credenciais. Não versione este arquivo.\n")
    yaml.dump(data, fh)
PYENV
    chmod 0600 "$DEVICES_FILE"
    rm -f "$frag"
    ok "devices.yaml gravado"
}

# ---------------------------------------------------------------------------
# 10. .env apontando para a configuração
# ---------------------------------------------------------------------------
write_env() {
    info "Gerando $REPO_DIR/.env …"
    cat > "$REPO_DIR/.env" <<EOF
INVENTORY_PATH=$DEVICES_FILE
CONFIG_PATH=$CONFIG_FILE
LOG_LEVEL=INFO
DEBUG=false
EOF
    ok ".env gravado"
}

# ---------------------------------------------------------------------------
# Symlinks
# ---------------------------------------------------------------------------
make_symlinks() {
    [ "$DO_SYMLINKS" -eq 1 ] || { warn "pulando symlinks (--no-symlinks)"; return 0; }
    ln -sfn "$REPO_DIR/.venv/bin/openglass"     /usr/local/bin/openglass
    ln -sfn "$REPO_DIR/.venv/bin/openglass-web" /usr/local/bin/openglass-web
    ok "symlinks criados (openglass, openglass-web)"
}

# ---------------------------------------------------------------------------
# Serviço systemd
# ---------------------------------------------------------------------------
install_service() {
    [ "$DO_SERVICE" -eq 1 ] || return 0
    command -v systemctl >/dev/null 2>&1 || { warn "systemd não encontrado — pule o serviço web"; return 0; }
    info "Criando unit do systemd (openglass.service)…"
    id openglass >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin openglass
    chown -R openglass:openglass "$CONFIG_DIR" 2>/dev/null || true

    cat > /etc/systemd/system/openglass.service <<EOF
[Unit]
Description=OpenGlass Web
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=openglass
Group=openglass
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/.venv/bin/openglass-web
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now openglass
    ok "serviço openglass ativo (http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000)"
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
uninstall() {
    if command -v systemctl >/dev/null 2>&1 && [ -f /etc/systemd/system/openglass.service ]; then
        systemctl disable --now openglass 2>/dev/null || true
        rm -f /etc/systemd/system/openglass.service
        systemctl daemon-reload
        ok "serviço openglass removido"
    fi
    for bin in openglass openglass-web; do
        rm -f "/usr/local/bin/$bin"
    done
    ok "symlinks removidos"
    printf '\nO diretório de configuração (%s) e o repositório (%s) foram mantidos.\n' \
        "$CONFIG_DIR" "$REPO_DIR"
    printf 'Para removê-los, apague manualmente (contêm credenciais).\n'
}

# ---------------------------------------------------------------------------
main() {
    if [ "$DO_UNINSTALL" -eq 1 ]; then
        uninstall
        exit 0
    fi

    printf '\n'
    printf "${C_CYAN}┌──────────────────────────────────────────┐${C_RESET}\n"
    printf "${C_CYAN}│         OpenGlass — instalador           │${C_RESET}\n"
    printf "${C_CYAN}└──────────────────────────────────────────┘${C_RESET}\n\n"

    step_root
    step_os
    step_update
    step_packages
    step_uv
    step_config_dir
    step_clone
    prefill_site_from_config
    wizard_site
    write_site_config
    wizard_devices
    write_devices_config
    write_env
    make_symlinks
    install_service

    # ------------------------------------------------------------------
    # Smoke test
    # ------------------------------------------------------------------
    info "Testando o CLI (listagem do inventário)…"
    if (cd "$REPO_DIR" && ".venv/bin/openglass" --list-devices); then
        ok "OpenGlass instalado e funcionando!"
    else
        warn "o CLI retornou erro — revise $DEVICES_FILE"
    fi

    show_msg "Concluído" \
"OpenGlass instalado!\n\nConfig      : $CONFIG_DIR\nRepositório : $REPO_DIR\nAtivos      : ${#DEV_NAMES[@]} novo(s) em $DEVICES_FILE\n\nCLI : openglass --device <nome> --command ping --param ip=8.8.8.8\nWeb : openglass-web  (http://<ip>:8000)"
}

main "$@"