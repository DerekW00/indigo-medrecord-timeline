"""`python -m app` -> uvicorn on 127.0.0.1:MRT_PORT (single process, no reload)."""
import logging

import uvicorn

from . import config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("app.api:app", host="127.0.0.1", port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
