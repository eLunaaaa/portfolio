#!/usr/bin/env bash
# Cache the runner's missing host tools and all dependencies selected by APT.
set -euo pipefail

readonly required_packages=(
  gcc-14-aarch64-linux-gnu
  g++-14-aarch64-linux-gnu
  cppcheck
  pkg-config
  libglib2.0-dev
)
readonly apt_options=(
  -o Acquire::Retries=1
  -o Acquire::http::Timeout=15
  -o Acquire::https::Timeout=15
)

fail() { echo "Host dependency cache: $*" >&2; exit 1; }

: "${HOST_DEB_CACHE_DIR:?Set HOST_DEB_CACHE_DIR to an absolute cache directory}"
[[ "$HOST_DEB_CACHE_DIR" == /* && "$HOST_DEB_CACHE_DIR" != / ]] ||
  fail "HOST_DEB_CACHE_DIR must be an absolute directory other than /."
readonly cache_dir="${HOST_DEB_CACHE_DIR%/}"
download_dir=
trap 'if [[ -n "$download_dir" ]]; then sudo rm -rf -- "$download_dir"; fi' EXIT

missing_packages=()
find_missing_packages() {
  missing_packages=()
  local package status
  for package in "${required_packages[@]}"; do
    status=$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null) || status=
    [[ "$status" == 'install ok installed' ]] || missing_packages+=("$package")
  done
}

validate_cache() {
  local line name file count=0
  local -A seen=()
  [[ -f "$cache_dir/SHA256SUMS" && ! -L "$cache_dir/SHA256SUMS" ]] ||
    fail "missing checksum manifest."
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Only basenames are allowed: a restored manifest must not reference other paths.
    [[ "$line" =~ ^[0-9a-f]{64}\ \ ([A-Za-z0-9][A-Za-z0-9._+:%~-]*\.deb)$ ]] ||
      fail "invalid checksum manifest entry."
    name="${BASH_REMATCH[1]}"
    [[ -z "${seen[$name]+present}" ]] || fail "duplicate checksum entry: $name"
    seen[$name]=1
    [[ -f "$cache_dir/$name" && ! -L "$cache_dir/$name" ]] ||
      fail "missing or invalid package: $name"
    count=$((count + 1))
  done < "$cache_dir/SHA256SUMS"
  (( count > 0 )) || fail "empty checksum manifest."
  for file in "$cache_dir"/*.deb; do
    name="${file##*/}"
    [[ -n "${seen[$name]+present}" ]] || fail "unlisted package: $name"
  done
  (cd "$cache_dir" && sha256sum --check --strict --status SHA256SUMS) ||
    fail "package checksum verification failed."
}

install_local_packages() {
  local archives="$1"
  local packages=("$archives"/*.deb)
  [[ -f "${packages[0]}" ]] || fail "APT did not produce any package archives."
  sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" \
    -o "Dir::Cache::archives=$archives" \
    -o APT::Keep-Downloaded-Packages=true \
    --yes --no-install-recommends --no-download install "${packages[@]}"
  find_missing_packages
  (( ${#missing_packages[@]} == 0 )) ||
    fail "required packages remain uninstalled: ${missing_packages[*]}"
}

if [[ "${HOST_DEB_CACHE_HIT:-false}" == true ]]; then
  validate_cache
  install_local_packages "$cache_dir"
  echo 'Host dependencies installed from the verified cache.'
else
  find_missing_packages
  if (( ${#missing_packages[@]} == 0 )); then
    echo 'All host dependencies are already installed; no cache is needed.'
    exit 0
  fi

  mkdir -p -- "$cache_dir"
  download_dir=$(mktemp -d "${cache_dir}.download.XXXXXX")
  chmod 755 "$download_dir"
  mkdir "$download_dir/archives"

  sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" \
    -o APT::Update::Error-Mode=any update
  sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" \
    -o "Dir::Cache::archives=$download_dir/archives" \
    --yes --no-install-recommends --download-only install "${missing_packages[@]}"
  install_local_packages "$download_dir/archives"

  # Publish only successful downloads. APT lock/partial files stay out of the cache.
  (cd "$download_dir/archives" && sha256sum -- *.deb) > "$download_dir/SHA256SUMS"
  rm -f -- "$cache_dir"/*.deb "$cache_dir/SHA256SUMS"
  cp -- "$download_dir/archives"/*.deb "$download_dir/SHA256SUMS" "$cache_dir/"
  validate_cache
  echo 'Host dependencies installed; package cache is ready to save.'
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'cache-ready=true\n' >> "$GITHUB_OUTPUT"
fi
