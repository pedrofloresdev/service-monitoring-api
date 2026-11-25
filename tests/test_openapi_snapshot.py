"""The API schema document must not change unless a spec says so (spec 003a RNF-1).

`tests/fixtures/openapi.json` was generated from `master` before spec 003a. An approved
schema change regenerates it in the same commit:

    DATABASE_URL=sqlite:// python -c "import json; from app.main import app; \
print(json.dumps(app.openapi(), sort_keys=True, indent=2))" > tests/fixtures/openapi.json
"""

import json
from pathlib import Path

from app.main import app

SNAPSHOT = Path(__file__).parent / "fixtures" / "openapi.json"


def test_openapi_document_matches_the_snapshot() -> None:
    assert app.openapi() == json.loads(SNAPSHOT.read_text())
