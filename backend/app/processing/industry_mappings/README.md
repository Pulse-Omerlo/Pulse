# Industry mappings

One file per network maps its own categories to NAICS sectors (`../naics_sectors.toml`).
The file is named after the tenant's `omerlo_media_id`, or its tenant id when it has none,
for example `demo-capitale.toml`. Set `INDUSTRY_MAPPINGS_DIR` to read them from elsewhere.

```toml
[categories."<category id>"]
name = "Gastronomie / Food"   # for people reading the file
sectors = ["72"]              # [] means known, but not an industry (for example News)
```

- Categories decide first. When none of an organization's categories maps to a sector,
  the keywords in `../industry_keywords.toml` are tried on its name and description.
- A category that appears on organizations but is missing from the file is logged as
  unmapped after each sync, so it can be added.
- Changing a file classifies the network's organizations again on the next sync.

The two demo files match the synthetic fixtures built with the default seed.
