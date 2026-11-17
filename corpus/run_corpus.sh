#!/bin/sh
# Reproduce the corpus table in README (read-only clones under /tmp).
set -e
mkdir -p /tmp/corpus && cd /tmp/corpus
for repo in mahmoud/boltons alecthomas/voluptuous r1chardj0n3s/parse \
            toumorokoshi/deepmerge linluozi/cachetools-lru-opt-lab \
            linluozi/cacheout-opt-lab linluozi/jsonpatch-apply-opt-lab \
            linluozi/emoji-opt-lab linluozi/wcwidth-opt-lab; do
  name=$(basename "$repo")
  [ -d "$name" ] || git clone -q --depth=1 "git@github-linluozi:$repo.git" "$name"
done
cd - >/dev/null
python3 -m slotfit.cli /tmp/corpus --json corpus/report.json --top 20
