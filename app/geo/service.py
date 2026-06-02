import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from urllib.request import urlopen, Request as UrlRequest

from ..config import settings
from ..geo.models import IpInfoCache
from ..dependencies import SessionDep
from ..redis_client import redis_client

_GEO_TTL = 60 * 60 * 24  # 24 hours
_GEO_PREFIX = "geo:ip:"


class IpInfoService:
    def __init__(self, token: Optional[str]):
        self.token = token

    def _fetch_remote(self, ip: str) -> Optional[Dict[str, Any]]:
        if not self.token or not ip:
            return None
        try:
            url = f"https://ipinfo.io/{ip}?token={self.token}"
            req = UrlRequest(url, headers={"User-Agent": "ipinfo-client/1.0"})
            with urlopen(req, timeout=3) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                return data
        except Exception:
            return None

    async def resolve(self, session: SessionDep, ip: str) -> Optional[Dict[str, Any]]:
        if not ip:
            return None

        # Redis cache first (shared across all workers)
        cached = await redis_client.get(f"{_GEO_PREFIX}{ip}")
        if cached:
            return json.loads(cached)

        # DB cache
        db_item = session.exec(IpInfoCache.select().where(IpInfoCache.ip == ip)).first()
        if db_item and (datetime.utcnow() - db_item.updated_at) < timedelta(days=3):
            result = {
                "country": db_item.country,
                "region": db_item.region,
                "city": db_item.city,
                "timezone": db_item.timezone,
                "loc": db_item.loc,
                "org": db_item.org,
            }
            await redis_client.set(f"{_GEO_PREFIX}{ip}", json.dumps(result), ex=_GEO_TTL)
            return result

        # Fetch from ipinfo
        data = self._fetch_remote(ip)
        if not data:
            return None
        result = {
            "country": data.get("country"),
            "region": data.get("region"),
            "city": data.get("city"),
            "timezone": data.get("timezone"),
            "loc": data.get("loc"),
            "org": data.get("org"),
        }

        # Upsert DB cache
        try:
            if db_item:
                db_item.country = result["country"]
                db_item.region = result["region"]
                db_item.city = result["city"]
                db_item.timezone = result["timezone"]
                db_item.loc = result["loc"]
                db_item.org = result["org"]
                db_item.updated_at = datetime.utcnow()
                session.add(db_item)
            else:
                session.add(
                    IpInfoCache(
                        ip=ip,
                        country=result["country"],
                        region=result["region"],
                        city=result["city"],
                        timezone=result["timezone"],
                        loc=result["loc"],
                        org=result["org"],
                        updated_at=datetime.utcnow(),
                    )
                )
            session.commit()
        except Exception:
            session.rollback()

        await redis_client.set(f"{_GEO_PREFIX}{ip}", json.dumps(result), ex=_GEO_TTL)
        return result


ipinfo_service = IpInfoService(settings.IPINFO_TOKEN)
