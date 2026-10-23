"""The built client served the way step 1 proposes: gzip, immutable assets, no-cache index.

Usage: python static.py <web dir> <port>

Only for comparing page loads: there is no API behind it, so the app shows its error state.
"""
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

web = Path(sys.argv[1])


class CachedAssets(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


async def index(request):
    return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

app = Starlette(routes=[Mount("/assets", CachedAssets(directory=web / "assets")),
                        Route("/{path:path}", index)])
app.add_middleware(GZipMiddleware, minimum_size=500)
uvicorn.run(app, port=int(sys.argv[2]), log_level="warning")
