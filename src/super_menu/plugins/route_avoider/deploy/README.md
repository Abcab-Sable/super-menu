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
<https://download.geofabrik.de> (a country, sub-region, or a whole continent), then:
```bash
docker compose down
rm -rf ./tiles                        # drop the old region's tiles
# set force_rebuild=True for one run, or just let the fresh volume rebuild
docker compose up -d
```

## Resource guide
| Coverage | Disk | RAM to build/run | Where |
|---|---|---|---|
| A country (e.g. Great Britain) | a few GB | ~4–8 GB | a laptop is fine |
| A continent (e.g. Europe) | tens of GB | 16 GB+ | a small VPS (~$40–80/mo) |

Cost is only compute: the engine, the OSM data, and the tiles are all free. Run it
locally and it's $0. Stop it any time with `docker compose down`.

## Notes
- Geocoding (place-name → point) is **not** part of Valhalla. The web UI resolves
  names via OpenStreetMap Nominatim; in the TUI/CLI pass coordinates (`lat,lng`).
- Avoid-zone circles are sent to Valhalla as `exclude_polygons`, so routes bend
  around your zones on real roads. "Avoid motorways" lowers Valhalla's
  `use_highways` weight for the `auto` costing.
