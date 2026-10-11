"""Hike route lines: the lazy route endpoint, filling routes into saved trips, and the VM sync CLI.

The VM can't reach Wikiloc (403), so routes are fetched where it can (the Mac) and merged into the VM's DB:
    python -m app.routes fetch --from https://<site> --out routes.json     # or --from path/to/trips.db
    python -m app.routes apply routes.json [--db data/trips.db]
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.auth_routes import current_user
from app.models import SavedTrip, _check_route, with_routes
from app.sources.wikiloc_route import RouteFetcher, wikiloc_number

ROUTES_FILE_VERSION = 1


# --- web --------------------------------------------------------------------

def missing_route_ids(hikes, have: set[str] = frozenset()) -> list[str]:
    """Wikiloc hikes without a route (and not in `have`): the only ones ever fetched."""
    return list(dict.fromkeys(h.id for h in hikes if not h.route and h.id not in have and wikiloc_number(h.id)))


async def fill_routes(fetcher: RouteFetcher, data, have: set[str] = frozenset()):
    """`data` (SavedTripIn / TripItemAdd-like with `hikes`) with routes for its hikes that lack one."""
    missing = missing_route_ids(data.hikes, have)
    if not missing:
        return data
    return with_routes(data, await fetcher.many(missing))


def route_router(fetcher: RouteFetcher) -> APIRouter:
    router = APIRouter()

    @router.get("/api/hikes/{hike_id}/route")
    async def hike_route(hike_id: str, request: Request):
        """{route: polyline | null}: the cache first; a page fetch only for logged-in users."""
        if wikiloc_number(hike_id) is None:
            raise HTTPException(status_code=422, detail="Not a Wikiloc hike id")
        hit = await asyncio.to_thread(fetcher.cached, hike_id)
        if hit or await asyncio.to_thread(current_user, request) is None:
            return {"route": hit}
        return {"route": await fetcher.route(hike_id)}

    return router


# --- CLI: fetch -------------------------------------------------------------

async def _trips_from_url(base: str, transport: httpx.AsyncBaseTransport | None = None) -> list[SavedTrip]:
    async with httpx.AsyncClient(base_url=base.rstrip("/"), transport=transport, timeout=30.0) as client:
        listing = (await client.get("/api/saved-trips")).raise_for_status().json()
        trips = []
        for summary in listing:
            r = await client.get(f"/api/saved-trips/{quote(summary['id'], safe='')}")
            if r.status_code == 200:
                trips.append(SavedTrip.model_validate(r.json()))
        return trips


def _trips_from_db(path: Path) -> list[SavedTrip]:
    if not path.is_file():
        raise SystemExit(f"No such trips database: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # read-only: never touches the source DB
    try:
        rows = conn.execute("SELECT id, doc FROM trips").fetchall()
    finally:
        conn.close()
    trips = []
    for trip_id, doc in rows:
        try:
            trips.append(SavedTrip.model_validate_json(doc))
        except ValueError:
            print(f"skipping unreadable trip {trip_id}", file=sys.stderr)
    return trips


async def fetch_routes(source: str, fetcher: RouteFetcher, refetch: bool = False,
                       transport: httpx.AsyncBaseTransport | None = None) -> tuple[dict, dict]:
    """Routes for every hike in the source's trips that has none (all hikes with `refetch`)."""
    if source.startswith(("http://", "https://")):
        trips = await _trips_from_url(source, transport)
    else:
        trips = await asyncio.to_thread(_trips_from_db, Path(source))
    hikes = [h for t in trips for h in t.hikes]
    ids = list(dict.fromkeys(h.id for h in hikes if wikiloc_number(h.id) and (refetch or not h.route)))
    got = await fetcher.many(ids)
    routes = {i: r for i, r in got.items() if r}
    stats = {"trips": len(trips), "hikes": len({h.id for h in hikes}), "wanted": len(ids), "fetched": len(routes),
             "failed": len(ids) - len(routes)}
    return routes, stats


