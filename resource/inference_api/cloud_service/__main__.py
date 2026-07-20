from __future__ import annotations

import os

import uvicorn


def main() -> None:
    try:
        port = int(os.getenv("PORT", "8080"))
    except ValueError as error:
        raise SystemExit("PORT must be an integer.") from error
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535.")
    uvicorn.run(
        "cloud_service.app:app",
        host="0.0.0.0",
        port=port,
        workers=1,
        proxy_headers=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()

