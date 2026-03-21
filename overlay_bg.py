import logging

from overlay import run_background_overlay

if __name__ == "__main__":
    logging.basicConfig(
        filename="overlay.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    run_background_overlay()
