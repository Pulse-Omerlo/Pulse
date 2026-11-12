# Sector targets

An optional file per network sets how its sector mix gaps are found. The file is named
after the tenant's `omerlo_media_id`, or its tenant id when it has none, for example
`demo-capitale.toml`. Set `SECTOR_TARGETS_DIR` to read them from elsewhere.

```toml
gap_points = 10      # a sector's share of organizations and of people further apart than this is a gap

region = "Québec"    # compare with this census metropolitan area (name or code)
region_margin = 5    # a sector more than 5 points below the region's share is a gap

[targets]
"31-33" = 8          # at least 8% of the network's organizations should be in manufacturing
"54" = 12.5
```

- Without a file, `gap_points` is 10 and there are no targets.
- Sector codes are the ones in `../naics_sectors.toml`. Targets are percentages of the
  network's classified organizations.
- A sector below its target is a gap even when the network has no organization in it.
- `region` is one of Quebec's census metropolitan areas in `../business_counts/quebec_cma.json`:
  Saguenay (408), Québec (421), Sherbrooke (433), Trois-Rivières (442), Drummondville (447)
  or Montréal (462). Without it, the network is not compared with a region. `region_margin`
  is 5 when left out.
- The new values apply from the next sync.
