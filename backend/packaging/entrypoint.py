from __future__ import annotations

import uvicorn

from app.config import settings
from app.main import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
