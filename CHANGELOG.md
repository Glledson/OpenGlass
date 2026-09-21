# Changelog

Todas as mudanças relevantes do OpenGlass são documentadas aqui. Formato
inspirado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

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