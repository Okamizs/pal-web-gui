#!/usr/bin/env bash
# Installs native-Linux UE4SS (no Proton/Wine) next to PalServer.sh, plus the
# BiggerBaseArea Lua mod. Only copies files — it does not touch systemd or
# restart anything, so it is safe to run while the server is up.
#
#   ./install_ue4ss_linux.sh
#
# Undo: delete libUE4SS.so / UE4SS-settings.ini / MemberVariableLayout.ini /
# Mods/ from the PalServer directory, and drop the LD_PRELOAD line from
# palserver.service.
set -euo pipefail

VERSION="v1.0.2-palworld-linux"
URL="https://github.com/BlackBookOfficial/ue4ss-linux-palworld/releases/download/${VERSION}/ue4ss-linux-palworld-${VERSION}.tar.gz"
ROOT="$HOME/.local/share/Steam/steamapps/common/PalServer"
REPO="$HOME/pal-web-gui"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

[ -f "$ROOT/PalServer.sh" ] || { echo "PalServer.sh not found in $ROOT"; exit 1; }

echo "==> downloading UE4SS ${VERSION}"
curl -fsSL -o "$STAGE/ue4ss.tar.gz" "$URL"
tar -xzf "$STAGE/ue4ss.tar.gz" -C "$STAGE"
[ -f "$STAGE/libUE4SS.so" ] || { echo "archive did not contain libUE4SS.so"; exit 1; }

echo "==> installing next to PalServer.sh"
cp "$STAGE/libUE4SS.so" "$STAGE/UE4SS-settings.ini" "$STAGE/MemberVariableLayout.ini" "$STAGE/BUILD_INFO.txt" "$ROOT/"
cp -r "$STAGE/Mods" "$ROOT/"

echo "==> disabling cheat/console mods (public server)"
sed -i -e 's/^CheatManagerEnablerMod : 1/CheatManagerEnablerMod : 0/' \
       -e 's/^ConsoleCommandsMod : 1/ConsoleCommandsMod : 0/' \
       -e 's/^ConsoleEnablerMod : 1/ConsoleEnablerMod : 0/' \
       -e 's/^LineTraceMod : 1/LineTraceMod : 0/' "$ROOT/Mods/mods.txt"

echo "==> installing BiggerBaseArea"
mkdir -p "$ROOT/Mods/BiggerBaseArea/Scripts"
cp "$REPO/ue4ss-mods/BiggerBaseArea/Scripts/main.lua" "$ROOT/Mods/BiggerBaseArea/Scripts/main.lua"
grep -q '^BiggerBaseArea' "$ROOT/Mods/mods.txt" || \
  sed -i 's/^BPModLoaderMod : 1/BPModLoaderMod : 1\nBiggerBaseArea : 1/' "$ROOT/Mods/mods.txt"

echo
echo "installed:"
ls -1 "$ROOT" | grep -E '^(libUE4SS\.so|UE4SS-settings\.ini|MemberVariableLayout\.ini|Mods)$'
echo
echo "enabled mods:"
grep -v '^$' "$ROOT/Mods/mods.txt" | grep -v ' : 0'
echo
echo "Files are in place. Nothing is loaded until the server restarts with LD_PRELOAD."
