import logging
import httpx

from vault.config import settings

logger = logging.getLogger(__name__)


async def trigger_jellyfin_refresh() -> bool:
    """Sends a library refresh request to Jellyfin if JELLYFIN_URL is configured."""
    if not settings.JELLYFIN_URL:
        return False

    url = f"{settings.JELLYFIN_URL.rstrip('/')}/Library/Refresh"
    headers = {}
    if settings.JELLYFIN_API_KEY:
        headers["X-Emby-Token"] = settings.JELLYFIN_API_KEY

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=headers)
            if resp.status_code in (200, 204):
                logger.info("Successfully triggered Jellyfin library refresh")
                return True
            logger.warning(f"Jellyfin library refresh returned status {resp.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Could not connect to Jellyfin for library refresh: {e}")
        return False
