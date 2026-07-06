# Self-hosted routing (Valhalla)

Route through your own engine instead of the OpenRouteService API — **no API key,
no per-request cost, no rate limits**, and it keeps working offline once the tiles
are built. This is the production path from the route-avoider proposal; the
`ValhallaAdapter` sits behind the same interface as the hosted engine, so nothing
above the adapter changes.

## Prerequisites
Docker + Docker Compose.

## 1. Build tiles and start the engine
```bash
cd src/super_menu/plugins/route_avoider/deploy
docker compose up -d
```
The **first** run downloads a Great Britain OSM extract (~1.5 GB) and builds
routing tiles into `./tiles` — roughly 10–30 min depending on your machine. Follow
progress with:
```bash
docker compose logs -f
```
Later starts reuse the tiles and come up in seconds.

## 2. Point super-menu at it
Add to your repo-root `.env`:
```
VALHALLA_URL=http://localhost:8002
```
Now `super-menu web` (and the TUI/CLI/MCP) route through your engine. Verify with:
```bash
super-menu route-avoider config      # → "engine": "valhalla"
```
`VALHALLA_URL` takes precedence over `ORS_API_KEY`, so you can leave a key set as a
fallback.

## Change the region
Edit `tile_urls` in `docker-compose.yml` to any extract from
<https://download.geofabrik.de> (a country, sub-region, or a whole continent), then
set `force_rebuild=True` for one run so the new region's tiles are built:
```bash
docker compose up -d --force-recreate   # rebuilds tiles for the new region
```
Flip `force_rebuild` back to `False` afterwards. Don't `rm -rf ./tiles` — that would
delete the tracked `valhalla.json` seed (see below); `force_rebuild` rebuilds the
tiles in place and the container re-merges the seed, so the avoid-zone limit sticks.

## Avoid-zone size limit — already handled
Valhalla's stock config caps `exclude_polygons` at a **10 km circumference**, and the
cap is on the *summed* perimeter of **every** avoid zone in a request — not per zone.
A ~1.6 km-radius circle already fills 10 km, so any real avoid set is rejected with
"Exceeded maximum circumference for exclude_polygons". (The obvious "raise it to
1,000,000 m" is a trap: that's ~1,000 km of *total* perimeter — only a single
~160 km-radius zone's worth — still well under what a handful of zones sum to.)

So this deploy just removes the ceiling. The tracked `tiles/valhalla.json` is a
one-key seed setting `service_limits.max_exclude_polygons_length` to
**1,000,000,000 m**; the container deep-merges it into the freshly generated config
on first build (`update_existing_config=True` backfills every other key, so nothing
else is pinned). That's ~8× the plugin's own hard maximum — 40 zones × 500 km radius
≈ 1.26×10⁸ m of perimeter — so under the plugin's `MAX_ZONES` / `MAX_RADIUS_KM` caps
you *can't* reach the limit. There is nothing to configure.

Pointing super-menu at a **different** Valhalla whose `valhalla.json` lacks this key is
the only way to still hit the wall; set `max_exclude_polygons_length` there and restart.

## Resource guide
| Coverage | Disk | RAM to build/run | Where |
|---|---|---|---|
| A country (e.g. Great Britain) | a few GB | ~8–10 GB to build, less to run | a laptop is fine |
| A continent (e.g. Europe) | tens of GB | 16 GB+ | a small VPS (~$40–80/mo) |

**Windows/WSL2 gotcha:** Docker Desktop's VM defaults to 50% of host RAM. If the
build exceeds that, the VM dies mid-build and — because tiles are only written at
the end — the restarted container starts over, forever. On a 16 GB machine create
`%USERPROFILE%\.wslconfig` with `[wsl2]` / `memory=11GB`, run `wsl --shutdown`, and
restart Docker before building. A GB build takes ~35 min on 4 threads once it has
the memory.

Cost is only compute: the engine, the OSM data, and the tiles are all free. Run it
locally and it's $0. Stop it any time with `docker compose down`.

## Notes
- Geocoding (place-name → point) is **not** part of Valhalla. The web UI resolves
  names via OpenStreetMap Nominatim; in the TUI/CLI pass coordinates (`lat,lng`).
- Avoid-zone circles are sent to Valhalla as `exclude_polygons`, so routes bend
  around your zones on real roads. "Avoid motorways" lowers Valhalla's
  `use_highways` weight for the `auto` costing.
