#!/usr/bin/env bash
# Installs native-Linux UE4SS (no Proton/Wine) next to PalServer.sh, plus the
# BiggerBaseArea Lua mod. Only copies files — it does not touch systemd or
# restart anything, so it is safe to run while the server is up.
#
#   ./install_ue4ss_linux.sh                                  # release tarball
#   UE4SS_SO=~/ue4ss-src/build_linux_Dev_gcc/Game__Dev__Linux64/lib/libUE4SS.so ./install_ue4ss_linux.sh
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
# Refresh the bundled mods but keep the operator's mods.txt (which mods are on)
# across re-runs; the toggles below only touch this script's own entries.
[ -f "$ROOT/Mods/mods.txt" ] && cp "$ROOT/Mods/mods.txt" "$STAGE/mods.txt.keep"
cp -r "$STAGE/Mods" "$ROOT/"
[ -f "$STAGE/mods.txt.keep" ] && cp "$STAGE/mods.txt.keep" "$ROOT/Mods/mods.txt"

# The tagged release binary is known-broken (repo issues #1/#11); the fixes
# live on the linux-native branch and must be built locally. Point at that
# build to use it instead of the release .so.
if [ -n "${UE4SS_SO:-}" ]; then
  echo "==> using locally built ${UE4SS_SO}"
  cp "$UE4SS_SO" "$ROOT/libUE4SS.so"
fi

# Shipped settings crash a headless server (issue #1): the debug GUI thread
# SIGSEGVs with no display, and empty MajorVersion/MinorVersion make the
# settings parser throw -> SIGABRT. Palworld is UE 5.1.
echo "==> fixing headless settings"
sed -i -e 's/^ConsoleEnabled = 1/ConsoleEnabled = 0/' \
       -e 's/^GuiConsoleEnabled = 1/GuiConsoleEnabled = 0/' \
       -e 's/^GuiConsoleVisible = 1/GuiConsoleVisible = 0/' \
       -e 's/^MajorVersion = *$/MajorVersion = 5/' \
       -e 's/^MinorVersion = *$/MinorVersion = 1/' "$ROOT/UE4SS-settings.ini"

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
