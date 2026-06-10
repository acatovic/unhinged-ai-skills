#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/acatovic/unhinged-ai-skills.git"
SKILL_NAME="${1:-}"
TARGET_MODE="${2:---both}"
INSTALL_SCOPE="${3:---project}"

if [ -z "$SKILL_NAME" ]; then
  echo "Usage: install.sh <skill-name> [--claude|--codex|--both] [--project|--user]"
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

git clone --depth 1 "$REPO_URL" "$TMP_DIR/unhinged-ai-skills" >/dev/null

SOURCE="$TMP_DIR/unhinged-ai-skills/skills/$SKILL_NAME"

if [ ! -f "$SOURCE/SKILL.md" ]; then
  echo "Skill not found: $SKILL_NAME"
  exit 1
fi

install_skill() {
  local dest="$1"

  mkdir -p "$(dirname "$dest")"
  rm -rf "$dest"
  cp -R "$SOURCE" "$dest"

  echo "Installed $SKILL_NAME -> $dest"
}

case "$INSTALL_SCOPE" in
  --project)
    CLAUDE_DEST="./.claude/skills/$SKILL_NAME"
    CODEX_DEST="./.agents/skills/$SKILL_NAME"
    ;;
  --user)
    CLAUDE_DEST="$HOME/.claude/skills/$SKILL_NAME"
    CODEX_DEST="$HOME/.agents/skills/$SKILL_NAME"
    ;;
  *)
    echo "Unknown scope: $INSTALL_SCOPE"
    echo "Use --project or --user"
    exit 1
    ;;
esac

case "$TARGET_MODE" in
  --claude)
    install_skill "$CLAUDE_DEST"
    ;;
  --codex)
    install_skill "$CODEX_DEST"
    ;;
  --both)
    install_skill "$CLAUDE_DEST"
    install_skill "$CODEX_DEST"
    ;;
  *)
    echo "Unknown target: $TARGET_MODE"
    echo "Use --claude, --codex, or --both"
    exit 1
    ;;
esac
