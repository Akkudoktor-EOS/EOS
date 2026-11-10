#!/bin/sh
# Build akkudoktoreos-native wheels for Linux with maturin + zig (cross-compiling, no Docker
# image per platform). Needs: Rust (rustup targets below), maturin >= 1.9, ziglang.
#   native/scripts/build-wheels.sh [OUT_DIR] [TARGET ...]
# Default targets: x86_64 and aarch64 for glibc (manylinux2014) and musl (musllinux_1_2), and
# armv7 for both. CI uses maturin-action instead (.github/workflows/native.yml).
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$HERE/dist}
[ $# -gt 0 ] && shift
TARGETS=${*:-"x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu armv7-unknown-linux-gnueabihf x86_64-unknown-linux-musl aarch64-unknown-linux-musl armv7-unknown-linux-musleabihf"}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
cd "$HERE/akkudoktoreos-native"
for target in $TARGETS; do
  case "$target" in
    *-musl*) compat=musllinux_1_2 ;;
    *) compat=manylinux2014 ;;
  esac
  echo "== $target ($compat)"
  maturin build --release --zig --target "$target" --compatibility "$compat" --out "$OUT"
done
ls -l "$OUT"
