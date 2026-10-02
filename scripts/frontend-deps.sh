#!/usr/bin/env bash
# Install the frontend dependencies on the Linux filesystem and link them into frontend/node_modules.
#
# The repository may live on a Windows drive under WSL, where every file read crosses to NTFS and Vitest spends
# minutes loading jsdom and its modules. The sources stay where they are; only node_modules moves to the Linux
# filesystem, into a directory of its own for each checkout, and frontend/node_modules becomes a link to it.
# npm ci replaces a linked node_modules with a real directory, so it runs in that directory instead, on copies of
# package.json, package-lock.json and .npmrc. Run this script wherever the docs say `npm --prefix frontend ci`.
#
# Usage: scripts/frontend-deps.sh [extra npm ci arguments]
# BOOKREVIVER_FRONTEND_DEPS overrides the directory the dependencies are installed into.
set -xo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd) || exit 1
frontend="${repo_root}/frontend"
# One directory per checkout, so two worktrees with different lock files never share their dependencies
checkout_id=$(printf '%s' "${repo_root}" | sha1sum | cut -c1-12)
store="${BOOKREVIVER_FRONTEND_DEPS:-${XDG_CACHE_HOME:-${HOME}/.cache}/bookreviver/frontend-${checkout_id}}"

mkdir -p "${store}"
for file in package.json package-lock.json .npmrc; do
  if [[ -f "${frontend}/${file}" ]]; then
    cp "${frontend}/${file}" "${store}/${file}" || exit 1
  fi
done
(cd "${store}" && npm ci "$@") || exit 1

# A real directory left by an earlier npm ci in frontend/ gives way to the link
if [[ -d "${frontend}/node_modules" && ! -L "${frontend}/node_modules" ]]; then
  rm -rf "${frontend}/node_modules" || exit 1
fi
ln -sfn "${store}/node_modules" "${frontend}/node_modules" || exit 1
echo "frontend/node_modules -> ${store}/node_modules"
