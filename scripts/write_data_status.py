"""Refresh the small status summary without acquiring or repairing data."""

from config import config
from shp_utils.data_status import write_data_status
from shp_utils.logging_utils import setup_logging


def main() -> None:
    setup_logging("write_data_status.log")
    write_data_status(config.DATA_DIR)


if __name__ == "__main__":
    main()
