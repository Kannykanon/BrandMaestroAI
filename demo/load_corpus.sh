#!/usr/bin/env bash
# Load the SALVAGE demo corpus into a BrandMaestro AI instance as a business's
# previous best-performing content, which is what the Brand Brain is built from.
#
# Usage:
#   ./demo/load_corpus.sh <base-url> <username> <password>
#
# The account is created if it does not exist, then logged into either way.
# Two roles, two channels, and never both:
#
#   corpus/reference/    -> doc_role=reference -> RAG retrieval (what to write)
#
# A voice document is never indexed for retrieval. If it were, the writer would
# be handed the style references as material and would reproduce them, which is
# what the whole system exists not to do. Facts come from the reference sheet and
# from Parallel's web search.
#
# The reference sheet is uploaded once per content type, because retrieval is
# scoped to (business_id, content_type).
#
# Directory names map to the content types the pipeline uses:
#   press-release/ -> blog       (UI label: Press Release Model)
#   social/        -> social     (UI label: Social Caption Model)
#   trailer-copy/  -> ad         (UI label: Trailer Copy Model)
#   talent-bios/   -> proposal   (UI label: Talent Bio Model)
set -euo pipefail

BASE="${1:?usage: load_corpus.sh <base-url> <username> <password>}"
USER="${2:?username required}"
PASS="${3:?password required}"

CORPUS="$(cd "$(dirname "$0")" && pwd)/corpus"

json_field() { python -c "import sys,json;d=json.load(sys.stdin);print(d$1)"; }

echo "==> registering or logging in as $USER"
# A 400 here means the account already exists, which is not an error for this
# script — the login below is the step that has to succeed.
curl -s -X POST "$BASE/users/create" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\",\"email\":\"$USER@harborline.example\",\"first_name\":\"Harbor\",\"last_name\":\"Line\"}" \
  > /dev/null || true

TOKEN=$(curl -s -X POST "$BASE/users/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$USER" --data-urlencode "password=$PASS" \
  | json_field "['access_token']")

BUSINESS_ID=$(curl -s "$BASE/users/me" -H "Authorization: Bearer $TOKEN" \
  | json_field "['business_id']")

echo "    business_id: $BUSINESS_ID"

upload_dir() {
    local dir="$1" content_type="$2"
    for f in "$CORPUS/$dir"/*.txt; do
        printf '    %-46s -> %s\n' "$(basename "$f")" "$content_type"
        curl -s -X POST "$BASE/documents/brand-voice" \
          -H "Authorization: Bearer $TOKEN" \
          -F "business_id=$BUSINESS_ID" \
          -F "content_type=$content_type" \
          -F "file=@$f" > /dev/null
    done
}

upload_reference() {
    local content_type="$1"
    for f in "$CORPUS/reference"/*.txt; do
        printf '    %-46s -> %s (reference)\n' "$(basename "$f")" "$content_type"
        curl -s -X POST "$BASE/documents/product" \
          -H "Authorization: Bearer $TOKEN" \
          -F "business_id=$BUSINESS_ID" \
          -F "content_type=$content_type" \
          -F "file=@$f" > /dev/null
    done
}

echo "==> uploading voice references (Brand Brain)"
upload_dir press-release blog
upload_dir social        social
upload_dir trailer-copy  ad
upload_dir talent-bios   proposal

echo "==> uploading the fact sheet (retrieval)"
for ct in blog social ad proposal; do
    upload_reference "$ct"
done

echo "==> uploaded documents"
curl -s "$BASE/documents" -H "Authorization: Bearer $TOKEN" | python -c "
import sys, json
from collections import Counter
docs = json.load(sys.stdin)
for ct, n in sorted(Counter(d['content_type'] for d in docs).items()):
    print(f'    {ct:10} {n} document(s)')
print(f'    {\"total\":10} {len(docs)}')
"

cat <<NOTE

==> done
The Brand Brain for each content type is synthesised asynchronously, and the
synthesis is debounced, so allow a minute or so before the first generation.
NOTE
