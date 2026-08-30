# P7 Workbench dependency audit

Verified on 2026-08-15 before installation.

| Package | Frozen range | Python baseline | License | Why it is used |
|---|---:|---:|---|---|
| FastAPI | `>=0.141,<0.142` | 3.10+ | MIT | Typed HTTP boundary and OpenAPI schema |
| Uvicorn | `>=0.52,<0.53` | 3.10+ | BSD-3-Clause | Minimal local ASGI server |
| Jinja2 | `>=3.1.6,<3.2` | 3.7+ | BSD-3-Clause | Autoescaped server-rendered workbench shell |
| HTTPX2 | `>=2.10,<2.11` | 3.9+ | BSD-3-Clause | Starlette 1.x supported test client transport; development only |

The project runs Python 3.12 and Pydantic 2.13, which are inside the declared
FastAPI/Pydantic compatibility range. Only minimal `uvicorn` is installed. The
`standard` extra is intentionally excluded because the MVP does not need reload,
WebSockets, `python-dotenv`, YAML logging, or alternate event-loop/protocol packages.

The versions were checked against the official PyPI project metadata and current
package index. All three projects have current releases and source repositories.
This audit does not claim that the packages are vulnerability-free; the final P7
gate still requires `pip check`, regression tests, and review of future updates.

Starlette 1.x warns when its `TestClient` falls back to legacy `httpx` and now
supports `httpx2`. The maintained HTTPX2 package is therefore frozen only in the
development extra; it is not part of the runtime server path.

Sources:

- <https://pypi.org/project/fastapi/>
- <https://pypi.org/project/uvicorn/>
- <https://pypi.org/project/Jinja2/>
- <https://pypi.org/project/httpx2/>
- <https://www.starlette.io/release-notes/>
