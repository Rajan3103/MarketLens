"""Pick the data client: real Dhan when the keys are set, otherwise DEMO data.

Pages and engines only ever do:
    from marketlens.broker.market_data import get_data_client
    client = get_data_client()
    chain = client.get_option_chain("NIFTY", expiry)
    if client.is_demo: show a "DEMO DATA" banner

Both clients have the same methods; Dhan calls go through @retry and the speed limits.
"""
from marketlens import config
from marketlens.broker.demo_data import DemoDataClient
from marketlens.broker.dhan_client import DhanError
from marketlens.broker.dhan_data import DhanDataClient
from marketlens.logger import get_logger

log = get_logger(__name__)


# Return DhanDataClient if the keys are set, else DemoDataClient (or an error if DEMO_MODE=false).
def get_data_client():
    """The one place that decides between real and DEMO data."""
    dhan = DhanDataClient()
    if dhan.is_ready():
        return dhan
    if config.DEMO_MODE:
        log.info("Dhan keys not set: using DEMO data")
        return DemoDataClient()
    raise DhanError("Dhan key not set (DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN). "
                    "Add them to .env, or set DEMO_MODE=true to use DEMO data.")
