class ScrapeError(Exception):
    """Base class for scraping failures (retried by Prefect)."""


class ScrapeBlockedError(ScrapeError):
    """The source answered with a bot-wall / captcha / unexpected markup."""


class TransientScrapeError(ScrapeError):
    """Network-level or injected transient failure."""