# --- CLI: apply -------------------------------------------------------------

def load_routes_file(path: Path) -> dict[str, str]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("routes"), dict):
        raise SystemExit(f"{path}: not a routes file (expected {{\"routes\": {{hike id: polyline}}}})")
    routes = {}
    for hike_id, route in data["routes"].items():
        try:
            if isinstance(hike_id, str) and _check_route(route):
                routes[hike_id] = route
        except ValueError:
            print(f"skipping invalid route for {hike_id}", file=sys.stderr)
    return routes


def apply_routes(db_path: Path, routes: dict[str, str]) -> dict:
    """Give hikes without a route their route from `routes`, in every trip, in one transaction.

    Only the trips table's documents change, and in them only `route` of hikes that had none (a route that would
    push a trip past the size cap is left out); updated_at stays, so open editors see no conflict."""
    if not Path(db_path).is_file():
        raise SystemExit(f"No such trips database: {db_path}")
    stats = {"trips_updated": 0, "hikes_routed": 0, "already_routed": 0, "dropped_size_cap": 0, "unreadable": 0}
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            for trip_id, doc in conn.execute("SELECT id, doc FROM trips").fetchall():
                try:
                    trip = SavedTrip.model_validate_json(doc)
                except ValueError:
                    stats["unreadable"] += 1
                    continue
                wanted = [h for h in trip.hikes if h.id in routes]
                stats["already_routed"] += sum(1 for h in wanted if h.route)
                missing = [h for h in wanted if not h.route]
                if not missing:
                    continue
                filled = with_routes(trip, routes)
                added = sum(1 for old, new in zip(trip.hikes, filled.hikes) if new.route and not old.route)
                stats["dropped_size_cap"] += len(missing) - added
                if added:
                    conn.execute("UPDATE trips SET doc = ? WHERE id = ?", (filled.model_dump_json(), trip_id))
                    stats["trips_updated"] += 1
                    stats["hikes_routed"] += added
    finally:
        conn.close()
    return stats


# --- entry point --------------------------------------------------------------

def main(argv: list[str] | None = None, fetcher: RouteFetcher | None = None,
         transport: httpx.AsyncBaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.routes", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="fetch routes for the hikes of every saved trip (run where Wikiloc answers)")
    f.add_argument("--from", dest="source", required=True,
                   help="the site's base URL (reads its public /api/saved-trips) or a local trips.db path")
    f.add_argument("--out", required=True, type=Path, help="routes file to write (JSON)")
    f.add_argument("--all", action="store_true", help="also fetch hikes that already have a route")
    a = sub.add_parser("apply", help="merge a routes file into a trips database (run on the server)")
    a.add_argument("routes_file", type=Path)
    a.add_argument("--db", type=Path, default=None, help="trips database (default: TRIPS_DB / data/trips.db)")
    args = parser.parse_args(argv)

    if args.command == "fetch":
        if fetcher is None:
            from app.cache import DiskCache
            from app.config import Settings
            fetcher = RouteFetcher(cache=DiskCache(Settings().cache_dir), cooldown_s=0)
        routes, stats = asyncio.run(fetch_routes(args.source, fetcher, refetch=args.all, transport=transport))
        args.out.write_text(json.dumps({"version": ROUTES_FILE_VERSION, "routes": routes}, indent=0))
        print(f"{stats['trips']} trips, {stats['hikes']} hikes; fetched {stats['fetched']} of {stats['wanted']} "
              f"without a route ({stats['failed']} failed) → {args.out}")
        return 0

    db = args.db
    if db is None:
        from app.config import Settings
        db = Settings().trips_db
    routes = load_routes_file(args.routes_file)
    stats = apply_routes(db, routes)
    print(f"{len(routes)} routes in {args.routes_file}; {stats['hikes_routed']} hikes in {stats['trips_updated']} "
          f"trips got a route; {stats['already_routed']} already had one; {stats['dropped_size_cap']} left out "
          f"(trip size cap); {stats['unreadable']} unreadable trips skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
